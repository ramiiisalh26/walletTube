import asyncio
from sqlalchemy import text
from db.session import AsyncSessionLocal

async def check():
    async with AsyncSessionLocal() as s:
        r = await s.execute(text("""
            SELECT id, youtube_video_id, status, priority, attempts, max_attempts, error_message
            FROM indexing_jobs
            WHERE youtube_video_id IN ('rfscVS0vtbw', '_uQrJ0TkZlc', 'kqtD5dpn9C8')
            ORDER BY priority DESC, id
        """))
        rows = r.fetchall()
        print(f"{'id':>6}  {'youtube_video_id':20}  {'status':12}  {'pri':4}  {'att':4}  {'max':4}  error")
        print("-" * 80)
        for row in rows:
            print(f"{row[0]:>6}  {row[1]:20}  {row[2]:12}  {row[3]:4}  {row[4]:4}  {row[5]:4}  {row[6] or ''}")

asyncio.run(check())
