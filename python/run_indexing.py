"""
Standalone runner — processes pending indexing_jobs and writes transcript_chunks.

Usage:
    python run_indexing.py                         # process up to 5 pending jobs then exit
    python run_indexing.py --limit 20              # process up to 20 then exit
    python run_indexing.py --video-id dQw4w        # process one specific video then exit
    python run_indexing.py --watch                 # loop forever: drain queue, sleep, repeat
    python run_indexing.py --watch --interval 120  # loop with 2-minute pause between batches
    python run_indexing.py --watch --batch 50      # process 50 jobs per cycle
"""
import asyncio
import argparse
import logging
import signal
import sys
from sqlalchemy import select, text

# Force UTF-8 output so Unicode box-drawing chars work on Windows terminals
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from config import settings
from db.session import AsyncSessionLocal
from db.models import IndexingJob, TranscriptChunk
from services.classifier.classifier import VideoClassifier
from services.embedding.model import EmbeddingModel
from services.indexing.pipeline import IndexingPipeline
from services.transcript.fetcher import TranscriptRateLimitError, clear_rate_ban

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run_indexing")

# Pause durations after a 429 when YouTube doesn't send a Retry-After header.
# 30 min → 60 min → 90 min, escalating per consecutive rate-limited cycle.
_BAN_DURATIONS = (1800, 3600, 5400)


async def _load_classifier() -> VideoClassifier:
    classifier = VideoClassifier()
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("SELECT slug, embedding FROM industries WHERE embedding IS NOT NULL AND is_active = TRUE")
        )
        rows = result.fetchall()
    if rows:
        classifier.set_industry_embeddings({r.slug: list(r.embedding) for r in rows})
        logger.info("Classifier: loaded %d industry embeddings", len(rows))
    else:
        logger.info("Classifier: no industry embeddings found — using keyword matching only")
    return classifier


async def _fetch_jobs(limit: int, video_id: str | None) -> list[IndexingJob]:
    async with AsyncSessionLocal() as session:
        q = (
            select(IndexingJob)
            .where(IndexingJob.status == "pending")
            .where(IndexingJob.attempts < IndexingJob.max_attempts)
            .order_by(IndexingJob.priority.desc(), IndexingJob.created_at.asc())
        )
        if video_id:
            q = q.where(IndexingJob.youtube_video_id == video_id)
        q = q.limit(limit)
        result = await session.execute(q)
        return list(result.scalars().all())


async def _print_chunks(video_id_int: int, yt_id: str) -> int:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(TranscriptChunk)
            .where(TranscriptChunk.video_id == video_id_int)
            .order_by(TranscriptChunk.chunk_index)
        )
        chunks = result.scalars().all()

    if not chunks:
        return 0

    bar = "─" * 72
    print(f"\n{bar}")
    print(f"  transcript_chunks for  {yt_id}  ({len(chunks)} chunks)")
    print(bar)
    for c in chunks:
        snippet = c.text[:100].replace("\n", " ")
        if len(c.text) > 100:
            snippet += "…"
        print(f"  [{c.chunk_index:03d}]  {c.start_time:>8.1f}s – {c.end_time:<8.1f}s  {snippet}")
    print(bar + "\n")
    return len(chunks)


async def _run_batch(
    limit: int,
    video_id: str | None,
    model: EmbeddingModel,
    classifier: VideoClassifier,
) -> dict:
    jobs = await _fetch_jobs(limit, video_id)

    if not jobs:
        logger.info("No pending jobs — queue is empty")
        return {"indexed": 0, "skipped": 0, "failed": 0, "rate_limited": 0, "total": 0}

    logger.info("Found %d pending job(s) to process", len(jobs))
    indexed, skipped, failed, rate_limited = 0, 0, 0, 0
    retry_after_s: int | None = None

    for job in jobs:
        yt_id = job.youtube_video_id
        logger.info("┌── Job %d │ %s │ priority=%d attempts=%d",
                    job.id, yt_id, job.priority, job.attempts)

        try:
            async with AsyncSessionLocal() as session:
                fresh = await session.merge(job)
                pipeline = IndexingPipeline(
                    session=session,
                    embedding_model=model,
                    classifier=classifier,
                )
                ok = await pipeline.run(fresh)
                vid_pk = fresh.video_id

            async with AsyncSessionLocal() as session:
                refreshed = await session.get(IndexingJob, job.id)
                final_status = refreshed.status if refreshed else "unknown"
                error_msg    = refreshed.error_message if refreshed else ""

        except TranscriptRateLimitError as exc:
            logger.warning("└── ⏳ rate-limit %s — left pending, cooldown active", yt_id)
            rate_limited += 1
            if exc.retry_after:
                retry_after_s = max(retry_after_s or 0, exc.retry_after)
            continue
        except Exception as exc:
            logger.error("└── ✗ job %s crashed (DB/network error): %s", yt_id, exc)
            failed += 1
            continue

        if ok and vid_pk:
            count = await _print_chunks(vid_pk, yt_id)
            logger.info("└── ✓ indexed  %s → %d chunk(s) written to transcript_chunks", yt_id, count)
            indexed += 1
        elif final_status == "skipped":
            logger.warning("└── ⊘ skipped  %s — %s", yt_id, error_msg or "no transcript")
            skipped += 1
        elif "rate" in (error_msg or "").lower() or "429" in (error_msg or ""):
            logger.warning("└── ⏳ rate-limit %s — left pending, retry later", yt_id)
            rate_limited += 1
        else:
            logger.error("└── ✗ failed   %s — %s", yt_id, error_msg or "see logs above")
            failed += 1

    return {"indexed": indexed, "skipped": skipped, "failed": failed,
            "rate_limited": rate_limited, "total": len(jobs),
            "retry_after_s": retry_after_s}


