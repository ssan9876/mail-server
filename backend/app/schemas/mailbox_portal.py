"""Schemas for the mailbox self-service portal."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class MailboxProfile(BaseModel):
    id: uuid.UUID
    address: str
    display_name: str | None
    quota_mb: int
    is_active: bool
    created_at: datetime


class MailboxPasswordChange(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8, max_length=1024)
