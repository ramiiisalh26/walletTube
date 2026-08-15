r"""Show bi_score / cross_prob / framework-adjust / final_score per result for a query,
mirroring the production pipeline (full-context rerank + bi-blend + framework boost), so
you can see the ranking and tune SEARCH_BI_BLEND_WEIGHT / SEARCH_FRAMEWORK_WEIGHT. Read-only.

    $env:PYTHONPATH="."; .venv\Scripts\python scripts\_diag_rerank_blend.py "How to create CRUD operation in fastApi"

Force the wide-pool failure case (wrong-framework videos enter the pool):
    $env:SEARCH_THRESHOLD_MIN_RESULTS="999"; .venv\Scripts\python scripts\_diag_rerank_blend.py "..."
"""
import asyncio
import re
import sys

from sqlalchemy import text

from api.modules.search.reranker import CrossEncoderReranker, should_refine
from api.services.search_service import (
    _CHUNK_THRESHOLD_ALL_SQL, _CHUNK_SEARCH_ALL_SQL,
    _CHUNK_LIMIT, _CHUNKS_PER_VIDEO, _FALLBACK_PRELIMIT,
    _FRAMEWORKS, _framework_adjust, _RERANK_POOL,
)
from config import settings
from db.session import AsyncSessionLocal
from services.embedding.model import EmbeddingModel

Q = sys.argv[1] if len(sys.argv) > 1 else "How to create CRUD operation in fastApi"
P = "Represent this sentence for searching relevant passages: "


async def main() -> None:
    print(f"query: {Q!r}")
    print(f"bi_blend_weight={settings.search_bi_blend_weight}  "
          f"framework_weight={settings.search_framework_weight}  "
          f"threshold={settings.search_threshold_similarity}\n")

    vec = EmbeddingModel.get().encode_one(P + Q)
    vs = "[" + ",".join(str(v) for v in vec) + "]"

    # Mirror the real chunk-search path: threshold first, top-N fallback if sparse.
    async with AsyncSessionLocal() as s:
        rows = await s.execute(
            text(_CHUNK_THRESHOLD_ALL_SQL),
            {"vector": vs,
             "threshold": settings.search_threshold_similarity,
             "hard_cap": settings.search_threshold_hard_cap},
        )
        raw = [dict(r) for r in rows.mappings().all()]
        if len(raw) < settings.search_threshold_min_results:
            await s.execute(text(f"SET LOCAL hnsw.ef_search = {_FALLBACK_PRELIMIT}"))
            rows = await s.execute(
                text(_CHUNK_SEARCH_ALL_SQL),
                {"vector": vs, "min_score": settings.search_min_similarity,
                 "prelimit": _FALLBACK_PRELIMIT,
                 "limit": _CHUNK_LIMIT, "per_video": _CHUNKS_PER_VIDEO},
            )
            raw = [dict(r) for r in rows.mappings().all()]

    print(f"candidate chunks: {len(raw)}")
    if not should_refine(raw):
        print("(< 2 chunks — reranker skipped, bi-encoder order used as-is)")
        return

    results = CrossEncoderReranker.get().rerank(
        Q, raw, _RERANK_POOL, settings.search_bi_blend_weight
    )

    # Mirror the production framework boost/penalty.
    q_fw = set(re.split(r"\W+", Q.lower())) & _FRAMEWORKS
    w = settings.search_framework_weight
    if q_fw and w > 0:
        rival = _FRAMEWORKS - q_fw
        for r in results:
            r["fw_adj"] = _framework_adjust(r["title"], r["text"], q_fw, rival, w)
            r["final_score"] = min(1.0, max(0.0, r["final_score"] + r["fw_adj"]))
        results.sort(key=lambda s: s["final_score"], reverse=True)
    else:
        for r in results:
            r["fw_adj"] = 0.0
    results = results[:10]

    print(f"query frameworks: {sorted(q_fw) or '(none)'}\n")
    print(f"{'#':<3}{'final':>7}{'bi':>7}{'cross':>7}{'fw':>6}  title")
    print("-" * 80)
    for i, r in enumerate(results, 1):
        safe_title = (r["title"] or "").encode("ascii", "replace").decode("ascii")[:46]
        print(f"{i:<3}{r['final_score']*100:>6.1f}%{r['bi_score']*100:>6.1f}%"
              f"{r['cross_prob']*100:>6.1f}%{r['fw_adj']*100:>+5.0f}  {safe_title}")


asyncio.run(main())
