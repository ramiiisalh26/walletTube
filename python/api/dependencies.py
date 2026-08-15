"""
FastAPI dependency injection — DB session, Redis, JWT-authenticated user.
"""
import logging
from typing import Annotated

import redis.asyncio as aioredis
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.services import jwt_service
from config import settings
from db.models import User
from db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)

# ── Database ──────────────────────────────────────────────────────────────────

async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session

DbDep = Annotated[AsyncSession, Depends(get_db)]

# ── Redis ─────────────────────────────────────────────────────────────────────

_redis_pool: aioredis.Redis | None = None


def get_redis_pool() -> aioredis.Redis:
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
    return _redis_pool


async def get_redis() -> aioredis.Redis:
    return get_redis_pool()

RedisDep = Annotated[aioredis.Redis, Depends(get_redis)]

# ── Auth ──────────────────────────────────────────────────────────────────────

async def _user_from_token(
    creds: HTTPAuthorizationCredentials | None,
    session: AsyncSession,
) -> User | None:
    if not creds:
        return None
    try:
        email = jwt_service.decode_token(creds.credentials)
    except JWTError:
        return None

    result = await session.execute(select(User).where(User.email == email, User.is_active == True))
    return result.scalar_one_or_none()


async def get_current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: DbDep,
) -> User:
    user = await _user_from_token(creds, session)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def get_optional_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: DbDep,
) -> User | None:
    return await _user_from_token(creds, session)


CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalUser = Annotated[User | None, Depends(get_optional_user)]
