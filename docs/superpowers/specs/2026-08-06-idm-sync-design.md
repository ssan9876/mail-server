# IdM Sync — Phase 1: Provisioning API

**Date:** 2026-08-06
**Status:** Approved for planning
**Scope:** Phase 1 of 4

## Problem

An external identity management system (built separately, by the same author)
must be the source of truth for who has email here. Today mailboxes and admin
users are created by hand in the dashboard. The IdM needs to create, update, and
offboard both.

## Decisions

| Question | Decision |
|---|---|
| Direction | IdM pushes to a mail-server API |
| API shape | Declarative upsert keyed by the IdM's stable user ID |
| Admin login | OIDC SSO (phase 2); phase 1 only provisions the accounts |
| Mailbox password | IdM sends it; stored as a Dovecot-compatible hash |
| Address | IdM sends the full address; the domain must already exist here |
| Offboarding | Disable immediately; purge after a retention window (phase 4) |
| API auth | Service token, IdM on the internal Docker network |

## Phasing

Phase 1 is this document: the provisioning API, mailbox and admin sync, display
name, quota, aliases, and disable-on-deprovision.

Later phases, each getting its own spec and plan:

2. OIDC SSO login for the dashboard, and role mapping from IdM claims.
3. Groups to distribution lists.
4. Retention purge job for deprovisioned mailboxes.

Phase 4 is separate for a concrete reason, not just size: the backend container
does not mount the `maildata` volume (`docker-compose.yml:120,146` — only
postfix and dovecot do). Deleting mail therefore needs a deployment change (a
new mount or a `doveadm` call), not just application code.

## Constraints found in the existing system

**The API cannot touch the mail store.** Provisioning creates and disables
mailbox rows; Maildir files are unreachable from the backend container.

**Dovecot and Postfix disagree about where a mailbox lives.** Postfix's
`virtual_mailbox_maps.cf:6` reads the authoritative `m.maildir_path` column,
while Dovecot's `user_query` recomputes the path as
`'/maildata/' || d.name || '/' || m.local_part`
(`docker/dovecot/dovecot-sql.conf.ext.tmpl:19-20`). They agree today only
because `maildir_path` is always derived that way at creation. A rename would
repoint Dovecot at a new empty directory and orphan the user's mail. Fixing this
is in scope, because the declarative model makes renames routine.

**Mailbox services are user-scoped.** Every function in `mailbox_service.py`
takes a `User` and scopes by domain ownership. Provisioning has no user.

## Data model

Three new tables. No columns change on existing tables.

### `idm_service_tokens`

How the IdM authenticates.

| Column | Notes |
|---|---|
| `id` | UUID PK |
| `name` | Human label |
| `token_hash` | SHA-256 of the raw token |
| `prefix` | First 8 chars, for identifying a token without exposing it |
| `is_active` | Revocation |
| `expires_at` | Nullable |
| `last_used_at` | Nullable |
| `created_by` | FK to `users.id`, `ON DELETE SET NULL` |
| timestamps | Via `TimestampMixin` |

The raw token is returned once at creation and never stored. SHA-256 rather than
Argon2 is deliberate: this is verified on every provisioning call, and the token
is a high-entropy random secret, so the slow-hash argument for user passwords
does not apply.

### `idm_identities`

The link that makes the API declarative.

| Column | Notes |
|---|---|
| `id` | UUID PK |
| `external_id` | The IdM's stable user ID; unique, indexed |
| `mailbox_id` | Nullable FK to `mailboxes.id`, `ON DELETE SET NULL` |
| `user_id` | Nullable FK to `users.id`, `ON DELETE SET NULL` |
| `last_synced_at` | Nullable |
| `last_payload_hash` | Nullable; enables the no-op short circuit |
| `deprovisioned_at` | Nullable; read by the phase-4 purge job |
| timestamps | Via `TimestampMixin` |

Both FKs are nullable because an identity may be mail-only, admin-only, or both.

Keying on `external_id` rather than the address is what makes renames correct: a
changed email becomes a rename of an existing mailbox, not an orphan plus a new
empty one.

### `idm_identity_aliases`

Tracks which aliases this integration owns.

| Column | Notes |
|---|---|
| `identity_id` | FK to `idm_identities.id`, `ON DELETE CASCADE` |
| `alias_id` | FK to `aliases.id`, `ON DELETE CASCADE` |

Composite primary key on (`identity_id`, `alias_id`); no surrogate id.

Without this table a sync cannot distinguish an IdM-managed alias from one an
admin created by hand, and would delete the admin's. Only IdM-owned aliases are
ever removed.

### Enum change

Add `IDM = "idm"` to `ActorType` (`app/models/enums.py`) so the audit trail
separates IdM-driven changes from admin and system ones. Enums are rendered as
VARCHAR + CHECK (`20260619_0001_initial_schema.py:26`), so the migration drops
and recreates the `actortype` check constraint.

