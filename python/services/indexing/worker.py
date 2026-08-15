"""
Celery worker — picks pending indexing_jobs from the DB and runs the pipeline.

Run a worker:
    celery -A services.indexing.worker worker --loglevel=info --concurrency=1

IMPORTANT: run with --concurrency=1. The rotating proxy allows ~10 concurrent
connections and a single drain already fans out to indexing_workers (8) concurrent
fetches. Two drains in parallel (e.g. drain_fast + drain_queue on separate worker
slots) would exceed the proxy limit. concurrency=1 serialises all YouTube-touching
tasks; throughput comes from each drain's internal 8-way fan-out, not Celery slots.
"""
import asyncio
import concurrent.futures
import logging
import threading
import time as _time
from datetime import datetime, timedelta, timezone

from celery import Celery
from sqlalchemy import select, update

from config import settings
from db.models import IndexingJob
from db.session import WorkerSessionLocal as AsyncSessionLocal
from services.classifier.classifier import VideoClassifier
from services.embedding.model import EmbeddingModel
from services.indexing.pipeline import IndexingPipeline
from services.transcript.fetcher import TranscriptRateLimitError, clear_rate_ban, proxy_enabled, reset_semaphore

logger = logging.getLogger(__name__)

# Ban durations (seconds) when YouTube doesn't send a Retry-After header.
# 30 min → 60 min → 90 min, escalating per consecutive rate-limited drain.
_BAN_DURATIONS = (1800, 3600, 5400)

# Drain-level rate-limit state: a 429 anywhere in a batch sets _BAN_UNTIL and
# every drain tick before that moment skips without touching YouTube. The jobs
# themselves stay 'pending' (pipeline resets them), so the first drain after
# the ban expires retries them — same 30/60/90 escalation, no sleeping tasks.
_BAN_UNTIL: float = 0.0   # time.monotonic() timestamp
_BAN_STRIKES: int = 0     # consecutive drains that ended rate-limited

# Beat fires drain_queue every 5 min but a 600-job batch needs 20+ min at the
# global 2s/request rate — overlapping drains would re-process the same jobs.
_DRAIN_LOCK = threading.Lock()

# 'processing' jobs untouched for this long belong to a crashed/restarted worker
# and are reset to 'pending' at the start of each drain. Short window because the
# 75s fetch backstop means no live job stays 'processing' more than ~2 min, so
# anything older is an orphan from a restart — recover it fast.
_STALE_PROCESSING_AFTER = timedelta(minutes=10)

# Fast-lane: freshly-added channel jobs are inserted with priority 8. drain_fast
# processes ONLY those (priority >= _FRESH_PRIORITY) so a channel-add can be drained
# immediately — triggered on channel-add + a short beat — instead of waiting for the
# backlog tick. _FAST_BATCH caps one fast drain.
_FRESH_PRIORITY = 8
_FAST_BATCH = 50

celery_app = Celery(
    "ytsearch_worker",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,            # acknowledge only after task completes (prevents data loss)
    worker_prefetch_multiplier=1,   # one task per worker slot at a time
)

# Shared model instances — loaded once per worker process
_embedding_model: EmbeddingModel | None = None
_classifier: VideoClassifier | None = None


def _get_model() -> EmbeddingModel:
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = EmbeddingModel.get()
    return _embedding_model


async def _get_classifier_async() -> VideoClassifier:
    global _classifier
    if _classifier is None:
        _classifier = VideoClassifier()
        await _load_industry_embeddings(_classifier)
    return _classifier


async def _load_industry_embeddings(classifier: VideoClassifier) -> None:
    """Load industry embeddings from DB into the classifier for Level 4 classification."""
    from sqlalchemy import text
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("SELECT slug, embedding FROM industries WHERE embedding IS NOT NULL AND is_active = TRUE")
        )
        embeddings = {row.slug: list(row.embedding) for row in result.fetchall()}
        classifier.set_industry_embeddings(embeddings)
        logger.info("Loaded %d industry embeddings into classifier", len(embeddings))


def _run_async(coro):
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


@celery_app.task(name="indexing.process_video", bind=True, max_retries=3)
def process_video(self, youtube_video_id: str, priority: int = 5, queued_by: str = "manual") -> dict:
    """
    Enqueue a single video for indexing.
    Creates an indexing_job row and runs the pipeline immediately.
    """
    return _run_async(_process_video_async(youtube_video_id, priority, queued_by))


