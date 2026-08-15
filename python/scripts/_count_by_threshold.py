r"""Show how many chunks clear each similarity bar for a query (to set expectations
for the 0.80 threshold).

    $env:PYTHONPATH="."; .venv\Scripts\python scripts\_count_by_threshold.py "how does binary search work"
"""
import asyncio
import sys

from sqlalchemy import text

from config import settings
from db.session import AsyncSessionLocal
from services.embedding.model import EmbeddingModel

Q = sys.argv[1] if len(sys.argv) > 1 else "how does binary search work"
P = "Represent this sentence for searching relevant passages: "


async def main() -> None:
    print(f"settings: threshold={settings.search_threshold_similarity}  "
          f"min_results={settings.search_threshold_min_results} (0 = no fallback)\n")
    vec = EmbeddingModel.get().encode_one(P + Q)
    vs = "[" + ",".join(str(v) for v in vec) + "]"
    async with AsyncSessionLocal() as s:
        await s.execute(text("SET LOCAL hnsw.ef_search = 800"))
        print(f"query: {Q!r}")
        for th in (0.70, 0.72, 0.75, 0.80):
            n = (await s.execute(text(
                "SELECT count(*) FROM (SELECT 1 FROM transcript_chunks tc "
                "JOIN videos v ON tc.video_id=v.id "
                "WHERE v.indexing_status='indexed' "
                "AND 1-(tc.embedding<=>CAST(:v AS vector))>=:th "
                "ORDER BY tc.embedding<=>CAST(:v AS vector) LIMIT 800) x"),
                {"v": vs, "th": th})).scalar()
            print(f"  >= {th:.2f}: {n} chunks")


asyncio.run(main())
