"""
Bulk-add many YouTube channels at once for MVP launch.

Usage:
    python -m scripts.bulk_add_channels                    # reads CHANNELS list below
    python -m scripts.bulk_add_channels --dry-run          # show what would be added, no DB writes
    python -m scripts.bulk_add_channels --delay 2          # seconds between API calls (default 1)

Add your channel handles/IDs to the CHANNELS list below.
Accepts both @handles and UCxxxx channel IDs.
"""
import argparse
import asyncio
import logging
import time

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Channel, IndexingJob
from db.session import AsyncSessionLocal

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

# ── Add your channels here ─────────────────────────────────────────────────────
CHANNELS: list[str] = [
    # Paste @handles or UCxxxx IDs, one per line:
    # "@channelhandle",
    # "UCxxxxxxxxxxxxxxxxxxxxxx",
]
# ──────────────────────────────────────────────────────────────────────────────


async def add_channel(
    session: AsyncSession,
    channel_id: str,
    crawler,
    dry_run: bool,
) -> dict:
    import asyncio as _asyncio
    loop = _asyncio.get_event_loop()

    meta = await loop.run_in_executor(None, crawler.get_channel_meta, channel_id)
    if meta is None:
        logger.warning("  SKIP %s — not found or quota exhausted", channel_id)
        return {"handle": channel_id, "status": "not_found"}

    result = await session.execute(
        select(Channel).where(Channel.youtube_channel_id == meta.youtube_channel_id)
    )
    channel = result.scalar_one_or_none()
    is_new = channel is None

    if dry_run:
        existing_videos = (await session.execute(
            text("SELECT COUNT(*) FROM videos v JOIN channels ch ON ch.id = v.channel_id WHERE ch.youtube_channel_id = :cid"),
            {"cid": meta.youtube_channel_id},
        )).scalar() or 0
        logger.info(
            "  [DRY RUN] %s (%s) — %d subs — new=%s — %d videos already indexed",
            meta.name, meta.youtube_channel_id, meta.subscriber_count, is_new, existing_videos,
        )
        return {"handle": channel_id, "name": meta.name, "status": "dry_run", "is_new": is_new}

    # Upsert channel
    if channel is None:
        channel = Channel(
            youtube_channel_id=meta.youtube_channel_id,
            name=meta.name,
            description=meta.description,
            thumbnail_url=meta.thumbnail_url,
            subscriber_count=meta.subscriber_count,
            is_monitored=True,
            primary_industry_id=1,  # Programming & Development
        )
        session.add(channel)
        await session.flush()
    else:
        channel.is_monitored = True
        channel.subscriber_count = meta.subscriber_count
        if channel.primary_industry_id is None:
            channel.primary_industry_id = 1

    # Fetch all video IDs (full history on first add)
    from services.crawler.channel_crawler import YouTubeCrawler as _C
    since = channel.last_crawled_at
    video_ids = await loop.run_in_executor(
        None, crawler.get_channel_video_ids, meta.uploads_playlist_id, since
    )

    if not video_ids:
        from datetime import datetime, timezone
        channel.last_crawled_at = datetime.now(timezone.utc)
        await session.commit()
        logger.info("  %s (%s) — 0 new videos", meta.name, meta.youtube_channel_id)
        return {"handle": channel_id, "name": meta.name, "queued": 0}

    already_videos = {row[0] for row in (await session.execute(
        text("SELECT youtube_video_id FROM videos WHERE youtube_video_id = ANY(:ids)"),
        {"ids": video_ids},
    )).fetchall()}
    already_jobs = {row[0] for row in (await session.execute(
        text("SELECT youtube_video_id FROM indexing_jobs WHERE youtube_video_id = ANY(:ids) AND status IN ('pending','processing','done')"),
        {"ids": video_ids},
    )).fetchall()}
    new_ids = [v for v in video_ids if v not in already_videos and v not in already_jobs]

    if new_ids:
        session.add_all([
            IndexingJob(
                youtube_video_id=vid,
                channel_id=channel.id,
                industry_id=1,
                priority=8,
                queued_by="bulk_add_channels",
            )
            for vid in new_ids
        ])

    from datetime import datetime, timezone
    channel.last_crawled_at = datetime.now(timezone.utc)
    await session.commit()

    logger.info(
        "  %s — fetched=%d  already_known=%d  queued=%d",
        meta.name, len(video_ids), len(video_ids) - len(new_ids), len(new_ids),
    )
    return {"handle": channel_id, "name": meta.name, "queued": len(new_ids)}


async def main(dry_run: bool, delay: float) -> None:
    from services.crawler.channel_crawler import YouTubeCrawler

    if not CHANNELS:
        logger.error("No channels defined. Add handles/IDs to the CHANNELS list in this script.")
        return

    crawler = YouTubeCrawler()
    total_queued = 0

    logger.info("Processing %d channels (dry_run=%s)", len(CHANNELS), dry_run)

    async with AsyncSessionLocal() as session:
        for i, handle in enumerate(CHANNELS, 1):
            logger.info("[%d/%d] %s", i, len(CHANNELS), handle)
            result = await add_channel(session, handle, crawler, dry_run)
            total_queued += result.get("queued", 0)
            if i < len(CHANNELS):
                time.sleep(delay)  # be gentle with the YouTube API

    if not dry_run:
        logger.info("Done. Total videos queued for indexing: %d", total_queued)
        logger.info("Start the worker to process them: python run_indexing.py --watch")
    else:
        logger.info("Dry run complete. Re-run without --dry-run to add channels.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bulk-add YouTube channels")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no DB writes")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds between API calls (default 1)")
    args = parser.parse_args()
    asyncio.run(main(args.dry_run, args.delay))
