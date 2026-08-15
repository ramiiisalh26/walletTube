"""
Ingestion service — replaces Java's YouTubeDiscoveryService, YouTubeQuotaGuard,
VideoIngestionQueue, and IndexingJobRepository admin methods.
"""
import asyncio
import logging
from datetime import date, timezone, timedelta, datetime

import httpx
from sqlalchemy import select, text, func
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db.models import IndexingJob, Video
from services.indexing import dispatch

logger = logging.getLogger(__name__)

_INGEST_QUEUE_KEY = "yt:ingest:queue"

# Lua: atomically init-if-absent, check sufficiency, decrement, set expiry.
# Returns remaining quota or -1 if insufficient.
_QUOTA_LUA = """
local current = tonumber(redis.call('GET', KEYS[1]))
if current == nil then
    current = tonumber(ARGV[1])
end
if current < tonumber(ARGV[2]) then
    return -1
end
local newVal = current - tonumber(ARGV[2])
redis.call('SET', KEYS[1], newVal)
redis.call('EXPIREAT', KEYS[1], tonumber(ARGV[3]))
return newVal
"""


def _quota_key() -> str:
    return f"yt:quota:{date.today().isoformat()}"


def _next_midnight_epoch() -> int:
    tomorrow = datetime.now(timezone.utc).date() + timedelta(days=1)
    return int(datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=timezone.utc).timestamp())


async def _try_consume_quota(redis, units: int) -> bool:
    script = redis.register_script(_QUOTA_LUA)
    remaining = await script(
        keys=[_quota_key()],
        args=[
            str(settings.youtube_quota_per_day),
            str(units),
            str(_next_midnight_epoch()),
        ],
    )
    if remaining is None or int(remaining) < 0:
        logger.warning("YouTube API quota exhausted for today")
        return False
    return True


async def get_quota_remaining(redis) -> int:
    val = await redis.get(_quota_key())
    if val is None:
        return settings.youtube_quota_per_day
    return int(val)


async def _search_youtube_videos(query: str, max_results: int) -> list[str]:
    if not settings.youtube_api_key:
        logger.error("YOUTUBE_API_KEY not configured — skipping discovery")
        return []

    url = "https://www.googleapis.com/youtube/v3/search"
    params = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "maxResults": min(max_results, 50),
        "key": settings.youtube_api_key,
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
        video_ids = [
            item["id"]["videoId"]
            for item in data.get("items", [])
            if item.get("id", {}).get("videoId")
        ]
        logger.info("YouTube search '%s' → %d video IDs", query, len(video_ids))
        return video_ids
    except Exception as exc:
        logger.error("YouTube search failed for '%s': %s", query, exc)
        return []


async def _filter_already_indexed(session: AsyncSession, video_ids: list[str]) -> list[str]:
    if not video_ids:
        return []
    result = await session.execute(
        select(Video.youtube_video_id).where(Video.youtube_video_id.in_(video_ids))
    )
    already = {row[0] for row in result.all()}
    return [vid for vid in video_ids if vid not in already]


async def queue_size(redis) -> int:
    length = await redis.llen(_INGEST_QUEUE_KEY)
    return int(length or 0)


async def discover(session: AsyncSession, redis, query: str) -> dict:
    if not await _try_consume_quota(redis, settings.youtube_search_cost_units):
        return {
            "query": query,
            "enqueued": 0,
            "redis_queue_size": await queue_size(redis),
            "quota_remaining": 0,
        }

    found = await _search_youtube_videos(query, settings.youtube_max_results_per_search)
    new_ids = await _filter_already_indexed(session, found)

    for vid_id in new_ids:
        await redis.rpush(_INGEST_QUEUE_KEY, vid_id)

    logger.info(
        "Discovery '%s': %d found, %d new (enqueued), %d already indexed",
        query, len(found), len(new_ids), len(found) - len(new_ids),
    )
    return {
        "query": query,
        "enqueued": len(new_ids),
        "redis_queue_size": await queue_size(redis),
        "quota_remaining": await get_quota_remaining(redis),
    }


