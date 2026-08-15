"""
Admin ingestion endpoints — replaces Java's IngestionController.java.
Protected by ADMIN role check (plan.slug == 'team' OR explicit admin flag).

Two ways to authenticate:
  1. Authorization: Bearer <jwt>  (normal login flow)
  2. X-Admin-Key: <secret>        (set ADMIN_API_KEY in .env — no login needed)
"""
import asyncio
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status

logger = logging.getLogger(__name__)

from api.dependencies import OptionalUser, DbDep, RedisDep
from api.services import ingestion_service
from config import settings
from db.models import User


def require_admin(
    request: Request,
    user: OptionalUser,
    x_admin_key: str | None = Header(default=None),
) -> User | None:
    # Localhost bypass — no auth needed when called from the same machine
    client = request.client.host if request.client else ""
    if client in ("127.0.0.1", "::1"):
        return user

    # API-key path — no JWT needed (useful for curl / scripts from remote)
    if settings.admin_api_key and x_admin_key == settings.admin_api_key:
        return user

    # JWT path — must be authenticated
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if user.plan.slug not in ("team",) and user.email not in _admin_emails():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


def _admin_emails() -> set[str]:
    raw = getattr(settings, "admin_emails", "")
    return {e.strip() for e in raw.split(",") if e.strip()}


AdminUser = Depends(require_admin)

router = APIRouter(prefix="/api/ingestion", tags=["ingestion"])


@router.post("/discover")
async def discover(
    session: DbDep,
    redis: RedisDep,
    query: str = Query(..., min_length=1),
    _: User = AdminUser,
) -> dict:
    """Search YouTube and enqueue discovered videos for indexing."""
    return await ingestion_service.discover(session, redis, query)


@router.post("/channel")
async def crawl_channel(
    session: DbDep,
    channel_id: str = Query(
        ...,
        min_length=2,
        description="YouTube channel ID (UCxxxxxx) or handle (@channelname)",
    ),
    _: User = AdminUser,
) -> dict:
    """
    Crawl a specific YouTube channel immediately and queue all its videos for indexing.
    Accepts either a channel ID (UCxxxxxx) or a @handle.
    Returns how many videos were fetched and how many are newly queued.
    """
    result = await ingestion_service.crawl_channel(session, channel_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.post("/channel-background")
async def crawl_channel_background(
    channel_id: str = Query(
        ..., min_length=2,
        description="YouTube channel ID (UCxxxxxx) or handle (@channelname)",
    ),
    _: None = AdminUser,
) -> dict:
    """
    Queue a channel for background indexing — returns immediately.
    The extension calls this when it detects a channel not yet in the DB.
    Uses asyncio.create_task (more reliable than BackgroundTasks for async closures).
    """
    async def _bg(cid: str) -> None:
        try:
            from db.session import AsyncSessionLocal
            from api.services.ingestion_service import crawl_channel as _crawl
            logger.info("channel-background: starting crawl for %s", cid)
            async with AsyncSessionLocal() as session:
                result = await _crawl(session, cid)
            logger.info("channel-background: done %s → %s", cid, result)
        except Exception as exc:
            logger.error("channel-background: failed for %s: %s", cid, exc, exc_info=True)

    asyncio.create_task(_bg(channel_id))
    return {"status": "queued", "channel_id": channel_id}


@router.get("/status")
async def pipeline_status(
    session: DbDep,
    redis: RedisDep,
    _: User = AdminUser,
) -> dict:
    """Snapshot of the indexing pipeline job counts and queue size."""
    return await ingestion_service.pipeline_status(session, redis)
