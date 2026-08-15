import asyncio
from sqlalchemy import text
from db.session import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(text(
            "SELECT v.indexing_status, COUNT(*) AS videos, "
            "COUNT(t.id) AS has_transcript "
            "FROM videos v "
            "LEFT JOIN transcripts t ON t.video_id = v.id AND t.is_primary = TRUE "
            "GROUP BY v.indexing_status ORDER BY videos DESC"
        ))).fetchall()

        print(f"{'Status':<15} {'Videos':>8} {'Has Transcript':>15} {'Missing':>8}")
        print("-" * 52)
        total_v, total_t = 0, 0
        for r in rows:
            missing = r.videos - r.has_transcript
            print(f"{r.indexing_status:<15} {r.videos:>8,} {r.has_transcript:>15,} {missing:>8,}")
            total_v += r.videos
            total_t += r.has_transcript
        print("-" * 52)
        print(f"{'TOTAL':<15} {total_v:>8,} {total_t:>15,} {total_v - total_t:>8,}")

asyncio.run(main())
