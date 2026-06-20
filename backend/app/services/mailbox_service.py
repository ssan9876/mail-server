"""
Mailbox management: CRUD scoped by domain ownership, Dovecot-compatible
password hashing, Maildir path assignment, and routing-collision checks.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.dovecot_password import hash_for_dovecot
from app.core.exceptions import ConflictError, NotFoundError
from app.models.alias import Alias
from app.models.domain import Domain
from app.models.mailbox import Mailbox
from app.models.user import User
from app.services import domain_service


def _maildir_path(domain_name: str, local_part: str) -> str:
    # Maildir++ layout: <root>/<domain>/<local_part>/
    return f"{settings.MAILDIR_ROOT.rstrip('/')}/{domain_name}/{local_part}/"


async def _local_part_taken(db: AsyncSession, domain_id: uuid.UUID, local_part: str) -> bool:
    mb = await db.execute(
        select(Mailbox.id).where(
            Mailbox.domain_id == domain_id, Mailbox.local_part == local_part
        )
    )
    if mb.first():
        return True
    al = await db.execute(
        select(Alias.id).where(Alias.domain_id == domain_id, Alias.local_part == local_part)
    )
    return al.first() is not None


async def list_mailboxes(db: AsyncSession, user: User, domain_id: uuid.UUID) -> list[Mailbox]:
    await domain_service.get_domain(db, user, domain_id)  # enforces access
    result = await db.execute(
        select(Mailbox).where(Mailbox.domain_id == domain_id).order_by(Mailbox.local_part)
    )
    return list(result.scalars().all())


async def get_mailbox(db: AsyncSession, user: User, mailbox_id: uuid.UUID) -> Mailbox:
    mailbox = await db.get(Mailbox, mailbox_id)
    if mailbox is None:
        raise NotFoundError("Mailbox not found.")
    # Reuse domain scoping (raises 404 if the caller can't access the domain).
    await domain_service.get_domain(db, user, mailbox.domain_id)
    return mailbox


async def create_mailbox(
    db: AsyncSession,
    user: User,
    domain_id: uuid.UUID,
    *,
    local_part: str,
    password: str,
    display_name: str | None = None,
    quota_mb: int | None = None,
) -> Mailbox:
    domain: Domain = await domain_service.get_domain(db, user, domain_id)

    if await _local_part_taken(db, domain_id, local_part):
        raise ConflictError(f"{local_part}@{domain.name} already exists (mailbox or alias).")

    mailbox = Mailbox(
        domain_id=domain_id,
        local_part=local_part,
        password_hash=hash_for_dovecot(password),
        display_name=display_name,
        quota_mb=quota_mb if quota_mb is not None else settings.DEFAULT_MAILBOX_QUOTA_MB,
        maildir_path=_maildir_path(domain.name, local_part),
    )
    db.add(mailbox)
    await db.commit()
    await db.refresh(mailbox)
    return mailbox


async def update_mailbox(
    db: AsyncSession,
    user: User,
    mailbox_id: uuid.UUID,
    *,
    display_name: str | None = None,
    quota_mb: int | None = None,
    is_active: bool | None = None,
    password: str | None = None,
) -> Mailbox:
    mailbox = await get_mailbox(db, user, mailbox_id)
    if display_name is not None:
        mailbox.display_name = display_name
    if quota_mb is not None:
        mailbox.quota_mb = quota_mb
    if is_active is not None:
        mailbox.is_active = is_active
    if password is not None:
        mailbox.password_hash = hash_for_dovecot(password)
    await db.commit()
    await db.refresh(mailbox)
    return mailbox


async def delete_mailbox(db: AsyncSession, user: User, mailbox_id: uuid.UUID) -> None:
    mailbox = await get_mailbox(db, user, mailbox_id)
    await db.delete(mailbox)
    await db.commit()


async def get_by_id(db: AsyncSession, mailbox_id: uuid.UUID) -> Mailbox | None:
    """Unscoped lookup by id (used by the authenticated mailbox itself)."""
    return await db.get(Mailbox, mailbox_id)


async def get_by_address(db: AsyncSession, email: str) -> Mailbox | None:
    """Look up a mailbox by full address (used by the password-reset flow)."""
    email = email.strip().lower()
    if "@" not in email:
        return None
    local_part, domain_name = email.rsplit("@", 1)
    result = await db.execute(
        select(Mailbox)
        .join(Domain, Mailbox.domain_id == Domain.id)
        .where(Domain.name == domain_name, Mailbox.local_part == local_part)
    )
    return result.scalar_one_or_none()
