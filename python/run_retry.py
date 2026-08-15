"""
Retry cron — processes indexing_retry_jobs (failed/skipped videos).

Usage:
    python run_retry.py                   # process up to 10 due retry jobs then exit
    python run_retry.py --limit 50        # process up to 50 then exit
    python run_retry.py --watch           # loop forever (default interval: 3600s / 1h)
    python run_retry.py --watch --interval 1800  # check every 30 min
"""
import asyncio
import argparse
import logging
import signal
import sys
from datetime import datetime, timezone

from sqlalchemy import select

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from config import settings
from db.session import AsyncSessionLocal
from db.models import IndexingRetryJob, IndexingJob, TranscriptChunk
from services.classifier.classifier import VideoClassifier
from services.embedding.model import EmbeddingModel
from services.indexing.pipeline import IndexingPipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run_retry")


async def _load_classifier() -> VideoClassifier:
    from sqlalchemy import text
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
        logger.info("Classifier: no industry embeddings — keyword matching only")
    return classifier


async def _fetch_due_retries(limit: int) -> list[IndexingRetryJob]:
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as session:
        q = (
            select(IndexingRetryJob)
            .where(IndexingRetryJob.status == "pending")
            .where(IndexingRetryJob.retry_exhausted == False)  # noqa: E712
            .where(IndexingRetryJob.retry_after <= now)
            .order_by(IndexingRetryJob.retry_after.asc())
            .limit(limit)
        )
        result = await session.execute(q)
        return list(result.scalars().all())


async def _print_chunks(video_id_int: int, yt_id: str) -> int:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(TranscriptChunk)
            .where(TranscriptChunk.video_id == video_id_int)
            .order_by(TranscriptChunk.chunk_index)
        )
        return len(result.scalars().all())


async def _run_retry_batch(
    limit: int,
    model: EmbeddingModel,
    classifier: VideoClassifier,
) -> dict:
    retries = await _fetch_due_retries(limit)

    if not retries:
        logger.info("No due retry jobs")
        return {"indexed": 0, "skipped": 0, "failed": 0, "exhausted": 0, "total": 0}

    logger.info("Found %d due retry job(s)", len(retries))
    indexed, skipped, failed, exhausted = 0, 0, 0, 0

    for retry in retries:
        yt_id = retry.youtube_video_id
        logger.info("┌── Retry %d │ %s │ reason=%s attempts=%d/%d",
                    retry.id, yt_id, retry.reason, retry.attempts, retry.max_attempts)

        try:
            # Build a synthetic IndexingJob so the pipeline can run
            async with AsyncSessionLocal() as session:
                r = await session.merge(retry)
                r.attempts += 1
                r.updated_at = datetime.now(timezone.utc)

                # Find or create a real IndexingJob row for the pipeline
                job_result = await session.execute(
                    select(IndexingJob)
                    .where(IndexingJob.youtube_video_id == yt_id)
                    .order_by(IndexingJob.created_at.desc())
                    .limit(1)
                )
                job = job_result.scalar_one_or_none()
                if job is None:
                    job = IndexingJob(
                        youtube_video_id=yt_id,
                        channel_id=retry.channel_id,
                        status="pending",
                        queued_by="retry_cron",
                    )
                    session.add(job)
                    await session.flush()
                elif job.status in ("done", "skipped"):
                    # Already indexed by main loop since this retry was queued — mark done
                    r.status = "done"
                    await session.commit()
                    logger.info("└── ✓ already indexed  %s — marking retry done", yt_id)
                    indexed += 1
                    continue

                job.status = "pending"
                await session.flush()

                pipeline = IndexingPipeline(
                    session=session,
                    embedding_model=model,
                    classifier=classifier,
                )
                ok = await pipeline.run(job)
                vid_pk = job.video_id

            async with AsyncSessionLocal() as session:
                r2 = await session.get(IndexingRetryJob, retry.id)
                j2 = await session.get(IndexingJob, job.id) if job else None
                final_job_status = j2.status if j2 else "unknown"

                if ok:
                    r2.status = "done"
                    logger.info("└── ✓ retry indexed  %s", yt_id)
                    indexed += 1
                elif final_job_status == "skipped":
                    skipped += 1
                    if r2.attempts >= r2.max_attempts:
                        r2.retry_exhausted = True
                        r2.status = "permanently_failed"
                        logger.warning("└── ✗ retry exhausted  %s — permanently_failed", yt_id)
                        exhausted += 1
                        skipped -= 1
                    else:
                        from datetime import timedelta
                        r2.retry_after = datetime.now(timezone.utc) + timedelta(hours=24)
                        logger.warning("└── ⊘ retry skipped  %s — rescheduled 24h", yt_id)
                else:
                    if r2.attempts >= r2.max_attempts:
                        r2.retry_exhausted = True
                        r2.status = "permanently_failed"
                        logger.error("└── ✗ retry exhausted  %s — permanently_failed", yt_id)
                        exhausted += 1
                    else:
                        from datetime import timedelta
                        r2.retry_after = datetime.now(timezone.utc) + timedelta(hours=1)
                        logger.error("└── ✗ retry failed  %s — rescheduled 1h", yt_id)
                        failed += 1

                r2.updated_at = datetime.now(timezone.utc)
                await session.commit()

        except Exception as exc:
            logger.error("└── ✗ retry %d crashed: %s", retry.id, exc)
            failed += 1

    return {"indexed": indexed, "skipped": skipped, "failed": failed,
            "exhausted": exhausted, "total": len(retries)}


