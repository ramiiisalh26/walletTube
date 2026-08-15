"""
Unit tests for analytics instrumentation.
No database or network required — DB calls are mocked.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_session(fetchone_return=None):
    """Return a minimal AsyncSession mock."""
    result = MagicMock()
    result.fetchone.return_value = fetchone_return

    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()
    return session


# ── search_service: search_event_id in response ──────────────────────────────

def test_search_response_has_search_event_id_field():
    from api.schemas.search import SearchResponse, SearchResult
    resp = SearchResponse(
        query="test",
        total_results=0,
        source="indexed",
        latency_ms=10,
        indexing_more=False,
        results=[],
        search_event_id=42,
    )
    assert resp.search_event_id == 42


def test_search_response_search_event_id_defaults_to_none():
    from api.schemas.search import SearchResponse
    resp = SearchResponse(
        query="test",
        total_results=0,
        source="indexed",
        latency_ms=10,
        indexing_more=False,
        results=[],
    )
    assert resp.search_event_id is None


def test_search_request_carries_session_id_and_referrer():
    from api.schemas.search import SearchRequest
    req = SearchRequest(
        query="hello",
        session_id="sess-abc",
        referrer_clip_slug="xY3kPqRt",
    )
    assert req.session_id == "sess-abc"
    assert req.referrer_clip_slug == "xY3kPqRt"


# ── analytics_service.record_click_event ─────────────────────────────────────

@pytest.mark.asyncio
async def test_record_click_event_resolves_video_and_inserts():
    from api.services.analytics_service import record_click_event

    # First execute → video lookup; second → INSERT
    video_row = MagicMock()
    video_row.__getitem__ = lambda self, k: 99  # row[0] == 99
    video_row.__bool__ = lambda self: True

    fetchone_results = [video_row, None]
    call_count = 0

    async def fake_execute(stmt, params=None):
        nonlocal call_count
        result = MagicMock()
        result.fetchone.return_value = fetchone_results[call_count] if call_count < len(fetchone_results) else None
        call_count += 1
        return result

    session = AsyncMock()
    session.execute = fake_execute
    session.commit = AsyncMock()

    await record_click_event(
        session,
        search_event_id=7,
        user_id=None,
        session_id="s1",
        youtube_video_id="abcXYZ",
        result_position=2,
        clicked_timestamp=35.5,
    )

    session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_record_click_event_swallows_db_error():
    """Failures must NOT propagate — analytics is fire-and-forget."""
    from api.services.analytics_service import record_click_event

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=RuntimeError("db down"))
    session.commit = AsyncMock()

    # Should not raise
    await record_click_event(
        session,
        search_event_id=1,
        user_id=None,
        session_id=None,
        youtube_video_id="abc",
        result_position=1,
        clicked_timestamp=0.0,
    )


# ── metrics ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_weekly_retention_no_cohort_returns_zero():
    from api.modules.analytics.metrics import weekly_retention

    row = MagicMock()
    row.cohort = 0
    row.retained = 0
    session = _make_session(fetchone_return=row)

    result = await weekly_retention(session)
    assert result == 0.0


@pytest.mark.asyncio
async def test_weekly_retention_calculates_percentage():
    from api.modules.analytics.metrics import weekly_retention

    row = MagicMock()
    row.cohort = 10
    row.retained = 4
    session = _make_session(fetchone_return=row)

    result = await weekly_retention(session)
    assert result == pytest.approx(40.0)


@pytest.mark.asyncio
async def test_search_hit_rate_no_searches_returns_zero():
    from api.modules.analytics.metrics import search_hit_rate

    row = MagicMock()
    row.total_searches = 0
    row.searches_with_click = 0
    session = _make_session(fetchone_return=row)

    result = await search_hit_rate(session, days=7)
    assert result == 0.0


@pytest.mark.asyncio
async def test_search_hit_rate_calculates_correctly():
    from api.modules.analytics.metrics import search_hit_rate

    row = MagicMock()
    row.total_searches = 100
    row.searches_with_click = 35
    session = _make_session(fetchone_return=row)

    result = await search_hit_rate(session, days=7)
    assert result == pytest.approx(35.0)


@pytest.mark.asyncio
async def test_free_to_paid_conversion_no_hitters_returns_zero():
    from api.modules.analytics.metrics import free_to_paid_conversion

    row = MagicMock()
    row.total_hitters = 0
    row.upgraded = 0
    session = _make_session(fetchone_return=row)

    result = await free_to_paid_conversion(session)
    assert result == 0.0


@pytest.mark.asyncio
async def test_free_to_paid_conversion_calculates_percentage():
    from api.modules.analytics.metrics import free_to_paid_conversion

    row = MagicMock()
    row.total_hitters = 50
    row.upgraded = 5
    session = _make_session(fetchone_return=row)

    result = await free_to_paid_conversion(session)
    assert result == pytest.approx(10.0)
