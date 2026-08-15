"""
Core indexing pipeline — one video at a time.

Steps (in order):
  1. Fetch transcript  (TranscriptFetcher)
  2. Classify industry (VideoClassifier — if not already known)
  3. Chunk transcript  (chunker)
  4. Batch embed chunks (EmbeddingModel)
  5. Write to DB       (transcripts + transcript_chunks rows)
  6. Compute & store video-level embedding (UPDATE videos SET embedding = mean(chunks))
  7. Mark job done
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    Channel, IndexingJob, IndexingRetryJob, Language, Transcript,
    TranscriptChunk, TranscriptParentChunk, Video,
)
from services.classifier.classifier import VideoClassifier
from services.embedding.model import EmbeddingModel
from services.transcript.chunker import chunk_transcript_with_parents
from services.transcript.fetcher import TranscriptRateLimitError, TranscriptResult, fetch_transcript

logger = logging.getLogger(__name__)

# Set to True the first time a quotaExceeded error is hit; cleared on worker restart.
# Prevents hammering the API with calls that will all fail for the rest of the day.
_metadata_quota_exhausted: bool = False


class IndexingPipeline:

    def __init__(
        self,
        session: AsyncSession,
        embedding_model: EmbeddingModel,
        classifier: VideoClassifier,
    ) -> None:
        self._db = session
        self._model = embedding_model
        self._classifier = classifier

    async def run(self, job: IndexingJob) -> bool:
        """
        Execute the full pipeline for one job.
        Returns True on success, False on unrecoverable failure.
        """
        video_id = job.youtube_video_id
        logger.info("Indexing video %s (job %s)", video_id, job.id)

        # ── 1. Ensure video row exists ────────────────────────────────────────
        video = await self._get_or_create_video(job)
        if video is None:
            await self._fail_job(job, "Could not resolve video row")
            return False

        # Already fully indexed (job stayed pending after a crash mid-commit)
        if video.indexing_status == "indexed":
            job.status = "done"
            job.updated_at = datetime.now(timezone.utc)
            await self._db.commit()
            logger.info("Video %s already indexed — marking job done", video_id)
            return True

        # ── 2. Fetch transcript ───────────────────────────────────────────────
        try:
            result: TranscriptResult | None = await fetch_transcript(video_id)
        except TranscriptRateLimitError:
            await self._rate_limit_job(job)
            raise  # propagate so the worker can stop the entire batch
        if result is None:
            await self._skip_job(job, "No transcript available")
            return False

        # ── 3. Classify industry (if not already on the video) ────────────────
        if video.primary_industry_id is None:
            await self._classify_video(video, job)

        # ── 4. Chunk (two-level: parents for context, children for search) ───────
        parents, chunks = chunk_transcript_with_parents(result.segments)
        if not chunks:
            await self._skip_job(job, "Transcript produced zero chunks")
            return False

        # ── 5. Resolve language row ───────────────────────────────────────────
        lang_id = await self._resolve_language(result.language_code)

        # ── 6. Write transcript row ───────────────────────────────────────────
        full_text = " ".join(c.text for c in chunks)
        transcript = Transcript(
            video_id=video.id,
            language_id=lang_id,
            source=_map_source(result.source),
            full_text=full_text,
            word_count=len(full_text.split()),
            is_primary=True,
            model_version="BAAI/bge-base-en-v1.5",
        )
        try:
            async with self._db.begin_nested():   # savepoint — rolls back only this block on conflict
                self._db.add(transcript)
                await self._db.flush()
        except IntegrityError:
            # Another concurrent job for the same video_id already committed this
            # transcript row. The savepoint was rolled back automatically so the
            # outer transaction is still valid — just mark the job done and exit.
            job.status = "done"
            job.updated_at = datetime.now(timezone.utc)
            await self._db.commit()
            logger.info("Video %s transcript already written by concurrent job — marking done", video_id)
            return True

        # ── 7. Write parent chunks (text-only, no embedding) ──────────────────
        parent_rows = [
            TranscriptParentChunk(
                transcript_id=transcript.id,
                video_id=video.id,
                chunk_index=p.index,
                text=p.text,
                start_time=p.start_time,
                end_time=p.end_time,
                word_count=p.word_count,
            )
            for p in parents
        ]
        self._db.add_all(parent_rows)
        await self._db.flush()  # get parent IDs

        parent_id_map = {p.index: parent_rows[i].id for i, p in enumerate(parents)}

        # ── 8. Embed children only (prepend last 80 chars of prev boundary) ───
        embed_texts = [
            (c.prev_text[-80:] + " " + c.text if c.prev_text else c.text)
            for c in chunks
        ]
        # Offload CPU-bound encoding to a thread so the event loop stays free
        # for other parallel pipeline coroutines during this ~1-3s call.
        vectors = await asyncio.to_thread(self._model.encode_batch, embed_texts)

        # ── 9. Write child chunk rows with parent FK ──────────────────────────
        chunk_rows = [
            TranscriptChunk(
                transcript_id=transcript.id,
                video_id=video.id,
                chunk_index=c.index,
                text=c.text,
                start_time=c.start_time,
                end_time=c.end_time,
                prev_text=c.prev_text,
                next_text=c.next_text,
                parent_chunk_id=parent_id_map.get(c.parent_index),
                embedding=vec,
            )
            for c, vec in zip(chunks, vectors)
        ]
        self._db.add_all(chunk_rows)

        # ── 10. Compute video-level embedding (mean of child vectors) ──────────
        video.embedding = self._model.mean_vector(vectors)
        video.indexing_status = "indexed"
        video.indexed_at = datetime.now(timezone.utc)

        # ── 11. Mark job done ────────────────────────────────────────────────
        job.status = "done"
        job.updated_at = datetime.now(timezone.utc)

        await self._db.commit()
        logger.info("Indexed video %s — %d chunks", video_id, len(chunks))
        return True

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _get_or_create_video(self, job: IndexingJob) -> Video | None:
        if job.video_id:
            v = await self._db.get(Video, job.video_id)
            if v:
                return v

        # Video row might not exist yet (job was queued before the video was crawled)
        result = await self._db.execute(
            select(Video).where(Video.youtube_video_id == job.youtube_video_id)
        )
        video = result.scalar_one_or_none()
        if video:
            job.video_id = video.id
            return video

        # Create a minimal row — use a savepoint so a concurrent INSERT from the
        # crawler doesn't abort the whole transaction
        try:
            async with self._db.begin_nested():
                video = Video(
                    youtube_video_id=job.youtube_video_id,
                    title=job.youtube_video_id,
                    channel_id=job.channel_id,
                    primary_industry_id=job.industry_id,
                    indexing_status="processing",
                )
                self._db.add(video)
                await self._db.flush()
            await self._enrich_video_metadata(video)
        except IntegrityError:
            # Crawler beat us — fetch the row it just committed
            result = await self._db.execute(
                select(Video).where(Video.youtube_video_id == job.youtube_video_id)
            )
            video = result.scalar_one_or_none()
            if video is None:
                return None

        job.video_id = video.id
        return video

    async def _classify_video(self, video: Video, job: IndexingJob) -> None:
        channel_slug: str | None = None
        if video.channel_id:
            ch = await self._db.get(Channel, video.channel_id)
            if ch and ch.primary_industry_id:
                video.primary_industry_id = ch.primary_industry_id
                await self._assign_topic(video)
                return

        result = self._classifier.classify(
            title=video.title or "",
            description=video.description or "",
            channel_industry_slug=channel_slug,
        )
        if result.industry_slug:
            ind = await self._db.execute(
                text("SELECT id FROM industries WHERE slug = :s"),
                {"s": result.industry_slug},
            )
            row = ind.fetchone()
            if row:
                video.primary_industry_id = row[0]
        await self._assign_topic(video)

    async def _enrich_video_metadata(self, video: Video) -> None:
        """Fetch real title/description/tags from YouTube API and store on the video row."""
        global _metadata_quota_exhausted
        from config import settings as _settings
        if not _settings.youtube_api_key or _metadata_quota_exhausted:
            return
        try:
            from services.crawler.channel_crawler import YouTubeCrawler
            metas = await asyncio.to_thread(
                lambda: YouTubeCrawler().get_videos_meta([video.youtube_video_id])
            )
            if not metas:
                return
            meta = metas[0]
            video.title           = meta.title
            video.description     = meta.description
            video.thumbnail_url   = meta.thumbnail_url
            video.duration_seconds = meta.duration_seconds
            video.view_count      = meta.view_count or 0
            video.tags            = meta.tags or []
            video.yt_category_id  = meta.yt_category_id
            if meta.published_at:
                video.published_at = datetime.fromisoformat(
                    meta.published_at.replace("Z", "+00:00")
                )
        except Exception as exc:
            if "quotaExceeded" in str(exc):
                _metadata_quota_exhausted = True
                logger.warning(
                    "YouTube Data API quota exhausted — metadata enrichment disabled "
                    "for this session. Videos will be indexed without titles/thumbnails "
                    "and backfilled when quota resets (midnight Pacific)."
                )
            else:
                logger.warning("Metadata fetch failed for %s: %s", video.youtube_video_id, exc)

    async def _assign_topic(self, video: Video) -> None:
        """Find the closest topic using title+description embedding (more accurate than mean chunk vector)."""
        if video.primary_industry_id is None or video.id is None:
            return
        text_to_embed = f"{video.title or ''}. {(video.description or '')[:300]}"
        vec = await asyncio.to_thread(self._model.encode_one, text_to_embed)
        vec_str = "[" + ",".join(str(v) for v in vec) + "]"
        row = (await self._db.execute(
            text("""
                SELECT id FROM topics
                WHERE industry_id = :ind_id AND is_active = TRUE AND embedding IS NOT NULL
                ORDER BY embedding <=> CAST(:vec AS vector)
                LIMIT 1
            """),
            {"ind_id": video.primary_industry_id, "vec": vec_str},
        )).fetchone()
        if row:
            await self._db.execute(
                text("""
                    INSERT INTO video_topics (video_id, topic_id, confidence)
                    VALUES (:vid, :tid, 0.85)
                    ON CONFLICT DO NOTHING
                """),
                {"vid": video.id, "tid": row[0]},
            )

    async def _resolve_language(self, lang_code: str) -> int:
        result = await self._db.execute(
            select(Language).where(Language.code == lang_code)
        )
        lang = result.scalar_one_or_none()
        if lang:
            return lang.id
        # Fallback to English
        result = await self._db.execute(select(Language).where(Language.code == "en"))
        return result.scalar_one().id

    async def _set_video_status(self, job: IndexingJob, status: str) -> None:
        """Keep the video row's indexing_status in sync with the job outcome.
        Without this the video is created as 'processing' and only ever moved to
        'indexed' on success — so any skip/fail/rate-limit left it stuck on
        'processing' forever (and 'unclassified', since classification needs a
        transcript). Never downgrades a row that already reached 'indexed'."""
        v = None
        if job.video_id:
            v = await self._db.get(Video, job.video_id)
        if v is None:
            result = await self._db.execute(
                select(Video).where(Video.youtube_video_id == job.youtube_video_id)
            )
            v = result.scalar_one_or_none()
        if v is not None and v.indexing_status != "indexed":
            v.indexing_status = status

    async def _fail_job(self, job: IndexingJob, reason: str) -> None:
        job.attempts += 1
        job.error_message = reason
        job.status = "failed" if job.attempts >= job.max_attempts else "pending"
        job.updated_at = datetime.now(timezone.utc)
        await self._set_video_status(job, job.status)
        self._db.add(IndexingRetryJob(
            original_job_id=job.id,
            youtube_video_id=job.youtube_video_id,
            channel_id=job.channel_id,
            reason="failed",
            error_message=reason,
            retry_after=datetime.now(timezone.utc) + timedelta(hours=1),
        ))
        await self._db.commit()
        logger.error("Job %s failed: %s — queued for retry in 1h", job.id, reason)

    async def _rate_limit_job(self, job: IndexingJob) -> None:
        """Keep the job pending (don't consume an attempt) so it retries after the ban lifts."""
        job.status = "pending"
        job.error_message = "YouTube 429 — rate limited, will retry"
        job.updated_at = datetime.now(timezone.utc)
        await self._set_video_status(job, "pending")
        await self._db.commit()
        logger.warning("Job %s rate-limited by YouTube — left as pending", job.id)

    async def _skip_job(self, job: IndexingJob, reason: str) -> None:
        job.status = "skipped"
        job.error_message = reason
        job.updated_at = datetime.now(timezone.utc)
        await self._set_video_status(job, "skipped")
        self._db.add(IndexingRetryJob(
            original_job_id=job.id,
            youtube_video_id=job.youtube_video_id,
            channel_id=job.channel_id,
            reason="skipped",
            error_message=reason,
            retry_after=datetime.now(timezone.utc) + timedelta(hours=24),
        ))
        await self._db.commit()
        logger.info("Job %s skipped: %s — queued for retry in 24h", job.id, reason)


def _map_source(raw_source: str) -> str:
    mapping = {
        "youtube_manual": "youtube_manual",
        "youtube_auto":   "youtube_auto",
        "yt_dlp":         "youtube_auto",  # yt-dlp fetches the same auto-captions
        "whisper":        "whisper",
    }
    return mapping.get(raw_source, "youtube_auto")