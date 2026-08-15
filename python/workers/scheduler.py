"""
Celery Beat schedule — all cron jobs for the Python side.

Run Beat alongside a worker:
    celery -A workers.scheduler beat --loglevel=info
    celery -A workers.scheduler worker --loglevel=info --concurrency=4

Or together (dev only — not recommended for production):
    celery -A workers.scheduler worker --beat --loglevel=info --concurrency=4
"""
import asyncio
import logging

from celery import Celery
from celery.schedules import crontab

from config import settings
from services.crawler.channel_crawler import YouTubeCrawler
from db.models import Channel, IndexingJob
from db.session import WorkerSessionLocal as AsyncSessionLocal
from services.indexing.worker import celery_app, drain_queue, drain_fast

logger = logging.getLogger(__name__)

# Reuse the same Celery app from the worker module
app = celery_app

app.conf.beat_schedule = {

    # ── Every hour: fetch trending videos across all regions ─────────────────
    "fetch-trending-hourly": {
        "task": "workers.scheduler.fetch_trending",
        "schedule": crontab(minute=0),       # :00 of every hour
    },

    # ── Every 6 hours: check monitored channels for new uploads ───────────────
    "scan-channels-6h": {
        "task": "workers.scheduler.scan_monitored_channels",
        "schedule": crontab(minute=0, hour="*/6"),
    },

    # ── Every 60s: drain the backlog in small batches ─────────────────────────
    # Small batch + short interval keeps backlog throughput (~25/60s ≈ old 150/300s)
    # while freeing the single worker slot ~every minute, so an immediate drain_fast
    # (triggered on channel-add) runs within ~1 min. Ticks that fire while a drain is
    # running skip via _DRAIN_LOCK; ban windows skip via _BAN_UNTIL.
    "drain-queue-1m": {
        "task": "indexing.drain_queue",
        "schedule": 60,                      # seconds
        "kwargs": {"batch_size": 25},
    },

    # ── Every 2 min: fast-lane safety net for fresh (priority>=8) jobs ─────────
    # The primary trigger is the immediate enqueue on channel-add (dispatch
    # .trigger_fast_drain); this beat catches any missed trigger and clears a large
    # channel in chunks. drain_fast shares _DRAIN_LOCK so it never overlaps a backlog drain.
    "drain-fast-2m": {
        "task": "indexing.drain_fast",
        "schedule": 120,                     # seconds
        "kwargs": {"batch_size": 50},
    },

    # ── Daily at midnight UTC: discover new channels by topic rotation ─────────
    "discover-channels-daily": {
        "task": "workers.scheduler.discover_channels",
        "schedule": crontab(hour=0, minute=0),
    },

    # ── Every Sunday 4 AM: re-classify uncategorised videos ───────────────────
    "reclassify-weekly": {
        "task": "workers.scheduler.reclassify_uncategorised",
        "schedule": crontab(hour=4, minute=0, day_of_week=0),
    },

    # ── Every Saturday 2 AM: bulk-discover new channels via yt-dlp + GitHub ──
    "bulk-discover-weekly": {
        "task": "workers.scheduler.bulk_discover_channels",
        "schedule": crontab(hour=2, minute=0, day_of_week=6),
    },

    # whisper-retry-4h disabled — re-enable when Whisper is needed
    # "whisper-retry-4h": {
    #     "task": "workers.scheduler.whisper_retry",
    #     "schedule": crontab(minute=0, hour="*/4"),
    #     "kwargs": {"batch_size": 10},
    #     "options": {"queue": "default"},
    # },
}

# Topic rotation: each day of the week focuses on one category (Mon–Sun)
_DAILY_TOPICS = [
    "python programming tutorial",      # Monday
    "javascript web development",       # Tuesday
    "machine learning tutorial",        # Wednesday
    "react nextjs tutorial",            # Thursday
    "docker kubernetes devops",         # Friday
    "system design interview",          # Saturday
    "java spring boot tutorial",        # Sunday
]


@app.task(name="workers.scheduler.fetch_trending")
def fetch_trending() -> dict:
    return asyncio.run(_fetch_trending_async())


async def _fetch_trending_async() -> dict:
    from sqlalchemy import text
    crawler = YouTubeCrawler()
    loop = asyncio.get_event_loop()

    # Fetch all 3 regions in parallel
    region_results = await asyncio.gather(*[
        loop.run_in_executor(None, crawler.get_trending_video_ids, region, 50)
        for region in ["US", "GB", "IN"]
    ])
    all_ids: list[str] = [vid for ids in region_results for vid in ids]

    if not all_ids:
        return {"queued": 0}

    async with AsyncSessionLocal() as session:
        already_videos = {row[0] for row in (await session.execute(
            text("SELECT youtube_video_id FROM videos WHERE youtube_video_id = ANY(:ids)"),
            {"ids": all_ids},
        )).fetchall()}
        already_jobs = {row[0] for row in (await session.execute(
            text("SELECT youtube_video_id FROM indexing_jobs WHERE youtube_video_id = ANY(:ids) AND status IN ('pending','processing','done')"),
            {"ids": all_ids},
        )).fetchall()}
        new_ids = [v for v in all_ids if v not in already_videos and v not in already_jobs]

        if new_ids:
            session.add_all([
                IndexingJob(youtube_video_id=vid, priority=6, queued_by="fetch_trending")
                for vid in new_ids
            ])
            await session.commit()

    queued = len(new_ids) if new_ids else 0
    logger.info("fetch_trending: queued %d videos (%d already known)", queued, len(all_ids) - queued)
    return {"queued": queued}


