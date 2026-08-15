"""
Authentication service — register, login, refresh.
Mirrors the logic in Java's AuthService.java.
"""
import logging

from fastapi import HTTPException, status
from passlib.context import CryptContext
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api.schemas.auth import AuthResponse, LoginRequest, RegisterRequest
from api.services import jwt_service
from db.models import User

logger = logging.getLogger(__name__)

_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    return _pwd_ctx.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_ctx.verify(plain, hashed)


def _build_response(user: User) -> AuthResponse:
    return AuthResponse(
        access_token=jwt_service.create_access_token(user.email),
        refresh_token=jwt_service.create_refresh_token(user.email),
        email=user.email,
        name=user.name,
    )


async def register(session: AsyncSession, req: RegisterRequest) -> AuthResponse:
    existing = await session.execute(select(User).where(User.email == req.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")

    # Fetch free plan id
    result = await session.execute(
        text("SELECT id FROM subscription_plans WHERE slug = 'free' LIMIT 1")
    )
    free_plan_id: int | None = result.scalar_one_or_none()
    if not free_plan_id:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Subscription plans not seeded")

    user = User(
        email=req.email,
        name=req.name,
        password_hash=hash_password(req.password),
        provider="email",
        plan_id=free_plan_id,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return _build_response(user)


async def login(session: AsyncSession, req: LoginRequest) -> AuthResponse:
    result = await session.execute(select(User).where(User.email == req.email))
    user = result.scalar_one_or_none()

    if not user or not user.password_hash or not verify_password(req.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account deactivated")

    return _build_response(user)


async def refresh(session: AsyncSession, refresh_token: str) -> AuthResponse:
    from jose import JWTError
    try:
        email = jwt_service.decode_token(refresh_token)
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    result = await session.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    return _build_response(user)
