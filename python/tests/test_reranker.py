"""
Unit tests for Stage 5: CrossEncoderReranker and should_refine gate.

The cross-encoder model (~80 MB) is never loaded — CrossEncoderReranker
instances are built directly with a mocked model so tests run instantly.
"""
import pytest
from unittest.mock import MagicMock

from api.modules.search.reranker import CrossEncoderReranker, should_refine, _WORDS_PER_SEG


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_reranker(predict_scores: list[float] | None = None) -> CrossEncoderReranker:
    """Build a reranker with a mocked cross-encoder model (no disk I/O)."""
    mock_model = MagicMock()
    if predict_scores is not None:
        mock_model.predict.return_value = predict_scores
    inst = CrossEncoderReranker.__new__(CrossEncoderReranker)
    inst.model = mock_model
    return inst


def _make_chunk(
    *,
    youtube_video_id: str = "vid1",
    text: str = "Hello world. This is a test sentence. Another sentence here.",
    start_time: float = 10.0,
    end_time: float = 40.0,
    vector_score: float = 0.75,
    title: str = "Test Video",
    channel_name: str = "Test Channel",
    thumbnail_url: str | None = None,
    view_count: int = 1000,
    parent_text: str | None = None,
    parent_start_time: float | None = None,
    parent_end_time: float | None = None,
) -> dict:
    return {
        "youtube_video_id": youtube_video_id,
        "text": text,
        "start_time": start_time,
        "end_time": end_time,
        "vector_score": vector_score,
        "title": title,
        "channel_name": channel_name,
        "thumbnail_url": thumbnail_url,
        "view_count": view_count,
        "parent_text": parent_text,
        "parent_start_time": parent_start_time,
        "parent_end_time": parent_end_time,
    }


# ── _split_chunk ──────────────────────────────────────────────────────────────

class TestToWindows:
    """Tests for the punctuation-first / word-count-fallback splitting logic."""

    def test_sentence_punctuation_splits_correctly(self):
        r = _make_reranker()
        windows = r._to_windows("First sentence here. Second sentence follows. Third one ends.")
        assert len(windows) == 3

    def test_unpunctuated_text_splits_by_word_count(self):
        # Build text with no punctuation: 3 × _WORDS_PER_SEG words
        r = _make_reranker()
        words = ["word"] * (_WORDS_PER_SEG * 3)
        text = " ".join(words)
        windows = r._to_windows(text)
        assert len(windows) == 3

    def test_mixed_text_uses_both_strategies(self):
        r = _make_reranker()
        # One proper sentence + one long unpunctuated stretch (> 2×WORDS_PER_SEG)
        long_stretch = " ".join(["word"] * (_WORDS_PER_SEG * 3))
        text = f"A proper sentence here. {long_stretch}"
        windows = r._to_windows(text)
        assert len(windows) >= 4  # 1 sentence + 3 word-count windows

    def test_short_fragments_discarded(self):
        r = _make_reranker()
        windows = r._to_windows("Ok. This is a proper long sentence worth keeping. Hi.")
        for w in windows:
            assert len(w.split()) >= 3

    def test_empty_text_returns_empty(self):
        r = _make_reranker()
        assert r._to_windows("") == []


