r"""Verify the fix: the new all-indexed-videos threshold search returns chunks
for the query that previously got 0 results.

    $env:PYTHONPATH="."; .venv\Scripts\python scripts\_verify_search_fix.py "how does async await work in javascript"
"""
import asyncio
import sys

from sqlalchemy import text

from config import settings
from db.session import AsyncSessionLocal
from services.embedding.model import EmbeddingModel

Q = sys.argv[1] if len(sys.argv) > 1 else "how does async await work in javascript"
P = "Represent this sentence for searching relevant passages: "


async def main():
    vec = EmbeddingModel.get().encode_one(P + Q)
    vs = "[" + ",".join(str(v) for v in vec) + "]"
    async with AsyncSessionLocal() as s:
        n = (await s.execute(text(
            "SELECT count(*) FROM transcript_chunks tc JOIN videos v ON tc.video_id = v.id "
            "WHERE v.indexing_status = 'indexed' "
            "AND 1 - (tc.embedding <=> CAST(:v AS vector)) >= :th"),
            {"v": vs, "th": settings.search_threshold_similarity})).scalar()
    print(f"query: {Q!r}")
    print(f"NEW path -> {n} chunks >= {settings.search_threshold_similarity}  (old routed path returned 0)")
    print("FIXED" if n > 0 else "still 0 — investigate further")


asyncio.run(main())
