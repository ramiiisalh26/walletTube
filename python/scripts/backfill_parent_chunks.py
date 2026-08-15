"""
Backfill parent chunks for already-indexed videos.

For each video that has transcript_chunks but no transcript_parent_chunks,
this script:
  1. Loads all child chunks ordered by start_time.
  2. Groups them into ~PARENT_SECONDS non-overlapping time windows.
  3. Inserts a transcript_parent_chunks row per group.
  4. Updates each child's parent_chunk_id FK.
  5. Commits per video — idempotent and resumable.

Usage:
    python -m scripts.backfill_parent_chunks
    python -m scripts.backfill_parent_chunks --video-id dQw4w9WgXcQ  # single video
"""
import argparse
import asyncio
import logging

from sqlalchemy import text

from db.session import AsyncSessionLocal

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PARENT_SECONDS = 120


def _group_children(children: list, parent_seconds: int = PARENT_SECONDS) -> list[list]:
    """
    Partition children into contiguous ~parent_seconds windows with no overlap.
    A new group opens when accumulated span (child.end_time - group_start) >= parent_seconds.
    """
    if not children:
        return []

    groups: list[list] = []
    current: list = []
    group_start = float(children[0].start_time)

    for c in children:
        current.append(c)
        if float(c.end_time) - group_start >= parent_seconds:
            groups.append(current)
            current = []
            group_start = float(c.end_time)  # next window starts right after this one

    if current:
        groups.append(current)

    return groups


async def backfill_video(session, video_db_id: int, yt_id: str) -> int:
    # Idempotency check — skip if parents already exist for this video
    existing = (
        await session.execute(
            text(
                "SELECT COUNT(*) FROM transcript_parent_chunks WHERE video_id = :vid"
            ),
            {"vid": video_db_id},
        )
    ).scalar()
    if existing > 0:
        logger.debug("  skip %s — already has %d parents", yt_id, existing)
        return 0

    # Load child chunks ordered by start_time
    rows = (
        await session.execute(
            text(
                """
                SELECT tc.id, tc.transcript_id, tc.start_time, tc.end_time, tc.text
                FROM transcript_chunks tc
                WHERE tc.video_id = :vid
                ORDER BY tc.start_time
                """
            ),
            {"vid": video_db_id},
        )
    ).fetchall()

    if not rows:
        logger.debug("  skip %s — no child chunks", yt_id)
        return 0

    transcript_id = rows[0].transcript_id
    groups = _group_children(rows)
    parent_count = 0

    for idx, group in enumerate(groups):
        parent_text = " ".join(r.text for r in group)
        parent_start = float(group[0].start_time)
        parent_end = float(group[-1].end_time)
        word_count = len(parent_text.split())

        parent_id = (
            await session.execute(
                text(
                    """
                    INSERT INTO transcript_parent_chunks
                        (transcript_id, video_id, chunk_index, text,
                         start_time, end_time, word_count)
                    VALUES (:tid, :vid, :idx, :txt, :t0, :t1, :wc)
                    RETURNING id
                    """
                ),
                {
                    "tid": transcript_id,
                    "vid": video_db_id,
                    "idx": idx,
                    "txt": parent_text,
                    "t0": parent_start,
                    "t1": parent_end,
                    "wc": word_count,
                },
            )
        ).scalar()

        child_ids = [r.id for r in group]
        await session.execute(
            text(
                "UPDATE transcript_chunks SET parent_chunk_id = :pid"
                " WHERE id = ANY(CAST(:ids AS bigint[]))"
            ),
            {"pid": parent_id, "ids": child_ids},
        )
        parent_count += 1

    await session.commit()
    return parent_count


async def main(only_video_id: str | None = None) -> None:
    async with AsyncSessionLocal() as session:
        if only_video_id:
            rows = (
                await session.execute(
                    text(
                        "SELECT id, youtube_video_id FROM videos"
                        " WHERE youtube_video_id = :yt AND indexing_status = 'indexed'"
                    ),
                    {"yt": only_video_id},
                )
            ).fetchall()
        else:
            rows = (
                await session.execute(
                    text(
                        "SELECT id, youtube_video_id FROM videos"
                        " WHERE indexing_status = 'indexed' ORDER BY id"
                    )
                )
            ).fetchall()

    logger.info("Found %d indexed video(s) to process", len(rows))
    total_parents = 0

    for row in rows:
        async with AsyncSessionLocal() as session:
            n = await backfill_video(session, row.id, row.youtube_video_id)
            if n:
                logger.info("  ✓ %s — %d parent chunks created", row.youtube_video_id, n)
                total_parents += n

    logger.info("Done. %d parent chunks created across %d videos.", total_parents, len(rows))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--video-id", default=None, help="Process a single YouTube video ID"
    )
    args = parser.parse_args()
    asyncio.run(main(only_video_id=args.video_id))