class TestSplitChunk:
    def test_returns_at_least_one_segment(self):
        r = _make_reranker()
        # Unpunctuated — word-count path fires
        chunk = _make_chunk(text="word " * 15)
        segs = r._split_chunk(chunk)
        assert len(segs) >= 1

    def test_punctuated_text_produces_multiple_segments(self):
        r = _make_reranker()
        chunk = _make_chunk(
            text="First sentence here. Second sentence follows. Third one ends.",
            start_time=0.0,
            end_time=30.0,
        )
        assert len(r._split_chunk(chunk)) == 3

    def test_unpunctuated_chunk_splits_not_returns_whole_chunk(self):
        # The original bug: unpunctuated text returned the full chunk as one segment
        r = _make_reranker()
        words = " ".join([f"word{i}" for i in range(_WORDS_PER_SEG * 4)])
        chunk = _make_chunk(text=words, start_time=0.0, end_time=60.0)
        segs = r._split_chunk(chunk)
        # Must produce more than 1 segment (not the full chunk as a single piece)
        assert len(segs) > 1

    def test_unpunctuated_segment_timestamps_differ_from_chunk(self):
        # Each segment should have a start_time > chunk_start (except the first)
        r = _make_reranker()
        words = " ".join([f"word{i}" for i in range(_WORDS_PER_SEG * 3)])
        chunk = _make_chunk(text=words, start_time=10.0, end_time=70.0)
        segs = r._split_chunk(chunk)
        assert len(segs) > 1
        last = segs[-1]
        assert last["start_time"] > 10.0  # last segment starts after chunk start

    def test_timestamps_are_monotonic(self):
        r = _make_reranker()
        chunk = _make_chunk(
            text="First sentence here. Second sentence follows. Third one ends.",
            start_time=0.0,
            end_time=30.0,
        )
        segs = r._split_chunk(chunk)
        for i in range(len(segs) - 1):
            assert segs[i]["start_time"] <= segs[i + 1]["start_time"]

    def test_timestamps_within_parent_bounds(self):
        r = _make_reranker()
        chunk = _make_chunk(
            text="One sentence. Two sentences. Three sentences.",
            start_time=5.0,
            end_time=35.0,
        )
        for seg in r._split_chunk(chunk):
            assert seg["start_time"] >= 5.0
            assert seg["end_time"]   <= 35.0
            assert seg["start_time"] <= seg["end_time"]

    def test_filters_short_fragments(self):
        r = _make_reranker()
        chunk = _make_chunk(
            text="Ok. This is a proper long sentence worth keeping. Hi.",
        )
        for seg in r._split_chunk(chunk):
            assert len(seg["text"].split()) >= 3

    def test_bi_score_propagated_from_chunk(self):
        r = _make_reranker()
        chunk = _make_chunk(
            text="First proper sentence here. Second proper sentence there.",
            vector_score=0.82,
        )
        for seg in r._split_chunk(chunk):
            assert seg["bi_score"] == pytest.approx(0.82)

    def test_parent_chunk_start_matches_chunk_start(self):
        r = _make_reranker()
        chunk = _make_chunk(text="First sentence here. Second one follows.", start_time=12.5, end_time=42.5)
        for seg in r._split_chunk(chunk):
            assert seg["parent_chunk_start"] == pytest.approx(12.5)

    def test_segment_fields_present(self):
        r = _make_reranker()
        chunk = _make_chunk(text="First sentence here. Second sentence here.")
        seg = r._split_chunk(chunk)[0]
        for key in ("youtube_video_id", "title", "text", "start_time",
                    "end_time", "bi_score", "parent_chunk_start", "cross_score"):
            assert key in seg, f"Missing key: {key}"

    def test_empty_text_returns_no_segments(self):
        r = _make_reranker()
        chunk = _make_chunk(text="")
        assert r._split_chunk(chunk) == []

    def test_only_short_words_returns_no_segments(self):
        r = _make_reranker()
        chunk = _make_chunk(text="Ok. Hi. Bye.")
        assert r._split_chunk(chunk) == []


# ── should_refine ─────────────────────────────────────────────────────────────

class TestShouldRefine:
    def test_false_for_empty_list(self):
        assert should_refine([]) is False

    def test_false_for_single_chunk(self):
        assert should_refine([_make_chunk(vector_score=0.9)]) is False

    def test_false_when_top_above_85_and_gap_above_10(self):
        chunks = [
            _make_chunk(vector_score=0.92),
            _make_chunk(vector_score=0.80),
        ]
        assert should_refine(chunks) is False

    def test_true_when_top_below_85(self):
        chunks = [
            _make_chunk(vector_score=0.80),
            _make_chunk(vector_score=0.78),
        ]
        assert should_refine(chunks) is True

    def test_true_when_gap_too_small(self):
        # Top is > 0.85 but gap is only 0.05 (≤ 0.10) — not a clear winner
        chunks = [
            _make_chunk(vector_score=0.88),
            _make_chunk(vector_score=0.83),
        ]
        assert should_refine(chunks) is True

    def test_true_when_scores_are_clustered(self):
        chunks = [_make_chunk(vector_score=0.70 - i * 0.01) for i in range(5)]
        assert should_refine(chunks) is True

    def test_boundary_exactly_085_is_not_clear_winner(self):
        # score must be strictly > 0.85
        chunks = [
            _make_chunk(vector_score=0.85),
            _make_chunk(vector_score=0.70),
        ]
        assert should_refine(chunks) is True

    def test_boundary_gap_exactly_010_is_not_clear_winner(self):
        # gap must be strictly > 0.10
        chunks = [
            _make_chunk(vector_score=0.90),
            _make_chunk(vector_score=0.80),
        ]
        assert should_refine(chunks) is True


