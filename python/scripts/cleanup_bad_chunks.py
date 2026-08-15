"""
Cleanup script — removes degenerate transcript chunks from the DB.

Three categories deleted:
  1. Zero / sub-half-second duration  (481 chunks)  — last-segment artefacts
     with no duration field; useless for timestamp navigation.
  2. Oversized chunks > 90s           (1,891 chunks) — produced by the gap bug
     in the chunker (now fixed); a single transcript gap could span hours.
  3. Tiny text < 20 chars             (4,375 chunks) — single words / noise;
     overlap with the above two categories.

After running this script you should also re-index the affected videos so the
chunker's gap fix produces correct replacements.  Use:
    python scripts/cleanup_bad_chunks.py --requeue

Flags
-----
--dry-run    Print counts only, make no changes (default).
--execute    Actually delete rows.
--requeue    After deleting, set those videos back to indexing_status='pending'
             in the videos table so drain_queue will re-index them.
"""
import argparse
import asyncio
import logging

from sqlalchemy import text
from db.session import AsyncSessionLocal

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)


ZERO_DUR_SQL = """
    SELECT id, video_id FROM transcript_chunks
    WHERE (end_time - start_time) < 0.5
"""

OVERSIZED_SQL = """
    SELECT id, video_id FROM transcript_chunks
    WHERE (end_time - start_time) > 90
"""

TINY_TEXT_SQL = """
    SELECT id, video_id FROM transcript_chunks
    WHERE LENGTH(text) < 20
"""


async def main(execute: bool, requeue: bool) -> None:
    async with AsyncSessionLocal() as session:

        async def _collect(sql: str, label: str) -> tuple[list[int], set[int]]:
            rows = (await session.execute(text(sql))).fetchall()
            ids      = [r[0] for r in rows]
            video_ids = {r[1] for r in rows}
            logger.info("%-30s  %6d chunks  across %d videos", label, len(ids), len(video_ids))
            return ids, video_ids

        zero_ids,  zero_vids  = await _collect(ZERO_DUR_SQL,   "zero/sub-0.5s duration")
        over_ids,  over_vids  = await _collect(OVERSIZED_SQL,  "oversized > 90s")
        tiny_ids,  tiny_vids  = await _collect(TINY_TEXT_SQL,  "tiny text < 20 chars")

        all_ids      = list(set(zero_ids) | set(over_ids) | set(tiny_ids))
        all_video_ids = zero_vids | over_vids | tiny_vids

        logger.info("")
        logger.info("%-30s  %6d chunks  across %d videos (combined, deduped)",
                    "TOTAL TO DELETE", len(all_ids), len(all_video_ids))

        if not execute:
            logger.info("")
            logger.info("Dry-run — no changes made.  Pass --execute to delete.")
            return

        # Delete in batches of 5000 to avoid long lock times
        BATCH = 5_000
        deleted = 0
        for i in range(0, len(all_ids), BATCH):
            batch = all_ids[i : i + BATCH]
            result = await session.execute(
                text("DELETE FROM transcript_chunks WHERE id = ANY(:ids)"),
                {"ids": batch},
            )
            deleted += result.rowcount
            logger.info("Deleted batch %d-%d  (%d rows so far)", i, i + len(batch), deleted)

        logger.info("Deleted %d chunks total", deleted)

        if requeue and all_video_ids:
            await session.execute(
                text("""
                    UPDATE videos
                    SET indexing_status = 'pending', embedding = NULL
                    WHERE id = ANY(:ids)
                """),
                {"ids": list(all_video_ids)},
            )
            logger.info("Requeued %d videos for re-indexing", len(all_video_ids))

        await session.commit()
        logger.info("Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Remove degenerate transcript chunks")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run",  dest="execute", action="store_false", default=False)
    mode.add_argument("--execute",  dest="execute", action="store_true")
    parser.add_argument("--requeue", action="store_true",
                        help="Reset affected videos to pending so they get re-indexed")
    args = parser.parse_args()
    asyncio.run(main(execute=args.execute, requeue=args.requeue))
