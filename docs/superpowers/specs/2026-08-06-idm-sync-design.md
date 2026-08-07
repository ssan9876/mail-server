# IdM Sync — Phase 1: Provisioning API

**Date:** 2026-08-06
**Status:** Approved for planning
**Scope:** Phase 1 of 4
**Counterpart system:** `D:\identity-manager` — see its
`docs/superpowers/specs/2026-08-04-identity-provider-core-design.md`

## Problem

An external identity provider is the source of truth for who has email here.
Today mailboxes and admin users are created by hand in the dashboard. The IdM
must own that lifecycle: create, update, suspend, and offboard.

## The counterpart system

The IdM is a single-tenant identity provider built on NestJS + Postgres +
Keycloak. Two of its properties determine this design:

**Keycloak owns every credential.** The IdM's first global constraint is that it
never generates, transmits, or stores a credential. Keycloak stores passwords
one-way and does not release them. There is therefore no password for the IdM to
send us, and asking for one would violate the constraint the whole system is
built around.

**Changes leave the IdM through a transactional outbox.** A mutation writes its
row, its audit entry, and an outbox event in one transaction; a worker drains
the outbox and applies changes to a `target`. Targets are additive by design —
`keycloak` today, with `active_directory` and `google_workspace` planned. The
mail server becomes another target.

Three properties of that worker shape our API:

- It **reconciles to desired state and never applies a delta**, so it will
  re-assert a user's full state on every retry.
- It applies events **in order per aggregate**.
- It retries with backoff and dead-letters visibly.

So idempotency is not a nicety here; it is the contract the worker depends on. A
declarative upsert is precisely what it needs to call.

## Decisions

| Question | Decision |
|---|---|
| Direction | IdM pushes; the mail server is a new outbox target |
| API shape | Declarative upsert keyed by the IdM's user ID |
| Mailbox password | **Not synced.** App passwords, generated after SSO (phase 2) |
| Admin login | OIDC against Keycloak (phase 2); phase 1 provisions the row |
| Address | IdM sends the full address; the domain must already exist here |
| Status model | The IdM's own four: pending, active, suspended, deactivated |
| Offboarding | Disable immediately; purge mail after a retention window (phase 4) |
| API auth | Service token, IdM on the internal Docker network |

## Phasing

Phase 1 is this document: the provisioning API, mailbox and admin record sync,
display name, quota, aliases, and status lifecycle. No credentials of any kind.

Later phases, each getting its own spec:

2. **Keycloak OIDC + app passwords.** Admin dashboard SSO, mailbox portal SSO,
   and per-device app passwords. This is the milestone that makes an
   IdM-provisioned mailbox independently usable.
3. Groups to distribution lists.
4. Retention purge job for deactivated mailboxes.

Phase 1 does not strand users. The existing admin dashboard can still set a
mailbox password by hand (`app/api/v1/mailboxes.py`), so provisioned mailboxes
remain usable by the current manual route until phase 2 lands.

Phase 4 is separate for a concrete reason beyond size: the backend container
does not mount the `maildata` volume (`docker-compose.yml:120,146` — only
postfix and dovecot do). Deleting mail needs a deployment change, not just
application code.

## Work required in the identity-manager repo

Out of scope here, and needing its own spec in that repo:

- A `mail_server` value in the outbox `target` column, and a connector that
  calls this API.
- `'mail_server'` added to `external_identities.system`.
- Four seeded attribute definitions the connector reads: `mail_enabled`
  (who gets a mailbox), `mail_quota_mb`, `mail_aliases`, and `mail_admin_role`.
- Making the outbox genuinely multi-target. This is the bulk of that work and
  is larger than the connector itself; see the counterpart spec.
- Deciding whether mail deactivation is synchronous-first. The IdM already makes
  that exception for Keycloak because offboarding cannot wait for a queue to
  drain. The argument is stronger for mail: an offboarded employee holding an
  open IMAP connection is still reading mail. Recommended, but it is that repo's
  decision.

## Constraints found in this repo

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

How the IdM's connector authenticates.

