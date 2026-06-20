"""Audit log read endpoints (superadmin only)."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import DbDep, require_superadmin
from app.schemas.audit import AuditLogRead
from app.services import audit_service

router = APIRouter(
    prefix="/audit",
    tags=["audit"],
    dependencies=[Depends(require_superadmin)],
)


@router.get("", response_model=list[AuditLogRead])
async def list_audit_logs(
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    action: str | None = None,
) -> list[AuditLogRead]:
    logs = await audit_service.list_logs(db, limit=limit, offset=offset, action=action)
    return [AuditLogRead.model_validate(log) for log in logs]