### Not doing

`mailboxes` and `users` gain no `external_id` column. The link lives only in
`idm_identities`, so the integration stays a bolt-on: drop the three tables and
the mail server is exactly what it is today.

## API

### Provisioning — service token, `/api/v1/provisioning/*`

| Method | Path | Purpose |
|---|---|---|
| `PUT` | `/identities/{external_id}` | Upsert; the only call the IdM needs |
| `GET` | `/identities/{external_id}` | Read back current state |
| `DELETE` | `/identities/{external_id}` | Deprovision |
| `GET` | `/health` | Validates the token, touches nothing |

`GET` and `DELETE` return `404` for an unknown `external_id`. `DELETE` on an
already-deprovisioned identity returns `200` and changes nothing, so the IdM can
retry an offboarding safely.

### Token management — JWT auth, `require_superadmin`, `/api/v1/idm/tokens`

Create, list, revoke. Create returns the raw token exactly once.

### Upsert payload

```json
{
  "email": "jane@acme.com",
  "display_name": "Jane Doe",
  "quota_mb": 4096,
  "status": "active",
  "password": "…",
  "aliases": ["j.doe@acme.com"],
  "admin": { "role": "domain_admin", "domains": ["acme.com"] }
}
```

`status` is one of `active`, `suspended`, `deprovisioned`. `admin` may be
`null`. `password` is required only when creating a mailbox.

`aliases` entries are full addresses and must be in a domain hosted here; one
that is not returns `422`. Each alias's destination is the identity's own
mailbox address, so an identity with no mailbox cannot have aliases — that
combination returns `422`.

Every domain named in `admin.domains` must already exist here; `422` otherwise.

### Scalar-vs-collection rule

An absent scalar field means leave unchanged. An explicit `null` clears a
nullable scalar. A present collection is the complete desired set; an absent
collection is left untouched, so a minimal payload cannot wipe a user's aliases.

## Convergence algorithm

`PUT /identities/{external_id}` runs these steps in one transaction:

1. Resolve and validate the token. `403` if unknown, inactive, or expired.
2. Split `email`. The domain must already exist here — `422` otherwise. The mail
   server never auto-creates domains, since that pulls in DKIM key generation
   and DNS record management. An existing but inactive domain is accepted:
   provisioning is a control-plane act, and refusing it would make bringing a
   domain back online require a full re-sync from the IdM.
3. Load or create the `idm_identities` row, taking a row-level lock
   (`SELECT … FOR UPDATE`) so concurrent pushes for one user cannot interleave.
4. No-op check: if the canonical hash of the payload equals `last_payload_hash`,
   update `last_synced_at` and return. Argon2 is salted, so without this every
   repeated push rewrites the password hash and burns CPU for no change.
5. Mailbox:
   - Create if absent. `password` required; `422` with an explicit message if
     missing.
   - Rename if `email` changed, keeping `maildir_path` fixed so existing mail
     follows the user. Collision-check against mailboxes and aliases; `409` on
     conflict.
   - Apply `display_name`, `quota_mb`, and `status`. `active` sets
     `is_active = true` and clears `deprovisioned_at`. `suspended` sets
     `is_active = false` and leaves `deprovisioned_at` alone — a suspension is
     not an offboarding and must never start the retention clock.
     `deprovisioned` sets `is_active = false` and stamps `deprovisioned_at` if
     it is not already set, so repeated deprovision calls do not extend the
     retention window.
   - Rehash the password if one was sent, via `hash_for_dovecot`.
6. Aliases, when the field is present: add what is missing, remove IdM-owned
   ones no longer listed. A requested alias that exists but is not IdM-owned
   returns `409` rather than being silently adopted.
7. Admin: an `admin` block ensures a `users` row with that role and ownership of
   the listed domains. `"admin": null` deactivates the linked user rather than
   deleting it. Passwords are never set on admin users — they log in via OIDC in
   phase 2, so `password_hash` gets an unusable placeholder value that no input
   can hash to.
8. Write one `idm.identity.synced` audit entry summarising what actually
   changed, with actor type `IDM` and `commit=False` so it shares the
   transaction.

Returns `200` with the resulting state.

## Deprovisioning

`DELETE /identities/{external_id}`, or `status: "deprovisioned"` on an upsert:

- `mailbox.is_active = false` — logins refused, new mail rejected
- Linked admin user deactivated
- IdM-owned aliases deactivated
- `deprovisioned_at` stamped, if not already set
- `last_payload_hash` cleared
- Maildir untouched

