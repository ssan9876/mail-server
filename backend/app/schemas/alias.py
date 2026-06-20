"""Alias schemas."""
from __future__ import annotations

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.alias import Alias

_LOCAL_PART_RE = r"^[a-z0-9](?:[a-z0-9._+\-]{0,62}[a-z0-9])?$"
CATCH_ALL = "@"


def _validate_alias_local_part(v: str) -> str:
    v = v.strip().lower()
    if v == CATCH_ALL:
        return v  # domain catch-all
    if not re.match(_LOCAL_PART_RE, v):
        raise ValueError("Invalid alias local part.")
    return v


class AliasCreate(BaseModel):
    local_part: str = Field(min_length=1, max_length=64)
    destinations: list[EmailStr] = Field(min_length=1)

    _norm_local = field_validator("local_part")(_validate_alias_local_part)


class AliasUpdate(BaseModel):
    destinations: list[EmailStr] | None = Field(default=None, min_length=1)
    is_active: bool | None = None


class AliasRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    domain_id: uuid.UUID
    local_part: str
    destinations: list[str]
    is_active: bool
    created_at: datetime

    @classmethod
    def from_model(cls, alias: Alias) -> "AliasRead":
        return cls(
            id=alias.id,
            domain_id=alias.domain_id,
            local_part=alias.local_part,
            destinations=[d for d in alias.destination.split(",") if d],
            is_active=alias.is_active,
            created_at=alias.created_at,
        )
