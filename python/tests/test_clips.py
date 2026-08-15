"""
Unit tests for shared-clips service and schemas.
No database or network required — DB calls are mocked.
"""
import re
import pytest
from unittest.mock import AsyncMock, MagicMock


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_request(base_url="http://testserver/"):
    req = MagicMock()
    req.base_url = MagicMock()
    req.base_url.__str__ = lambda self: base_url
    return req


def _video_row(video_db_id=5):
    row = MagicMock()
    row.__getitem__ = lambda self, k: video_db_id
    return row


def _make_session_for_create(video_db_id=5, raise_on_insert=None):
    """
    Session that:
      - returns a video row on the first execute (SELECT id FROM videos)
      - either succeeds or raises on the second execute (INSERT)
    """
    calls = []

    async def fake_execute(stmt, params=None):
        result = MagicMock()
        if not calls:
            # first call: video lookup
            result.fetchone.return_value = _video_row(video_db_id)
        elif raise_on_insert and len(calls) < len(raise_on_insert) + 1:
            calls.append(1)
            raise raise_on_insert[len(calls) - 2]
        else:
            result.fetchone.return_value = None
        calls.append(1)
        return result

    session = AsyncMock()
    session.execute = fake_execute
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


# ── Schema tests ──────────────────────────────────────────────────────────────

def test_create_clip_request_schema():
    from api.schemas.clips import CreateClipRequest
    req = CreateClipRequest(
        youtube_video_id="abc123",
        start_time=10.0,
        end_time=40.0,
        transcript_text="hello world",
    )
    assert req.youtube_video_id == "abc123"
    assert req.session_id is None


def test_create_clip_response_schema():
    from api.schemas.clips import CreateClipResponse
    resp = CreateClipResponse(slug="aBcDeFgH", share_url="http://x/clips/aBcDeFgH")
    assert resp.view_count == 0


def test_clip_data_schema():
    from api.schemas.clips import ClipData
    d = ClipData(
        slug="s1",
        youtube_video_id="abc",
        title="T",
        channel_name="C",
        thumbnail_url=None,
        embed_url="https://youtube.com/embed/abc?start=10",
        transcript_text="text",
        start_time=10.0,
        end_time=40.0,
        view_count=3,
    )
    assert d.view_count == 3


# ── clips_service: create_clip ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_clip_returns_slug_and_share_url():
    from api.services.clips_service import create_clip

    session = _make_session_for_create(video_db_id=5)
    request = _make_request("http://testserver/")

    slug, share_url = await create_clip(
        session, "vidABC", 10.0, 40.0, "transcript text", user_id=None, request=request
    )

    assert slug
    assert share_url == f"http://testserver/clips/{slug}"
    session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_slug_is_url_safe_and_8_chars():
    from api.services.clips_service import create_clip

    session = _make_session_for_create()
    request = _make_request()

    slug, _ = await create_clip(
        session, "vid1", 0.0, 60.0, "some text", user_id=None, request=request
    )

    assert len(slug) == 8
    assert re.match(r'^[A-Za-z0-9_\-]+$', slug), f"slug not URL-safe: {slug!r}"


@pytest.mark.asyncio
async def test_create_clip_retries_on_unique_violation():
    """On a unique-constraint error the service should retry and succeed."""
    from api.services.clips_service import create_clip

    # Simulate: first INSERT raises unique violation, second succeeds
    call_count = 0

    async def fake_execute(stmt, params=None):
        nonlocal call_count
        result = MagicMock()
        call_count += 1
        if call_count == 1:
            # video SELECT
            result.fetchone.return_value = _video_row(7)
        elif call_count == 2:
            # first INSERT → unique violation
            raise Exception("unique constraint violation")
        else:
            result.fetchone.return_value = None
        return result

    session = AsyncMock()
    session.execute = fake_execute
    session.commit = AsyncMock()
    session.rollback = AsyncMock()

    request = _make_request()
    slug, _ = await create_clip(
        session, "vid1", 0.0, 30.0, "text", user_id=None, request=request
    )
    assert slug
    # rollback was called once (on the collision), then commit on success
    session.rollback.assert_called_once()
    session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_create_clip_raises_404_for_unknown_video():
    from fastapi import HTTPException
    from api.services.clips_service import create_clip

    session = AsyncMock()
    result = MagicMock()
    result.fetchone.return_value = None  # video not found
    session.execute = AsyncMock(return_value=result)

    with pytest.raises(HTTPException) as exc_info:
        await create_clip(
            session, "unknown", 0.0, 60.0, "text", user_id=None,
            request=_make_request()
        )
    assert exc_info.value.status_code == 404


# ── clips_service: get_clip / increment_view_count ────────────────────────────

@pytest.mark.asyncio
async def test_get_clip_returns_dict_with_embed_url():
    from api.services.clips_service import get_clip

    row = MagicMock()
    row.slug            = "testSlug"
    row.youtube_video_id = "vidXYZ"
    row.title           = "Great Video"
    row.thumbnail_url   = "https://img.yt/t.jpg"
    row.channel_name    = "Channel A"
    row.start_time      = 30.0
    row.end_time        = 60.0
    row.transcript_text = "some transcript text"
    row.view_count      = 5

    result = MagicMock()
    result.fetchone.return_value = row
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)

    data = await get_clip(session, "testSlug")

    assert data["slug"] == "testSlug"
    assert data["embed_url"].startswith("https://www.youtube.com/embed/vidXYZ")
    assert "start=30" in data["embed_url"]
    assert data["view_count"] == 5


@pytest.mark.asyncio
async def test_get_clip_raises_404_when_not_found():
    from fastapi import HTTPException
    from api.services.clips_service import get_clip

    result = MagicMock()
    result.fetchone.return_value = None
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)

    with pytest.raises(HTTPException) as exc_info:
        await get_clip(session, "nonexistent")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_increment_view_count_executes_update():
    from api.services.clips_service import increment_view_count

    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock())
    session.commit = AsyncMock()

    await increment_view_count(session, "abc123")

    session.execute.assert_called_once()
    session.commit.assert_called_once()
    # Verify the SQL touches view_count
    sql = str(session.execute.call_args[0][0])
    assert "view_count" in sql.lower()


@pytest.mark.asyncio
async def test_increment_view_count_swallows_errors():
    """Must not raise — analytics is fire-and-forget."""
    from api.services.clips_service import increment_view_count

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=RuntimeError("db down"))

    await increment_view_count(session, "abc")


# ── clip-attributed search records referrer ───────────────────────────────────

def test_search_request_accepts_referrer_clip_slug():
    from api.schemas.search import SearchRequest
    req = SearchRequest(query="async programming", referrer_clip_slug="xY3kPqRt")
    assert req.referrer_clip_slug == "xY3kPqRt"


def test_search_request_referrer_defaults_to_none():
    from api.schemas.search import SearchRequest
    req = SearchRequest(query="hello")
    assert req.referrer_clip_slug is None
