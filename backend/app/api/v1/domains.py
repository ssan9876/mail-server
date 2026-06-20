"""Domain management + DNS automation endpoints (admin-scoped)."""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import (
    CurrentUser,
    DbDep,
    get_client_ip,
    get_cloudflare_client,
    get_dns_resolver,
    require_admin,
)
from app.models.enums import ActorType
from app.schemas.domain import (
    DnsRecord,
    DomainCreate,
    DomainRead,
    DomainUpdate,
    VerificationResult,
)
from app.core.exceptions import ExternalServiceError
from app.services import audit_service, dkim_export_service, domain_service
from app.services.dns_service import CloudflareClient, CloudflareError
from app.services.dns_verify_service import DnsResolver

# Every route requires an admin (superadmin or domain_admin).
router = APIRouter(
    prefix="/domains",
    tags=["domains"],
    dependencies=[Depends(require_admin)],
)


@router.get("", response_model=list[DomainRead])
async def list_domains(current_user: CurrentUser, db: DbDep) -> list[DomainRead]:
    domains = await domain_service.list_domains(db, current_user)
    return [DomainRead.model_validate(d) for d in domains]


@router.post("", response_model=DomainRead, status_code=status.HTTP_201_CREATED)
async def create_domain(
    payload: DomainCreate, request: Request, current_user: CurrentUser, db: DbDep
) -> DomainRead:
    domain = await domain_service.create_domain(
        db, current_user, name=payload.name, owner_id=payload.owner_id
    )
    # Export the new DKIM key to the shared volume for Rspamd (best-effort).
    await dkim_export_service.try_sync(db)
    await audit_service.record(
        db,
        action="domain.created",
        actor_id=current_user.id,
        actor_type=ActorType.USER,
        target_type="domain",
        target_id=domain.id,
        metadata={"name": domain.name},
        ip_address=get_client_ip(request),
    )
    return DomainRead.model_validate(domain)


@router.post("/dkim/sync")
async def sync_dkim_keys(current_user: CurrentUser, db: DbDep) -> dict[str, int]:
    """Re-export all DKIM keys + selector map to the shared volume for Rspamd."""
    exported = await dkim_export_service.sync_all(db)
    return {"exported": exported}


@router.get("/{domain_id}", response_model=DomainRead)
async def get_domain(domain_id: uuid.UUID, current_user: CurrentUser, db: DbDep) -> DomainRead:
    domain = await domain_service.get_domain(db, current_user, domain_id)
    return DomainRead.model_validate(domain)


@router.patch("/{domain_id}", response_model=DomainRead)
async def update_domain(
    domain_id: uuid.UUID, payload: DomainUpdate, current_user: CurrentUser, db: DbDep
) -> DomainRead:
    domain = await domain_service.update_domain(
        db, current_user, domain_id,
        is_active=payload.is_active, catch_all_box=payload.catch_all_box,
    )
    return DomainRead.model_validate(domain)


@router.delete("/{domain_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def delete_domain(
    domain_id: uuid.UUID, request: Request, current_user: CurrentUser, db: DbDep
) -> Response:
    await domain_service.delete_domain(db, current_user, domain_id)
    await audit_service.record(
        db,
        action="domain.deleted",
        actor_id=current_user.id,
        actor_type=ActorType.USER,
        target_type="domain",
        target_id=domain_id,
        ip_address=get_client_ip(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{domain_id}/dns-records", response_model=list[DnsRecord])
async def get_dns_records(
    domain_id: uuid.UUID, current_user: CurrentUser, db: DbDep
) -> list[DnsRecord]:
    """The records the operator should publish (or that we can publish to Cloudflare)."""
    domain = await domain_service.get_domain(db, current_user, domain_id)
    return domain_service.desired_records(domain)


@router.post("/{domain_id}/dkim/rotate", response_model=DomainRead)
async def rotate_dkim(
    domain_id: uuid.UUID, request: Request, current_user: CurrentUser, db: DbDep
) -> DomainRead:
    domain = await domain_service.rotate_dkim(db, current_user, domain_id)
    await dkim_export_service.try_sync(db)
    await audit_service.record(
        db,
        action="domain.dkim_rotated",
        actor_id=current_user.id,
        actor_type=ActorType.USER,
        target_type="domain",
        target_id=domain.id,
        ip_address=get_client_ip(request),
    )
    return DomainRead.model_validate(domain)


@router.post("/{domain_id}/dns/publish", response_model=list[DnsRecord])
async def publish_dns(
    domain_id: uuid.UUID,
    request: Request,
    current_user: CurrentUser,
    db: DbDep,
    cloudflare: Annotated[CloudflareClient, Depends(get_cloudflare_client)],
) -> list[DnsRecord]:
    """Push the desired records to Cloudflare for the domain's zone."""
    domain = await domain_service.get_domain(db, current_user, domain_id)
    records = domain_service.desired_records(domain)
    try:
        await cloudflare.publish_records(domain.name, records)
    except CloudflareError as exc:
        # Surface a clean 502 with Cloudflare's message instead of a raw 500.
        raise ExternalServiceError(f"Cloudflare publish failed: {exc}") from exc
    await audit_service.record(
        db,
        action="domain.dns_published",
        actor_id=current_user.id,
        actor_type=ActorType.USER,
        target_type="domain",
        target_id=domain.id,
        ip_address=get_client_ip(request),
    )
    return records


@router.post("/{domain_id}/dns/verify", response_model=VerificationResult)
async def verify_dns(
    domain_id: uuid.UUID,
    current_user: CurrentUser,
    db: DbDep,
    resolver: Annotated[DnsResolver, Depends(get_dns_resolver)],
) -> VerificationResult:
    """Resolve the domain's records and update its verification flags."""
    return await domain_service.verify_and_update(db, current_user, domain_id, resolver)
