"""IdM service-token management (superadmin only)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import CurrentUser, DbDep, get_client_ip, require_superadmin
from app.models.enums import ActorType
from app.schemas.idm_token import IdmTokenCreate, IdmTokenCreated, IdmTokenRead
from app.services import audit_service, idm_token_service

router = APIRouter(
    prefix="/idm/tokens", tags=["idm"], dependencies=[Depends(require_superadmin)]
)


@router.post("", response_model=IdmTokenCreated, status_code=status.HTTP_201_CREATED)
async def create_token(
    payload: IdmTokenCreate, request: Request, current_user: CurrentUser, db: DbDep
) -> IdmTokenCreated:
    token, raw = await idm_token_service.create_token(
        db, name=payload.name, created_by=current_user.id, expires_at=payload.expires_at
    )
    await audit_service.record(
        db,
        action="idm.token.created",
        actor_id=current_user.id,
        actor_type=ActorType.USER,
        target_type="idm_service_token",
        target_id=token.id,
        metadata={"name": token.name, "prefix": token.prefix},
        ip_address=get_client_ip(request),
    )
    return IdmTokenCreated(**IdmTokenRead.model_validate(token).model_dump(), token=raw)


@router.get("", response_model=list[IdmTokenRead])
async def list_tokens(db: DbDep) -> list[IdmTokenRead]:
    tokens = await idm_token_service.list_tokens(db)
    return [IdmTokenRead.model_validate(t) for t in tokens]


@router.delete(
    "/{token_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response
)
async def revoke_token(
    token_id: uuid.UUID, request: Request, current_user: CurrentUser, db: DbDep
) -> Response:
    token = await idm_token_service.revoke_token(db, token_id)
    await audit_service.record(
        db,
        action="idm.token.revoked",
        actor_id=current_user.id,
        actor_type=ActorType.USER,
        target_type="idm_service_token",
        target_id=token.id,
        ip_address=get_client_ip(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
