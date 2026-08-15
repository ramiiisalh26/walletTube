"""
User profile and saved-video management.
Mirrors the logic in Java's UserService.java and UserController.java.
"""
import logging

from fastapi import HTTPException, status
from sqlalchemy import select, text, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas.user import PaginatedHistory, PlanInfo, SearchHistoryItem, UserProfile
from db.models import SearchHistory, SavedVideo, SubscriptionPlan, User, Video

logger = logging.getLogger(__name__)


async def get_profile(session: AsyncSession, user: User) -> UserProfile:
    result = await session.execute(
        select(SubscriptionPlan).where(SubscriptionPlan.id == user.plan_id)
    )
    plan = result.scalar_one()
    return UserProfile(
        id=user.id,
        email=user.email,
        name=user.name,
        avatar_url=user.avatar_url,
        plan=PlanInfo(
            id=plan.id,
            name=plan.name,
            slug=plan.slug,
            searches_per_day=plan.searches_per_day,
            has_api_access=plan.has_api_access,
        ),
        created_at=user.created_at,
    )


async def get_history(
    session: AsyncSession, user_id: int, page: int, size: int
) -> PaginatedHistory:
    offset = page * size

    count_result = await session.execute(
        select(func.count()).select_from(SearchHistory).where(SearchHistory.user_id == user_id)
    )
    total = count_result.scalar_one()

    result = await session.execute(
        select(SearchHistory)
        .where(SearchHistory.user_id == user_id)
        .order_by(SearchHistory.created_at.desc())
        .offset(offset)
        .limit(size)
    )
    rows = result.scalars().all()

    return PaginatedHistory(
        items=[
            SearchHistoryItem(
                id=r.id,
                query=r.query,
                result_count=r.result_count,
                source=r.source,
                industry_id=r.industry_id,
                latency_ms=r.latency_ms,
                created_at=r.created_at,
            )
            for r in rows
        ],
        total=total,
        page=page,
        size=size,
    )


async def _resolve_video_id(session: AsyncSession, youtube_video_id: str) -> int:
    result = await session.execute(
        select(Video.id).where(Video.youtube_video_id == youtube_video_id)
    )
    video_id = result.scalar_one_or_none()
    if video_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Video not found: {youtube_video_id}")
    return video_id


async def save_video(session: AsyncSession, user_id: int, youtube_video_id: str) -> None:
    video_id = await _resolve_video_id(session, youtube_video_id)
    await session.execute(
        text("""
            INSERT INTO saved_videos (user_id, video_id)
            VALUES (:user_id, :video_id)
            ON CONFLICT DO NOTHING
        """),
        {"user_id": user_id, "video_id": video_id},
    )
    await session.commit()


async def unsave_video(session: AsyncSession, user_id: int, youtube_video_id: str) -> None:
    video_id = await _resolve_video_id(session, youtube_video_id)
    await session.execute(
        delete(SavedVideo).where(
            SavedVideo.user_id == user_id,
            SavedVideo.video_id == video_id,
        )
    )
    await session.commit()


async def update_industry_interest(
    session: AsyncSession, user_id: int, industry_id: int, signal: float
) -> None:
    try:
        await session.execute(
            text("SELECT update_user_interest(:user_id, :industry_id, :signal)"),
            {"user_id": user_id, "industry_id": industry_id, "signal": signal},
        )
        await session.commit()
    except Exception as exc:
        logger.warning("update_industry_interest failed: %s", exc)
