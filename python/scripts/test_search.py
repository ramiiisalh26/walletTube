"""
Search pipeline diagnostic -- traces every stage end-to-end with full logging.

Usage
-----
  python -m scripts.test_search "how does Python handle memory"
  python -m scripts.test_search "java abstract class vs interface" --top 5
  python -m scripts.test_search "react hooks useState" --no-refine   # bi-encoder path only
  python -m scripts.test_search "Python memory" --grpc               # use gRPC server (must be running)

What it prints
--------------
  [0] Raw query + preprocessing (abbreviation expansion, decomposition)
  [1] Embedding -- time + vector stats
  [2] Industry detection -- top 3 candidates + winner
  [3] Topic matching -- all topics above threshold
  [4] Video collection -- count from topics + fallback
  [5] Video ranking -- top 15 videos by embedding similarity
  [6] Bi-encoder chunk search -- top 20 chunks with scores
  [6b] Per-video distribution -- how many chunks came from each video
  [7] should_refine() gate -- True/False and why
  [8] Segment splitting -- chunk -> sentence windows
  [9] Cross-encoder scoring -- ALL segments ranked
  [10] Deduplication -- winning segment per video
  [11] Final results -- top N with all scores side-by-side
"""
import argparse
import asyncio
import logging
import re
import sys
import time
from typing import Any

# -- stdlib logging: quiet SQLAlchemy noise ------------------------------------
logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s  %(name)s  %(message)s",
)
logging.getLogger("sqlalchemy").setLevel(logging.ERROR)
logging.getLogger("asyncpg").setLevel(logging.ERROR)


# -- helpers -------------------------------------------------------------------

SEP  = "-" * 72
SEP2 = "=" * 72

def hdr(n: str) -> None:
    print(f"\n{SEP2}\n  {n}\n{SEP2}")

def sub(n: str) -> None:
    print(f"\n{SEP}\n  {n}\n{SEP}")

def row(label: str, value: Any, width: int = 28) -> None:
    print(f"  {label:<{width}} {value}")

def score_bar(v: float, width: int = 20) -> str:
    filled = round(v * width)
    return "[" + "#" * filled + "." * (width - filled) + f"] {v:.4f}"


# -- main diagnostic -----------------------------------------------------------

