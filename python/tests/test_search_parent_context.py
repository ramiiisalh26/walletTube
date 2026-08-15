"""
Unit tests for search schema parent context (small-to-big retrieval).
No database or network required — schema / serialisation tests only.
"""
import pytest

from api.schemas.search import ParentContext, SearchResult, SearchResponse


# ── ParentContext ─────────────────────────────────────────────────────────────

def test_parent_context_fields():
    ctx = ParentContext(text="hello world", start_time=10.0, end_time=130.0)
    assert ctx.text == "hello world"
    assert ctx.start_time == 10.0
    assert ctx.end_time == 130.0


def test_parent_context_serialises():
    ctx = ParentContext(text="abc", start_time=0.0, end_time=120.0)
    d = ctx.model_dump()
    assert d == {"text": "abc", "start_time": 0.0, "end_time": 120.0}


# ── SearchResult — backward compatibility ─────────────────────────────────────

def _make_result(**overrides) -> SearchResult:
    base = dict(
        video_id="abc123",
        title="Test Video",
        thumbnail_url=None,
        channel_name="Test Channel",
        text="some transcript text",
        start_time=30.0,
        end_time=60.0,
        view_count=1000,
        similarity=0.82,
        youtube_url="https://www.youtube.com/watch?v=abc123&t=30",
        embed_url="https://www.youtube.com/embed/abc123?start=30&autoplay=1",
    )
    base.update(overrides)
    return SearchResult(**base)


def test_search_result_context_defaults_to_none():
    r = _make_result()
    assert r.context is None


def test_search_result_accepts_parent_context():
    ctx = ParentContext(text="wider context", start_time=0.0, end_time=120.0)
    r = _make_result(context=ctx)
    assert r.context is not None
    assert r.context.text == "wider context"


def test_search_result_serialises_with_context():
    ctx = ParentContext(text="ctx text", start_time=5.0, end_time=125.0)
    r = _make_result(context=ctx)
    d = r.model_dump()
    assert d["context"] == {"text": "ctx text", "start_time": 5.0, "end_time": 125.0}


def test_search_result_serialises_without_context():
    r = _make_result()
    d = r.model_dump()
    assert d["context"] is None


def test_search_result_existing_fields_unchanged():
    r = _make_result()
    assert r.video_id == "abc123"
    assert r.similarity == 0.82
    assert r.youtube_url.endswith("&t=30")


# ── SearchResponse ────────────────────────────────────────────────────────────

def test_search_response_with_context_results():
    ctx = ParentContext(text="parent", start_time=0.0, end_time=120.0)
    result = _make_result(context=ctx)
    resp = SearchResponse(
        query="test query",
        total_results=1,
        source="cosine",
        latency_ms=42,
        indexing_more=False,
        detected_industry=None,
        results=[result],
    )
    assert resp.results[0].context is not None
    assert resp.results[0].context.text == "parent"


def test_search_response_mixed_context():
    """Results with and without context coexist in the same response (legacy + new rows)."""
    r_with = _make_result(
        context=ParentContext(text="ctx", start_time=0.0, end_time=120.0)
    )
    r_without = _make_result(video_id="xyz999")
    resp = SearchResponse(
        query="q",
        total_results=2,
        source="cosine",
        latency_ms=10,
        indexing_more=False,
        results=[r_with, r_without],
    )
    assert resp.results[0].context is not None
    assert resp.results[1].context is None
