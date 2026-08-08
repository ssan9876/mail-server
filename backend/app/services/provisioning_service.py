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

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, UnprocessableError
from app.models.alias import Alias
from app.models.domain import Domain
from app.models.enums import ActorType, UserRole
from app.models.idm import IdmIdentity, IdmIdentityAlias
from app.models.mailbox import Mailbox
from app.models.user import User
from app.schemas.provisioning import (
    UNUSABLE_PASSWORD_HASH,
    IdentityUpsert,
    canonical_hash,
)
from app.services import audit_service, domain_service, mailbox_service, user_service

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

    One unit of work: on any failure the session is rolled back, so a partial
    payload failure leaves the identity exactly as it was rather than
    half-converged. The audit entry is written inside the same transaction, so
    the log can never record a change that did not commit.
    """
    try:
        return await _upsert_identity_inner(
            db, external_id, payload, token_id=token_id, ip_address=ip_address
        )
    except Exception:
        # `app.core.database.get_db` also rolls back on any exception escaping
        # a request, so this is redundant for the HTTP path — but this
        # function is also called directly (see tests/test_provisioning_*.py,
        # which drive it against a bare session with no request lifecycle
        # around it). Rolling back here, rather than trusting the caller to,
        # is what keeps "on any failure the session is rolled back" true
        # regardless of caller. Do not remove this on the assumption
        # `get_db` already covers it.
        await db.rollback()
        raise


async def _upsert_identity_inner(
    db: AsyncSession,
    external_id: str,
    payload: IdentityUpsert,
    *,
    token_id: uuid.UUID | None = None,
    ip_address: str | None = None,
) -> IdmIdentity:
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

    # Alias and admin convergence below both QUERY the database for collisions.
    # Sessions are built with `autoflush=False` (see `app.core.database`), so a
    # rename applied in memory just above would be invisible to those queries:
    # freeing `jane` by renaming the mailbox to `jsmith` and re-adding `jane`
    # as an alias in the same push would raise a bogus "already exists". Flush
    # so the collision checks read current state. Do not rely on autoflush —
    # production does not have it, and neither does the test suite.
    await db.flush()

    mailbox = await db.get(Mailbox, identity.mailbox_id)
    if mailbox is not None:
        await _converge_aliases(db, identity, mailbox, domain.name, payload)
    elif payload.aliases:
        # Currently unreachable in phase 1: `email` is mandatory on every
        # payload and `_converge_mailbox` above always creates or finds a
        # mailbox for it, so `identity.mailbox_id` is never None by this
        # point. Kept as a defensive invariant for a later phase that may
        # introduce admin-only identities with no mailbox — an alias must
        # forward somewhere, and without a mailbox there is no destination
        # to point at.
        raise UnprocessableError("Cannot set aliases on an identity with no mailbox.")

    _apply_lifecycle_stamp(identity, payload.status)
    identity.status = payload.status

    reassigned_domains = await _converge_admin(db, identity, str(payload.email).lower(), payload)

    identity.last_payload_hash = payload_hash
    identity.last_synced_at = datetime.now(timezone.utc)

    await audit_service.record(
        db,
        action="idm.identity.synced",
        actor_id=None,
        actor_type=ActorType.IDM,
        target_type="idm_identity",
        target_id=identity.id,
        metadata={
            "external_id": external_id,
            "email": str(payload.email).lower(),
            "status": payload.status,
            "token_id": str(token_id) if token_id else None,
            # Domain ownership is last-write-wins, so this is the only record
            # that an administrator's domain was reassigned away from them.
            "reassigned_domains": reassigned_domains or None,
        },
        ip_address=ip_address,
        # Shares this transaction: the log must never outlive a rollback.
        commit=False,
    )

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

    # Aliases THIS identity already owns are not real collisions for its own
    # primary address: `_converge_aliases` reconciles them later in the very
    # same transaction, so swapping the primary address with an alias the IdM
    # already holds (`jane` <-> `j.doe`) is a legitimate rename, not a clash.
    # Without this exclusion that swap is a permanent 409 and the IdM has no
    # way to express a name change over an address it already owns.
    #
    # Deliberately narrow: aliases owned by a DIFFERENT identity, aliases an
    # admin created by hand, and every mailbox still collide as before.
    own_alias_ids = set(
        (
            await db.execute(
                select(IdmIdentityAlias.alias_id).where(
                    IdmIdentityAlias.identity_id == identity.id
                )
            )
        ).scalars()
    )

    if mailbox is None:
        if await mailbox_service.local_part_taken(
            db, domain.id, local_part, exclude_alias_ids=own_alias_ids
        ):
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
            db,
            domain.id,
            local_part,
            exclude_mailbox_id=mailbox.id,
            exclude_alias_ids=own_alias_ids,
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


async def _converge_aliases(
    db: AsyncSession,
    identity: IdmIdentity,
    mailbox: Mailbox,
    domain_name: str,
    payload: IdentityUpsert,
) -> None:
    """Reconcile the IdM-owned alias set.

    Only aliases recorded in `idm_identity_aliases` are ever modified or
    removed. An alias an admin created by hand is never adopted — that returns
    a conflict instead, so a sync can never quietly take ownership of something
    it did not create.
    """
    destination = f"{mailbox.local_part}@{domain_name}"

    owned_rows = (
        await db.execute(
            select(Alias, IdmIdentityAlias)
            .join(IdmIdentityAlias, IdmIdentityAlias.alias_id == Alias.id)
            .where(IdmIdentityAlias.identity_id == identity.id)
        )
    ).all()
    owned = {alias for alias, _ in owned_rows}

    # A rename can promote an owned alias to the primary address (see the
    # `exclude_alias_ids` exclusion in `_converge_mailbox`), and nothing else
    # reconciles it away: with `aliases` omitted, the branch below would repoint
    # it at the new primary and leave an alias whose destination is its own
    # address. Delivery survives that — `virtual_mailbox_self.cf` terminates
    # self-mappings — but while the mailbox is DEACTIVATED the alias stays
    # active and short-circuits `virtual_alias_catchall.cf`, so mail that should
    # have reached the domain catch-all bounces instead. Drop it here, before
    # either branch can see it.
    self_key = (mailbox.domain_id, mailbox.local_part)
    for alias in [a for a in owned if (a.domain_id, a.local_part) == self_key]:
        await db.execute(
            delete(IdmIdentityAlias).where(IdmIdentityAlias.alias_id == alias.id)
        )
        await db.delete(alias)
        owned.discard(alias)

    # An omitted collection means "leave untouched" — only the destination is
    # refreshed, so a rename still repoints aliases the IdM already owns. The
    # `is None` arm is unreachable via the schema (an explicit null is a 422);
    # it stays as a defensive invariant because the annotation is still
    # `list[str] | None`, which is what keeps OMISSION expressible.
    if "aliases" not in payload.model_fields_set or payload.aliases is None:
        for alias in owned:
            alias.destination = destination
        return

    desired: dict[tuple[uuid.UUID, str], str] = {}
    for address in payload.aliases:
        alias_local, alias_domain_name = _split_address(address)
        alias_domain = await domain_service.get_by_name(db, alias_domain_name)
        if alias_domain is None:
            raise UnprocessableError(
                f"Alias domain {alias_domain_name} is not hosted here."
            )
        desired[(alias_domain.id, alias_local)] = address

    owned_by_key = {(a.domain_id, a.local_part): a for a in owned}

    for key, alias in owned_by_key.items():
        if key not in desired:
            await db.execute(
                delete(IdmIdentityAlias).where(
                    IdmIdentityAlias.alias_id == alias.id
                )
            )
            await db.delete(alias)

    for (domain_id, alias_local), address in desired.items():
        existing = owned_by_key.get((domain_id, alias_local))
        if existing is not None:
            existing.destination = destination
            continue

        clash = (
            await db.execute(
                select(Alias).where(
                    Alias.domain_id == domain_id, Alias.local_part == alias_local
                )
            )
        ).scalar_one_or_none()
        if clash is not None:
            # Two genuinely different situations, and the operator triaging the
            # dead-lettered event needs to tell them apart: an alias an admin
            # created by hand (fix it here, or drop it from the IdM) versus one
            # another IdM identity already owns (fix it in the IdM, and the
            # other identity's external_id says which record).
            other_external_id = (
                await db.execute(
                    select(IdmIdentity.external_id)
                    .join(
                        IdmIdentityAlias,
                        IdmIdentityAlias.identity_id == IdmIdentity.id,
                    )
                    .where(IdmIdentityAlias.alias_id == clash.id)
                )
            ).scalar_one_or_none()
            if other_external_id is not None:
                raise ConflictError(
                    f"{address} already exists and is managed by IdM identity "
                    f"{other_external_id}."
                )
            raise ConflictError(
                f"{address} already exists and is not managed by the IdM."
            )
        if await mailbox_service.local_part_taken(db, domain_id, alias_local):
            raise ConflictError(f"{address} already exists (mailbox or alias).")

        alias = Alias(
            domain_id=domain_id, local_part=alias_local, destination=destination
        )
        db.add(alias)
        await db.flush()
        db.add(IdmIdentityAlias(identity_id=identity.id, alias_id=alias.id))


_ADMIN_ROLES = {
    "superadmin": UserRole.SUPERADMIN,
    "domain_admin": UserRole.DOMAIN_ADMIN,
}


async def _converge_admin(
    db: AsyncSession, identity: IdmIdentity, email: str, payload: IdentityUpsert
) -> list[dict]:
    """Reconcile the control-plane user record.

    Provisioned admins never get a usable password: they authenticate via SSO
    in phase 2, and this row exists so an SSO login has something to resolve
    to — the same shape the counterpart system's own console requires (a local
    row plus a role grant before authorization works).

    Two separate concerns live here, and conflating them was a real
    access-revocation hole:

      - `is_active` is derived from `status`, which is MANDATORY on every
        payload. It therefore tracks the status mapping for any linked user on
        every push, whether or not `admin` was sent.
      - Role and domain ownership come from `admin`, which is optional. The
        absent-collection rule ("a minimal payload cannot wipe what it does not
        mention") applies to those, and only to those.

    Returns the domain ownership reassignments this call performed, each as
    `{"domain": <name>, "from": <previous owner id, or None>}` — Task 8 folds
    this into the audit entry so a silent takeover is never actually silent.
    """
    reassigned_domains: list[dict] = []

    user: User | None = (
        await db.get(User, identity.user_id) if identity.user_id is not None else None
    )

    if user is not None:
        # Offboarding arrives as `{"email": ..., "status": "deactivated"}` with
        # no `admin` key at all — the IdM has no reason to restate an admin
        # block for someone who just left. Returning early on an absent `admin`
        # used to skip this line and leave a departed employee with full
        # mail-admin access. `status` was present; honour it.
        user.is_active = STATUS_ACTIVE_MAP[payload.status]

    if "admin" not in payload.model_fields_set:
        return reassigned_domains

    if payload.admin is None:
        # Deactivate, never delete: audit entries reference this row. This
        # deliberately overrides the status mapping above — an explicit
        # `"admin": null` means "no longer an operator" regardless of whether
        # the person is still an active employee.
        #
        # DEMOTE THE ROLE, not just `is_active`. Expressing revocation only as
        # `is_active = False` was not durable: `is_active` is the SAME field
        # the status mapping writes unconditionally a few lines above, on every
        # push. So the very next ordinary payload that omitted `admin` and
        # carried `status: "active"` — a display-name edit, a quota change, or
        # the connector simply dropping a now-absent `mail_admin_role`
        # attribute rather than sending an explicit null — set it straight back
        # to True, silently restoring a revoked SUPERADMIN: `role` had never
        # been touched and `identity.user_id` still pointed at the row.
        # Revocation now lands in the ROLE dimension, which ONLY an `admin`
        # block ever writes, so the two concerns this function's own docstring
        # separates no longer collide on a single column.
        if user is not None:
            user.is_active = False
            user.role = UserRole.USER
        return reassigned_domains

    # `users.email` is unique, but not every row is IdM-managed — e.g. the
    # bootstrap superadmin has a `users` row with no mailbox at all, so
    # `_converge_mailbox`'s local_part/alias check never sees it and a
    # colliding push would sail past it. Catch the collision here, before
    # touching the row, so it surfaces as a dead-letterable ConflictError
    # naming the address rather than a raw unique-constraint IntegrityError
    # that the IdM connector would retry forever.
    existing_by_email = await user_service.get_by_email(db, email)
    if existing_by_email is not None and (
        user is None or existing_by_email.id != user.id
    ):
        raise ConflictError(
            f"{email} is already an operator account not managed by the IdM."
        )

    if user is None:
        user = User(
            email=email,
            password_hash=UNUSABLE_PASSWORD_HASH,
            role=_ADMIN_ROLES[payload.admin.role],
        )
        db.add(user)
        await db.flush()
        identity.user_id = user.id
    else:
        user.email = email
        user.role = _ADMIN_ROLES[payload.admin.role]

    # Also covers the row just created above, which the status mapping at the
    # top of this function could not have seen.
    user.is_active = STATUS_ACTIVE_MAP[payload.status]

    # Domain ownership is last-write-wins, by deliberate ruling: the IdM is
    # the source of truth for who administers what, so a push naming a domain
    # another admin currently owns MAY reassign it. Two identities that
    # alternate naming the same domain will legitimately ping-pong ownership
    # back and forth — that is accepted, not a bug to guard against. What is
    # NOT accepted is doing it silently, hence the return value below.
    #
    # Ownership here is also add-only within a single call: there is no "this
    # identity no longer administers X" signal in `admin.domains`, so a domain
    # dropped from the list is simply never revisited — it stays with whoever
    # holds it.
    for domain_name in payload.admin.domains:
        domain = await domain_service.get_by_name(db, domain_name)
        if domain is None:
            raise UnprocessableError(f"Domain {domain_name} is not hosted here.")
        if domain.owner_id != user.id:
            reassigned_domains.append(
                {
                    "domain": domain.name,
                    "from": str(domain.owner_id) if domain.owner_id else None,
                }
            )
        domain.owner_id = user.id

    return reassigned_domains


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
