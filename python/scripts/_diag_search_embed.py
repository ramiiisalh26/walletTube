r"""Why does the API return no results when chunks clearly exist?
Compare the API's gRPC embedding vs the local model embedding for the same query.

    $env:PYTHONPATH="."; .venv\Scripts\python scripts\_diag_search_embed.py "how does async await work in javascript"
"""
import asyncio
import math
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import text

from config import settings
from db.session import AsyncSessionLocal

QUERY = sys.argv[1] if len(sys.argv) > 1 else "how does async await work in javascript"
PREFIX = "Represent this sentence for searching relevant passages: "


def cos(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)); nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


async def top_sim(s, vec):
    vs = "[" + ",".join(str(v) for v in vec) + "]"
    return (await s.execute(text(
        "SELECT 1 - (embedding <=> CAST(:v AS vector)) FROM transcript_chunks "
        "ORDER BY embedding <=> CAST(:v AS vector) LIMIT 1"), {"v": vs})).scalar()


async def main():
    print(f"query: {QUERY!r}\n")
    from services.embedding.model import EmbeddingModel
    local = EmbeddingModel.get().encode_one(PREFIX + QUERY)

    from api.services import embedding_client
    try:
        grpc_vec = await embedding_client.embed_one(PREFIX + QUERY)
        grpc_ok = True
    except Exception as e:
        print(f"gRPC embed FAILED: {type(e).__name__}: {str(e)[:120]}")
        print("  -> the API can't embed at all; that would 500, not 'no results'")
        grpc_vec, grpc_ok = None, False

    print(f"local model dim: {len(local)}")
    if grpc_ok:
        print(f"gRPC  model dim: {len(grpc_vec)}")
        print(f"cosine(local, gRPC) = {cos(local, grpc_vec):.4f}   (1.0 = same model, low = DIFFERENT model)")

    async with AsyncSessionLocal() as s:
        print(f"\ntop chunk similarity (whole index):")
        print(f"  with LOCAL vector: {await top_sim(s, local):.4f}")
        if grpc_ok:
            print(f"  with gRPC  vector: {await top_sim(s, grpc_vec):.4f}")

    print(f"\nAPI thresholds:")
    print(f"  search_threshold_similarity = {settings.search_threshold_similarity}  (must beat this)")
    print(f"  search_threshold_min_results= {settings.search_threshold_min_results}")
    print(f"  search_min_similarity       = {settings.search_min_similarity}  (fallback floor)")


asyncio.run(main())