async def _process_video_async(
    youtube_video_id: str,
    priority: int,
    queued_by: str,
) -> dict:
    async with AsyncSessionLocal() as session:
        # Find or create the job row
        result = await session.execute(
            select(IndexingJob)
            .where(IndexingJob.youtube_video_id == youtube_video_id)
            .where(IndexingJob.status.in_(["pending", "failed"]))
            .where(IndexingJob.attempts < IndexingJob.max_attempts)
            .limit(1)
        )
        job = result.scalar_one_or_none()

        if job is None:
            job = IndexingJob(
                youtube_video_id=youtube_video_id,
                priority=priority,
                queued_by=queued_by,
            )
            session.add(job)
            await session.flush()

        job.status = "processing"
        await session.commit()

        pipeline = IndexingPipeline(
            session=session,
            embedding_model=_get_model(),
            classifier=await _get_classifier_async(),
        )
        success = await pipeline.run(job)
        return {"video_id": youtube_video_id, "success": success}


@celery_app.task(name="indexing.drain_queue")
def drain_queue(batch_size: int = 20) -> dict:
    """
    Pull `batch_size` pending jobs from the DB and process them.
    Called by the scheduler every 5 minutes to keep the queue moving.
    """
    if not _DRAIN_LOCK.acquire(blocking=False):
        logger.info("drain_queue: previous drain still running — skipping this tick")
        return {"skipped": "previous drain still running"}
    try:
        remaining = _BAN_UNTIL - _time.monotonic()
        if remaining > 0:
            logger.warning(
                "drain_queue: YouTube ban active — skipping (%dm%02ds remaining)",
                int(remaining) // 60, int(remaining) % 60,
            )
            return {"skipped": "rate-limit ban", "retry_in_s": int(remaining)}

        logger.info("drain_queue ▶ starting (batch_size=%d) | proxy=%s workers=%d concurrent_fetch=%d",
                    batch_size,
                    "ON (rotating residential)" if proxy_enabled() else "OFF (direct)",
                    settings.indexing_workers, settings.transcript_concurrent_limit)
        result = _run_async(_drain_queue_async(batch_size))
        logger.info("drain_queue ■ done → %s", result)
        return result
    finally:
        _DRAIN_LOCK.release()


@celery_app.task(name="indexing.drain_fast")
def drain_fast(batch_size: int = _FAST_BATCH) -> dict:
    """
    Fast-lane drain: process ONLY freshly-queued high-priority jobs
    (priority >= _FRESH_PRIORITY, e.g. a just-added channel) so they index within
    ~1 min instead of waiting for the backlog tick. Triggered immediately on
    channel-add and by a short beat safety net.

    Shares _DRAIN_LOCK with drain_queue so the two never run at once — the proxy
    allows ~10 concurrent connections and one drain already uses 8. With the worker
    at --concurrency=1 this task simply runs on the next free slot (right after any
    in-flight backlog batch), then claims the priority-8 jobs first.
    """
    if not _DRAIN_LOCK.acquire(blocking=False):
        logger.info("drain_fast: a drain is already running — fresh jobs picked up next slot")
        return {"skipped": "previous drain still running"}
    try:
        remaining = _BAN_UNTIL - _time.monotonic()
        if remaining > 0:
            logger.warning(
                "drain_fast: YouTube ban active — skipping (%dm%02ds remaining)",
                int(remaining) // 60, int(remaining) % 60,
            )
            return {"skipped": "rate-limit ban", "retry_in_s": int(remaining)}

        logger.info("drain_fast ▶ starting (batch_size=%d, priority>=%d) | proxy=%s workers=%d",
                    batch_size, _FRESH_PRIORITY,
                    "ON (rotating residential)" if proxy_enabled() else "OFF (direct)",
                    settings.indexing_workers)
        result = _run_async(_drain_queue_async(batch_size, min_priority=_FRESH_PRIORITY))
        logger.info("drain_fast ■ done → %s", result)
        return result
    finally:
        _DRAIN_LOCK.release()