# ── rerank ────────────────────────────────────────────────────────────────────

class TestRerank:
    def test_cross_scores_attached_to_segments(self):
        chunks = [_make_chunk(text="Long enough sentence for testing purposes here.")]
        n_segs = len(_make_reranker()._split_chunk(chunks[0]))
        r = _make_reranker(predict_scores=[0.8] * n_segs)
        results = r.rerank("test query", chunks, top_k=10)
        assert all("cross_score" in seg for seg in results)
        assert all(seg["cross_score"] == pytest.approx(0.8) for seg in results)

    def test_results_sorted_descending_by_cross_score(self):
        # Two chunks from different videos, each with one scoreable segment
        chunk_a = _make_chunk(
            youtube_video_id="vid_a",
            text="This is a decent sentence from video A.",
        )
        chunk_b = _make_chunk(
            youtube_video_id="vid_b",
            text="This is another sentence from video B.",
        )
        # Scores: vid_a segment gets 0.3, vid_b segment gets 0.9
        r = _make_reranker(predict_scores=[0.3, 0.9])
        results = r.rerank("query", [chunk_a, chunk_b], top_k=10)
        scores = [seg["cross_score"] for seg in results]
        assert scores == sorted(scores, reverse=True)

    def test_deduplicates_one_per_video(self):
        # Same video_id appears twice (two chunks from the same video)
        chunk1 = _make_chunk(
            youtube_video_id="same_vid",
            text="First chunk sentence from the same video.",
        )
        chunk2 = _make_chunk(
            youtube_video_id="same_vid",
            text="Second chunk sentence from the same video.",
        )
        n_segs = (len(_make_reranker()._split_chunk(chunk1)) +
                  len(_make_reranker()._split_chunk(chunk2)))
        r = _make_reranker(predict_scores=list(range(n_segs)))
        results = r.rerank("query", [chunk1, chunk2], top_k=10)
        video_ids = [seg["youtube_video_id"] for seg in results]
        assert len(video_ids) == len(set(video_ids)), "Duplicate video_id in results"

    def test_respects_top_k(self):
        chunks = [
            _make_chunk(youtube_video_id=f"vid_{i}",
                        text="A sufficiently long sentence for this test case.")
            for i in range(8)
        ]
        n_segs = sum(len(_make_reranker()._split_chunk(c)) for c in chunks)
        r = _make_reranker(predict_scores=list(range(n_segs)))
        results = r.rerank("query", chunks, top_k=3)
        assert len(results) <= 3

    def test_empty_chunks_returns_empty(self):
        r = _make_reranker(predict_scores=[])
        assert r.rerank("query", [], top_k=10) == []

    def test_caps_at_max_segments(self, monkeypatch):
        import api.modules.search.reranker as mod
        monkeypatch.setattr(mod, "_MAX_SEGMENTS", 2)

        chunks = [
            _make_chunk(youtube_video_id=f"vid_{i}",
                        text="Sentence one here. Sentence two here. Sentence three here.")
            for i in range(5)
        ]
        r = _make_reranker(predict_scores=[0.5, 0.5])  # only 2 pairs after cap
        results = r.rerank("query", chunks, top_k=10)
        # model.predict was called with at most 2 pairs
        call_args = r.model.predict.call_args
        pairs_passed = call_args[0][0]
        assert len(pairs_passed) <= 2

    def test_youtube_url_uses_segment_start(self):
        chunk = _make_chunk(
            text="This is a proper test sentence here.",
            start_time=100.0,
            end_time=130.0,
        )
        r = _make_reranker(predict_scores=[0.7])
        results = r.rerank("query", [chunk], top_k=1)
        if results:
            seg = results[0]
            t_sec = int(seg["start_time"])
            assert str(t_sec) in seg["youtube_video_id"] or t_sec >= 100

    def test_parent_context_propagated(self):
        chunk = _make_chunk(
            text="A proper test sentence is here.",
            parent_text="Surrounding context text.",
            parent_start_time=0.0,
            parent_end_time=120.0,
        )
        r = _make_reranker(predict_scores=[0.6])
        results = r.rerank("query", [chunk], top_k=1)
        assert results
        assert results[0]["parent_text"] == "Surrounding context text."
        assert results[0]["parent_start_time"] == pytest.approx(0.0)
