"""
Assign topics to videos using YouTube metadata (title + description).

More accurate than embedding-mean because:
  - Title is laser-focused on the subject
  - Description explicitly names tools/technologies
  - 50 videos per API call = 1 quota unit (800 videos = 16 units)

Usage:
    python scripts/assign_topics_from_crawler.py             # skip already-assigned
    python scripts/assign_topics_from_crawler.py --force     # re-assign everything
    python scripts/assign_topics_from_crawler.py --dry-run   # print assignments, no DB writes
"""
import asyncio
import argparse
import logging
import sys

sys.path.insert(0, ".")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("assign_topics")


def _vec_str(vec: list[float]) -> str:
    return "[" + ",".join(str(v) for v in vec) + "]"


async def run(force: bool, dry_run: bool) -> None:
    from db.session import AsyncSessionLocal
    from services.crawler.channel_crawler import YouTubeCrawler
    from services.embedding.model import EmbeddingModel
    from sqlalchemy import text

    logger.info("Loading embedding model…")
    model = EmbeddingModel.get()
    logger.info("Model ready")

    crawler = YouTubeCrawler()

    async with AsyncSessionLocal() as session:
        if force:
            # Re-assign all indexed videos regardless of existing topic
            rows = (await session.execute(text("""
                SELECT v.id, v.youtube_video_id, v.primary_industry_id
                FROM videos v
                WHERE v.indexing_status = 'indexed'
                  AND v.primary_industry_id IS NOT NULL
                ORDER BY v.id
            """))).fetchall()
        else:
            # Only videos without a topic yet
            rows = (await session.execute(text("""
                SELECT v.id, v.youtube_video_id, v.primary_industry_id
                FROM videos v
                WHERE v.indexing_status = 'indexed'
                  AND v.primary_industry_id IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM video_topics vt WHERE vt.video_id = v.id
                  )
                ORDER BY v.id
            """))).fetchall()

    total = len(rows)
    logger.info("Videos to process: %d  (force=%s  dry_run=%s)", total, force, dry_run)
    if total == 0:
        logger.info("Nothing to do.")
        return

    assigned = 0
    skipped  = 0
    api_calls = 0

    # Process in batches of 50 (1 quota unit per batch)
    for offset in range(0, total, 50):
        batch = rows[offset: offset + 50]
        batch_yt_ids  = [r[1] for r in batch]
        batch_by_ytid = {r[1]: r for r in batch}  # youtube_video_id → (id, yt_id, industry_id)

        # ── Fetch fresh metadata from YouTube API ─────────────────────────────
        metas = crawler.get_videos_meta(batch_yt_ids)
        api_calls += 1
        logger.info("Batch %d–%d: fetched %d metadata records (API call #%d)",
                    offset + 1, offset + len(batch), len(metas), api_calls)

        if not metas:
            skipped += len(batch)
            continue

        # ── Embed title + description for each video ──────────────────────────
        texts = [f"{m.title}. {m.description[:300] if m.description else ''}" for m in metas]
        vectors = model.encode_batch(texts)

        async with AsyncSessionLocal() as session:
            for meta, vec in zip(metas, vectors):
                db_row = batch_by_ytid.get(meta.youtube_video_id)
                if db_row is None:
                    skipped += 1
                    continue

                video_db_id  = db_row[0]
                industry_id  = db_row[2]
                vec_str      = _vec_str(vec)

                # Find closest topic in this industry
                topic_row = (await session.execute(text("""
                    SELECT id, name
                    FROM topics
                    WHERE industry_id = :ind_id
                      AND is_active = TRUE
                      AND embedding IS NOT NULL
                    ORDER BY embedding <=> CAST(:vec AS vector)
                    LIMIT 1
                """), {"ind_id": industry_id, "vec": vec_str})).fetchone()

                if topic_row is None:
                    logger.warning("No topics for industry_id=%d  video=%s", industry_id, meta.youtube_video_id)
                    skipped += 1
                    continue

                if dry_run:
                    logger.info("[dry-run] %s → topic '%s'", meta.title[:60], topic_row[1])
                    assigned += 1
                    continue

                # ── Save metadata columns ─────────────────────────────────────
                published_at_val = None
                if meta.published_at:
                    from datetime import datetime as dt
                    published_at_val = dt.fromisoformat(meta.published_at.replace("Z", "+00:00"))

                await session.execute(text("""
                    UPDATE videos SET
                        title            = :title,
                        description      = :description,
                        thumbnail_url    = :thumbnail_url,
                        duration_seconds = :duration_seconds,
                        view_count       = :view_count,
                        tags             = CAST(:tags AS text[]),
                        yt_category_id   = :yt_category_id,
                        published_at     = :published_at
                    WHERE id = :vid
                """), {
                    "title":            meta.title,
                    "description":      meta.description or "",
                    "thumbnail_url":    meta.thumbnail_url,
                    "duration_seconds": meta.duration_seconds,
                    "view_count":       meta.view_count or 0,
                    "tags":             meta.tags or [],
                    "yt_category_id":   meta.yt_category_id,
                    "published_at":     published_at_val,
                    "vid":              video_db_id,
                })

                # ── Assign topic ──────────────────────────────────────────────
                if force:
                    await session.execute(
                        text("DELETE FROM video_topics WHERE video_id = :vid"),
                        {"vid": video_db_id},
                    )

                await session.execute(text("""
                    INSERT INTO video_topics (video_id, topic_id, confidence)
                    VALUES (:vid, :tid, 0.85)
                    ON CONFLICT DO NOTHING
                """), {"vid": video_db_id, "tid": topic_row[0]})
                assigned += 1

            if not dry_run:
                await session.commit()

        logger.info("Progress %d/%d — assigned=%d  skipped=%d",
                    min(offset + 50, total), total, assigned, skipped)

    logger.info("Done — assigned=%d  skipped=%d  api_calls=%d  quota_used=%d",
                assigned, skipped, api_calls, api_calls)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Assign topics to videos via YouTube crawler")
    parser.add_argument("--force",   action="store_true", help="Re-assign even if topic already exists")
    parser.add_argument("--dry-run", action="store_true", help="Print assignments without writing to DB")
    args = parser.parse_args()
    asyncio.run(run(force=args.force, dry_run=args.dry_run))
