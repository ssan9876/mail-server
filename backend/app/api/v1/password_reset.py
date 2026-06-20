"""Public (unauthenticated) mailbox password-reset endpoints."""
from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import DbDep
from app.core.config import settings
from app.schemas.password_reset import (
    PasswordResetConfirm,
    PasswordResetRequest,
    PasswordResetResponse,
)
from app.services import password_reset_service

router = APIRouter(prefix="/password-reset", tags=["password-reset"])

# A constant, non-committal message so the response never reveals whether the
# address exists.
_GENERIC_MESSAGE = "If that mailbox exists, a reset link has been sent."


@router.post("/request", response_model=PasswordResetResponse)
async def request_reset(payload: PasswordResetRequest, db: DbDep) -> PasswordResetResponse:
    raw_token = await password_reset_service.request_reset(db, payload.email)
    # Outside production, surface the token so the flow is testable end-to-end.
    debug_token = raw_token if (raw_token and not settings.is_production) else None
    return PasswordResetResponse(message=_GENERIC_MESSAGE, debug_token=debug_token)


@router.post("/confirm", status_code=status.HTTP_200_OK, response_model=PasswordResetResponse)
async def confirm_reset(payload: PasswordResetConfirm, db: DbDep) -> PasswordResetResponse:
    await password_reset_service.confirm_reset(db, payload.token, payload.new_password)
    return PasswordResetResponse(message="Password updated.")
