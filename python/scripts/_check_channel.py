import asyncio
from sqlalchemy import text
from db.session import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(text(
            "SELECT id, name, youtube_channel_id, primary_industry_id, is_monitored, last_crawled_at "
            "FROM channels WHERE name ILIKE '%stanford%' "
            "   OR youtube_channel_id = 'UCBa5G_ESCn8Yd4vw5U-gIcg'"
        ))).fetchall()
        if rows:
            for r in rows:
                print(f"id={r.id} name={r.name!r} yt_id={r.youtube_channel_id} "
                      f"industry_id={r.primary_industry_id} monitored={r.is_monitored} "
                      f"last_crawled={r.last_crawled_at}")
        else:
            print("NOT FOUND in channels table")

asyncio.run(main())