async def _drain_queue_async(batch_size: int, min_priority: int | None = None) -> dict:
    global _BAN_UNTIL, _BAN_STRIKES

    # Each Celery task call creates a fresh event loop via asyncio.run().
    # Reset the transcript semaphore so it binds to THIS loop, not a previous one.
    logger.debug("_drain_queue_async: resetting semaphore for new event loop")
    reset_semaphore()
    clear_rate_ban()  # the ban window (if any) has passed — allow requests again

    async with AsyncSessionLocal() as session:
        # Recover jobs orphaned by a crashed worker: stuck in 'processing'
        # with no update for hours.
        stale_cutoff = datetime.now(timezone.utc) - _STALE_PROCESSING_AFTER
        recovered = await session.execute(
            update(IndexingJob)
            .where(IndexingJob.status == "processing")
            .where(IndexingJob.updated_at < stale_cutoff)
            .values(status="pending")
        )
        if recovered.rowcount:
            logger.warning("_drain_queue_async: recovered %d stale 'processing' jobs", recovered.rowcount)

        # Claim only 'pending' jobs. Claimed rows turn 'processing' before the
        # row locks release, so a concurrent drain or run_indexing.py process
        # can never pick the same jobs twice.
        claim = (
            select(IndexingJob)
            .where(IndexingJob.status == "pending")
            .where(IndexingJob.attempts < IndexingJob.max_attempts)
        )
        if min_priority is not None:
            claim = claim.where(IndexingJob.priority >= min_priority)
        claim = (
            claim
            .order_by(IndexingJob.priority.desc(), IndexingJob.created_at.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        result = await session.execute(claim)
        jobs = result.scalars().all()
        if jobs:
            for job in jobs:
                job.status = "processing"
        await session.commit()

    logger.info("_drain_queue_async: fetched %d pending jobs from DB", len(jobs))
    if not jobs:
        logger.info("_drain_queue_async: queue is empty — nothing to process")
        return {"processed": 0, "failed": 0}

    classifier = await _get_classifier_async()
    model = _get_model()

    # DB semaphore: cap open sessions so we don't exhaust the connection pool
    _db_sem = asyncio.Semaphore(settings.indexing_workers)

    async def _run_one(job: IndexingJob) -> bool | TranscriptRateLimitError:
        logger.debug("_run_one ▶ %s (job_id=%s)", job.youtube_video_id, job.id)
        async with _db_sem:
            async with AsyncSessionLocal() as session:
                job_fresh = await session.merge(job)
                pipeline = IndexingPipeline(
                    session=session,
                    embedding_model=model,
                    classifier=classifier,
                )
                try:
                    ok = await pipeline.run(job_fresh)
                    logger.debug("_run_one ■ %s → %s", job.youtube_video_id, "ok" if ok else "failed")
                    return ok
                except TranscriptRateLimitError as rate_exc:
                    # Pipeline already reset the job to 'pending'. Hand the 429
                    # up to the batch level so it can set the drain ban window.
                    return rate_exc
                except Exception as exc:
                    logger.error("_run_one crashed %s: %s: %s",
                                 job.youtube_video_id, type(exc).__name__, exc, exc_info=True)
                    raise

    results = await asyncio.gather(*[_run_one(j) for j in jobs], return_exceptions=True)

    rate_hits = [r for r in results if isinstance(r, TranscriptRateLimitError)]
    processed = sum(1 for r in results if r is True)
    failed = sum(
        1 for r in results
        if r is False or (isinstance(r, Exception) and not isinstance(r, TranscriptRateLimitError))
    )

    if rate_hits and not proxy_enabled():
        # Direct connection: one shared IP, so a 429 means back off everything.
        # Pause all future drains: prefer YouTube's Retry-After header, else
        # escalate 30 → 60 → 90 min per consecutive rate-limited drain.
        retry_after = max((r.retry_after for r in rate_hits if r.retry_after), default=None)
        ban_s = retry_after or _BAN_DURATIONS[min(_BAN_STRIKES, len(_BAN_DURATIONS) - 1)]
        _BAN_STRIKES += 1
        _BAN_UNTIL = _time.monotonic() + ban_s
        source = "Retry-After header" if retry_after else "fallback schedule"
        logger.warning(
            "drain_queue: 429 from YouTube — %d job(s) left pending, drains paused %dm (%s, strike %d)",
            len(rate_hits), ban_s // 60, source, _BAN_STRIKES,
        )
    elif rate_hits:
        # Proxy mode: each video had its own exit IP, so a 429 is isolated —
        # leave those jobs pending and they retry on a fresh IP next drain.
        # No batch-wide pause.
        _BAN_STRIKES = 0
        logger.info("drain_queue: %d job(s) rate-limited on their proxy exit IP — "
                    "left pending, will retry on a fresh IP (no batch pause)", len(rate_hits))
    else:
        _BAN_STRIKES = 0

    # Log first 3 exceptions so we can diagnose silent failures
    exceptions_seen = 0
    for job, r in zip(jobs, results):
        if (isinstance(r, Exception) and not isinstance(r, TranscriptRateLimitError)
                and exceptions_seen < 3):
            logger.error("_run_one crashed for %s: %s: %s",
                         job.youtube_video_id, type(r).__name__, r)
            exceptions_seen += 1

    logger.info("drain_queue: processed=%d failed=%d rate_limited=%d (batch=%d)",
                processed, failed, len(rate_hits), len(jobs))
    return {"processed": processed, "failed": failed, "rate_limited": len(rate_hits)}