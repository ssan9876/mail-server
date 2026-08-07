"""
External identity-provider integration.

Deliberately a bolt-on: `mailboxes` and `users` gain no columns, so dropping
these three tables returns the system to exactly what it was before. The link
is keyed on the IdM's own user id rather than the email address, which is what
makes a rename a rename instead of an orphan plus a new empty mailbox.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class IdmServiceToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A bearer token the IdM's connector authenticates with.

    SHA-256 rather than Argon2 on purpose: this is verified on every
    provisioning call and the token is a high-entropy random secret, so the
    slow-hash argument that applies to user-chosen passwords does not apply.
    """

    __tablename__ = "idm_service_tokens"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    # First 16 chars of the raw token — identifies a token in the UI and in logs
    # without being usable to authenticate.
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<IdmServiceToken {self.name} ({self.prefix}…)>"


class IdmIdentity(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Links one IdM user to the local rows provisioned for them."""

    __tablename__ = "idm_identities"

    external_id: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    # Captured now though nothing reads it yet: phase 2 resolves an SSO login
    # to this row by username, and backfilling it later means re-syncing every
    # identity.
    idm_username: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Nullable on both sides — an identity may be mail-only, admin-only, or both.
    mailbox_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("mailboxes.id", ondelete="SET NULL"), nullable=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # The IdM's own lifecycle value, stored verbatim. Cannot be derived from
    # `mailbox.is_active` + `deactivated_at`: `pending` and `suspended` are
    # both inactive-and-unstamped, so deriving would collapse them into one.
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)

    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Canonical hash of the last applied payload, for the no-op short circuit.
    last_payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Stamped on offboarding; read by the phase-4 retention purge job.
    deactivated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<IdmIdentity {self.external_id}>"


class IdmIdentityAlias(Base):
    """Marks an alias as IdM-owned.

    Without this, a sync cannot tell an IdM-managed alias from one an admin
    created by hand, and would delete the admin's. Only rows recorded here are
    ever removed by a sync.
    """

    __tablename__ = "idm_identity_aliases"

    identity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("idm_identities.id", ondelete="CASCADE"), primary_key=True
    )
    alias_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("aliases.id", ondelete="CASCADE"), primary_key=True
    )
