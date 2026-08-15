r"""Trace the API's video-routing for a query to see where results get lost.

    $env:PYTHONPATH="."; .venv\Scripts\python scripts\_diag_search_routing.py "how does async await work in javascript"
"""
import asyncio
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import text

from config import settings
from db.session import AsyncSessionLocal
from services.embedding.model import EmbeddingModel

QUERY = sys.argv[1] if len(sys.argv) > 1 else "how does async await work in javascript"
PREFIX = "Represent this sentence for searching relevant passages: "


async def main():
    vec = EmbeddingModel.get().encode_one(PREFIX + QUERY)
    vs = "[" + ",".join(str(v) for v in vec) + "]"
    print(f"query: {QUERY!r}\n")

    async with AsyncSessionLocal() as s:
        # 4. industry detection
        row = (await s.execute(text("""
            SELECT id, slug, 1 - (embedding <=> CAST(:v AS vector)) sim
            FROM industries WHERE is_active=TRUE AND embedding IS NOT NULL
            ORDER BY embedding <=> CAST(:v AS vector) LIMIT 1"""), {"v": vs})).fetchone()
        print(f"4. industry detect: {row.slug if row else None}  sim={(row.sim if row else 0):.3f}  (needs >= 0.40)")
        industry_id = row.id if row and row.sim >= 0.40 else None
        print(f"   -> industry_id used: {industry_id}")

        # 5. topics
        topic_ids = []
        if industry_id:
            trows = (await s.execute(text("""
                SELECT id, name, 1 - (embedding <=> CAST(:v AS vector)) sim FROM topics
                WHERE industry_id=:i AND is_active=TRUE AND embedding IS NOT NULL
                  AND 1 - (embedding <=> CAST(:v AS vector)) >= 0.35
                ORDER BY embedding <=> CAST(:v AS vector) LIMIT 8"""), {"v": vs, "i": industry_id})).fetchall()
            topic_ids = [r[0] for r in trows]
            print(f"5. topics matched: {[(r[1], round(r[2], 3)) for r in trows]}")
        else:
            print("5. topics: SKIPPED (no industry)")

        # 6. collect videos
        video_ids: list[int] = []
        if topic_ids:
            video_ids = [r[0] for r in (await s.execute(text(
                "SELECT DISTINCT video_id FROM video_topics WHERE topic_id = ANY(CAST(:t AS integer[]))"),
                {"t": topic_ids})).fetchall()]
        if not video_ids and industry_id:
            video_ids = [r[0] for r in (await s.execute(text(
                "SELECT id FROM videos WHERE primary_industry_id=:i AND indexing_status='indexed'"),
                {"i": industry_id})).fetchall()]
        from_routing = len(video_ids)
        unc = [r[0] for r in (await s.execute(text(
            "SELECT id FROM videos WHERE primary_industry_id IS NULL AND indexing_status='indexed'"))).fetchall()]
        video_ids = list(set(video_ids) | set(unc))
        global_fallback = not video_ids
        if global_fallback:
            video_ids = [r[0] for r in (await s.execute(text(
                "SELECT id FROM videos WHERE indexing_status='indexed'"))).fetchall()]
        print(f"6. videos: from_routing={from_routing}  +unclassified={len(unc)}  global_fallback={global_fallback}")
        print(f"   -> FINAL video pool: {len(video_ids)} videos")

        # 7. chunk threshold search on that pool
        hits = (await s.execute(text("""
            SELECT count(*) FROM transcript_chunks tc
            WHERE tc.video_id = ANY(CAST(:vids AS integer[]))
              AND 1 - (tc.embedding <=> CAST(:v AS vector)) >= :th"""),
            {"vids": video_ids, "v": vs, "th": settings.search_threshold_similarity})).scalar()
        print(f"7. chunks >= {settings.search_threshold_similarity} in pool: {hits}  "
              f"(min {settings.search_threshold_min_results} or it falls back)")

        # where IS the best chunk, and is it in the pool?
        top = (await s.execute(text("""
            SELECT tc.video_id, 1 - (tc.embedding <=> CAST(:v AS vector)) sim
            FROM transcript_chunks tc ORDER BY tc.embedding <=> CAST(:v AS vector) LIMIT 1"""),
            {"v": vs})).fetchone()
        print(f"\nBEST chunk anywhere: video_id={top[0]}  sim={top[1]:.3f}  "
              f"IN POOL? {top[0] in set(video_ids)}")


asyncio.run(main())
