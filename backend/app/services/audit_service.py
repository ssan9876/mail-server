"""Append-only audit logging helper."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.enums import ActorType


async def record(
    db: AsyncSession,
    *,
    action: str,
    actor_id: uuid.UUID | None = None,
    actor_type: ActorType | None = None,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
    metadata: dict[str, Any] | None = None,
    ip_address: str | None = None,
    commit: bool = True,
) -> AuditLog:
    """Persist an audit entry. Commits by default so logs survive a later rollback."""
    entry = AuditLog(
        action=action,
        actor_id=actor_id,
        actor_type=actor_type,
        target_type=target_type,
        target_id=target_id,
        meta=metadata,
        ip_address=ip_address,
    )
    db.add(entry)
    if commit:
        await db.commit()
    else:
        await db.flush()
    return entry


async def list_logs(
    db: AsyncSession,
    *,
    limit: int = 100,
    offset: int = 0,
    action: str | None = None,
) -> list[AuditLog]:
    """Most-recent-first audit entries, optionally filtered by action."""
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
    if action:
        stmt = stmt.where(AuditLog.action == action)
    stmt = stmt.limit(limit).offset(offset)
    return list((await db.execute(stmt)).scalars().all())
