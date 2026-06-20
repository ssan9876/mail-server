"""Append-only audit trail of security-relevant actions."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    Integer,
    String,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.base import Base
from app.models.enums import ActorType

# JSONB on Postgres, generic JSON elsewhere (tests on SQLite).
JSONVariant = JSON().with_variant(JSONB, "postgresql")
# Native INET on Postgres, plain string elsewhere.
IPVariant = String(45).with_variant(INET, "postgresql")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    # BIGINT on Postgres; INTEGER on SQLite so rowid autoincrement works in tests.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    actor_type: Mapped[ActorType | None] = mapped_column(
        Enum(ActorType, native_enum=False, length=20, validate_strings=True),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    target_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    meta: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONVariant, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(IPVariant, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AuditLog #{self.id} {self.action} by {self.actor_type}:{self.actor_id}>"
