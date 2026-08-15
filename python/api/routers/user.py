from fastapi import APIRouter, Query

from api.dependencies import CurrentUser, DbDep
from api.schemas.user import PaginatedHistory, UserProfile
from api.services import user_service

router = APIRouter(prefix="/api/user", tags=["user"])


@router.get("/me", response_model=UserProfile)
async def get_me(user: CurrentUser, session: DbDep) -> UserProfile:
    return await user_service.get_profile(session, user)


@router.get("/history", response_model=PaginatedHistory)
async def get_history(
    user: CurrentUser,
    session: DbDep,
    page: int = Query(default=0, ge=0),
    size: int = Query(default=20, ge=1, le=100),
) -> PaginatedHistory:
    return await user_service.get_history(session, user.id, page, size)


@router.post("/saves/{youtube_video_id}", status_code=204)
async def save_video(youtube_video_id: str, user: CurrentUser, session: DbDep) -> None:
    await user_service.save_video(session, user.id, youtube_video_id)


@router.delete("/saves/{youtube_video_id}", status_code=204)
async def unsave_video(youtube_video_id: str, user: CurrentUser, session: DbDep) -> None:
    await user_service.unsave_video(session, user.id, youtube_video_id)
