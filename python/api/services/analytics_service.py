"""
Analytics recording — all operations are fire-and-forget background tasks.
Failures are swallowed so they never affect search latency.
"""
import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)


async def record_popular_search(query: str, industry_id: int | None) -> None:
    # Opens its OWN session: this runs as a fire-and-forget background task that
    # outlives the request, so it must NOT reuse the request-scoped session —
    # that races the request's session.close() and raises IllegalStateChangeError.
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("""
                    INSERT INTO popular_searches (query, industry_id, search_count, last_searched_at)
                    VALUES (:query, :industry_id, 1, NOW())
                    ON CONFLICT (query) DO UPDATE
                    SET search_count     = popular_searches.search_count + 1,
                        last_searched_at = NOW()
                """),
                {"query": query, "industry_id": industry_id},
            )
            await session.commit()
    except Exception as exc:
        logger.warning("record_popular_search failed for '%s': %s", query, exc)


async def record_click(
    search_id: int,
    video_id: int,
    chunk_id: int | None,
    position: int,
) -> None:
    # Own session — fire-and-forget task outlives the request (see record_popular_search).
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("""
                    INSERT INTO search_clicks (search_id, video_id, chunk_id, position)
                    VALUES (:search_id, :video_id, :chunk_id, :position)
                """),
                {"search_id": search_id, "video_id": video_id, "chunk_id": chunk_id, "position": position},
            )
            await session.commit()
    except Exception as exc:
        logger.warning("record_click failed: %s", exc)


async def record_click_event(
    session: AsyncSession,
    search_event_id: int,
    user_id: int | None,
    session_id: str | None,
    youtube_video_id: str,
    result_position: int,
    clicked_timestamp: float,
) -> None:
    """Write a search_click_events row (new analytics schema, keyed by youtube_video_id)."""
    try:
        # Resolve youtube_video_id → internal video PK
        row = (await session.execute(
            text("SELECT id FROM videos WHERE youtube_video_id = :yt_id"),
            {"yt_id": youtube_video_id},
        )).fetchone()
        video_db_id = row[0] if row else None

        await session.execute(
            text("""
                INSERT INTO search_clicks
                    (search_id, video_id, position, user_id, session_id, clicked_timestamp)
                VALUES
                    (:search_id, :video_id, :position, :user_id, :session_id, :clicked_ts)
            """),
            {
                "search_id": search_event_id,
                "video_id": video_db_id,
                "position": result_position,
                "user_id": user_id,
                "session_id": session_id,
                "clicked_ts": clicked_timestamp,
            },
        )
        await session.commit()
    except Exception as exc:
        logger.warning("record_click_event failed: %s", exc)


async def record_interaction(
    user_id: int | None,
    video_id: int,
    interaction_type: str,
) -> None:
    # Own session — fire-and-forget task outlives the request.
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("""
                    INSERT INTO video_interactions (user_id, video_id, interaction_type)
                    VALUES (:user_id, :video_id, :type)
                """),
                {"user_id": user_id, "video_id": video_id, "type": interaction_type},
            )
            await session.commit()
    except Exception as exc:
        logger.warning("record_interaction failed: %s", exc)


def fire_and_forget(coro) -> None:
    """Schedule a coroutine in the background without awaiting it."""
    asyncio.create_task(coro)
