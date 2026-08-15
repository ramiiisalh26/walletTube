"""
Stage 5: Cross-encoder re-ranking with bi-encoder blend.

Scores each bi-encoder candidate chunk with ms-marco-MiniLM-L-6-v2 using the
FULL chunk text (not tiny out-of-context fragments), then ranks by a blend of
the bi-encoder cosine score and the cross-encoder probability:

    final = bi_weight * bi_score + (1 - bi_weight) * cross_prob

Scoring whole chunks gives the cross-encoder enough context to tell, e.g., a
FastAPI tutorial from an Angular one; the bi-blend keeps a strong semantic match
from being flipped by a single lucky fragment. Deduplicated to one chunk per
video. No database writes, no new columns — purely in-memory.
"""
import logging
import math
from typing import Any

logger = logging.getLogger(__name__)

# Model: MiniLM-L-6 is a small, fast cross-encoder. electra-base (the old default)
# is a full transformer — measured 20s for 300 segments on CPU, unusable.
_MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"

# Hard cap on chunks scored per query. Cross-encoder is CPU-bound; cost is roughly
# linear in (chunk count × chunk length). Scoring the full chunk is more tokens per
# pair than the old 12-word fragments, but there is only ONE pair per chunk instead
# of ~8, so the total stays modest. 50 covers far more than the top-10 we display.
# Lower if searches feel slow; raise if you have GPU/headroom.
_MAX_CHUNKS_SCORED = 50


def _sigmoid(x: float) -> float:
    """Convert a cross-encoder raw logit to a [0, 1] probability."""
    return 1.0 / (1.0 + math.exp(-max(-500.0, min(500.0, x))))


# ── Gate ─────────────────────────────────────────────────────────────────────

def should_refine(chunks: list[Any]) -> bool:
    """
    Bi-encoder is a retriever only — it fetches and sorts candidates by
    vector proximity.  Cross-encoder always makes the final ranking decision.
    Only skip CE when there are fewer than 2 chunks (nothing to compare).
    """
    return len(chunks) >= 2


# ── Reranker ──────────────────────────────────────────────────────────────────

class CrossEncoderReranker:
    """
    Singleton cross-encoder wrapper.  Load once at startup; reuse every query.
    """
    _instance: "CrossEncoderReranker | None" = None

    def __init__(self) -> None:
        from sentence_transformers import CrossEncoder
        self.model = CrossEncoder(_MODEL_NAME)
        logger.info("Cross-encoder loaded (%s)", _MODEL_NAME)
        # Warmup — eliminates cold-start latency spike on first real request
        self.model.predict([("warmup query", "warmup passage")])

    @classmethod
    def get(cls) -> "CrossEncoderReranker":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── Private helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _to_candidate(
        chunk: Any, cross_logit: float, cross_prob: float, bi: float, final: float
    ) -> dict:
        """
        Build one reranked candidate from a full bi-encoder chunk.

        Ranking and the in-video jump are chunk-level, so timestamps are the chunk
        bounds (the card already displays the full chunk text). The key set here
        matches exactly what the result builder in search_service reads.
        """
        chunk_start = float(chunk["start_time"])
        chunk_end   = float(chunk["end_time"])
        text        = chunk["text"]
        return {
            "youtube_video_id":   chunk["youtube_video_id"],
            "title":              chunk["title"],
            "thumbnail_url":      chunk.get("thumbnail_url"),
            "channel_name":       chunk.get("channel_name"),
            "view_count":         int(chunk.get("view_count") or 0),
            "text":               text,          # full chunk — what we scored
            "chunk_text":         text,          # full chunk — what the card shows
            "start_time":         chunk_start,   # hot-zone indicator = whole chunk
            "end_time":           chunk_end,
            "bi_score":           bi,
            "cross_score":        cross_logit,   # raw logit (search_service sigmoids for display)
            "cross_prob":         cross_prob,
            "final_score":        final,         # blended — drives ranking + similarity
            "parent_chunk_start": chunk_start,
            "parent_chunk_end":   chunk_end,
            "parent_text":        chunk.get("parent_text"),
            "parent_start_time":  chunk.get("parent_start_time"),
            "parent_end_time":    chunk.get("parent_end_time"),
        }

    # ── Public API ────────────────────────────────────────────────────────────

    def rerank(
        self,
        query: str,
        chunks: list[Any],
        top_k: int = 10,
        bi_weight: float = 0.4,
    ) -> list[dict]:
        """
        Synchronous CPU-bound method — call via asyncio.run_in_executor().

        1. Cap to the top _MAX_CHUNKS_SCORED chunks by bi-encoder score.
        2. Score every (query, FULL chunk text) pair with the cross-encoder.
        3. Blend: final = bi_weight*bi_score + (1-bi_weight)*sigmoid(cross_score).
        4. Sort by final_score descending.
        5. Deduplicate: keep the highest-scoring chunk per video.
        6. Return top_k results.
        """
        if not chunks:
            return []

        # Keep the strongest bi-encoder candidates — bounds cross-encoder cost.
        candidates = list(chunks)
        if len(candidates) > _MAX_CHUNKS_SCORED:
            candidates.sort(key=lambda c: float(c["vector_score"]), reverse=True)
            candidates = candidates[:_MAX_CHUNKS_SCORED]

        pairs  = [(query, c["text"]) for c in candidates]
        scores = self.model.predict(pairs, batch_size=32)

        segments: list[dict] = []
        for chunk, raw in zip(candidates, scores):
            cross_logit = float(raw)
            cross_prob  = _sigmoid(cross_logit)
            bi          = float(chunk["vector_score"])
            final       = bi_weight * bi + (1.0 - bi_weight) * cross_prob
            segments.append(self._to_candidate(chunk, cross_logit, cross_prob, bi, final))

        segments.sort(key=lambda s: s["final_score"], reverse=True)

        # One best chunk per video
        seen:    set[str]   = set()
        results: list[dict] = []
        for seg in segments:
            vid = seg["youtube_video_id"]
            if vid not in seen:
                seen.add(vid)
                results.append(seg)
            if len(results) >= top_k:
                break

        return results
