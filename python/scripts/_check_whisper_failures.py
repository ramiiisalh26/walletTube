import asyncio
from sqlalchemy import text
from db.session import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(text(
            "SELECT j.youtube_video_id, j.attempts, j.error_message, j.status, "
            "       v.title, v.youtube_video_id AS yt_id "
            "FROM indexing_retry_jobs j "
            "LEFT JOIN videos v ON v.youtube_video_id = j.youtube_video_id "
            "WHERE j.error_message != 'No transcript available' "
            "   OR j.attempts > 1 "
            "ORDER BY j.attempts DESC, j.updated_at DESC "
            "LIMIT 20"
        ))).fetchall()

        if not rows:
            # Fall back: show all with attempts > 0
            rows = (await session.execute(text(
                "SELECT j.youtube_video_id, j.attempts, j.error_message, j.status "
                "FROM indexing_retry_jobs j "
                "WHERE j.attempts > 0 "
                "ORDER BY j.attempts DESC LIMIT 20"
            ))).fetchall()

        for r in rows:
            title = getattr(r, "title", None) or "—"
            print(f"video={r.youtube_video_id}  attempts={r.attempts}  "
                  f"status={r.status}")
            print(f"  title  : {title[:70]}")
            print(f"  reason : {r.error_message}")
            print()

asyncio.run(main())