@app.task(name="workers.scheduler.scan_monitored_channels")
def scan_monitored_channels() -> dict:
    return asyncio.run(_scan_channels_async())


async def _scan_channels_async() -> dict:
    from sqlalchemy import select, text
    from datetime import datetime, timezone

    crawler = YouTubeCrawler()
    loop = asyncio.get_event_loop()

    async with AsyncSessionLocal() as session:
        channels = (await session.execute(
            select(Channel).where(Channel.is_monitored == True)  # noqa: E712
        )).scalars().all()

    if not channels:
        return {"queued": 0}

    # Semaphore: max 5 parallel YouTube API calls to stay within quota rate limits
    _api_sem = asyncio.Semaphore(5)

    async def _fetch_one(channel: Channel) -> tuple[Channel, list[str]]:
        """Fetch new video IDs for one channel. Runs concurrently with others."""
        async with _api_sem:
            meta = await loop.run_in_executor(
                None, crawler.get_channel_meta, channel.youtube_channel_id
            )
            if not meta:
                return channel, []
            video_ids = await loop.run_in_executor(
                None, crawler.get_channel_video_ids, meta.uploads_playlist_id, channel.last_crawled_at
            )
            return channel, video_ids

    # All channels crawled in parallel (up to 5 at a time)
    fetch_results = await asyncio.gather(*[_fetch_one(ch) for ch in channels])

    # Collect all video IDs across all channels for a single bulk dedup query
    all_video_ids: list[str] = [vid for _, ids in fetch_results for vid in ids]
    queued = 0

    async with AsyncSessionLocal() as session:
        if all_video_ids:
            existing_videos = {row[0] for row in (await session.execute(
                text("SELECT youtube_video_id FROM videos WHERE youtube_video_id = ANY(:ids)"),
                {"ids": all_video_ids},
            )).fetchall()}
            existing_jobs = {row[0] for row in (await session.execute(
                text("SELECT youtube_video_id FROM indexing_jobs "
                     "WHERE youtube_video_id = ANY(:ids) AND status IN ('pending','processing','done')"),
                {"ids": all_video_ids},
            )).fetchall()}
            already_known = existing_videos | existing_jobs
        else:
            already_known = set()

        now = datetime.now(timezone.utc)
        for channel, video_ids in fetch_results:
            channel = await session.merge(channel)
            channel.last_crawled_at = now

            new_ids = [v for v in video_ids if v not in already_known]
            if new_ids:
                session.add_all([
                    IndexingJob(
                        youtube_video_id=vid,
                        channel_id=channel.id,
                        industry_id=channel.primary_industry_id,
                        priority=7,
                        queued_by="scan_channels",
                    )
                    for vid in new_ids
                ])
                queued += len(new_ids)
                logger.info("channel %s: %d fetched, %d new queued",
                            channel.youtube_channel_id, len(video_ids), len(new_ids))

        await session.commit()

    logger.info("scan_monitored_channels: queued %d new videos across %d channels",
                queued, len(channels))
    return {"queued": queued}


@app.task(name="workers.scheduler.discover_channels")
def discover_channels() -> dict:
    """Runs daily — uses a rotating topic so we cover all programming sub-topics weekly."""
    from datetime import datetime
    day_of_week = datetime.utcnow().weekday()
    topic = _DAILY_TOPICS[day_of_week % len(_DAILY_TOPICS)]
    return asyncio.run(_discover_channels_async(topic))