async def run(query: str, top_k: int, force_refine: bool, use_grpc: bool) -> None:
    from sqlalchemy import text
    from db.session import AsyncSessionLocal
    from config import settings
    from api.services.search_service import (
        _expand_abbreviations, _decompose_query,
        _BGE_QUERY_PREFIX, _to_vector_str,
        _INDUSTRY_MIN_SIM, _TOPIC_MIN_SIM,
        _TOPIC_LIMIT, _VIDEO_RANK_LIMIT,
        _CHUNK_LIMIT, _CHUNKS_PER_VIDEO,
        _CHUNK_SEARCH_SQL, _CHUNK_SEARCH_ALL_SQL,
        _TOPIC_SQL, _VIDEOS_BY_TOPICS_SQL,
        _VIDEOS_BY_INDUSTRY_SQL, _RANK_VIDEOS_SQL,
    )
    from api.modules.search.reranker import CrossEncoderReranker, should_refine

    # -- embed function -- local model or gRPC ---------------------------------
    if use_grpc:
        from api.services import embedding_client
        async def embed_texts(texts: list[str]) -> list[list[float]]:
            return await embedding_client.embed_batch(texts)
    else:
        from services.embedding.model import EmbeddingModel
        model = EmbeddingModel.get()
        async def embed_texts(texts: list[str]) -> list[list[float]]:
            return await asyncio.to_thread(model.encode_batch, texts)

    print(f"\n{'='*72}")
    print(f"  SEARCH PIPELINE DIAGNOSTIC")
    print(f"  Query : {query!r}")
    print(f"  Mode  : {'gRPC' if use_grpc else 'local model (no gRPC needed)'}")
    print(f"{'='*72}")

    async with AsyncSessionLocal() as session:

        # -- [0] Query preprocessing -------------------------------------------
        hdr("[0] QUERY PREPROCESSING")
        expanded    = _expand_abbreviations(query)
        sub_queries = _decompose_query(expanded)

        row("original",  query)
        row("expanded",  expanded)
        row("sub-queries", sub_queries)
        changed = expanded != query
        row("abbreviations expanded?", "YES -> " + repr(expanded) if changed else "no")
        row("decomposed into parts?",  f"YES ({len(sub_queries)} parts)" if len(sub_queries) > 1 else "no (single query)")

        # -- [1] Embedding -----------------------------------------------------
        hdr("[1] EMBEDDING")
        t0 = time.perf_counter()
        prefixed = [_BGE_QUERY_PREFIX + q for q in sub_queries]
        vecs = await embed_texts(prefixed)
        embed_ms = (time.perf_counter() - t0) * 1000

        if len(vecs) > 1:
            n, d = len(vecs), len(vecs[0])
            vector = [sum(vecs[j][i] for j in range(n)) / n for i in range(d)]
            row("strategy", f"averaged {n} sub-query vectors")
        else:
            vector = vecs[0]
            row("strategy", "single vector")

        norm = sum(x*x for x in vector) ** 0.5
        row("dimensions", len(vector))
        row("L2 norm (should be ~1.0)", f"{norm:.6f}")
        row("embed latency", f"{embed_ms:.1f} ms")
        vector_str = _to_vector_str(vector)
        print(f"  first 8 dims : {[round(v,4) for v in vector[:8]]}")

        # -- [2] Industry detection --------------------------------------------
        hdr("[2] INDUSTRY DETECTION")
        rows = (await session.execute(
            text("""
                SELECT id, slug, 1 - (embedding <=> CAST(:vec AS vector)) AS sim
                FROM industries
                WHERE is_active = TRUE AND embedding IS NOT NULL
                ORDER BY embedding <=> CAST(:vec AS vector)
                LIMIT 5
            """),
            {"vec": vector_str},
        )).fetchall()

        industry_id   = None
        industry_slug = None
        for i, r in enumerate(rows):
            marker = ""
            if r.sim >= _INDUSTRY_MIN_SIM and industry_id is None:
                industry_id   = r.id
                industry_slug = r.slug
                marker = "  <-- WINNER"
            bar = score_bar(min(r.sim, 1.0))
            print(f"  #{i+1} {r.slug:<30} {bar}{marker}")

        if industry_id:
            row("\ndetected industry", industry_slug)
            row("threshold", _INDUSTRY_MIN_SIM)
        else:
            print(f"\n  No industry detected above threshold {_INDUSTRY_MIN_SIM} -- global search")

        # -- [3] Topic matching ------------------------------------------------
        hdr("[3] TOPIC MATCHING")
        topic_ids: list[int] = []
        if industry_id:
            topic_rows = (await session.execute(
                text(_TOPIC_SQL),
                {"industry_id": industry_id, "vec": vector_str,
                 "min_sim": _TOPIC_MIN_SIM, "limit": _TOPIC_LIMIT},
            )).fetchall()
            topic_ids = [r[0] for r in topic_rows]

            if topic_rows:
                for i, r in enumerate(topic_rows):
                    name_row = (await session.execute(
                        text("SELECT name FROM topics WHERE id = :id"), {"id": r[0]}
                    )).fetchone()
                    name = name_row[0] if name_row else str(r[0])
                    print(f"  #{i+1} id={r[0]}  {name}")
            else:
                print("  No topics matched")
        else:
            print("  Skipped (no industry)")

        row("\ntopics matched", len(topic_ids))
        row("threshold", _TOPIC_MIN_SIM)

        # -- [4] Video collection ----------------------------------------------
        hdr("[4] VIDEO COLLECTION")
        video_ids: list[int] = []

        if topic_ids:
            vrows = (await session.execute(
                text(_VIDEOS_BY_TOPICS_SQL), {"topic_ids": topic_ids}
            )).fetchall()
            video_ids = [r[0] for r in vrows]
            print(f"  from topics          : {len(video_ids)} videos")

        if not video_ids and industry_id:
            vrows = (await session.execute(
                text(_VIDEOS_BY_INDUSTRY_SQL), {"industry_id": industry_id}
            )).fetchall()
            video_ids = [r[0] for r in vrows]
            print(f"  fallback (industry)  : {len(video_ids)} videos")

        unclassified = (await session.execute(text("""
            SELECT id FROM videos
            WHERE primary_industry_id IS NULL
              AND indexing_status = 'indexed'
              AND embedding IS NOT NULL
        """))).fetchall()
        extra = {r[0] for r in unclassified}
        video_ids = list(set(video_ids) | extra)
        print(f"  + unclassified       : {len(extra)} videos added")
        print(f"  TOTAL before ranking : {len(video_ids)} videos")

        # -- [5] Video ranking -------------------------------------------------
        hdr("[5] VIDEO RANKING  (top " + str(_VIDEO_RANK_LIMIT) + ")")
        if video_ids:
            ranked = (await session.execute(
                text(_RANK_VIDEOS_SQL),
                {"video_ids": video_ids, "vector": vector_str, "limit": _VIDEO_RANK_LIMIT},
            )).fetchall()
            video_ids = [r[0] for r in ranked]

            # Fetch titles for display
            title_rows = (await session.execute(
                text("SELECT id, title, youtube_video_id FROM videos WHERE id = ANY(:ids)"),
                {"ids": video_ids[:15]},
            )).fetchall()
            title_map = {r[0]: (r[1] or r[2] or str(r[0]))[:55] for r in title_rows}

            sim_rows = (await session.execute(
                text("""
                    SELECT id, 1 - (embedding <=> CAST(:vec AS vector)) AS sim
                    FROM videos WHERE id = ANY(:ids) AND embedding IS NOT NULL
                    ORDER BY embedding <=> CAST(:vec AS vector) LIMIT 15
                """),
                {"ids": video_ids[:15], "vec": vector_str},
            )).fetchall()

            for i, r in enumerate(sim_rows):
                title = title_map.get(r[0], str(r[0]))
                bar   = score_bar(min(float(r[1]), 1.0), 15)
                print(f"  #{i+1:>2}  {bar}  {title}")

            print(f"\n  showing top 15 of {len(video_ids)} ranked videos")

        # -- [6] Bi-encoder chunk search ---------------------------------------
        hdr("[6] BI-ENCODER CHUNK SEARCH  (limit=" + str(_CHUNK_LIMIT) + ", per_video=" + str(_CHUNKS_PER_VIDEO) + ")")
        t0 = time.perf_counter()
        if video_ids:
            chunk_rows = await session.execute(
                text(_CHUNK_SEARCH_SQL),
                {"vector": vector_str, "video_ids": video_ids,
                 "min_score": settings.search_min_similarity,
                 "limit": _CHUNK_LIMIT, "per_video": _CHUNKS_PER_VIDEO},
            )
        else:
            chunk_rows = await session.execute(
                text(_CHUNK_SEARCH_ALL_SQL),
                {"vector": vector_str, "min_score": settings.search_min_similarity,
                 "limit": _CHUNK_LIMIT, "per_video": _CHUNKS_PER_VIDEO},
            )
        raw_results = [dict(r) for r in chunk_rows.mappings().all()]
        chunk_ms = (time.perf_counter() - t0) * 1000

        row("chunks returned",   len(raw_results))
        row("chunk search time", f"{chunk_ms:.1f} ms")
        row("min_score filter",  settings.search_min_similarity)

        if raw_results:
            top_score = float(raw_results[0]["vector_score"])
            bot_score = float(raw_results[-1]["vector_score"])
            row("top vector_score",  f"{top_score:.4f}")
            row("bottom vector_score", f"{bot_score:.4f}")
            row("score spread",      f"{top_score - bot_score:.4f}")

        print()
        print(f"  {'#':>3}  {'vector_score':>12}  {'video title':<45}  snippet")
        print(f"  {'-'*3}  {'-'*12}  {'-'*45}  {'-'*30}")
        for i, r in enumerate(raw_results[:20]):
            title   = (r.get("title") or "")[:45]
            snippet = r["text"][:40].replace("\n", " ")
            print(f"  #{i+1:>2}  {float(r['vector_score']):>12.4f}  {title:<45}  {snippet}...")
        if len(raw_results) > 20:
            print(f"  ... {len(raw_results) - 20} more chunks not shown")

        # -- [6b] Per-video distribution ---------------------------------------
        sub("[6b] CHUNKS PER VIDEO (distribution)")
        from collections import Counter
        vid_counts = Counter(r["title"][:50] for r in raw_results)
        for title, cnt in vid_counts.most_common(10):
            bar = "#" * cnt + "." * (_CHUNKS_PER_VIDEO - cnt)
            print(f"  {bar}  {cnt}x  {title}")
        if len(vid_counts) > 10:
            print(f"  ... {len(vid_counts)-10} more videos")
        unique_vids = len(vid_counts)
        row("\nunique videos in chunk pool", unique_vids)
        row("max chunks from one video",   vid_counts.most_common(1)[0][1] if vid_counts else 0)

        # -- [7] should_refine() gate ------------------------------------------
        hdr("[7] SHOULD_REFINE() GATE")
        refined = should_refine(raw_results)
        if force_refine:
            refined = True
            print("  --no-refine flag NOT set -- forcing refined=True for this run")

        if len(raw_results) >= 2:
            top    = float(raw_results[0]["vector_score"])
            second = float(raw_results[1]["vector_score"])
            gap    = top - second
            row("top score",    f"{top:.4f}")
            row("2nd score",    f"{second:.4f}")
            row("gap",          f"{gap:.4f}")
            row("rule (skip if)", "top > 0.85 AND gap > 0.10")
            rule_met = top > 0.85 and gap > 0.10
            row("rule met?",    f"{'YES -> cross-encoder SKIPPED' if rule_met else 'NO  -> cross-encoder RUNS'}")
        row("\nrefined",        refined)

        if not refined:
            # -- bi-encoder-only path ------------------------------------------
            sub("BI-ENCODER ONLY PATH (cross-encoder skipped)")
            print("  Results sorted by vector_score DESC (+ keyword bonus if tech query)")
            for i, r in enumerate(raw_results[:top_k]):
                print(f"  #{i+1:>2}  bi={float(r['vector_score']):.4f}  "
                      f"{(r.get('title') or '')[:50]}  |  {r['text'][:50]}...")
            print(f"\n  Returning {min(top_k, len(raw_results))} results.  Done.")
            return

        # -- [8] Segment splitting ---------------------------------------------
        hdr("[8] SEGMENT SPLITTING  (cross-encoder path)")
        reranker = CrossEncoderReranker.get()

        all_segs: list[dict] = []
        for chunk in raw_results:
            segs = reranker._split_chunk(chunk)
            all_segs.extend(segs)

        row("chunks in",          len(raw_results))
        row("segments produced",  len(all_segs))
        row("avg segs/chunk",     f"{len(all_segs)/max(len(raw_results),1):.2f}")

        # Show splitting for the top chunk
        if raw_results:
            top_chunk = raw_results[0]
            top_segs  = reranker._split_chunk(top_chunk)
            print(f"\n  Top chunk split ({len(top_segs)} segments):")
            print(f"  chunk text : {top_chunk['text'][:80]}...")
            print(f"  chunk span : {top_chunk['start_time']:.1f}s - {top_chunk['end_time']:.1f}s")
            for s in top_segs:
                print(f"    [{s['start_time']:.1f}s-{s['end_time']:.1f}s]  {s['text']}")

        from api.modules.search.reranker import _MAX_SEGMENTS
        if len(all_segs) > _MAX_SEGMENTS:
            all_segs.sort(key=lambda s: s["bi_score"], reverse=True)
            all_segs = all_segs[:_MAX_SEGMENTS]
            print(f"\n  Trimmed to {_MAX_SEGMENTS} (by bi_score) -- cap hit")

        # -- [9] Cross-encoder scoring -----------------------------------------
        hdr("[9] CROSS-ENCODER SCORING  (" + str(len(all_segs)) + " pairs)")
        t0 = time.perf_counter()
        pairs  = [(query, s["text"]) for s in all_segs]
        scores = reranker.model.predict(pairs, batch_size=32)
        ce_ms  = (time.perf_counter() - t0) * 1000

        for seg, score in zip(all_segs, scores):
            seg["cross_score"] = float(score)

        all_segs.sort(key=lambda s: s["cross_score"], reverse=True)

        row("pairs scored",      len(all_segs))
        row("cross-encoder time", f"{ce_ms:.1f} ms")
        row("top cross_score",   f"{all_segs[0]['cross_score']:.4f}  (logit, not probability)")
        row("bottom cross_score",f"{all_segs[-1]['cross_score']:.4f}")

        import math
        sigmoid = lambda x: 1.0 / (1.0 + math.exp(-max(-500.0, min(500.0, x))))
        row("top as probability", f"{sigmoid(all_segs[0]['cross_score']):.4f}")

        print(f"\n  {'#':>3}  {'cross':>8}  {'prob':>6}  {'bi':>6}  {'time':>14}  {'video':<35}  segment")
        print(f"  {'-'*3}  {'-'*8}  {'-'*6}  {'-'*6}  {'-'*14}  {'-'*35}  {'-'*30}")
        for i, s in enumerate(all_segs[:25]):
            prob  = sigmoid(s["cross_score"])
            bi    = s["bi_score"]
            span  = f"{s['start_time']:.1f}-{s['end_time']:.1f}s"
            title = (s.get("title") or "")[:35]
            text  = s["text"][:35].replace("\n", " ")
            print(f"  #{i+1:>2}  {s['cross_score']:>8.3f}  {prob:>6.4f}  {bi:>6.4f}  {span:>14}  {title:<35}  {text}...")

        if len(all_segs) > 25:
            print(f"  ... {len(all_segs)-25} more segments not shown")

        # -- [10] Deduplication ------------------------------------------------
        hdr("[10] DEDUPLICATION  (best segment per video)")
        seen: set[str] = set()
        final_segs: list[dict] = []
        for s in all_segs:
            vid = s["youtube_video_id"]
            if vid not in seen:
                seen.add(vid)
                final_segs.append(s)
            if len(final_segs) >= top_k:
                break

        row("unique videos", len(seen))
        row(f"top {top_k} selected", len(final_segs))
        print()

        for i, s in enumerate(final_segs):
            rank_in_full = next(j for j, fs in enumerate(all_segs) if fs is s)
            print(f"  #{i+1}  was rank #{rank_in_full+1} in cross-encoder  ->  {(s.get('title') or '')[:55]}")

        # -- [11] Final results ------------------------------------------------
        hdr("[11] FINAL RESULTS")
        print(f"  {'#':>2}  {'cross_prob':>10}  {'bi_score':>8}  {'time':>14}  title / segment")
        print(f"  {'-'*2}  {'-'*10}  {'-'*8}  {'-'*14}  {'-'*60}")
        for i, s in enumerate(final_segs):
            prob  = sigmoid(s["cross_score"])
            bi    = s["bi_score"]
            span  = f"{s['start_time']:.1f}-{s['end_time']:.1f}s"
            title = (s.get("title") or "")[:40]
            seg   = s["text"][:45].replace("\n", " ")
            print(f"  {i+1:>2}  {prob:>10.4f}  {bi:>8.4f}  {span:>14}  {title}")
            print(f"      segment: {seg}...")
            if s.get("parent_text"):
                ctx = s["parent_text"][:80].replace("\n", " ")
                print(f"      context: {ctx}...")
            print()

        # -- summary -----------------------------------------------------------
        hdr("SUMMARY")
        row("query",              query)
        row("industry detected",  industry_slug or "none")
        row("topics matched",     len(topic_ids))
        row("videos in pool",     len(video_ids))
        row("chunks retrieved",   len(raw_results))
        row("unique videos/chunks", unique_vids)
        row("segments to CE",     len(all_segs))
        row("embed latency",      f"{embed_ms:.1f} ms")
        row("chunk search latency",f"{chunk_ms:.1f} ms")
        row("cross-encoder latency",f"{ce_ms:.1f} ms")
        row("TOTAL latency",      f"{embed_ms + chunk_ms + ce_ms:.1f} ms  (excl. industry+topic)")
        row("final results",      len(final_segs))


# -- entry point ---------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Search pipeline diagnostic")
    parser.add_argument("query",        help="Search query")
    parser.add_argument("--top",        type=int, default=10, help="Top-K results (default 10)")
    parser.add_argument("--no-refine",  dest="force_refine", action="store_false", default=True,
                        help="Skip cross-encoder (bi-encoder only path)")
    parser.add_argument("--grpc",       action="store_true",
                        help="Use gRPC embedding server instead of local model")
    args = parser.parse_args()
    asyncio.run(run(args.query, args.top, args.force_refine, args.grpc))
