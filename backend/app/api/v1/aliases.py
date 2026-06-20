"""Alias management endpoints (admin-scoped by domain ownership)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import CurrentUser, DbDep, get_client_ip, require_admin
from app.models.enums import ActorType
from app.schemas.alias import AliasCreate, AliasRead, AliasUpdate
from app.services import alias_service, audit_service

router = APIRouter(tags=["aliases"], dependencies=[Depends(require_admin)])


@router.get("/domains/{domain_id}/aliases", response_model=list[AliasRead])
async def list_aliases(
    domain_id: uuid.UUID, current_user: CurrentUser, db: DbDep
) -> list[AliasRead]:
    aliases = await alias_service.list_aliases(db, current_user, domain_id)
    return [AliasRead.from_model(a) for a in aliases]


@router.post(
    "/domains/{domain_id}/aliases",
    response_model=AliasRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_alias(
    domain_id: uuid.UUID,
    payload: AliasCreate,
    request: Request,
    current_user: CurrentUser,
    db: DbDep,
) -> AliasRead:
    alias = await alias_service.create_alias(
        db, current_user, domain_id,
        local_part=payload.local_part,
        destinations=[str(d) for d in payload.destinations],
    )
    await audit_service.record(
        db,
        action="alias.created",
        actor_id=current_user.id,
        actor_type=ActorType.USER,
        target_type="alias",
        target_id=alias.id,
        metadata={"local_part": alias.local_part, "catch_all": alias.local_part == "@"},
        ip_address=get_client_ip(request),
    )
    return AliasRead.from_model(alias)


@router.get("/aliases/{alias_id}", response_model=AliasRead)
async def get_alias(alias_id: uuid.UUID, current_user: CurrentUser, db: DbDep) -> AliasRead:
    alias = await alias_service.get_alias(db, current_user, alias_id)
    return AliasRead.from_model(alias)


@router.patch("/aliases/{alias_id}", response_model=AliasRead)
async def update_alias(
    alias_id: uuid.UUID, payload: AliasUpdate, current_user: CurrentUser, db: DbDep
) -> AliasRead:
    alias = await alias_service.update_alias(
        db, current_user, alias_id,
        destinations=[str(d) for d in payload.destinations] if payload.destinations else None,
        is_active=payload.is_active,
    )
    return AliasRead.from_model(alias)


@router.delete(
    "/aliases/{alias_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_alias(
    alias_id: uuid.UUID, request: Request, current_user: CurrentUser, db: DbDep
) -> Response:
    await alias_service.delete_alias(db, current_user, alias_id)
    await audit_service.record(
        db,
        action="alias.deleted",
        actor_id=current_user.id,
        actor_type=ActorType.USER,
        target_type="alias",
        target_id=alias_id,
        ip_address=get_client_ip(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
