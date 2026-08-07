"""
Provisioning payload — a deliberately CLOSED schema.

`extra="forbid"` is the security control, not a nicety. The counterpart system
never transmits a credential (Keycloak owns them), and there is no free-form
attribute bag here, so no HR field can reach the mail server even by accident:
there is nowhere in the schema to put one. Adding a field is a deliberate
change to this file.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

IdentityStatus = Literal["pending", "active", "suspended", "deactivated"]

# Written to `password_hash` on provisioned rows. Not a valid hash in any
# scheme: `verify_password` and `verify_dovecot` both return False for every
# input rather than raising, so it can never authenticate.
UNUSABLE_PASSWORD_HASH = "!idm-provisioned-no-password"


class AdminSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["superadmin", "domain_admin"]
    domains: list[str] = Field(default_factory=list)


class IdentityUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str | None = Field(default=None, max_length=255)
    email: EmailStr
    display_name: str | None = Field(default=None, max_length=255)
    quota_mb: int | None = Field(default=None, ge=0, le=10_000_000)
    status: IdentityStatus
    aliases: list[str] | None = None
    admin: AdminSpec | None = None

    @field_validator("quota_mb")
    @classmethod
    def _reject_null_quota(cls, value: int | None) -> int:
        """`mailboxes.quota_mb` is NOT NULL, so there is no cleared state to
        express. Omitting the field means "leave unchanged"; sending an
        explicit null is rejected rather than silently treated as absent,
        which would report convergence while leaving the quota stale.

        Field validators do not run on defaults, so this fires only when the
        caller actually supplied a value.
        """
        if value is None:
            raise ValueError("quota_mb may be omitted, but not null.")
        return value


class IdentityRead(BaseModel):
    external_id: str
    email: str | None
    status: IdentityStatus
    mailbox_id: uuid.UUID | None
    user_id: uuid.UUID | None
    aliases: list[str]
    last_synced_at: datetime | None
    deactivated_at: datetime | None


def canonical_hash(payload: IdentityUpsert) -> str:
    """Stable hash of exactly the fields the caller SET.

    `exclude_unset` is what preserves the absent-vs-null distinction the
    convergence rules depend on: omitting `display_name` and explicitly sending
    `null` mean different things and must not hash alike.
    """
    data = payload.model_dump(exclude_unset=True, mode="json")
    blob = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
