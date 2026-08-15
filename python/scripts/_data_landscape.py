r"""Show what's indexed, arranged by industry / topic / channel — so you can
picture your searchable data and pick test queries that will hit it.

    $env:PYTHONPATH="."; .venv\Scripts\python scripts\_data_landscape.py
"""
import asyncio
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import text

from db.session import AsyncSessionLocal


async def main() -> None:
    async with AsyncSessionLocal() as s:
        async def rows(q, **p):
            return (await s.execute(text(q), p)).fetchall()

        async def sc(q, **p):
            return (await s.execute(text(q), p)).scalar()

        idx = await sc("SELECT count(*) FROM videos WHERE indexing_status='indexed'")
        chunks = await sc("SELECT count(*) FROM transcript_chunks")
        print("=" * 68)
        print(f"  SEARCHABLE NOW:  {idx} videos   |   {chunks} chunks")
        print("=" * 68)

        print("\n--- BY INDUSTRY (indexed videos) ---")
        for r in await rows("""
            SELECT COALESCE(i.name,'(unclassified)') name, count(v.id) c
            FROM videos v LEFT JOIN industries i ON v.primary_industry_id = i.id
            WHERE v.indexing_status = 'indexed'
            GROUP BY i.name ORDER BY c DESC"""):
            print(f"  {r[1]:>6}  {r[0]}")

        top_iid = await sc("""
            SELECT primary_industry_id FROM videos
            WHERE indexing_status='indexed' AND primary_industry_id IS NOT NULL
            GROUP BY primary_industry_id ORDER BY count(*) DESC LIMIT 1""")
        if top_iid:
            print("\n--- SAMPLE VIDEOS in the biggest industry (by views) ---")
            for r in await rows("""
                SELECT title FROM videos
                WHERE primary_industry_id = :i AND indexing_status='indexed'
                ORDER BY view_count DESC NULLS LAST LIMIT 10""", i=top_iid):
                print(f"   - {r[0][:72]}")

        print("\n--- TOP TOPICS (by indexed videos) ---")
        trows = await rows("""
            SELECT t.name, count(DISTINCT vt.video_id) c
            FROM topics t
            JOIN video_topics vt ON vt.topic_id = t.id
            JOIN videos v ON v.id = vt.video_id AND v.indexing_status='indexed'
            GROUP BY t.id, t.name ORDER BY c DESC LIMIT 15""")
        if trows:
            for r in trows:
                print(f"  {r[1]:>6}  {r[0]}")
        else:
            print("  (no topic assignments yet — search falls back to industry + global)")

        print("\n--- TOP CHANNELS (indexed videos) ---")
        for r in await rows("""
            SELECT ch.name, count(v.id) c
            FROM channels ch JOIN videos v ON v.channel_id = ch.id AND v.indexing_status='indexed'
            GROUP BY ch.id, ch.name ORDER BY c DESC LIMIT 10"""):
            print(f"  {r[1]:>5}  {r[0][:55]}")


asyncio.run(main())
