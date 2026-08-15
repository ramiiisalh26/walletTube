import asyncio
from sqlalchemy import text
from db.session import AsyncSessionLocal

async def reset():
    sql = """
        UPDATE indexing_jobs
        SET status='pending', error_message=NULL
        WHERE status IN ('skipped', 'processing')
          AND youtube_video_id IN ('rfscVS0vtbw', '_uQrJ0TkZlc', 'kqtD5dpn9C8')
        RETURNING id, youtube_video_id
    """
    async with AsyncSessionLocal() as s:
        r = await s.execute(text(sql))
        rows = r.fetchall()
        await s.commit()
        print(f"Reset {len(rows)} job(s): {[(r[0], r[1]) for r in rows]}")

asyncio.run(reset())
