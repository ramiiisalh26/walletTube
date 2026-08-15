r"""Before/after: show how the cross-encoder reorders real search results.

Bypasses the API/Redis/gRPC — embeds the query with the local model, retrieves
top chunks from the DB (same pgvector cosine the API uses), then shows the
bi-encoder order vs the cross-encoder rerank.

    $env:PYTHONPATH="."; .venv\Scripts\python scripts\test_rerank_quality.py "python decorators explained"
"""
import asyncio
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import text

from api.modules.search.reranker import CrossEncoderReranker
from db.session import AsyncSessionLocal
from services.embedding.model import EmbeddingModel

QUERY = sys.argv[1] if len(sys.argv) > 1 else "how does garbage collection work"
PREFIX = "Represent this sentence for searching relevant passages: "


async def main() -> None:
    vec = EmbeddingModel.get().encode_one(PREFIX + QUERY)
    vecstr = "[" + ",".join(str(v) for v in vec) + "]"

    async with AsyncSessionLocal() as s:
        rows = (await s.execute(text("""
            SELECT v.youtube_video_id, v.title, tc.text, tc.start_time, tc.end_time,
                   1 - (tc.embedding <=> CAST(:vec AS vector)) AS vector_score
            FROM transcript_chunks tc JOIN videos v ON tc.video_id = v.id
            WHERE v.indexing_status = 'indexed'
              AND 1 - (tc.embedding <=> CAST(:vec AS vector)) >= 0.5
            ORDER BY tc.embedding <=> CAST(:vec AS vector)
            LIMIT 60
        """), {"vec": vecstr})).mappings().all()

    chunks = [dict(r) for r in rows]
    print(f"\nquery: {QUERY!r}   |   retrieved {len(chunks)} chunks\n")
    if not chunks:
        print("no results — try another query or wait for more videos to index")
        return

    print("=== BEFORE: bi-encoder order (what users get now) ===")
    for c in chunks[:5]:
        print(f"  {c['vector_score']:.3f}  {c['title'][:45]:45}  {c['text'][:55]}")

    for c in chunks:  # fields the reranker expects
        c.update(thumbnail_url=None, channel_name=None, view_count=0,
                 parent_text=None, parent_start_time=0, parent_end_time=0)
    reranked = CrossEncoderReranker.get().rerank(QUERY, chunks, 5)

    print("\n=== AFTER: cross-encoder rerank (with reranker enabled) ===")
    for seg in reranked:
        print(f"  {seg['cross_score']:+.3f}  {seg['title'][:45]:45}  {seg['text'][:55]}")


asyncio.run(main())
