"""
Alias management: CRUD scoped by domain ownership, with catch-all support
(local_part == "@") and routing-collision checks against mailboxes.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.models.alias import Alias
from app.models.mailbox import Mailbox
from app.models.user import User
from app.services import domain_service

CATCH_ALL = "@"


async def list_aliases(db: AsyncSession, user: User, domain_id: uuid.UUID) -> list[Alias]:
    await domain_service.get_domain(db, user, domain_id)
    result = await db.execute(
        select(Alias).where(Alias.domain_id == domain_id).order_by(Alias.local_part)
    )
    return list(result.scalars().all())


async def get_alias(db: AsyncSession, user: User, alias_id: uuid.UUID) -> Alias:
    alias = await db.get(Alias, alias_id)
    if alias is None:
        raise NotFoundError("Alias not found.")
    await domain_service.get_domain(db, user, alias.domain_id)
    return alias


async def create_alias(
    db: AsyncSession,
    user: User,
    domain_id: uuid.UUID,
    *,
    local_part: str,
    destinations: list[str],
) -> Alias:
    domain = await domain_service.get_domain(db, user, domain_id)

    # An alias local_part must be unique within the domain...
    existing = await db.execute(
        select(Alias.id).where(Alias.domain_id == domain_id, Alias.local_part == local_part)
    )
    if existing.first():
        raise ConflictError(f"Alias {local_part}@{domain.name} already exists.")

    # ...and (unless it's the catch-all) must not collide with a real mailbox.
    if local_part != CATCH_ALL:
        clash = await db.execute(
            select(Mailbox.id).where(
                Mailbox.domain_id == domain_id, Mailbox.local_part == local_part
            )
        )
        if clash.first():
            raise ConflictError(f"A mailbox {local_part}@{domain.name} already exists.")

    alias = Alias(
        domain_id=domain_id,
        local_part=local_part,
        destination=",".join(destinations),
    )
    db.add(alias)
    await db.commit()
    await db.refresh(alias)
    return alias


async def update_alias(
    db: AsyncSession,
    user: User,
    alias_id: uuid.UUID,
    *,
    destinations: list[str] | None = None,
    is_active: bool | None = None,
) -> Alias:
    alias = await get_alias(db, user, alias_id)
    if destinations is not None:
        alias.destination = ",".join(destinations)
    if is_active is not None:
        alias.is_active = is_active
    await db.commit()
    await db.refresh(alias)
    return alias


async def delete_alias(db: AsyncSession, user: User, alias_id: uuid.UUID) -> None:
    alias = await get_alias(db, user, alias_id)
    await db.delete(alias)
    await db.commit()