| Column | Notes |
|---|---|
| `id` | UUID PK |
| `name` | Human label |
| `token_hash` | SHA-256 of the raw token |
| `prefix` | First 16 chars, for identifying a token without exposing it |
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
| `external_id` | The IdM's user UUID; unique, indexed |
| `idm_username` | The IdM's `username` for this user |
| `mailbox_id` | Nullable FK to `mailboxes.id`, `ON DELETE SET NULL` |
| `user_id` | Nullable FK to `users.id`, `ON DELETE SET NULL` |
| `last_synced_at` | Nullable |
| `last_payload_hash` | Nullable; enables the no-op short circuit |
| `deactivated_at` | Nullable; read by the phase-4 purge job |
| timestamps | Via `TimestampMixin` |

Both FKs are nullable because an identity may be mail-only, admin-only, or both.

Keying on `external_id` rather than the address is what makes renames correct: a
changed email becomes a rename of an existing mailbox, not an orphan plus a new
empty one.

`idm_username` is stored in phase 1 even though nothing reads it yet. Phase 2
needs it to match a Keycloak token back to a local row, and the IdM's decision 1
pins principal resolution to `username`. Capturing it now costs a column;
backfilling it later means re-syncing every identity.

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
| `PUT` | `/identities/{external_id}` | Upsert; the only call the connector makes |
| `GET` | `/identities/{external_id}` | Read back current state |
| `GET` | `/health` | Validates the token, touches nothing |

`GET` returns `404` for an unknown `external_id`.

**There is no `DELETE`.** The IdM has no delete operation — `deactivated` is
terminal and removal propagates as a `status_changed` event. Offboarding arrives
as an ordinary upsert carrying `"status": "deactivated"`. Adding a delete verb
here would invent a lifecycle the source system does not have.

### Token management — JWT auth, `require_superadmin`, `/api/v1/idm/tokens`

Create, list, revoke. Create returns the raw token exactly once.

### Upsert payload

```json
{
  "username": "jdoe",
  "email": "jane@acme.com",
  "display_name": "Jane Doe",
  "quota_mb": 4096,
  "status": "active",
  "aliases": ["j.doe@acme.com"],
  "admin": { "role": "domain_admin", "domains": ["acme.com"] }
}
```

**No credential field exists on this payload, in either direction.** That is
enforced by schema, and asserted by a test, so a future change cannot quietly
reintroduce one.

**The schema is closed — there is no free-form attribute bag.** This matters
beyond tidiness. The IdM's attribute propagation to Keycloak is default-deny
because anything reaching Keycloak can surface in a JWT claim; here the same
protection is structural rather than a flag, since HR data has nowhere in the
payload to go. Adding a field is a deliberate change to this schema, not a
checkbox on an attribute definition.

`status` is one of `pending`, `active`, `suspended`, `deactivated`, matching the
IdM's lifecycle exactly. `admin` may be `null`.

`aliases` entries are full addresses and must be in a domain hosted here; one
that is not returns `422`. Each alias's destination is the identity's own
mailbox address, so an identity with no mailbox cannot have aliases — that
combination returns `422`.

Every domain named in `admin.domains` must already exist here; `422` otherwise.

### Scalar-vs-collection rule

An absent scalar field means leave unchanged. An explicit `null` clears a
nullable scalar. A present collection is the complete desired set; an absent
collection is left untouched, so a minimal payload cannot wipe a user's aliases.

## Status mapping

| IdM status | Mailbox | Admin user | `deactivated_at` |
|---|---|---|---|
| `pending` | Row created, `is_active = false` | Created, inactive | untouched |
| `active` | `is_active = true` | Active | cleared |
| `suspended` | `is_active = false` | Inactive | untouched |
| `deactivated` | `is_active = false` | Inactive | stamped if unset |

`pending` creates the mailbox row rather than deferring it. The address is
reserved the moment the IdM knows about the person, so a starter cannot lose
their intended address to a collision between offer and start date. It cannot
receive mail until activated.

A suspension must never stamp `deactivated_at` — suspension is not offboarding
and must not start the retention clock. Repeated `deactivated` pushes must not
move an existing stamp, or the IdM's reconciliation job would extend the
retention window indefinitely.