async def run(limit: int) -> None:
    logger.info("Loading embedding model: %s", settings.embedding_model_name)
    model = EmbeddingModel.get()
    logger.info("Model ready")
    classifier = await _load_classifier()
    stats = await _run_retry_batch(limit, model, classifier)

    print(f"\n{'═' * 72}")
    print(f"  RETRY SUMMARY   ✓ indexed: {stats['indexed']}   ⊘ skipped: {stats['skipped']}"
          f"   ✗ failed: {stats['failed']}   🚫 exhausted: {stats['exhausted']}")
    print(f"{'═' * 72}\n")


async def run_watch(limit: int, interval: int) -> None:
    logger.info("Loading embedding model: %s", settings.embedding_model_name)
    model = EmbeddingModel.get()
    logger.info("Model ready — retry watch mode: limit=%d  interval=%ds  (Ctrl+C to stop)",
                limit, interval)
    classifier = await _load_classifier()

    total_indexed = 0
    cycle = 0
    stop = asyncio.Event()

    def _on_signal(*_):
        logger.info("Shutdown signal — finishing current batch then stopping…")
        stop.set()

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    while not stop.is_set():
        cycle += 1
        logger.info("── Retry Cycle %d ──────────────────────────────────────────", cycle)
        stats = await _run_retry_batch(limit, model, classifier)
        total_indexed += stats["indexed"]

        print(f"  Retry Cycle {cycle}  ✓ {stats['indexed']}  ⊘ {stats['skipped']}"
              f"  ✗ {stats['failed']}  🚫 {stats['exhausted']}"
              f"  │  total retried so far: {total_indexed}")

        if stats["total"] == 0:
            logger.info("No due retries — sleeping %ds…", interval)

        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass

    logger.info("Retry watch stopped. Total indexed this session: %d", total_indexed)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process indexing_retry_jobs")
    parser.add_argument("--limit",    type=int, default=10,   help="Max retry jobs per run (default: 10)")
    parser.add_argument("--watch",    action="store_true",    help="Loop forever")
    parser.add_argument("--interval", type=int, default=3600, help="Seconds between cycles in watch mode (default: 3600)")
    args = parser.parse_args()

    if args.watch:
        asyncio.run(run_watch(args.limit, args.interval))
    else:
        asyncio.run(run(args.limit))
