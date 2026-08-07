"""IdM provisioning endpoints (service-token authenticated).

These routes are blocked at the Nginx edge and are reachable only on the
internal Docker network — see docker/nginx/templates/10-https.conf.template.

There is deliberately no DELETE: the counterpart system has no delete
operation, `deactivated` is terminal there, and offboarding arrives as an
ordinary upsert carrying that status. Adding a delete verb here would invent a
lifecycle the source of truth does not have.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from app.api.deps import DbDep, get_client_ip, require_service_token
from app.models.alias import Alias
from app.models.domain import Domain
from app.models.idm import IdmIdentity, IdmIdentityAlias, IdmServiceToken
from app.models.mailbox import Mailbox
from app.schemas.provisioning import IdentityRead, IdentityUpsert
from app.services import provisioning_service

router = APIRouter(prefix="/provisioning", tags=["provisioning"])

ServiceToken = Annotated[IdmServiceToken, Depends(require_service_token)]


@router.get("/health")
async def health(token: ServiceToken) -> dict[str, str]:
    """Validates the caller's token and touches nothing else."""
    return {"status": "ok"}


async def _to_read(db, identity: IdmIdentity) -> IdentityRead:
    email = None
    if identity.mailbox_id is not None:
        mailbox = await db.get(Mailbox, identity.mailbox_id)
        if mailbox is not None:
            domain = await db.get(Domain, mailbox.domain_id)
            email = f"{mailbox.local_part}@{domain.name}" if domain else None

    rows = (
        await db.execute(
            select(Alias, Domain.name)
            .join(IdmIdentityAlias, IdmIdentityAlias.alias_id == Alias.id)
            .join(Domain, Alias.domain_id == Domain.id)
            .where(IdmIdentityAlias.identity_id == identity.id)
        )
    ).all()
    aliases = sorted(f"{alias.local_part}@{name}" for alias, name in rows)

    return IdentityRead(
        external_id=identity.external_id,
        email=email,
        status=identity.status,
        mailbox_id=identity.mailbox_id,
        user_id=identity.user_id,
        aliases=aliases,
        last_synced_at=identity.last_synced_at,
        deactivated_at=identity.deactivated_at,
    )


@router.put("/identities/{external_id}", response_model=IdentityRead)
async def upsert_identity(
    external_id: str,
    payload: IdentityUpsert,
    request: Request,
    token: ServiceToken,
    db: DbDep,
) -> IdentityRead:
    identity = await provisioning_service.upsert_identity(
        db,
        external_id,
        payload,
        token_id=token.id,
        ip_address=get_client_ip(request),
    )
    return await _to_read(db, identity)


@router.get("/identities/{external_id}", response_model=IdentityRead)
async def get_identity(
    external_id: str, token: ServiceToken, db: DbDep
) -> IdentityRead:
    identity = await provisioning_service.get_identity(db, external_id)
    return await _to_read(db, identity)
