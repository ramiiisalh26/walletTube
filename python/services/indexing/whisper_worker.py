"""
Whisper retry worker — slow path for videos with no YouTube captions.

Reads indexing_retry_jobs WHERE error_message = 'No transcript available'
and transcribes each video's audio using faster-whisper, then runs the full
indexing pipeline (chunk → embed → store).

Runs as a separate cron job so it never blocks the main indexing pipeline.
Parallel: processes up to WHISPER_CONCURRENCY videos simultaneously using
asyncio.gather + thread offloading (Whisper releases the GIL so threads
give real parallelism here).
"""
import asyncio
import logging
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, text

from config import settings
from db.models import IndexingRetryJob, Transcript, TranscriptChunk, TranscriptParentChunk, Video
from db.session import AsyncSessionLocal
from services.embedding.model import EmbeddingModel
from services.transcript.chunker import chunk_transcript_with_parents
from services.transcript.fetcher import (
    TranscriptResult,
    _COOKIES,
    _YT_DLP_EXE,
    _resolve_cookies,
)

logger = logging.getLogger(__name__)

# Max parallel Whisper jobs. Each job downloads audio (~50 MB) and uses 1 CPU core.
# 2 is a safe default on a standard server — increase if you have more cores free.
WHISPER_CONCURRENCY = 2

# Language codes Whisper often misdetects when audio has little/no real speech
_SUSPICIOUS_LANGS = {
    "nn", "nb", "fo", "is", "gd", "cy", "ga", "gv", "kw", "br",
    "la", "sa", "pi", "cu", "und", "xx",
}

_whisper_model = None


def _get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        _whisper_model = WhisperModel(
            settings.whisper_model_size,
            device=settings.device,               # "cpu" dev | "cuda" GPU prod
            compute_type=settings.whisper_compute_type,  # "int8" CPU | "float16" GPU
        )
        logger.info(
            "Whisper %s loaded (device=%s compute=%s)",
            settings.whisper_model_size, settings.device, settings.whisper_compute_type,
        )
    return _whisper_model


def _transcribe_with_whisper(video_id: str) -> "tuple[TranscriptResult | None, str]":
    """
    Download audio via yt-dlp then transcribe with faster-whisper.
    Returns (result, reason) — reason is empty string on success, error description on failure.
    Runs in a thread.
    """
    cookies = _resolve_cookies()
    with tempfile.TemporaryDirectory() as tmp:
        out_template = str(Path(tmp) / f"{video_id}.%(ext)s")
        cmd = [
            _YT_DLP_EXE,
            "--extract-audio",
            "--audio-format", "mp3",
            "--audio-quality", "9",
            "--quiet",
            "-o", out_template,
        ]
        if cookies:
            cmd += ["--cookies", cookies]
        cmd.append(f"https://www.youtube.com/watch?v={video_id}")

        try:
            proc = subprocess.run(cmd, check=False, capture_output=True, timeout=300)
            if proc.returncode != 0:
                stderr = proc.stderr.decode(errors="replace").strip()
                reason = f"yt-dlp failed: {stderr[:150]}"
                logger.warning("yt-dlp audio download failed for %s: %s", video_id, stderr[:200])
                return None, reason
        except subprocess.TimeoutExpired:
            reason = "yt-dlp timeout (>300s)"
            logger.warning("yt-dlp timed out for %s", video_id)
            return None, reason
        except FileNotFoundError:
            reason = "yt-dlp not found on PATH"
            logger.warning("yt-dlp not found for %s", video_id)
            return None, reason

        audio_files = list(Path(tmp).glob(f"{video_id}.*"))
        if not audio_files:
            reason = "yt-dlp produced no audio file"
            logger.warning("No audio file after yt-dlp for %s", video_id)
            return None, reason

        try:
            model = _get_whisper_model()
            seg_iter, info = model.transcribe(str(audio_files[0]), beam_size=5)
            segments = [
                {"text": seg.text.strip(), "start": seg.start,
                 "duration": max(0.1, seg.end - seg.start)}
                for seg in seg_iter if seg.text.strip()
            ]
        except Exception as exc:
            reason = f"Whisper error: {exc}"
            logger.warning("Whisper transcription failed for %s: %s", video_id, exc)
            return None, reason

    if not segments:
        lang = getattr(info, "language", None) or "unknown"
        reason = f"Whisper found 0 speech segments (lang={lang}) — likely music/no speech"
        logger.warning("0 segments for %s lang=%s", video_id, lang)
        return None, reason

    lang = getattr(info, "language", None) or "en"
    logger.info("Whisper transcribed %s: %d segments lang=%s", video_id, len(segments), lang)
    return TranscriptResult(segments, "whisper", lang), ""