async def _discover_channels_async(topic: str) -> dict:
    from db.models import Channel as ChannelModel
    from sqlalchemy import select, text

    crawler = YouTubeCrawler()
    loop = asyncio.get_event_loop()

    channel_ids = await loop.run_in_executor(
        None, crawler.discover_channels_by_topic, topic, 25
    )
    if not channel_ids:
        return {"topic": topic, "added": 0}

    async with AsyncSessionLocal() as session:
        programming_industry = (await session.execute(
            text("SELECT id FROM industries WHERE slug = 'programming' LIMIT 1")
        )).scalar()

        # Check which channel IDs are already in DB in one query
        existing = {row[0] for row in (await session.execute(
            text("SELECT youtube_channel_id FROM channels WHERE youtube_channel_id = ANY(:ids)"),
            {"ids": channel_ids},
        )).fetchall()}
        new_ids = [cid for cid in channel_ids if cid not in existing]

    if not new_ids:
        logger.info("discover_channels (topic='%s'): all %d channels already known", topic, len(channel_ids))
        return {"topic": topic, "added": 0}

    # Fetch metadata for all new channels in parallel (up to 5 at a time)
    _api_sem = asyncio.Semaphore(5)

    async def _fetch_meta(cid: str):
        async with _api_sem:
            return cid, await loop.run_in_executor(None, crawler.get_channel_meta, cid)

    meta_results = await asyncio.gather(*[_fetch_meta(cid) for cid in new_ids])

    async with AsyncSessionLocal() as session:
        added = 0
        for cid, meta in meta_results:
            if not meta or meta.subscriber_count < 10_000:
                continue
            session.add(ChannelModel(
                youtube_channel_id=cid,
                name=meta.name,
                description=meta.description,
                thumbnail_url=meta.thumbnail_url,
                subscriber_count=meta.subscriber_count,
                is_monitored=True,
                primary_industry_id=programming_industry,
            ))
            added += 1
        await session.commit()

    logger.info("discover_channels (topic='%s'): added %d channels", topic, added)
    return {"topic": topic, "added": added}


@app.task(name="workers.scheduler.bulk_discover_channels")
def bulk_discover_channels() -> dict:
    """Weekly: discover new channels via yt-dlp search + GitHub lists, queue their videos."""
    from scripts.discover_channels_bulk import _discover_via_ytdlp, _discover_via_github
    from scripts.discover_channels_bulk import _YTDLP_QUERIES

    async def _run():
        from api.services.ingestion_service import crawl_channel
        from db.session import WorkerSessionLocal as AsyncSessionLocal
        from sqlalchemy import text
        import time

        channel_ids = _discover_via_ytdlp(_YTDLP_QUERIES)
        channel_ids |= _discover_via_github()

        if not channel_ids:
            return {"discovered": 0, "added": 0, "queued": 0}

        # Filter already known
        uc_ids = {c for c in channel_ids if c.startswith("UC")}
        async with AsyncSessionLocal() as session:
            known = {row[0] for row in (await session.execute(
                text("SELECT youtube_channel_id FROM channels "
                     "WHERE youtube_channel_id = ANY(:ids)"),
                {"ids": list(uc_ids)},
            )).fetchall()} if uc_ids else set()

        new_ids = channel_ids - known
        logger.info("bulk_discover: %d discovered, %d new", len(channel_ids), len(new_ids))

        added, queued = 0, 0
        for cid in sorted(new_ids):
            try:
                async with AsyncSessionLocal() as session:
                    result = await crawl_channel(session, cid)
                if "error" not in result:
                    added += 1
                    queued += result.get("queued", 0)
            except Exception as exc:
                logger.warning("bulk_discover: failed %s: %s", cid, exc)
            time.sleep(1.5)

        return {"discovered": len(channel_ids), "added": added, "queued": queued}

    return asyncio.run(_run())


@app.task(name="workers.scheduler.whisper_retry")
def whisper_retry(batch_size: int = 10) -> dict:
    """Retry videos that had no YouTube captions using local Whisper transcription."""
    from services.indexing.whisper_worker import run_whisper_retry
    return asyncio.run(run_whisper_retry(batch_size))


@app.task(name="workers.scheduler.reclassify_uncategorised")
def reclassify_uncategorised() -> dict:
    return asyncio.run(_reclassify_async())


async def _reclassify_async() -> dict:
    from sqlalchemy import select, text
    from services.classifier.classifier import VideoClassifier
    from services.embedding.model import EmbeddingModel
    from db.models import Video

    model = EmbeddingModel.get()
    classifier = VideoClassifier()

    async with AsyncSessionLocal() as session:
        ind_result = await session.execute(
            text("SELECT slug, embedding FROM industries WHERE embedding IS NOT NULL")
        )
        classifier.set_industry_embeddings(
            {row.slug: list(row.embedding) for row in ind_result.fetchall()}
        )

        result = await session.execute(
            select(Video)
            .where(Video.primary_industry_id == None)  # noqa: E711
            .where(Video.indexing_status == "indexed")
            .limit(500)
        )
        videos = result.scalars().all()
        updated = 0

        for video in videos:
            emb = model.encode_one(f"{video.title} {video.description or ''}")
            classification = classifier.classify(
                title=video.title or "",
                description=video.description or "",
                title_description_embedding=emb,
            )
            if classification.industry_slug:
                row = await session.execute(
                    text("SELECT id FROM industries WHERE slug = :s"),
                    {"s": classification.industry_slug},
                )
                ind_row = row.fetchone()
                if ind_row:
                    video.primary_industry_id = ind_row[0]
                    updated += 1

        await session.commit()

    logger.info("reclassify_uncategorised: updated %d videos", updated)
    return {"updated": updated}