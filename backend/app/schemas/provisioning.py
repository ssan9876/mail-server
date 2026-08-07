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

from app.schemas.mailbox import _validate_local_part

IdentityStatus = Literal["pending", "active", "suspended", "deactivated"]

# Written to `password_hash` on provisioned rows. `verify_password` and
# `verify_dovecot` both return False for every input rather than raising, so it
# can never authenticate through this API.
#
# The `{CRYPT}` prefix is load-bearing, not decoration. Dovecot reads
# `mailboxes.password_hash` directly, and a value with NO inline scheme prefix
# is interpreted under `default_pass_scheme` — so if that were ever set to
# PLAIN, a bare placeholder would become a working password shared by every
# provisioned mailbox. Naming a scheme explicitly makes the value inert
# whatever `default_pass_scheme` says. `*` is the conventional crypt(3) "no
# password" marker and cannot be produced by any crypt implementation, so
# nothing hashes to it.
UNUSABLE_PASSWORD_HASH = "{CRYPT}*idm-provisioned-no-password"


def _validate_alias_address(address: str) -> str:
    """Reject anything that is not a plain `local@domain` address.

    Unvalidated alias strings are a routing hazard, not just a tidiness issue:
    `_split_address` splits on the LAST `@`, so `"@@acme.example.com"` yields a
    local part of `"@"` — exactly the domain catch-all convention
    `docker/postfix/pgsql/virtual_alias_catchall.cf` matches. A single
    double-`@` typo would silently divert every unrouted address in the domain
    into one mailbox. The local part is held to `_LOCAL_PART_RE`, the same
    conservative subset the admin-facing mailbox and alias schemas use, which
    excludes `@` and the empty string by construction.
    """
    local_part, separator, domain_part = address.strip().rpartition("@")
    if not separator or not domain_part:
        raise ValueError(
            f"Invalid alias {address!r}: expected a local@domain address."
        )
    try:
        _validate_local_part(local_part)
    except ValueError:
        raise ValueError(
            f"Invalid alias {address!r}: {local_part!r} is not a valid local part."
        ) from None
    return address


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

    @field_validator("aliases")
    @classmethod
    def _validate_aliases(cls, value: list[str] | None) -> list[str]:
        """Reject an explicit null, and validate every entry.

        `[]` already expresses "remove all IdM-owned aliases", so null is
        redundant rather than a third meaning — and accepting it would be a
        silent divergence: it lands in `model_fields_set`, hashes as a distinct
        payload, advances `last_synced_at` and writes a success audit row while
        changing nothing. Same ruling as `quota_mb` above.

        Field validators do not run on defaults, so OMITTING the field still
        works and stays absent from `model_fields_set`.
        """
        if value is None:
            raise ValueError("aliases may be omitted, but not null.")
        return [_validate_alias_address(entry) for entry in value]


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
