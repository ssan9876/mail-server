"""Password-reset (mailbox self-service) schemas."""
from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str = Field(min_length=1)
    new_password: str = Field(min_length=8, max_length=1024)


class PasswordResetResponse(BaseModel):
    message: str
    # Populated only outside production so the flow is end-to-end testable;
    # in production the token is delivered by email, never returned here.
    debug_token: str | None = None