async def run(limit: int, video_id: str | None) -> None:
    logger.info("Loading embedding model: %s", settings.embedding_model_name)
    model = EmbeddingModel.get()
    logger.info("Model ready")

    classifier = await _load_classifier()
    stats = await _run_batch(limit, video_id, model, classifier)

    print(f"\n{'═' * 72}")
    print(f"  SUMMARY   ✓ indexed: {stats['indexed']}   ⊘ skipped: {stats['skipped']}"
          f"   ⏳ rate-limited: {stats['rate_limited']}   ✗ failed: {stats['failed']}")
    print(f"{'═' * 72}\n")


async def run_watch(batch: int, interval: int) -> None:
    logger.info("Loading embedding model: %s", settings.embedding_model_name)
    model = EmbeddingModel.get()
    logger.info("Model ready — watch mode: batch=%d  interval=%ds  (Ctrl+C to stop)", batch, interval)

    classifier = await _load_classifier()

    total_indexed = 0
    cycle = 0
    ban_strikes = 0
    stop = asyncio.Event()

    def _on_signal(*_):
        logger.info("Shutdown signal received — finishing current batch then stopping…")
        stop.set()

    signal.signal(signal.SIGINT,  _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    while not stop.is_set():
        cycle += 1
        logger.info("── Cycle %d ─────────────────────────────────────────────", cycle)
        stats = await _run_batch(batch, None, model, classifier)
        total_indexed += stats["indexed"]

        print(f"  Cycle {cycle}  ✓ {stats['indexed']}  ⊘ {stats['skipped']}"
              f"  ⏳ {stats['rate_limited']}  ✗ {stats['failed']}"
              f"  │  total indexed so far: {total_indexed}")

        if stats["rate_limited"] > 0:
            # 429 hit — pause before the next cycle, then clear the ban flag
            # so the next cycle is allowed to probe YouTube again.
            wait = stats.get("retry_after_s") or _BAN_DURATIONS[min(ban_strikes, len(_BAN_DURATIONS) - 1)]
            ban_strikes += 1
            logger.warning("YouTube 429 — pausing %dm before next cycle (strike %d)",
                           wait // 60, ban_strikes)
            try:
                await asyncio.wait_for(stop.wait(), timeout=wait)
            except asyncio.TimeoutError:
                pass
            clear_rate_ban()
            continue

        ban_strikes = 0
        if stats["total"] == 0:
            logger.info("Queue empty — sleeping %ds before next check…", interval)

        # Interruptible sleep: wakes immediately on Ctrl+C
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass

    logger.info("Watch mode stopped. Total indexed this session: %d", total_indexed)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process indexing_jobs → transcript_chunks")
    parser.add_argument("--limit",    type=int, default=5,   help="Max jobs per run (one-shot mode, default: 5)")
    parser.add_argument("--video-id", metavar="YT_ID",       help="Process one specific YouTube video ID (one-shot)")
    parser.add_argument("--watch",    action="store_true",   help="Loop forever, draining queue continuously")
    parser.add_argument("--batch",    type=int, default=20,  help="Jobs per cycle in watch mode (default: 20)")
    parser.add_argument("--interval", type=int, default=60,  help="Seconds to sleep between cycles when queue is empty (default: 60)")
    args = parser.parse_args()

    if args.watch:
        asyncio.run(run_watch(args.batch, args.interval))
    else:
        asyncio.run(run(args.limit, args.video_id))
