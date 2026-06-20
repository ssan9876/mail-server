"""Domain + DNS schemas."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

# RFC 1035-ish domain label validation (no scheme, no trailing dot).
_DOMAIN_RE = r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))+$"


class DomainCreate(BaseModel):
    name: str = Field(min_length=3, max_length=253)
    # Superadmin only: assign the domain to a specific owner. Ignored otherwise.
    owner_id: uuid.UUID | None = None

    @field_validator("name")
    @classmethod
    def normalize_and_validate(cls, v: str) -> str:
        v = v.strip().lower().rstrip(".")
        import re

        if not re.match(_DOMAIN_RE, v):
            raise ValueError("Invalid domain name.")
        return v


class DomainUpdate(BaseModel):
    is_active: bool | None = None
    catch_all_box: uuid.UUID | None = None


class DomainRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    owner_id: uuid.UUID | None
    is_active: bool
    catch_all_box: uuid.UUID | None
    dkim_selector: str | None
    dns_verified: bool
    mx_verified: bool
    spf_verified: bool
    dmarc_verified: bool
    created_at: datetime
    updated_at: datetime
    # Note: dkim_private_key / dkim_public_key are intentionally NOT exposed.


class DnsRecord(BaseModel):
    """A DNS record the operator must publish (or that we publish for them)."""

    type: str
    name: str
    content: str
    priority: int | None = None
    ttl: int = 3600


class VerificationResult(BaseModel):
    mx_verified: bool
    spf_verified: bool
    dkim_verified: bool
    dmarc_verified: bool
    dns_verified: bool  # all of the above