## Convergence algorithm

`PUT /identities/{external_id}` runs these steps in one transaction:

1. Resolve and validate the token. `403` if unknown, inactive, or expired.
2. Split `email`. The domain must already exist here — `422` otherwise. The mail
   server never auto-creates domains, since that pulls in DKIM key generation
   and DNS record management. An existing but inactive domain is accepted:
   provisioning is a control-plane act, and refusing it would make bringing a
   domain back online require a full re-sync from the IdM.
3. Load or create the `idm_identities` row.

   **Row locking is NOT implemented — deferred, not done.** The intent was a
   row-level lock (`SELECT … FOR UPDATE`) so concurrent pushes for one user
   could not interleave: the IdM guarantees per-aggregate ordering, but two
   aggregates touching one identity, or a reconciliation run overlapping live
   traffic, can still race. It is deferred because SQLite — which the test
   suite runs on — cannot express `FOR UPDATE`, so the lock could not be
   covered by a test.

   Phase 1 is safe only because the connector runs a **single** worker, which
   serialises pushes at the source. Adding the lock is therefore a hard
   precondition before more than one connector worker runs concurrently: with
   two workers and no lock, two pushes for one identity can interleave and the
   last writer wins on a partially-read state.
4. No-op check: if the canonical hash of the payload equals `last_payload_hash`,
   update `last_synced_at` and return. The IdM's worker re-asserts full desired
   state on every retry and its reconciliation job re-pushes unchanged users
   wholesale, so without this the audit log fills with entries recording no
   change and every reconcile pass writes to every row.
5. Mailbox:
   - Create if absent, with an unusable placeholder `password_hash` that no
     input can hash to. The mailbox has no working credential until an app
     password is issued in phase 2, or an admin sets one manually.
   - Rename if `email` changed, keeping `maildir_path` fixed so existing mail
     follows the user. Collision-check against mailboxes and aliases; `409` on
     conflict.
   - Apply `display_name`, `quota_mb`, and the status mapping above.
6. Aliases, when the field is present: add what is missing, remove IdM-owned
   ones no longer listed. A requested alias that exists but is not IdM-owned
   returns `409` rather than being silently adopted.
7. Admin: an `admin` block ensures a `users` row with that role and ownership of
   the listed domains, and an unusable placeholder `password_hash`.
   `"admin": null` deactivates the linked user rather than deleting it.

   This mirrors what the IdM's own console does — its milestone 8 required a
   local user row matching the Keycloak username plus a role grant before
   authorization would work. Phase 2 resolves an SSO login to this row the same
   way.
8. Write one `idm.identity.synced` audit entry summarising what actually
   changed, with actor type `IDM` and `commit=False` so it shares the
   transaction.

Returns `200` with the resulting state.

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

Error responses matter more than usual here: they are consumed by the IdM's
worker, which decides from them whether to retry or dead-letter. `409` and `422`
are permanent failures the connector should dead-letter rather than retry
forever; `5xx` is retriable. This should be stated in the connector's spec too.

| Situation | Response |
|---|---|
| Unknown, revoked, or expired token | `403`, without indicating which |
| Domain not hosted here | `422`, names the domain |
| Address collides with a non-IdM mailbox or alias | `409`, no partial write |
| Alias in an unhosted domain | `422` |
| Aliases on an identity with no mailbox | `422` |
| `admin.domains` names an unknown domain | `422` |
| Malformed payload, or any credential field present | `422` from Pydantic |
| Concurrent push, same `external_id` | **Not serialised.** Row locking is deferred (see step 3); the single-worker connector is what prevents this today |
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

**There is no rate limiting on the provisioning routes.** No `@limiter.limit`
decorator is applied, and because the routes are blocked at the Nginx edge
(above), nginx's `limit_req` zones never see provisioning traffic either. The
control is network reachability plus the service token, not request volume — a
caller that can reach these routes at all already holds a valid token on the
internal Docker network. If provisioning is ever exposed beyond that network,
a rate limit becomes required rather than optional.

## Testing

Following the existing `backend/tests/` pattern — SQLite with the async
fixtures in `conftest.py`.

