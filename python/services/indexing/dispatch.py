"""
Lightweight Celery dispatch — lets the API enqueue indexing tasks WITHOUT importing
the heavy worker module (embedding model, pipeline, etc.).

Uses only the Celery broker (the same Redis the cron already runs on). It pushes a
task by name; the worker process — which has the real task registered — runs it. It
does NOT touch the cache:search:* keys, so it's safe alongside the search cache.
"""
import logging

from celery import Celery

from config import settings

logger = logging.getLogger(__name__)

# Broker-only client: enough to send_task; no result backend, no worker imports.
_dispatch = Celery(broker=settings.redis_url)


def trigger_fast_drain() -> None:
    """Enqueue an immediate fresh-only drain (priority >= 8). Best-effort — if the
    broker is unreachable the 2-min drain_fast beat safety net still catches the jobs."""
    try:
        _dispatch.send_task("indexing.drain_fast")
        logger.info("trigger_fast_drain: enqueued indexing.drain_fast")
    except Exception:
        logger.warning("trigger_fast_drain: enqueue failed — beat safety-net will catch it",
                       exc_info=True)
