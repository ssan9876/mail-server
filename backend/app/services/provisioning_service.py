"""
Declarative identity convergence for the IdM provisioning API.

The caller sends a user's desired end state; this module diffs it against what
exists and converges. Idempotency is the contract, not a nicety: the
counterpart system's sync worker reconciles to desired state and re-asserts a
user's full state on every retry, so a repeated identical push must do nothing
at all.

Authorization is the service token (see `deps.require_service_token`); there is
no `User` here, which is why every operation uses the unscoped primitives in
`mailbox_service` rather than the domain-scoped ones.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, UnprocessableError
from app.models.idm import IdmIdentity
from app.models.mailbox import Mailbox
from app.schemas.provisioning import (
    UNUSABLE_PASSWORD_HASH,
    IdentityUpsert,
    canonical_hash,
)
from app.services import domain_service, mailbox_service

# The IdM's four lifecycle values, mapped to mailbox reachability. Only
# `active` is a live principal — mirroring how the counterpart system treats
# status everywhere else.
STATUS_ACTIVE_MAP: dict[str, bool] = {
    "pending": False,
    "active": True,
    "suspended": False,
    "deactivated": False,
}


def _split_address(email: str) -> tuple[str, str]:
    local_part, _, domain_name = email.strip().lower().rpartition("@")
    # `domain_service.get_by_name` normalizes with `.strip().lower()` but NOT
    # `.rstrip(".")`, while `DomainCreate`'s validator does all three. This is
    # the first caller that does not go through that validator, so a trailing
    # dot (valid FQDN syntax) has to be stripped here or the lookup misses.
    domain_name = domain_name.rstrip(".")
    return local_part, domain_name


async def _load_identity(db: AsyncSession, external_id: str) -> IdmIdentity | None:
    result = await db.execute(
        select(IdmIdentity).where(IdmIdentity.external_id == external_id)
    )
    return result.scalar_one_or_none()


async def get_identity(db: AsyncSession, external_id: str) -> IdmIdentity:
    identity = await _load_identity(db, external_id)
    if identity is None:
        raise NotFoundError("Identity not found.")
    return identity


async def upsert_identity(
    db: AsyncSession,
    external_id: str,
    payload: IdentityUpsert,
    *,
    token_id: uuid.UUID | None = None,
    ip_address: str | None = None,
) -> IdmIdentity:
    """Converge one identity to the payload's desired state.

    Runs as a single unit of work: the caller's session is committed once at
    the end, so a failure part-way leaves the identity exactly as it was.
    """
    local_part, domain_name = _split_address(str(payload.email))
    domain = await domain_service.get_by_name(db, domain_name)
    if domain is None:
        # Never auto-create a domain: that pulls in DKIM key generation and DNS
        # record management, neither of which the IdM knows anything about.
        raise UnprocessableError(f"Domain {domain_name} is not hosted here.")

    identity = await _load_identity(db, external_id)
    if identity is None:
        identity = IdmIdentity(external_id=external_id)
        db.add(identity)
        await db.flush()

    payload_hash = canonical_hash(payload)
    if identity.last_payload_hash == payload_hash:
        # Nothing changed. The counterpart's reconciliation job re-pushes
        # unchanged users wholesale, so without this every reconcile pass would
        # write to every row and fill the audit log with no-change entries.
        identity.last_synced_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(identity)
        return identity

    if "username" in payload.model_fields_set:
        identity.idm_username = payload.username

    await _converge_mailbox(db, identity, domain, local_part, payload)
    _apply_lifecycle_stamp(identity, payload.status)
    identity.status = payload.status

    identity.last_payload_hash = payload_hash
    identity.last_synced_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(identity)
    return identity


async def _converge_mailbox(
    db: AsyncSession,
    identity: IdmIdentity,
    domain,
    local_part: str,
    payload: IdentityUpsert,
) -> None:
    is_active = STATUS_ACTIVE_MAP[payload.status]
    fields_set = payload.model_fields_set

    mailbox: Mailbox | None = (
        await db.get(Mailbox, identity.mailbox_id)
        if identity.mailbox_id is not None
        else None
    )

    if mailbox is None:
        if await mailbox_service.local_part_taken(db, domain.id, local_part):
            raise ConflictError(
                f"{local_part}@{domain.name} already exists (mailbox or alias)."
            )
        mailbox = await mailbox_service.create_mailbox_unscoped(
            db,
            domain,
            local_part=local_part,
            # No credential is ever supplied or derivable here — a mailbox has
            # no working password until an app password is issued (phase 2) or
            # an admin sets one by hand.
            password_hash=UNUSABLE_PASSWORD_HASH,
            display_name=payload.display_name,
            quota_mb=payload.quota_mb,
            is_active=is_active,
            commit=False,
        )
        identity.mailbox_id = mailbox.id
        return

    # Rename: the address moved. `maildir_path` deliberately stays put so the
    # user's existing mail follows them.
    if mailbox.local_part != local_part or mailbox.domain_id != domain.id:
        if await mailbox_service.local_part_taken(
            db, domain.id, local_part, exclude_mailbox_id=mailbox.id
        ):
            raise ConflictError(
                f"{local_part}@{domain.name} already exists (mailbox or alias)."
            )
        mailbox.local_part = local_part
        mailbox.domain_id = domain.id

    if "display_name" in fields_set:
        mailbox.display_name = payload.display_name
    if "quota_mb" in fields_set:
        # No `is None` guard: the schema rejects an explicit null, so presence
        # in fields_set means a real value was sent.
        mailbox.quota_mb = payload.quota_mb
    mailbox.is_active = is_active


def _apply_lifecycle_stamp(identity: IdmIdentity, status: str) -> None:
    """Maintain `deactivated_at`, which the phase-4 purge job reads.

    Only `deactivated` starts the retention clock. `suspended` deliberately
    leaves an existing stamp alone: a suspension is not an offboarding.
    Re-stamping on a repeat push would let the counterpart's reconciliation job
    extend the retention window indefinitely.
    """
    if status == "active":
        identity.deactivated_at = None
    elif status == "deactivated" and identity.deactivated_at is None:
        identity.deactivated_at = datetime.now(timezone.utc)
