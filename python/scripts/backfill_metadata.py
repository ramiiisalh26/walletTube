"""
Backfill missing metadata for videos that were indexed while YouTube Data API
quota was exhausted (title = youtube_video_id, thumbnail_url IS NULL).

Calls videos.list in batches of 50 (1 quota unit per batch = 50x more efficient
than the per-video enrichment in the indexing pipeline).

Usage:
    python scripts/backfill_metadata.py            # dry-run: shows count only
    python scripts/backfill_metadata.py --run      # actually updates the DB
    python scripts/backfill_metadata.py --run --limit 5000   # cap how many to fix
"""
import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backfill_metadata")


async def _run(dry_run: bool, limit: int) -> None:
    from sqlalchemy import text
    from db.session import AsyncSessionLocal
    from services.crawler.channel_crawler import YouTubeCrawler

    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            text("""
                SELECT id, youtube_video_id
                FROM videos
                WHERE thumbnail_url IS NULL
                  AND indexing_status = 'indexed'
                ORDER BY id
                LIMIT :limit
            """),
            {"limit": limit},
        )).fetchall()

    if not rows:
        logger.info("No videos with missing metadata found.")
        return

    logger.info("Found %d videos with missing metadata.", len(rows))

    if dry_run:
        logger.info("Dry-run mode — pass --run to apply updates.")
        return

    crawler = YouTubeCrawler()
    updated = 0
    failed_batches = 0

    # Process in batches of 50 — 1 quota unit per batch
    batch_size = 50
    for batch_start in range(0, len(rows), batch_size):
        batch = rows[batch_start : batch_start + batch_size]
        yt_ids = [r.youtube_video_id for r in batch]
        id_map  = {r.youtube_video_id: r.id for r in batch}

        try:
            metas = await asyncio.to_thread(crawler.get_videos_meta, yt_ids)
        except Exception as exc:
            logger.error("Batch %d failed: %s", batch_start // batch_size + 1, exc)
            failed_batches += 1
            if "quotaExceeded" in str(exc):
                logger.error("Quota exhausted — stopping. Re-run after midnight Pacific.")
                break
            continue

        if not metas:
            continue

        async with AsyncSessionLocal() as session:
            for meta in metas:
                vid_id = id_map.get(meta.youtube_video_id)
                if not vid_id:
                    continue
                published_at = None
                if meta.published_at:
                    try:
                        published_at = datetime.fromisoformat(
                            meta.published_at.replace("Z", "+00:00")
                        )
                    except ValueError:
                        pass

                await session.execute(
                    text("""
                        UPDATE videos SET
                            title            = :title,
                            description      = :description,
                            thumbnail_url    = :thumbnail_url,
                            duration_seconds = :duration_seconds,
                            view_count       = :view_count,
                            tags             = :tags,
                            yt_category_id   = :yt_category_id,
                            published_at     = COALESCE(published_at, :published_at)
                        WHERE id = :id
                          AND thumbnail_url IS NULL
                    """),
                    {
                        "id":               vid_id,
                        "title":            meta.title,
                        "description":      meta.description,
                        "thumbnail_url":    meta.thumbnail_url,
                        "duration_seconds": meta.duration_seconds,
                        "view_count":       meta.view_count or 0,
                        "tags":             meta.tags or [],
                        "yt_category_id":   meta.yt_category_id,
                        "published_at":     published_at,
                    },
                )
                updated += 1
            await session.commit()

        done = min(batch_start + batch_size, len(rows))
        logger.info("Progress: %d / %d  (updated so far: %d)", done, len(rows), updated)

    logger.info("Done. Updated: %d  |  Failed batches: %d", updated, failed_batches)
    logger.info("Quota used: ~%d units (%d batches of 50)", (len(rows) // 50) + 1, (len(rows) // 50) + 1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run",   action="store_true", help="Apply updates (default: dry-run)")
    parser.add_argument("--limit", type=int, default=50_000, help="Max videos to fix (default: 50000)")
    args = parser.parse_args()

    asyncio.run(_run(dry_run=not args.run, limit=args.limit))