**Token auth:** valid, revoked, expired, malformed, and missing tokens; a
provisioning token rejected on an admin endpoint; an operator JWT rejected on a
provisioning endpoint.

**No credentials:** a payload carrying `password`, `password_hash`, or any
similar field is rejected `422`. This is asserted, not assumed — it is the
constraint the counterpart system is built on.

**Convergence:** create; idempotent re-push writes nothing, asserting
`updated_at` is unchanged and no audit row was added (the property the IdM's
reconciliation job depends on); rename preserves `maildir_path`; quota and
display-name updates.

**Status lifecycle:** each of the four statuses maps as tabled; `pending`
creates an inactive mailbox; `suspended` does not stamp `deactivated_at`;
repeated `deactivated` pushes do not move the stamp; `deactivated` then `active`
reactivates and clears it.

**Aliases:** add; remove IdM-owned; refuse to modify admin-created; `409` when
adopting an existing non-IdM alias; an omitted `aliases` field leaves them
alone.

**Admin:** create with role and domains; role change; `"admin": null`
deactivates without deleting; the placeholder hash cannot be authenticated
against by any input.

**Failures:** every row of the failure table, plus a forced mid-sync error
asserting full rollback.

## Out of scope

Each of these gets its own spec: Keycloak OIDC login and app passwords, groups
to distribution lists, the retention purge job, and any pull or reconcile
direction. The `identity-manager` side of the integration is specified in that
repo.

## Open flag for phase 2

With admin login moving to Keycloak OIDC and provisioned admin rows holding
unusable placeholder passwords, a Keycloak outage would lock everyone out of
mail administration. The phase 2 spec should decide whether the bootstrap
`ADMIN_EMAIL` / `ADMIN_PASSWORD` superadmin (`app/core/config.py:50`) stays
password-capable as break-glass access. Recorded here so the decision is not
lost; it is not a phase 1 decision.

## Known issues carried forward

Found during implementation review and deliberately accepted, not fixed. Each
bounds a guarantee stated above, so anyone building phase 2 or the connector
should read this section alongside the failure table.

**Convergence is not self-healing against out-of-band drift.** The no-op short
circuit (step 4 of the convergence algorithm) returns before mailbox, alias and
admin convergence run. So if an operator re-enables a user the IdM deactivated —
through the dashboard, or directly in the database — an identical re-push will
match `last_payload_hash`, short-circuit, and never re-revoke. The IdM's
reconciliation job cannot repair drift it cannot see. Closing this means either
verifying observed state before the short circuit, or having the connector send
a periodic force-converge that bypasses the hash.

**Concurrent pushes for one identity are not serialised.** The row lock is
deferred (see the note in the convergence algorithm). Until it lands, exactly
one connector worker may target this mail server at a time. Nothing in code or
deployment configuration enforces that precondition.

**Domain ownership cannot express shared administration.** `Domain.owner_id` is
single-valued while `admin.domains` is a per-user list, so two identities that
both claim one domain will alternate ownership on every reconcile pass, each
push revoking the other's access. Ownership is also add-only: dropping a domain
from `admin.domains` does not release it. Representing shared administration
needs a join table, which is a schema change beyond this phase.

**IdM-owned aliases stay active when their identity is deactivated.** Only the
destination mailbox is deactivated. Delivery still fails, because the alias
forwards to an inactive mailbox — but the alias continues to match, which
suppresses the domain catch-all for that address.

**Provisioning has no application-level rate limit.** The routes are blocked at
the Nginx edge, so nginx's limiter never sees them, and no `@limiter.limit`
decorator is applied. Low practical risk given 256-bit service tokens on an
internal network, but it is not the defence-in-depth the Security section might
imply.

**Convergence is tested only on SQLite.** The migration tests run against real
PostgreSQL, but every service-layer test uses SQLite, which ignores `VARCHAR`
lengths and does not enforce `ON DELETE CASCADE` in this fixture. Length-overflow
and cascade behaviour are therefore reasoned rather than observed. Any value that
can exceed its column width surfaces as a `DataError` → 500, which the connector
treats as retriable and will retry indefinitely.