async def crawl_channel(session: AsyncSession, channel_id: str) -> dict:
    """
    Resolve a YouTube channel (ID or @handle), upsert it as monitored,
    and queue all not-yet-indexed videos as IndexingJob rows.
    """
    import asyncio
    from services.crawler.channel_crawler import YouTubeCrawler
    from db.models import Channel

    loop = asyncio.get_event_loop()
    crawler = YouTubeCrawler()

    meta = await loop.run_in_executor(None, crawler.get_channel_meta, channel_id)
    if meta is None:
        return {"error": f"Channel '{channel_id}' not found on YouTube or API quota exhausted"}

    # Upsert channel row — real channel ID from API (handles resolve to UCxxx)
    result = await session.execute(
        select(Channel).where(Channel.youtube_channel_id == meta.youtube_channel_id)
    )
    channel = result.scalar_one_or_none()
    # Resolve the default industry (Programming & Development) for new channels
    default_industry = (await session.execute(
        text("SELECT id FROM industries WHERE slug = 'programming' LIMIT 1")
    )).scalar()

    if channel is None:
        channel = Channel(
            youtube_channel_id=meta.youtube_channel_id,
            name=meta.name,
            description=meta.description,
            thumbnail_url=meta.thumbnail_url,
            subscriber_count=meta.subscriber_count,
            is_monitored=True,
            primary_industry_id=default_industry,
        )
        session.add(channel)
        await session.flush()  # materialise channel.id for FK on IndexingJob
    else:
        channel.name = meta.name
        channel.description = meta.description
        channel.thumbnail_url = meta.thumbnail_url
        if channel.primary_industry_id is None:
            channel.primary_industry_id = default_industry
        channel.subscriber_count = meta.subscriber_count
        channel.is_monitored = True

    # Always fetch full history — deduplication below skips already-indexed videos.
    # Incremental (since=last_crawled_at) is only for the automated scan_monitored_channels
    # cron. The admin API must always see the full catalogue so nothing gets missed.
    video_ids: list[str] = await loop.run_in_executor(
        None, crawler.get_channel_video_ids, meta.uploads_playlist_id, None
    )

    if not video_ids:
        channel.last_crawled_at = datetime.now(timezone.utc)
        await session.commit()
        return {
            "channel_id": meta.youtube_channel_id,
            "channel_name": meta.name,
            "total_fetched": 0,
            "already_known": 0,
            "queued": 0,
        }

    # Deduplicate against videos already in DB or already queued
    already_in_videos = await session.execute(
        text("SELECT youtube_video_id FROM videos WHERE youtube_video_id = ANY(:ids)"),
        {"ids": video_ids},
    )
    existing_video_ids = {row[0] for row in already_in_videos.fetchall()}

    already_in_jobs = await session.execute(
        text("""
            SELECT youtube_video_id FROM indexing_jobs
            WHERE youtube_video_id = ANY(:ids)
              AND status IN ('pending', 'processing', 'done')
        """),
        {"ids": video_ids},
    )
    existing_job_ids = {row[0] for row in already_in_jobs.fetchall()}

    already_known = existing_video_ids | existing_job_ids
    new_ids = [v for v in video_ids if v not in already_known]

    if new_ids:
        jobs = [
            IndexingJob(
                youtube_video_id=vid,
                channel_id=channel.id,
                industry_id=channel.primary_industry_id,
                priority=8,  # higher than automated scans (7) so these process first
                queued_by="admin_crawl_channel",
            )
            for vid in new_ids
        ]
        session.add_all(jobs)

    channel.last_crawled_at = datetime.now(timezone.utc)
    await session.commit()

    # Kick an immediate fresh-only drain (priority>=8) so these videos index within
    # ~1 min instead of waiting for the 5-min backlog tick. Best-effort.
    if new_ids:
        dispatch.trigger_fast_drain()

    logger.info(
        "crawl_channel %s (%s): %d fetched, %d already known, %d queued",
        meta.youtube_channel_id, meta.name, len(video_ids), len(already_known), len(new_ids),
    )
    return {
        "channel_id": meta.youtube_channel_id,
        "channel_name": meta.name,
        "total_fetched": len(video_ids),
        "already_known": len(already_known),
        "queued": len(new_ids),
    }


async def pipeline_status(session: AsyncSession, redis) -> dict:
    result = await session.execute(
        select(IndexingJob.status, func.count().label("count")).group_by(IndexingJob.status)
    )
    counts = {row.status: row.count for row in result.all()}

    return {
        "jobs_pending": counts.get("pending", 0),
        "jobs_processing": counts.get("processing", 0),
        "jobs_done": counts.get("done", 0),
        "jobs_failed": counts.get("failed", 0),
        "redis_queue_size": await queue_size(redis),
        "quota_remaining": await get_quota_remaining(redis),
    }
