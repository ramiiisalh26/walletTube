import hashlib
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt

from config import settings


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_access_token(email: str) -> str:
    expire = _now() + timedelta(minutes=settings.jwt_expiry_minutes)
    return jwt.encode(
        {"sub": email, "exp": expire, "iat": _now()},
        settings.jwt_secret,
        algorithm="HS256",
    )


def create_refresh_token(email: str) -> str:
    expire = _now() + timedelta(days=settings.jwt_refresh_expiry_days)
    return jwt.encode(
        {"sub": email, "exp": expire, "iat": _now(), "type": "refresh"},
        settings.jwt_secret,
        algorithm="HS256",
    )


def decode_token(token: str) -> str:
    """Returns email (subject) from a valid token; raises JWTError on failure."""
    payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    sub: str | None = payload.get("sub")
    if not sub:
        raise JWTError("Missing subject in token")
    return sub


def hash_token(token: str) -> str:
    """SHA-256 digest used to store/look up refresh tokens without exposing the raw value."""
    return hashlib.sha256(token.encode()).hexdigest()
