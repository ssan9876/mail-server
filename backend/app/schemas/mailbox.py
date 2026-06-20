"""Mailbox schemas."""
from __future__ import annotations

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

# RFC 5321-ish local part: a conservative, safe subset.
_LOCAL_PART_RE = r"^[a-z0-9](?:[a-z0-9._+\-]{0,62}[a-z0-9])?$"


def _validate_local_part(v: str) -> str:
    v = v.strip().lower()
    if not re.match(_LOCAL_PART_RE, v):
        raise ValueError("Invalid local part.")
    return v


class MailboxCreate(BaseModel):
    local_part: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=1024)
    display_name: str | None = Field(default=None, max_length=255)
    quota_mb: int | None = Field(default=None, ge=0, le=10_000_000)

    _norm_local = field_validator("local_part")(_validate_local_part)


class MailboxUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=255)
    quota_mb: int | None = Field(default=None, ge=0, le=10_000_000)
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=1024)


class MailboxRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    domain_id: uuid.UUID
    local_part: str
    display_name: str | None
    quota_mb: int
    is_active: bool
    maildir_path: str
    created_at: datetime
    updated_at: datetime
    # password_hash intentionally omitted.
