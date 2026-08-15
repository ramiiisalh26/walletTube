r"""Sweep hnsw.ef_search for the fallback query to find the speed/recall sweet spot.

    $env:PYTHONPATH="."; .venv\Scripts\python scripts\_verify_fallback_speed.py "how to link between python and java"
"""
import asyncio
import sys
import time

from sqlalchemy import text

from api.services.search_service import _CHUNK_SEARCH_ALL_SQL, _CHUNK_LIMIT, _CHUNKS_PER_VIDEO
from config import settings
from db.session import AsyncSessionLocal
from services.embedding.model import EmbeddingModel

Q = sys.argv[1] if len(sys.argv) > 1 else "how to link between python and java"
P = "Represent this sentence for searching relevant passages: "


async def main() -> None:
    vec = EmbeddingModel.get().encode_one(P + Q)
    vs = "[" + ",".join(str(v) for v in vec) + "]"
    print(f"query: {Q!r}  (was ~129,000ms with the old full-scan query)\n")
    for ef in (40, 100, 200, 400):
        async with AsyncSessionLocal() as s:
            await s.execute(text(f"SET LOCAL hnsw.ef_search = {ef}"))
            t0 = time.monotonic()
            rows = (await s.execute(text(_CHUNK_SEARCH_ALL_SQL), {
                "vector": vs, "min_score": settings.search_min_similarity,
                "prelimit": ef, "limit": _CHUNK_LIMIT, "per_video": _CHUNKS_PER_VIDEO,
            })).mappings().all()
            dt = (time.monotonic() - t0) * 1000
        print(f"  ef_search={ef:>4}: {dt:>7.0f}ms  ->  {len(rows)} chunks")


asyncio.run(main())
