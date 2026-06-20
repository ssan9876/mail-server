"""Mailbox management endpoints (admin-scoped by domain ownership)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import CurrentUser, DbDep, get_client_ip, require_admin
from app.models.enums import ActorType
from app.schemas.mailbox import MailboxCreate, MailboxRead, MailboxUpdate
from app.services import audit_service, mailbox_service

router = APIRouter(tags=["mailboxes"], dependencies=[Depends(require_admin)])


@router.get("/domains/{domain_id}/mailboxes", response_model=list[MailboxRead])
async def list_mailboxes(
    domain_id: uuid.UUID, current_user: CurrentUser, db: DbDep
) -> list[MailboxRead]:
    boxes = await mailbox_service.list_mailboxes(db, current_user, domain_id)
    return [MailboxRead.model_validate(b) for b in boxes]


@router.post(
    "/domains/{domain_id}/mailboxes",
    response_model=MailboxRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_mailbox(
    domain_id: uuid.UUID,
    payload: MailboxCreate,
    request: Request,
    current_user: CurrentUser,
    db: DbDep,
) -> MailboxRead:
    mailbox = await mailbox_service.create_mailbox(
        db, current_user, domain_id,
        local_part=payload.local_part,
        password=payload.password,
        display_name=payload.display_name,
        quota_mb=payload.quota_mb,
    )
    await audit_service.record(
        db,
        action="mailbox.created",
        actor_id=current_user.id,
        actor_type=ActorType.USER,
        target_type="mailbox",
        target_id=mailbox.id,
        metadata={"local_part": mailbox.local_part},
        ip_address=get_client_ip(request),
    )
    return MailboxRead.model_validate(mailbox)


@router.get("/mailboxes/{mailbox_id}", response_model=MailboxRead)
async def get_mailbox(mailbox_id: uuid.UUID, current_user: CurrentUser, db: DbDep) -> MailboxRead:
    mailbox = await mailbox_service.get_mailbox(db, current_user, mailbox_id)
    return MailboxRead.model_validate(mailbox)


@router.patch("/mailboxes/{mailbox_id}", response_model=MailboxRead)
async def update_mailbox(
    mailbox_id: uuid.UUID,
    payload: MailboxUpdate,
    request: Request,
    current_user: CurrentUser,
    db: DbDep,
) -> MailboxRead:
    mailbox = await mailbox_service.update_mailbox(
        db, current_user, mailbox_id,
        display_name=payload.display_name,
        quota_mb=payload.quota_mb,
        is_active=payload.is_active,
        password=payload.password,
    )
    if payload.password is not None:
        await audit_service.record(
            db,
            action="mailbox.password_changed",
            actor_id=current_user.id,
            actor_type=ActorType.USER,
            target_type="mailbox",
            target_id=mailbox.id,
            ip_address=get_client_ip(request),
        )
    return MailboxRead.model_validate(mailbox)


@router.delete(
    "/mailboxes/{mailbox_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_mailbox(
    mailbox_id: uuid.UUID, request: Request, current_user: CurrentUser, db: DbDep
) -> Response:
    await mailbox_service.delete_mailbox(db, current_user, mailbox_id)
    await audit_service.record(
        db,
        action="mailbox.deleted",
        actor_id=current_user.id,
        actor_type=ActorType.USER,
        target_type="mailbox",
        target_id=mailbox_id,
        ip_address=get_client_ip(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
