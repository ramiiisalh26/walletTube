"""One-off: diagnose why videos are stuck / unclassified / not indexed."""
import asyncio

from sqlalchemy import text

from db.session import AsyncSessionLocal


async def q(s, sql):
    return (await s.execute(text(sql))).fetchall()


async def main():
    async with AsyncSessionLocal() as s:
        print("=== videos by indexing_status ===")
        for r in await q(s, "SELECT indexing_status, count(*) c FROM videos GROUP BY indexing_status ORDER BY c DESC"):
            print(f"  {r[0]:12} {r[1]:>7}")

        print("\n=== indexing_jobs by status ===")
        for r in await q(s, "SELECT status, count(*) c FROM indexing_jobs GROUP BY status ORDER BY c DESC"):
            print(f"  {r[0]:12} {r[1]:>7}")

        print("\n=== UNCLASSIFIED videos (no industry) by indexing_status ===")
        for r in await q(s, "SELECT indexing_status, count(*) c FROM videos WHERE primary_industry_id IS NULL GROUP BY indexing_status ORDER BY c DESC"):
            print(f"  {r[0]:12} {r[1]:>7}")

        print("\n=== consistency checks ===")
        stuck = (await q(s, "SELECT count(*) FROM videos WHERE indexing_status='processing'"))[0][0]
        print(f"  videos stuck in 'processing'   : {stuck}")
        idx_nochunks = (await q(s, "SELECT count(*) FROM videos v WHERE v.indexing_status='indexed' AND NOT EXISTS (SELECT 1 FROM transcript_chunks c WHERE c.video_id=v.id)"))[0][0]
        print(f"  'indexed' but 0 chunks         : {idx_nochunks}")
        idx_noemb = (await q(s, "SELECT count(*) FROM videos WHERE indexing_status='indexed' AND embedding IS NULL"))[0][0]
        print(f"  'indexed' but no embedding     : {idx_noemb}")

        print("\n=== not-indexed videos vs their job status ===")
        for r in await q(s, "SELECT COALESCE(j.status,'(no job)') js, count(DISTINCT v.id) c FROM videos v LEFT JOIN indexing_jobs j ON v.youtube_video_id=j.youtube_video_id WHERE v.indexing_status<>'indexed' GROUP BY j.status ORDER BY c DESC"):
            print(f"  job={r[0]:12} videos={r[1]:>7}")

        print("\n=== pending jobs whose video is already indexed (would be skipped) ===")
        dup = (await q(s, "SELECT count(*) FROM indexing_jobs j JOIN videos v ON v.youtube_video_id=j.youtube_video_id WHERE j.status='pending' AND v.indexing_status='indexed'"))[0][0]
        print(f"  pending jobs for already-indexed videos: {dup}")


asyncio.run(main())
