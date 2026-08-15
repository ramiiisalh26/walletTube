from fastapi import APIRouter

from api.dependencies import DbDep, OptionalUser
from api.schemas.analytics import ClickEventRequest
from api.services import analytics_service

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


@router.post("/click")
async def record_click_event(
    req: ClickEventRequest,
    session: DbDep,
    user: OptionalUser,
) -> dict:
    """Record a result click for retention and hit-rate measurement."""
    user_id = user.id if user else None
    analytics_service.fire_and_forget(
        analytics_service.record_click_event(
            session,
            req.search_event_id,
            user_id,
            req.session_id,
            req.video_id,
            req.result_position,
            req.clicked_timestamp,
        )
    )
    return {"ok": True}
