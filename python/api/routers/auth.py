from fastapi import APIRouter, Header

from api.dependencies import DbDep
from api.schemas.auth import AuthResponse, LoginRequest, RegisterRequest
from api.services import auth_service

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=AuthResponse, status_code=201)
async def register(req: RegisterRequest, session: DbDep) -> AuthResponse:
    return await auth_service.register(session, req)


@router.post("/login", response_model=AuthResponse)
async def login(req: LoginRequest, session: DbDep) -> AuthResponse:
    return await auth_service.login(session, req)


@router.post("/refresh", response_model=AuthResponse)
async def refresh(
    session: DbDep,
    x_refresh_token: str = Header(..., alias="X-Refresh-Token"),
) -> AuthResponse:
    return await auth_service.refresh(session, x_refresh_token)