Clearing `last_payload_hash` is required for correctness, not tidiness. A
`DELETE` changes state without changing any payload, so if the hash survived,
re-pushing the user's unchanged `active` payload would match the stored hash,
short-circuit at step 4, and silently fail to reactivate them.

A later `PUT` with `status: "active"` fully reverses it, so a mistaken
offboarding is a one-call fix.

## Dovecot alignment fix

`user_query` in `docker/dovecot/dovecot-sql.conf.ext.tmpl` changes to select
`m.maildir_path` for `home` and `'maildir:' || m.maildir_path` for `mail`,
matching what `virtual_mailbox_maps.cf` already does. `maildir_path` becomes the
single authority for where mail lives.

For existing mailboxes the resulting path is byte-identical, so there is no data
migration and no files move. It is a config-template change and takes effect on
Dovecot restart. It must ship before rename is enabled, or the first rename
orphans a user's mail.

## Service layer

`mailbox_service.py` splits domain-scoped functions from unscoped primitives.
The existing `(db, user, …)` functions keep their exact signatures and behaviour
and delegate to the unscoped internals; the new `provisioning_service` calls the
same internals directly. Maildir-path construction and collision checks are not
duplicated.

Provisioning does not fabricate a `User` to satisfy the existing scoped
functions. A synthetic user that bypasses domain-ownership checks would be a
security smell and would pollute the audit trail.

New modules:

- `app/models/idm.py` — the three models
- `app/schemas/provisioning.py` — payload and response schemas
- `app/services/provisioning_service.py` — convergence
- `app/services/idm_token_service.py` — token issue, verify, revoke
- `app/api/v1/provisioning.py` — service-token routes
- `app/api/v1/idm_tokens.py` — superadmin token management
- `app/api/deps.py` — a `require_service_token` dependency
- One Alembic migration

## Failure modes

| Situation | Response |
|---|---|
| Unknown, revoked, or expired token | `403`, without indicating which |
| Domain not hosted here | `422`, names the domain |
| Address collides with a non-IdM mailbox or alias | `409`, no partial write |
| Create without a password | `422`, explicit message |
| Malformed payload | `422` from Pydantic |
| Concurrent push, same `external_id` | Serialised by row lock |
| Mid-sync database error | Whole sync rolls back |

The whole convergence runs in one transaction, so a partial failure leaves the
identity exactly as it was. The audit entry is written inside that transaction,
so the log never records a change that rolled back.

## Security

Provisioning routes are excluded from the Nginx server block and are reachable
only on the internal Docker network, so a leaked token is useless from outside.
Tokens are compared with `secrets.compare_digest`.

The service-token dependency is entirely separate from `get_current_user`, so a
provisioning token can never satisfy an operator endpoint and an operator JWT
can never satisfy a provisioning endpoint. This mirrors the isolation the
existing code already enforces between user and mailbox principals
(`app/api/deps.py:62`).

Rate limiting reuses `app/core/ratelimit.py`.

## Testing

Following the existing `backend/tests/` pattern — SQLite with the async
fixtures in `conftest.py`.

**Token auth:** valid, revoked, expired, malformed, and missing tokens; a
provisioning token rejected on an admin endpoint; an operator JWT rejected on a
provisioning endpoint.

**Convergence:** create; idempotent re-push writes nothing, asserting
`updated_at` and the password hash are both unchanged (this is the test that
catches the salted-rehash bug); rename preserves `maildir_path`; quota and
display-name updates; suspend then reactivate.

**Aliases:** add; remove IdM-owned; refuse to modify admin-created; `409` when
adopting an existing non-IdM alias; an omitted `aliases` field leaves them
alone.

**Admin:** create with role and domains; role change; `"admin": null`
deactivates without deleting.

**Failures:** unknown domain; collision; create without password; alias in an
unhosted domain; aliases on an identity with no mailbox; `admin.domains` naming
an unknown domain; a forced mid-sync error asserting full rollback.

**Deprovision:** `DELETE` then re-push of the unchanged `active` payload
reactivates the user — the regression test for the cleared `last_payload_hash`;
`DELETE` twice is a no-op; a `suspended` push does not stamp
`deprovisioned_at`; repeated `deprovisioned` pushes do not move it.

**Audit:** exactly one `idm.identity.synced` entry per real change, and none on
a no-op.

## Out of scope

Each of these gets its own spec: OIDC SSO login, groups to distribution lists,
the retention purge job, and any pull or reconcile direction.

## Open flag for phase 2

Pure OIDC SSO plus phase 1 provisioning admin users with unusable passwords
means an IdM outage locks everyone out of mail administration. The phase 2 spec
should decide whether the bootstrap `ADMIN_EMAIL` / `ADMIN_PASSWORD` superadmin
(`app/core/config.py:50`) stays password-capable as break-glass access. Recorded
here so the decision is not lost; it is not a phase 1 decision.
