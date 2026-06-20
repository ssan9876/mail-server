"""Mailbox self-service portal: login + profile + password change."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import (
    DbDep,
    RedisDep,
    get_client_ip,
    get_current_mailbox,
    get_token_payload,
)
from app.core.exceptions import NotFoundError
from app.models.domain import Domain
from app.models.enums import ActorType
from app.models.mailbox import Mailbox
from app.schemas.auth import LoginRequest, LogoutRequest, RefreshRequest, TokenPair
from app.schemas.mailbox_portal import MailboxPasswordChange, MailboxProfile
from app.services import audit_service, mailbox_auth_service

router = APIRouter(prefix="/mailbox", tags=["mailbox-portal"])

CurrentMailbox = Annotated[Mailbox, Depends(get_current_mailbox)]


async def _profile(db: DbDep, mailbox: Mailbox) -> MailboxProfile:
    domain = await db.get(Domain, mailbox.domain_id)
    if domain is None:  # pragma: no cover - referential integrity guarantees this
        raise NotFoundError("Mailbox domain missing.")
    return MailboxProfile(
        id=mailbox.id,
        address=f"{mailbox.local_part}@{domain.name}",
        display_name=mailbox.display_name,
        quota_mb=mailbox.quota_mb,
        is_active=mailbox.is_active,
        created_at=mailbox.created_at,
    )


@router.post("/login", response_model=TokenPair)
async def login(payload: LoginRequest, request: Request, db: DbDep, redis: RedisDep) -> TokenPair:
    tokens = await mailbox_auth_service.login(db, redis, payload.email, payload.password)
    await audit_service.record(
        db,
        action="mailbox.login",
        actor_type=ActorType.MAILBOX,
        metadata={"email": payload.email},
        ip_address=get_client_ip(request),
    )
    return tokens


@router.post("/refresh", response_model=TokenPair)
async def refresh(payload: RefreshRequest, db: DbDep, redis: RedisDep) -> TokenPair:
    return await mailbox_auth_service.refresh(db, redis, payload.refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def logout(
    payload: LogoutRequest,
    redis: RedisDep,
    token_payload: Annotated[dict, Depends(get_token_payload)],
) -> Response:
    await mailbox_auth_service.logout(redis, token_payload, payload.refresh_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=MailboxProfile)
async def me(current_mailbox: CurrentMailbox, db: DbDep) -> MailboxProfile:
    return await _profile(db, current_mailbox)


@router.post("/password", response_model=MailboxProfile)
async def change_password(
    payload: MailboxPasswordChange,
    request: Request,
    current_mailbox: CurrentMailbox,
    db: DbDep,
) -> MailboxProfile:
    await mailbox_auth_service.change_password(
        db, current_mailbox, payload.current_password, payload.new_password
    )
    await audit_service.record(
        db,
        action="mailbox.password_changed_self",
        actor_id=current_mailbox.id,
        actor_type=ActorType.MAILBOX,
        target_type="mailbox",
        target_id=current_mailbox.id,
        ip_address=get_client_ip(request),
    )
    return await _profile(db, current_mailbox)
