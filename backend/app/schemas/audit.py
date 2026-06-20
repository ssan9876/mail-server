"""Audit log read schema."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models.enums import ActorType


class AuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    actor_id: uuid.UUID | None
    actor_type: ActorType | None
    action: str
    target_type: str | None
    target_id: uuid.UUID | None
    meta: dict[str, Any] | None
    ip_address: str | None
    created_at: datetime
