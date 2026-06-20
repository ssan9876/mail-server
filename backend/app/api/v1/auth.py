"""Authentication endpoints: login, refresh, logout, current user."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import (
    CurrentUser,
    DbDep,
    RedisDep,
    get_client_ip,
    get_token_payload,
)
from app.models.enums import ActorType
from app.schemas.auth import LoginRequest, LogoutRequest, RefreshRequest, TokenPair
from app.schemas.user import UserRead
from app.services import audit_service, auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenPair)
async def login(payload: LoginRequest, request: Request, db: DbDep, redis: RedisDep) -> TokenPair:
    """Exchange email + password for an access/refresh token pair."""
    tokens = await auth_service.login(db, redis, payload.email, payload.password)
    # Re-read the authenticated user id from the issued access token's subject
    # is unnecessary; record the login by email for the audit trail.
    await audit_service.record(
        db,
        action="auth.login",
        actor_type=ActorType.USER,
        metadata={"email": payload.email},
        ip_address=get_client_ip(request),
    )
    return tokens


@router.post("/refresh", response_model=TokenPair)
async def refresh(payload: RefreshRequest, db: DbDep, redis: RedisDep) -> TokenPair:
    """Rotate a valid refresh token into a fresh token pair."""
    return await auth_service.refresh(db, redis, payload.refresh_token)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def logout(
    payload: LogoutRequest,
    redis: RedisDep,
    token_payload: dict = Depends(get_token_payload),
) -> Response:
    """Revoke the current access token (and optionally a refresh token)."""
    await auth_service.logout(redis, token_payload, payload.refresh_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserRead)
async def me(current_user: CurrentUser) -> UserRead:
    """Return the authenticated user's profile."""
    return current_user