async def _process_one(retry_job: IndexingRetryJob, model: EmbeddingModel) -> bool:
    """Transcribe one video with Whisper and write chunks to DB. Returns True on success."""
    video_id = retry_job.youtube_video_id

    # Run blocking Whisper call in a thread (releases GIL → real parallelism)
    result, fail_reason = await asyncio.to_thread(
        _transcribe_with_whisper, video_id
    )

    async with AsyncSessionLocal() as session:
        if result is None:
            retry_job = await session.merge(retry_job)
            retry_job.attempts += 1
            retry_job.error_message = fail_reason   # store why it failed
            retry_job.retry_exhausted = retry_job.attempts >= retry_job.max_attempts
            if retry_job.retry_exhausted:
                retry_job.status = "permanently_failed"
                logger.info("Whisper giving up on %s: %s", video_id, fail_reason)
            await session.commit()
            return False

        # Find the video row
        video = (await session.execute(
            select(Video).where(Video.youtube_video_id == video_id)
        )).scalar_one_or_none()

        if video is None:
            logger.warning("Video row missing for %s — skipping", video_id)
            return False

        # Chunk
        parents, chunks = chunk_transcript_with_parents(result.segments)
        if not chunks:
            return False

        # Resolve language
        lang_row = (await session.execute(
            text("SELECT id FROM languages WHERE code = :c LIMIT 1"),
            {"c": result.language_code},
        )).fetchone()
        if not lang_row:
            lang_row = (await session.execute(
                text("SELECT id FROM languages WHERE code = 'en' LIMIT 1")
            )).fetchone()
        lang_id = lang_row[0]

        # Write transcript
        full_text = " ".join(c.text for c in chunks)
        transcript = Transcript(
            video_id=video.id,
            language_id=lang_id,
            source="whisper",
            full_text=full_text,
            word_count=len(full_text.split()),
            is_primary=True,
            model_version="BAAI/bge-base-en-v1.5",
        )
        session.add(transcript)
        await session.flush()

        # Write parent chunks
        parent_rows = [
            TranscriptParentChunk(
                transcript_id=transcript.id, video_id=video.id,
                chunk_index=p.index, text=p.text,
                start_time=p.start_time, end_time=p.end_time,
                word_count=p.word_count,
            )
            for p in parents
        ]
        session.add_all(parent_rows)
        await session.flush()
        parent_id_map = {p.index: parent_rows[i].id for i, p in enumerate(parents)}

        # Embed + write child chunks
        embed_texts = [
            (c.prev_text[-80:] + " " + c.text if c.prev_text else c.text)
            for c in chunks
        ]
        vectors = await asyncio.to_thread(model.encode_batch, embed_texts)

        session.add_all([
            TranscriptChunk(
                transcript_id=transcript.id, video_id=video.id,
                chunk_index=c.index, text=c.text,
                start_time=c.start_time, end_time=c.end_time,
                prev_text=c.prev_text, next_text=c.next_text,
                parent_chunk_id=parent_id_map.get(c.parent_index),
                embedding=vec,
            )
            for c, vec in zip(chunks, vectors)
        ])

        # Update video embedding + status
        video.embedding = model.mean_vector(vectors)
        video.indexing_status = "indexed"
        video.indexed_at = datetime.now(timezone.utc)

        # Mark retry job done
        retry_job = await session.merge(retry_job)
        retry_job.status = "done"
        retry_job.retry_exhausted = True
        retry_job.attempts += 1

        # Auto-flag into video_language_review if language is suspicious or
        # segment count is very low (likely no real speech in the video)
        seg_count = len(chunks)
        is_suspicious_lang = result.language_code in _SUSPICIOUS_LANGS
        is_low_segments = seg_count < 20
        if is_suspicious_lang or is_low_segments:
            reason = "suspicious_language" if is_suspicious_lang else "low_segments"
            if is_suspicious_lang and is_low_segments:
                reason = "suspicious_language+low_segments"
            await session.execute(text(
                "INSERT INTO video_language_review "
                "(video_id, transcript_id, detected_lang, segment_count, reason) "
                "VALUES (:vid, :tid, :lang, :segs, :reason) "
                "ON CONFLICT DO NOTHING"
            ), {
                "vid": video.id,
                "tid": transcript.id,
                "lang": result.language_code,
                "segs": seg_count,
                "reason": reason,
            })
            logger.warning(
                "Flagged %s for review: lang=%s segments=%d reason=%s",
                video_id, result.language_code, seg_count, reason,
            )

        await session.commit()
        logger.info("Whisper indexed %s — %d chunks", video_id, len(chunks))
        return True


async def run_whisper_retry(batch_size: int = 10) -> dict:
    """
    Pull up to batch_size pending whisper-retry jobs and process them in parallel.
    Called by the Celery cron job every few hours.
    """
    async with AsyncSessionLocal() as session:
        jobs = (await session.execute(
            select(IndexingRetryJob)
            .where(IndexingRetryJob.error_message == "No transcript available")
            .where(IndexingRetryJob.retry_exhausted == False)   # noqa: E712
            .where(IndexingRetryJob.status == "pending")
            .where(IndexingRetryJob.attempts < IndexingRetryJob.max_attempts)
            .order_by(IndexingRetryJob.created_at.asc())
            .limit(batch_size)
        )).scalars().all()

    if not jobs:
        logger.info("whisper_retry: no pending jobs")
        return {"processed": 0, "succeeded": 0, "failed": 0}

    logger.info("whisper_retry: processing %d jobs (concurrency=%d)", len(jobs), WHISPER_CONCURRENCY)

    model = EmbeddingModel.get()
    sem = asyncio.Semaphore(WHISPER_CONCURRENCY)

    async def _bounded(job):
        async with sem:
            return await _process_one(job, model)

    results = await asyncio.gather(*[_bounded(j) for j in jobs], return_exceptions=True)

    succeeded = sum(1 for r in results if r is True)
    failed = sum(1 for r in results if r is not True)
    logger.info("whisper_retry: succeeded=%d failed=%d", succeeded, failed)
    return {"processed": len(jobs), "succeeded": succeeded, "failed": failed}
