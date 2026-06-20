"""
Domain management: CRUD with ownership scoping, DKIM key lifecycle, desired
DNS record assembly, and verification-flag updates.

Access model:
  - superadmin   -> all domains; may assign owner on create.
  - domain_admin -> only domains they own.
  - user         -> no access (enforced at the router via require_admin).
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.exceptions import ConflictError, NotFoundError
from app.models.domain import Domain
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.domain import DnsRecord, VerificationResult
from app.services import dkim_service, dns_service, dns_verify_service
from app.services.dns_verify_service import DnsResolver


def _can_access(user: User, domain: Domain) -> bool:
    if user.role == UserRole.SUPERADMIN:
        return True
    if user.role == UserRole.DOMAIN_ADMIN:
        return domain.owner_id == user.id
    return False


async def list_domains(db: AsyncSession, user: User) -> list[Domain]:
    stmt = select(Domain).order_by(Domain.name)
    if user.role != UserRole.SUPERADMIN:
        stmt = stmt.where(Domain.owner_id == user.id)
    return list((await db.execute(stmt)).scalars().all())


async def get_domain(db: AsyncSession, user: User, domain_id: uuid.UUID) -> Domain:
    domain = await db.get(Domain, domain_id)
    if domain is None:
        raise NotFoundError("Domain not found.")
    if not _can_access(user, domain):
        # Hide existence from unauthorized callers.
        raise NotFoundError("Domain not found.")
    return domain


async def _get_by_name(db: AsyncSession, name: str) -> Domain | None:
    return (await db.execute(select(Domain).where(Domain.name == name))).scalar_one_or_none()


async def create_domain(
    db: AsyncSession,
    user: User,
    *,
    name: str,
    owner_id: uuid.UUID | None = None,
) -> Domain:
    if await _get_by_name(db, name) is not None:
        raise ConflictError(f"Domain {name} already exists.")

    # Only a superadmin may assign an arbitrary owner; everyone else owns it.
    if user.role == UserRole.SUPERADMIN:
        resolved_owner = owner_id or user.id
    else:
        resolved_owner = user.id

    keypair = dkim_service.generate_keypair()
    domain = Domain(
        name=name,
        owner_id=resolved_owner,
        dkim_selector=keypair.selector,
        dkim_private_key=crypto.encrypt(keypair.private_key_pem),
        dkim_public_key=keypair.public_key_b64,
    )
    db.add(domain)
    await db.commit()
    await db.refresh(domain)
    return domain


async def update_domain(
    db: AsyncSession,
    user: User,
    domain_id: uuid.UUID,
    *,
    is_active: bool | None = None,
    catch_all_box: uuid.UUID | None = None,
) -> Domain:
    domain = await get_domain(db, user, domain_id)
    if is_active is not None:
        domain.is_active = is_active
    if catch_all_box is not None:
        domain.catch_all_box = catch_all_box
    await db.commit()
    await db.refresh(domain)
    return domain


async def delete_domain(db: AsyncSession, user: User, domain_id: uuid.UUID) -> None:
    domain = await get_domain(db, user, domain_id)
    await db.delete(domain)
    await db.commit()


async def rotate_dkim(db: AsyncSession, user: User, domain_id: uuid.UUID) -> Domain:
    """Generate a fresh DKIM keypair (operator must re-publish DNS afterward)."""
    domain = await get_domain(db, user, domain_id)
    keypair = dkim_service.generate_keypair(domain.dkim_selector or dkim_service.DEFAULT_SELECTOR)
    domain.dkim_private_key = crypto.encrypt(keypair.private_key_pem)
    domain.dkim_public_key = keypair.public_key_b64
    # DNS no longer matches the new key until re-published + re-verified.
    domain.dkim_selector = keypair.selector
    domain.dns_verified = False
    await db.commit()
    await db.refresh(domain)
    return domain


def desired_records(domain: Domain) -> list[DnsRecord]:
    return dns_service.build_desired_records(
        domain.name,
        domain.dkim_selector or dkim_service.DEFAULT_SELECTOR,
        domain.dkim_public_key or "",
    )


async def verify_and_update(
    db: AsyncSession, user: User, domain_id: uuid.UUID, resolver: DnsResolver
) -> VerificationResult:
    domain = await get_domain(db, user, domain_id)
    result = await dns_verify_service.verify_domain(resolver, domain.name, domain.dkim_selector)

    domain.mx_verified = result.mx_verified
    domain.spf_verified = result.spf_verified
    domain.dmarc_verified = result.dmarc_verified
    domain.dns_verified = result.dns_verified
    await db.commit()
    return result
