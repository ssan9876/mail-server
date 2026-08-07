# IdM Sync Phase 1 (Provisioning API) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A declarative provisioning API that lets an external identity provider create, update, suspend, and offboard mailboxes and mail-admin records, keyed by the IdM's own user ID.

**Architecture:** One idempotent `PUT /api/v1/provisioning/identities/{external_id}` authenticated by a service token. The whole convergence — mailbox, aliases, admin record, audit entry — runs in a single transaction and reconciles to the desired state described by the payload. Three new tables link IdM identities to local rows without touching existing schema.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 (async), Alembic, Pydantic v2, pytest + pytest-asyncio, SQLite (tests) / PostgreSQL 16 (production).

**Spec:** `docs/superpowers/specs/2026-08-06-idm-sync-design.md`
**Counterpart spec (the calling side):** `D:\identity-manager\docs\superpowers\specs\2026-08-06-mail-server-connector-design.md`

## Global Constraints

- **No credential ever crosses this API, in either direction.** The payload schema uses `extra="forbid"`, so any credential-shaped field is a `422`. This is asserted by test, not assumed.
- The counterpart system has no delete. There is no `DELETE` endpoint; offboarding is `status: "deactivated"` on an ordinary upsert.
- Status values are exactly `pending`, `active`, `suspended`, `deactivated` — the IdM's own four.
- Absent scalar field = leave unchanged. Explicit `null` = clear. Present collection = complete desired set. Absent collection = leave untouched. Distinguishing absent from `null` requires `model_fields_set`, never a `None` check.
- The whole convergence commits or rolls back as one transaction, audit entry included (`commit=False` on the audit write).
- The backend container cannot reach the `maildata` volume. Never write, move, or delete Maildir files.
- `maildir_path` is immutable after creation. A rename changes `local_part`/`domain_id` only.
- Tests run from `backend/` with `python -m pytest`. All new tests follow the existing SQLite + `conftest.py` fixture pattern.
- Follow existing module conventions: `from __future__ import annotations`, services are modules of functions (not classes), routers are `APIRouter` instances aggregated in `app/api/v1/__init__.py`.

---

## File Structure

**Create:**

| File | Responsibility |
|---|---|
| `backend/app/models/idm.py` | The three IdM tables |
| `backend/app/schemas/idm_token.py` | Token create/read schemas |
| `backend/app/schemas/provisioning.py` | The closed upsert payload + response |
| `backend/app/services/idm_token_service.py` | Token issue, verify, revoke |
| `backend/app/services/provisioning_service.py` | Convergence |
| `backend/app/api/v1/idm_tokens.py` | Superadmin token management |
| `backend/app/api/v1/provisioning.py` | Service-token provisioning routes |
| `backend/alembic/versions/20260806_0003_idm_sync.py` | Schema migration |
| `backend/tests/test_idm_models.py` | Model + constraint tests |
| `backend/tests/test_idm_tokens.py` | Token service + API tests |
| `backend/tests/test_provisioning_schemas.py` | Payload validation tests |
| `backend/tests/test_provisioning_mailbox.py` | Mailbox convergence tests |
| `backend/tests/test_provisioning_aliases.py` | Alias convergence tests |
| `backend/tests/test_provisioning_admin.py` | Admin convergence tests |
| `backend/tests/test_provisioning_api.py` | End-to-end route tests |
| `backend/tests/test_mail_config_templates.py` | Config-template regression |

**Modify:**

| File | Change |
|---|---|
| `backend/app/models/enums.py` | Add `ActorType.IDM` |
| `backend/app/models/__init__.py` | Register the three new models |
| `backend/app/api/deps.py` | Add `require_service_token` |
| `backend/app/api/v1/__init__.py` | Register two routers |
| `backend/app/services/mailbox_service.py` | Split scoped from unscoped |
| `backend/app/services/domain_service.py` | Add public `get_by_name` |
| `docker/dovecot/dovecot-sql.conf.ext.tmpl` | `user_query` reads `maildir_path` |
| `docker/nginx/templates/10-https.conf.template` | Block `/api/v1/provisioning/` at the edge |

---

### Task 1: Models, enum, and migration

**Files:**
- Create: `backend/app/models/idm.py`
- Create: `backend/alembic/versions/20260806_0003_idm_sync.py`
- Modify: `backend/app/models/enums.py`
- Modify: `backend/app/models/__init__.py`
- Test: `backend/tests/test_idm_models.py`

**Interfaces:**
- Consumes: `Base`, `UUIDPrimaryKeyMixin`, `TimestampMixin` from `app.models.base`.
- Produces: `IdmServiceToken`, `IdmIdentity`, `IdmIdentityAlias` models; `ActorType.IDM`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_idm_models.py`:

```python
"""IdM table shape and constraint tests."""
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.enums import ActorType
from app.models.idm import IdmIdentity, IdmIdentityAlias, IdmServiceToken


def test_actor_type_has_idm():
    assert ActorType.IDM.value == "idm"


@pytest.mark.asyncio
async def test_external_id_is_unique(sessionmaker_):
    async with sessionmaker_() as session:
        session.add(IdmIdentity(external_id="user-1"))
        await session.commit()

    async with sessionmaker_() as session:
        session.add(IdmIdentity(external_id="user-1"))
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_identity_defaults_are_null(sessionmaker_):
    async with sessionmaker_() as session:
        session.add(IdmIdentity(external_id="user-2", idm_username="jdoe"))
        await session.commit()

    async with sessionmaker_() as session:
        identity = (
            await session.execute(
                select(IdmIdentity).where(IdmIdentity.external_id == "user-2")
            )
        ).scalar_one()
        assert identity.mailbox_id is None
        assert identity.user_id is None
        assert identity.last_payload_hash is None
        assert identity.deactivated_at is None
        assert identity.idm_username == "jdoe"
        assert identity.status == "pending"


@pytest.mark.asyncio
async def test_identity_alias_link_is_composite_pk(sessionmaker_):
    identity_id = uuid.uuid4()
    alias_id = uuid.uuid4()
    async with sessionmaker_() as session:
        session.add(IdmIdentity(id=identity_id, external_id="user-3"))
        await session.commit()

    async with sessionmaker_() as session:
        session.add(IdmIdentityAlias(identity_id=identity_id, alias_id=alias_id))
        await session.commit()

    async with sessionmaker_() as session:
        session.add(IdmIdentityAlias(identity_id=identity_id, alias_id=alias_id))
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_service_token_stores_hash_not_raw(sessionmaker_):
    async with sessionmaker_() as session:
        session.add(
            IdmServiceToken(name="idm", token_hash="a" * 64, prefix="idm_abcd")
        )
        await session.commit()

    async with sessionmaker_() as session:
        token = (await session.execute(select(IdmServiceToken))).scalar_one()
        assert token.is_active is True
        assert token.expires_at is None
        assert token.last_used_at is None
        assert not hasattr(token, "token")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_idm_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.idm'`

- [ ] **Step 3: Add `ActorType.IDM`**

In `backend/app/models/enums.py`, extend `ActorType`:

```python
class ActorType(str, enum.Enum):
    """Who performed an audited action."""

    USER = "user"
    MAILBOX = "mailbox"
    SYSTEM = "system"
    IDM = "idm"  # an external identity provider, via the provisioning API
```

- [ ] **Step 4: Create the models**

Create `backend/app/models/idm.py`:

```python
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
    # First 8 chars of the raw token — identifies a token in the UI and in logs
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
```

- [ ] **Step 5: Register the models**

In `backend/app/models/__init__.py`, add the import and exports:

```python
from app.models.idm import IdmIdentity, IdmIdentityAlias, IdmServiceToken
```

and add `"IdmServiceToken"`, `"IdmIdentity"`, `"IdmIdentityAlias"` to `__all__`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_idm_models.py -v`
Expected: PASS (5 tests)

- [ ] **Step 7: Write the migration**

Create `backend/alembic/versions/20260806_0003_idm_sync.py`:

```python
"""idm sync: service tokens, identity links, and the idm actor type

Revision ID: 0003_idm_sync
Revises: 0002_mail_lookup_grants
Create Date: 2026-08-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_idm_sync"
down_revision: Union[str, None] = "0002_mail_lookup_grants"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID_DEFAULT = sa.text("gen_random_uuid()")
_NOW = sa.text("now()")

# actor_type is VARCHAR + CHECK (native_enum=False), so widening it means
# dropping and recreating the constraint rather than an ALTER TYPE.
_OLD_ACTORS = "'user', 'mailbox', 'system'"
_NEW_ACTORS = "'user', 'mailbox', 'system', 'idm'"


def upgrade() -> None:
    op.create_table(
        "idm_service_tokens",
        sa.Column("id", sa.Uuid(), server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("prefix", sa.String(16), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_idm_service_tokens"),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"],
            name="fk_idm_service_tokens_created_by_users",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_idm_service_tokens_token_hash",
        "idm_service_tokens", ["token_hash"], unique=True,
    )

    op.create_table(
        "idm_identities",
        sa.Column("id", sa.Uuid(), server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("idm_username", sa.String(255), nullable=True),
        sa.Column("mailbox_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_payload_hash", sa.String(64), nullable=True),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_idm_identities"),
        sa.ForeignKeyConstraint(
            ["mailbox_id"], ["mailboxes.id"],
            name="fk_idm_identities_mailbox_id_mailboxes", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name="fk_idm_identities_user_id_users", ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_idm_identities_external_id", "idm_identities", ["external_id"], unique=True
    )

    op.create_table(
        "idm_identity_aliases",
        sa.Column("identity_id", sa.Uuid(), nullable=False),
        sa.Column("alias_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("identity_id", "alias_id", name="pk_idm_identity_aliases"),
        sa.ForeignKeyConstraint(
            ["identity_id"], ["idm_identities.id"],
            name="fk_idm_identity_aliases_identity_id_idm_identities",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["alias_id"], ["aliases.id"],
            name="fk_idm_identity_aliases_alias_id_aliases", ondelete="CASCADE",
        ),
    )

    # SQLite cannot drop a CHECK constraint; tests build the schema with
    # create_all rather than migrations, so skipping there is correct.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_constraint("ck_audit_logs_actortype", "audit_logs", type_="check")
        op.create_check_constraint(
            "actortype", "audit_logs", f"actor_type IN ({_NEW_ACTORS})"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Any 'idm' rows would violate the narrowed constraint. Audit logs are
        # append-only and must not be deleted, so they are relabelled 'system'
        # — the closest surviving value — rather than dropped.
        op.execute("UPDATE audit_logs SET actor_type = 'system' WHERE actor_type = 'idm'")
        op.drop_constraint("ck_audit_logs_actortype", "audit_logs", type_="check")
        op.create_check_constraint(
            "actortype", "audit_logs", f"actor_type IN ({_OLD_ACTORS})"
        )

    op.drop_table("idm_identity_aliases")
    op.drop_index("ix_idm_identities_external_id", table_name="idm_identities")
    op.drop_table("idm_identities")
    op.drop_index("ix_idm_service_tokens_token_hash", table_name="idm_service_tokens")
    op.drop_table("idm_service_tokens")
```

- [ ] **Step 8: Verify the whole suite still passes**

Run: `cd backend && python -m pytest -q`
Expected: PASS — all pre-existing tests plus the 5 new ones.

- [ ] **Step 9: Commit**

```bash
git add backend/app/models/idm.py backend/app/models/enums.py \
        backend/app/models/__init__.py \
        backend/alembic/versions/20260806_0003_idm_sync.py \
        backend/tests/test_idm_models.py
git commit -m "feat(idm): add service token, identity link, and alias-ownership tables"
```

---

### Task 2: Service tokens — issue, verify, revoke

**Files:**
- Create: `backend/app/services/idm_token_service.py`
- Create: `backend/app/schemas/idm_token.py`
- Create: `backend/app/api/v1/idm_tokens.py`
- Modify: `backend/app/api/deps.py`
- Modify: `backend/app/api/v1/__init__.py`
- Test: `backend/tests/test_idm_tokens.py`

**Interfaces:**
- Consumes: `IdmServiceToken` (Task 1); `PermissionDeniedError`, `NotFoundError` from `app.core.exceptions`; `require_superadmin`, `DbDep`, `CurrentUser` from `app.api.deps`.
- Produces:
  - `idm_token_service.create_token(db, *, name, created_by, expires_at=None) -> tuple[IdmServiceToken, str]` (model, raw token)
  - `idm_token_service.verify_token(db, raw_token) -> IdmServiceToken`
  - `idm_token_service.list_tokens(db) -> list[IdmServiceToken]`
  - `idm_token_service.revoke_token(db, token_id) -> IdmServiceToken`
  - `deps.require_service_token` — a FastAPI dependency returning `IdmServiceToken`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_idm_tokens.py`:

```python
"""Service-token issue/verify/revoke, and principal isolation."""
from datetime import datetime, timedelta, timezone

import pytest

from app.core.exceptions import PermissionDeniedError
from app.services import idm_token_service
from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD, login_headers


@pytest.mark.asyncio
async def test_create_returns_raw_token_once_and_stores_only_the_hash(sessionmaker_):
    async with sessionmaker_() as session:
        token, raw = await idm_token_service.create_token(
            session, name="idm-connector", created_by=None
        )
        assert raw.startswith("idm_")
        assert len(raw) > 20
        assert token.token_hash != raw
        assert token.prefix == raw[:16]
        assert raw not in token.token_hash


@pytest.mark.asyncio
async def test_verify_accepts_the_raw_token(sessionmaker_):
    async with sessionmaker_() as session:
        _, raw = await idm_token_service.create_token(
            session, name="idm", created_by=None
        )
    async with sessionmaker_() as session:
        verified = await idm_token_service.verify_token(session, raw)
        assert verified.name == "idm"
        assert verified.last_used_at is not None


@pytest.mark.asyncio
async def test_verify_rejects_unknown_revoked_and_expired(sessionmaker_):
    async with sessionmaker_() as session:
        with pytest.raises(PermissionDeniedError):
            await idm_token_service.verify_token(session, "idm_nope")

    async with sessionmaker_() as session:
        revoked, revoked_raw = await idm_token_service.create_token(
            session, name="revoked", created_by=None
        )
        await idm_token_service.revoke_token(session, revoked.id)

        expired, expired_raw = await idm_token_service.create_token(
            session,
            name="expired",
            created_by=None,
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )

    async with sessionmaker_() as session:
        with pytest.raises(PermissionDeniedError):
            await idm_token_service.verify_token(session, revoked_raw)
        with pytest.raises(PermissionDeniedError):
            await idm_token_service.verify_token(session, expired_raw)


@pytest.mark.asyncio
async def test_superadmin_can_manage_tokens_over_http(client, superadmin):
    headers = await login_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)

    created = await client.post(
        "/api/v1/idm/tokens", json={"name": "connector"}, headers=headers
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["token"].startswith("idm_")
    token_id = body["id"]

    listed = await client.get("/api/v1/idm/tokens", headers=headers)
    assert listed.status_code == 200
    # The raw token is never returned again.
    assert "token" not in listed.json()[0]

    revoked = await client.delete(f"/api/v1/idm/tokens/{token_id}", headers=headers)
    assert revoked.status_code == 204


@pytest.mark.asyncio
async def test_non_superadmin_cannot_manage_tokens(client, make_user):
    from app.models.enums import UserRole

    await make_user("domadmin@example.com", role=UserRole.DOMAIN_ADMIN)
    headers = await login_headers(client, "domadmin@example.com", "password123456")
    resp = await client.get("/api/v1/idm/tokens", headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_operator_jwt_is_not_a_service_token(client, superadmin):
    """An admin JWT must never satisfy a provisioning endpoint."""
    headers = await login_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    resp = await client.get("/api/v1/provisioning/health", headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_service_token_is_not_an_operator_credential(client, superadmin):
    """And a service token must never satisfy an admin endpoint."""
    admin_headers = await login_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    created = await client.post(
        "/api/v1/idm/tokens", json={"name": "connector"}, headers=admin_headers
    )
    raw = created.json()["token"]

    resp = await client.get(
        "/api/v1/domains", headers={"Authorization": f"Bearer {raw}"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_health_accepts_a_valid_service_token(client, superadmin):
    admin_headers = await login_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    created = await client.post(
        "/api/v1/idm/tokens", json={"name": "connector"}, headers=admin_headers
    )
    raw = created.json()["token"]

    resp = await client.get(
        "/api/v1/provisioning/health", headers={"Authorization": f"Bearer {raw}"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_idm_tokens.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.idm_token_service'`

- [ ] **Step 3: Write the token service**

Create `backend/app/services/idm_token_service.py`:

```python
"""
Service tokens for the IdM provisioning API.

Deliberately separate from every user/mailbox credential path: these tokens are
not JWTs and are never accepted by `get_current_user`, so a provisioning
credential can never satisfy an operator endpoint (and vice versa) by
construction rather than by a role check.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.models.idm import IdmServiceToken

TOKEN_PREFIX = "idm_"
_TOKEN_BYTES = 32
_PREFIX_LEN = 16


def generate_token() -> str:
    """A high-entropy, url-safe token with an identifying prefix."""
    return f"{TOKEN_PREFIX}{secrets.token_urlsafe(_TOKEN_BYTES)}"


def hash_token(raw_token: str) -> str:
    """Deterministic SHA-256 used for lookup. See IdmServiceToken's docstring
    for why this is not a slow hash."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


async def create_token(
    db: AsyncSession,
    *,
    name: str,
    created_by: uuid.UUID | None,
    expires_at: datetime | None = None,
) -> tuple[IdmServiceToken, str]:
    """Issue a token. The raw value is returned once and never stored."""
    raw = generate_token()
    token = IdmServiceToken(
        name=name,
        token_hash=hash_token(raw),
        prefix=raw[:_PREFIX_LEN],
        created_by=created_by,
        expires_at=expires_at,
    )
    db.add(token)
    await db.commit()
    await db.refresh(token)
    return token, raw


async def verify_token(db: AsyncSession, raw_token: str) -> IdmServiceToken:
    """Resolve a raw token, or raise PermissionDeniedError.

    Every rejection raises the same error with the same message: a caller must
    not be able to tell an unknown token from a revoked or expired one.
    """
    digest = hash_token(raw_token)
    result = await db.execute(
        select(IdmServiceToken).where(IdmServiceToken.token_hash == digest)
    )
    token = result.scalar_one_or_none()

    if token is None or not secrets.compare_digest(token.token_hash, digest):
        raise PermissionDeniedError("Invalid service token.")
    if not token.is_active:
        raise PermissionDeniedError("Invalid service token.")
    if token.expires_at is not None:
        expires_at = token.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            raise PermissionDeniedError("Invalid service token.")

    token.last_used_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(token)
    return token


async def list_tokens(db: AsyncSession) -> list[IdmServiceToken]:
    result = await db.execute(
        select(IdmServiceToken).order_by(IdmServiceToken.created_at.desc())
    )
    return list(result.scalars().all())


async def revoke_token(db: AsyncSession, token_id: uuid.UUID) -> IdmServiceToken:
    token = await db.get(IdmServiceToken, token_id)
    if token is None:
        raise NotFoundError("Service token not found.")
    token.is_active = False
    await db.commit()
    await db.refresh(token)
    return token
```

- [ ] **Step 4: Write the token schemas**

Create `backend/app/schemas/idm_token.py`:

```python
"""IdM service-token schemas."""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class IdmTokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    expires_at: datetime | None = None


class IdmTokenRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    prefix: str
    is_active: bool
    expires_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime
    # token_hash intentionally omitted.


class IdmTokenCreated(IdmTokenRead):
    """Returned exactly once, at creation."""

    token: str
```

- [ ] **Step 5: Add the `require_service_token` dependency**

In `backend/app/api/deps.py`, add after `get_current_mailbox`:

```python
async def require_service_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: DbDep,
):
    """Authenticate the IdM connector.

    Entirely separate from `get_current_user`: a service token is not a JWT, so
    an operator credential can never satisfy a provisioning endpoint and a
    provisioning token can never satisfy an operator one — the same isolation
    already enforced between user and mailbox principals, but structural rather
    than claim-based.
    """
    from app.services import idm_token_service

    if credentials is None:
        raise PermissionDeniedError("Service token required.")
    return await idm_token_service.verify_token(db, credentials.credentials)
```

- [ ] **Step 6: Write the token management router**

Create `backend/app/api/v1/idm_tokens.py`:

```python
"""IdM service-token management (superadmin only)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import CurrentUser, DbDep, get_client_ip, require_superadmin
from app.models.enums import ActorType
from app.schemas.idm_token import IdmTokenCreate, IdmTokenCreated, IdmTokenRead
from app.services import audit_service, idm_token_service

router = APIRouter(
    prefix="/idm/tokens", tags=["idm"], dependencies=[Depends(require_superadmin)]
)


@router.post("", response_model=IdmTokenCreated, status_code=status.HTTP_201_CREATED)
async def create_token(
    payload: IdmTokenCreate, request: Request, current_user: CurrentUser, db: DbDep
) -> IdmTokenCreated:
    token, raw = await idm_token_service.create_token(
        db, name=payload.name, created_by=current_user.id, expires_at=payload.expires_at
    )
    await audit_service.record(
        db,
        action="idm.token.created",
        actor_id=current_user.id,
        actor_type=ActorType.USER,
        target_type="idm_service_token",
        target_id=token.id,
        metadata={"name": token.name, "prefix": token.prefix},
        ip_address=get_client_ip(request),
    )
    return IdmTokenCreated(**IdmTokenRead.model_validate(token).model_dump(), token=raw)


@router.get("", response_model=list[IdmTokenRead])
async def list_tokens(db: DbDep) -> list[IdmTokenRead]:
    tokens = await idm_token_service.list_tokens(db)
    return [IdmTokenRead.model_validate(t) for t in tokens]


@router.delete(
    "/{token_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response
)
async def revoke_token(
    token_id: uuid.UUID, request: Request, current_user: CurrentUser, db: DbDep
) -> Response:
    token = await idm_token_service.revoke_token(db, token_id)
    await audit_service.record(
        db,
        action="idm.token.revoked",
        actor_id=current_user.id,
        actor_type=ActorType.USER,
        target_type="idm_service_token",
        target_id=token.id,
        ip_address=get_client_ip(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
```

- [ ] **Step 7: Create a minimal provisioning router with only `/health`**

Create `backend/app/api/v1/provisioning.py`:

```python
"""IdM provisioning endpoints (service-token authenticated).

These routes are blocked at the Nginx edge and are reachable only on the
internal Docker network — see docker/nginx/templates/10-https.conf.template.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import require_service_token

router = APIRouter(
    prefix="/provisioning",
    tags=["provisioning"],
    dependencies=[Depends(require_service_token)],
)


@router.get("/health")
async def health() -> dict[str, str]:
    """Validates the caller's token and touches nothing else."""
    return {"status": "ok"}
```

- [ ] **Step 8: Register both routers**

In `backend/app/api/v1/__init__.py`, add `idm_tokens` and `provisioning` to the import list and include them:

```python
api_router.include_router(idm_tokens.router)
api_router.include_router(provisioning.router)
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_idm_tokens.py -v`
Expected: PASS (8 tests)

- [ ] **Step 10: Commit**

```bash
git add backend/app/services/idm_token_service.py backend/app/schemas/idm_token.py \
        backend/app/api/v1/idm_tokens.py backend/app/api/v1/provisioning.py \
        backend/app/api/deps.py backend/app/api/v1/__init__.py \
        backend/tests/test_idm_tokens.py
git commit -m "feat(idm): add service tokens with superadmin management and health check"
```

---

### Task 3: Split scoped from unscoped mailbox operations

**Files:**
- Modify: `backend/app/services/mailbox_service.py`
- Modify: `backend/app/services/domain_service.py`
- Test: existing `backend/tests/test_mailboxes_api.py` must pass unmodified

**Interfaces:**
- Produces:
  - `mailbox_service.local_part_taken(db, domain_id, local_part, *, exclude_mailbox_id=None) -> bool`
  - `mailbox_service.maildir_path(domain_name, local_part) -> str`
  - `mailbox_service.create_mailbox_unscoped(db, domain, *, local_part, password_hash, display_name=None, quota_mb=None, is_active=True, commit=True) -> Mailbox`
  - `domain_service.get_by_name(db, name) -> Domain | None`

**Why:** provisioning has no `User` to scope by. Fabricating one to satisfy the existing signatures would bypass domain-ownership checks and pollute the audit trail, so the primitives are extracted instead and both callers share them.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_provisioning_mailbox.py` (create the file):

```python
"""Unscoped mailbox primitives used by provisioning."""
import pytest

from app.services import domain_service, mailbox_service
from app.models.domain import Domain


@pytest.mark.asyncio
async def test_create_mailbox_unscoped_needs_no_user(sessionmaker_):
    async with sessionmaker_() as session:
        domain = Domain(name="acme.test")
        session.add(domain)
        await session.commit()
        await session.refresh(domain)

        mailbox = await mailbox_service.create_mailbox_unscoped(
            session,
            domain,
            local_part="jane",
            password_hash="!unusable",
            display_name="Jane",
            quota_mb=2048,
        )
        assert mailbox.local_part == "jane"
        assert mailbox.maildir_path == "/maildata/acme.test/jane/"
        assert mailbox.is_active is True


@pytest.mark.asyncio
async def test_local_part_taken_can_exclude_self(sessionmaker_):
    async with sessionmaker_() as session:
        domain = Domain(name="acme2.test")
        session.add(domain)
        await session.commit()
        await session.refresh(domain)

        mailbox = await mailbox_service.create_mailbox_unscoped(
            session, domain, local_part="jane", password_hash="!unusable"
        )
        assert await mailbox_service.local_part_taken(session, domain.id, "jane") is True
        assert (
            await mailbox_service.local_part_taken(
                session, domain.id, "jane", exclude_mailbox_id=mailbox.id
            )
            is False
        )


@pytest.mark.asyncio
async def test_domain_get_by_name(sessionmaker_):
    async with sessionmaker_() as session:
        session.add(Domain(name="acme3.test"))
        await session.commit()

        found = await domain_service.get_by_name(session, "acme3.test")
        assert found is not None and found.name == "acme3.test"
        assert await domain_service.get_by_name(session, "nope.test") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_provisioning_mailbox.py -v`
Expected: FAIL — `AttributeError: module 'app.services.mailbox_service' has no attribute 'create_mailbox_unscoped'`

- [ ] **Step 3: Refactor `mailbox_service.py`**

Replace the private helpers at the top of `backend/app/services/mailbox_service.py` with public, unscoped primitives, and make the existing scoped functions delegate. Replace lines 22-38 (`_maildir_path` and `_local_part_taken`) with:

```python
def maildir_path(domain_name: str, local_part: str) -> str:
    """Maildir++ layout: <root>/<domain>/<local_part>/.

    Assigned once at creation and never recomputed. A rename deliberately keeps
    the original path so existing mail follows the user — Dovecot and Postfix
    both read this column rather than deriving the path.
    """
    return f"{settings.MAILDIR_ROOT.rstrip('/')}/{domain_name}/{local_part}/"


async def local_part_taken(
    db: AsyncSession,
    domain_id: uuid.UUID,
    local_part: str,
    *,
    exclude_mailbox_id: uuid.UUID | None = None,
) -> bool:
    """Whether `local_part@domain` is claimed by a mailbox or an alias.

    `exclude_mailbox_id` lets a rename ignore the row being renamed, which
    would otherwise always collide with itself.
    """
    mailbox_stmt = select(Mailbox.id).where(
        Mailbox.domain_id == domain_id, Mailbox.local_part == local_part
    )
    if exclude_mailbox_id is not None:
        mailbox_stmt = mailbox_stmt.where(Mailbox.id != exclude_mailbox_id)
    mb = await db.execute(mailbox_stmt)
    if mb.first():
        return True

    al = await db.execute(
        select(Alias.id).where(Alias.domain_id == domain_id, Alias.local_part == local_part)
    )
    return al.first() is not None


async def create_mailbox_unscoped(
    db: AsyncSession,
    domain: Domain,
    *,
    local_part: str,
    password_hash: str,
    display_name: str | None = None,
    quota_mb: int | None = None,
    is_active: bool = True,
    commit: bool = True,
) -> Mailbox:
    """Create a mailbox with no ownership check and a pre-computed hash.

    The caller is responsible for authorization. Used by the admin path (via
    `create_mailbox`, which checks domain ownership first) and by provisioning,
    which authenticates a service token instead of a user.
    """
    mailbox = Mailbox(
        domain_id=domain.id,
        local_part=local_part,
        password_hash=password_hash,
        display_name=display_name,
        quota_mb=quota_mb if quota_mb is not None else settings.DEFAULT_MAILBOX_QUOTA_MB,
        maildir_path=maildir_path(domain.name, local_part),
        is_active=is_active,
    )
    db.add(mailbox)
    if commit:
        await db.commit()
        await db.refresh(mailbox)
    else:
        await db.flush()
    return mailbox
```

- [ ] **Step 4: Make the scoped functions delegate**

Rewrite `create_mailbox` in the same file to reuse the primitives:

```python
async def create_mailbox(
    db: AsyncSession,
    user: User,
    domain_id: uuid.UUID,
    *,
    local_part: str,
    password: str,
    display_name: str | None = None,
    quota_mb: int | None = None,
) -> Mailbox:
    domain: Domain = await domain_service.get_domain(db, user, domain_id)

    if await local_part_taken(db, domain_id, local_part):
        raise ConflictError(f"{local_part}@{domain.name} already exists (mailbox or alias).")

    return await create_mailbox_unscoped(
        db,
        domain,
        local_part=local_part,
        password_hash=hash_for_dovecot(password),
        display_name=display_name,
        quota_mb=quota_mb,
    )
```

- [ ] **Step 5: Add `get_by_name` to `domain_service.py`**

In `backend/app/services/domain_service.py`, add a public wrapper next to the existing `_get_by_name` and have `_get_by_name` delegate to it (keeping both call sites working):

```python
async def get_by_name(db: AsyncSession, name: str) -> Domain | None:
    """Unscoped lookup by domain name — used by the provisioning path, which
    authenticates a service token rather than a user."""
    result = await db.execute(select(Domain).where(Domain.name == name.strip().lower()))
    return result.scalar_one_or_none()
```

Then replace the body of `_get_by_name` with `return await get_by_name(db, name)`.

- [ ] **Step 6: Run the new tests and the full suite**

Run: `cd backend && python -m pytest tests/test_provisioning_mailbox.py -v && python -m pytest -q`
Expected: PASS. The pre-existing `test_mailboxes_api.py` and `test_aliases_api.py` must pass **unmodified** — that is the check that this was a pure refactor.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/mailbox_service.py backend/app/services/domain_service.py \
        backend/tests/test_provisioning_mailbox.py
git commit -m "refactor(mailbox): extract unscoped primitives from domain-scoped services"
```

---

### Task 4: The provisioning payload schema

**Files:**
- Create: `backend/app/schemas/provisioning.py`
- Test: `backend/tests/test_provisioning_schemas.py`

**Interfaces:**
- Produces:
  - `IdentityStatus` — `Literal["pending", "active", "suspended", "deactivated"]`
  - `AdminSpec` — `role`, `domains`
  - `IdentityUpsert` — the closed payload
  - `IdentityRead` — the response
  - `canonical_hash(payload: IdentityUpsert) -> str`
  - `UNUSABLE_PASSWORD_HASH: str`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_provisioning_schemas.py`:

```python
"""Payload validation — most importantly, that no credential can be sent."""
import pytest
from pydantic import ValidationError

from app.core.dovecot_password import verify_dovecot
from app.core.security import verify_password
from app.schemas.provisioning import (
    UNUSABLE_PASSWORD_HASH,
    IdentityUpsert,
    canonical_hash,
)


def _valid(**overrides):
    base = {"email": "jane@acme.test", "status": "active"}
    base.update(overrides)
    return base


def test_minimal_payload_is_valid():
    payload = IdentityUpsert(**_valid())
    assert payload.email == "jane@acme.test"
    assert payload.status == "active"


@pytest.mark.parametrize(
    "field", ["password", "password_hash", "credential", "secret", "passwd"]
)
def test_any_credential_field_is_rejected(field):
    """The closed schema is what makes 'no credentials' structural."""
    with pytest.raises(ValidationError):
        IdentityUpsert(**_valid(**{field: "hunter2"}))


def test_unknown_field_is_rejected():
    with pytest.raises(ValidationError):
        IdentityUpsert(**_valid(department="Finance"))


@pytest.mark.parametrize("status", ["pending", "active", "suspended", "deactivated"])
def test_all_four_idm_statuses_are_accepted(status):
    assert IdentityUpsert(**_valid(status=status)).status == status


def test_deprovisioned_is_not_a_status():
    """The counterpart system has no such state — reject it loudly rather than
    silently coercing it."""
    with pytest.raises(ValidationError):
        IdentityUpsert(**_valid(status="deprovisioned"))


def test_absent_collection_is_distinguishable_from_empty_one():
    absent = IdentityUpsert(**_valid())
    empty = IdentityUpsert(**_valid(aliases=[]))
    assert "aliases" not in absent.model_fields_set
    assert "aliases" in empty.model_fields_set
    assert empty.aliases == []


def test_absent_admin_is_distinguishable_from_explicit_null():
    absent = IdentityUpsert(**_valid())
    explicit = IdentityUpsert(**_valid(admin=None))
    assert "admin" not in absent.model_fields_set
    assert "admin" in explicit.model_fields_set


def test_canonical_hash_is_stable_and_order_independent():
    a = IdentityUpsert(**_valid(display_name="Jane", quota_mb=1024))
    b = IdentityUpsert(**{"quota_mb": 1024, "display_name": "Jane", **_valid()})
    assert canonical_hash(a) == canonical_hash(b)


def test_canonical_hash_changes_with_content():
    a = IdentityUpsert(**_valid(quota_mb=1024))
    b = IdentityUpsert(**_valid(quota_mb=2048))
    assert canonical_hash(a) != canonical_hash(b)


def test_canonical_hash_distinguishes_absent_from_null():
    absent = IdentityUpsert(**_valid())
    explicit = IdentityUpsert(**_valid(display_name=None))
    assert canonical_hash(absent) != canonical_hash(explicit)


def test_unusable_password_hash_cannot_be_authenticated_against():
    for candidate in ["", "password", UNUSABLE_PASSWORD_HASH, "!"]:
        assert verify_password(candidate, UNUSABLE_PASSWORD_HASH) is False
        assert verify_dovecot(candidate, UNUSABLE_PASSWORD_HASH) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_provisioning_schemas.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.schemas.provisioning'`

- [ ] **Step 3: Write the schemas**

Create `backend/app/schemas/provisioning.py`:

```python
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

from pydantic import BaseModel, ConfigDict, EmailStr, Field

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_provisioning_schemas.py -v`
Expected: PASS (16 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas/provisioning.py backend/tests/test_provisioning_schemas.py
git commit -m "feat(idm): add closed provisioning payload schema forbidding credentials"
```

---

### Task 5: Mailbox convergence

**Files:**
- Create: `backend/app/services/provisioning_service.py`
- Test: `backend/tests/test_provisioning_mailbox.py` (append)

**Interfaces:**
- Consumes: `IdentityUpsert`, `canonical_hash`, `UNUSABLE_PASSWORD_HASH` (Task 4); `mailbox_service.create_mailbox_unscoped`, `local_part_taken` (Task 3); `domain_service.get_by_name` (Task 3).
- Produces:
  - `provisioning_service.upsert_identity(db, external_id, payload, *, token_id=None, ip_address=None) -> IdmIdentity`
  - `provisioning_service.get_identity(db, external_id) -> IdmIdentity`
  - `provisioning_service.STATUS_ACTIVE_MAP: dict[str, bool]`

Aliases and the admin record are handled in Tasks 6 and 7; this task converges the mailbox only and ignores those payload fields.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_provisioning_mailbox.py`:

```python
from datetime import datetime, timezone

from app.models.domain import Domain
from app.models.mailbox import Mailbox
from app.schemas.provisioning import UNUSABLE_PASSWORD_HASH, IdentityUpsert
from app.services import provisioning_service
from app.core.exceptions import ConflictError, NotFoundError
from sqlalchemy import select
import pytest


async def _seed_domain(sessionmaker_, name="corp.test"):
    async with sessionmaker_() as session:
        domain = Domain(name=name)
        session.add(domain)
        await session.commit()
        await session.refresh(domain)
        return domain


def _payload(**overrides):
    base = {"email": "jane@corp.test", "status": "active"}
    base.update(overrides)
    return IdentityUpsert(**base)


@pytest.mark.asyncio
async def test_creates_a_mailbox_with_an_unusable_password(sessionmaker_):
    await _seed_domain(sessionmaker_)
    async with sessionmaker_() as session:
        identity = await provisioning_service.upsert_identity(
            session, "ext-1", _payload(display_name="Jane", quota_mb=4096)
        )
        mailbox = await session.get(Mailbox, identity.mailbox_id)
        assert mailbox.local_part == "jane"
        assert mailbox.display_name == "Jane"
        assert mailbox.quota_mb == 4096
        assert mailbox.is_active is True
        assert mailbox.password_hash == UNUSABLE_PASSWORD_HASH
        assert identity.idm_username is None


@pytest.mark.asyncio
async def test_unknown_domain_is_rejected(sessionmaker_):
    async with sessionmaker_() as session:
        with pytest.raises(NotFoundError):
            await provisioning_service.upsert_identity(
                session, "ext-2", _payload(email="jane@nowhere.test")
            )


@pytest.mark.asyncio
async def test_inactive_domain_is_accepted(sessionmaker_):
    async with sessionmaker_() as session:
        session.add(Domain(name="paused.test", is_active=False))
        await session.commit()

    async with sessionmaker_() as session:
        identity = await provisioning_service.upsert_identity(
            session, "ext-3", _payload(email="jane@paused.test")
        )
        assert identity.mailbox_id is not None


@pytest.mark.asyncio
async def test_repeated_identical_push_is_a_no_op(sessionmaker_):
    await _seed_domain(sessionmaker_, "noop.test")
    payload = _payload(email="jane@noop.test", display_name="Jane")

    async with sessionmaker_() as session:
        identity = await provisioning_service.upsert_identity(session, "ext-4", payload)
        mailbox_id = identity.mailbox_id
        first_updated = (await session.get(Mailbox, mailbox_id)).updated_at
        first_hash = identity.last_payload_hash

    async with sessionmaker_() as session:
        again = await provisioning_service.upsert_identity(session, "ext-4", payload)
        mailbox = await session.get(Mailbox, mailbox_id)
        assert again.last_payload_hash == first_hash
        assert mailbox.updated_at == first_updated
        assert again.last_synced_at is not None


@pytest.mark.asyncio
async def test_rename_preserves_maildir_path(sessionmaker_):
    await _seed_domain(sessionmaker_, "rename.test")
    async with sessionmaker_() as session:
        identity = await provisioning_service.upsert_identity(
            session, "ext-5", _payload(email="jane@rename.test")
        )
        original_path = (await session.get(Mailbox, identity.mailbox_id)).maildir_path

    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session, "ext-5", _payload(email="jane.doe@rename.test")
        )
        mailbox = await session.get(Mailbox, identity.mailbox_id)
        assert mailbox.local_part == "jane.doe"
        assert mailbox.maildir_path == original_path


@pytest.mark.asyncio
async def test_rename_onto_an_existing_address_conflicts(sessionmaker_):
    domain = await _seed_domain(sessionmaker_, "clash.test")
    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session, "ext-6a", _payload(email="taken@clash.test")
        )
        await provisioning_service.upsert_identity(
            session, "ext-6b", _payload(email="jane@clash.test")
        )

    async with sessionmaker_() as session:
        with pytest.raises(ConflictError):
            await provisioning_service.upsert_identity(
                session, "ext-6b", _payload(email="taken@clash.test")
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,expected_active",
    [("pending", False), ("active", True), ("suspended", False), ("deactivated", False)],
)
async def test_status_maps_to_is_active(sessionmaker_, status, expected_active):
    await _seed_domain(sessionmaker_, f"st-{status}.test")
    async with sessionmaker_() as session:
        identity = await provisioning_service.upsert_identity(
            session,
            f"ext-st-{status}",
            _payload(email=f"jane@st-{status}.test", status=status),
        )
        mailbox = await session.get(Mailbox, identity.mailbox_id)
        assert mailbox.is_active is expected_active


@pytest.mark.asyncio
async def test_suspended_does_not_stamp_deactivated_at(sessionmaker_):
    """A suspension is not an offboarding and must not start the retention clock."""
    await _seed_domain(sessionmaker_, "susp.test")
    async with sessionmaker_() as session:
        identity = await provisioning_service.upsert_identity(
            session, "ext-7", _payload(email="jane@susp.test", status="suspended")
        )
        assert identity.deactivated_at is None


@pytest.mark.asyncio
async def test_repeated_deactivation_does_not_move_the_stamp(sessionmaker_):
    await _seed_domain(sessionmaker_, "deact.test")
    async with sessionmaker_() as session:
        identity = await provisioning_service.upsert_identity(
            session, "ext-8", _payload(email="jane@deact.test", status="deactivated")
        )
        first_stamp = identity.deactivated_at
        assert first_stamp is not None

    async with sessionmaker_() as session:
        # Vary an unrelated field so the no-op short circuit does not hide the
        # behaviour under test.
        again = await provisioning_service.upsert_identity(
            session,
            "ext-8",
            _payload(email="jane@deact.test", status="deactivated", display_name="J"),
        )
        assert again.deactivated_at == first_stamp


@pytest.mark.asyncio
async def test_reactivation_clears_the_stamp(sessionmaker_):
    await _seed_domain(sessionmaker_, "react.test")
    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session, "ext-9", _payload(email="jane@react.test", status="deactivated")
        )

    async with sessionmaker_() as session:
        identity = await provisioning_service.upsert_identity(
            session, "ext-9", _payload(email="jane@react.test", status="active")
        )
        mailbox = await session.get(Mailbox, identity.mailbox_id)
        assert identity.deactivated_at is None
        assert mailbox.is_active is True


@pytest.mark.asyncio
async def test_absent_scalar_leaves_the_value_unchanged(sessionmaker_):
    await _seed_domain(sessionmaker_, "scalar.test")
    async with sessionmaker_() as session:
        identity = await provisioning_service.upsert_identity(
            session, "ext-10", _payload(email="jane@scalar.test", display_name="Jane")
        )

    async with sessionmaker_() as session:
        # display_name omitted entirely — must not be cleared.
        await provisioning_service.upsert_identity(
            session, "ext-10", _payload(email="jane@scalar.test", quota_mb=8192)
        )
        mailbox = await session.get(Mailbox, identity.mailbox_id)
        assert mailbox.display_name == "Jane"
        assert mailbox.quota_mb == 8192


@pytest.mark.asyncio
async def test_explicit_null_clears_a_nullable_scalar(sessionmaker_):
    await _seed_domain(sessionmaker_, "clear.test")
    async with sessionmaker_() as session:
        identity = await provisioning_service.upsert_identity(
            session, "ext-11", _payload(email="jane@clear.test", display_name="Jane")
        )

    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session, "ext-11", _payload(email="jane@clear.test", display_name=None)
        )
        mailbox = await session.get(Mailbox, identity.mailbox_id)
        assert mailbox.display_name is None


@pytest.mark.asyncio
async def test_username_is_captured_when_sent(sessionmaker_):
    await _seed_domain(sessionmaker_, "uname.test")
    async with sessionmaker_() as session:
        identity = await provisioning_service.upsert_identity(
            session, "ext-12", _payload(email="jane@uname.test", username="jdoe")
        )
        assert identity.idm_username == "jdoe"


@pytest.mark.asyncio
async def test_get_identity_raises_for_unknown_external_id(sessionmaker_):
    async with sessionmaker_() as session:
        with pytest.raises(NotFoundError):
            await provisioning_service.get_identity(session, "never-seen")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_provisioning_mailbox.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.provisioning_service'`

- [ ] **Step 3: Write the convergence service**

Create `backend/app/services/provisioning_service.py`:

```python
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

from app.core.exceptions import ConflictError, NotFoundError
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
        raise NotFoundError(f"Domain {domain_name} is not hosted here.")

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
    if "quota_mb" in fields_set and payload.quota_mb is not None:
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_provisioning_mailbox.py -v`
Expected: PASS (all tests including the three from Task 3)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/provisioning_service.py backend/tests/test_provisioning_mailbox.py
git commit -m "feat(idm): converge mailboxes from declarative identity payloads"
```

---

### Task 6: Alias convergence

**Files:**
- Modify: `backend/app/services/provisioning_service.py`
- Test: `backend/tests/test_provisioning_aliases.py`

**Interfaces:**
- Consumes: `IdmIdentityAlias` (Task 1); `Alias` from `app.models.alias`.
- Produces: `provisioning_service._converge_aliases(db, identity, mailbox, domain_name, payload) -> None`, called from `upsert_identity`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_provisioning_aliases.py`:

```python
"""IdM-owned alias convergence."""
import pytest
from sqlalchemy import select

from app.core.exceptions import ConflictError, NotFoundError
from app.models.alias import Alias
from app.models.domain import Domain
from app.models.idm import IdmIdentityAlias
from app.schemas.provisioning import IdentityUpsert
from app.services import provisioning_service


async def _seed_domain(sessionmaker_, name):
    async with sessionmaker_() as session:
        domain = Domain(name=name)
        session.add(domain)
        await session.commit()
        await session.refresh(domain)
        return domain


def _payload(email, **overrides):
    base = {"email": email, "status": "active"}
    base.update(overrides)
    return IdentityUpsert(**base)


async def _alias_local_parts(session, domain_id):
    rows = await session.execute(
        select(Alias.local_part).where(Alias.domain_id == domain_id)
    )
    return sorted(r[0] for r in rows)


@pytest.mark.asyncio
async def test_aliases_are_created_and_pointed_at_the_mailbox(sessionmaker_):
    domain = await _seed_domain(sessionmaker_, "al1.test")
    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session,
            "al-1",
            _payload("jane@al1.test", aliases=["j.doe@al1.test", "jd@al1.test"]),
        )
        assert await _alias_local_parts(session, domain.id) == ["j.doe", "jd"]
        alias = (
            await session.execute(select(Alias).where(Alias.local_part == "j.doe"))
        ).scalar_one()
        assert alias.destination == "jane@al1.test"


@pytest.mark.asyncio
async def test_alias_set_is_authoritative_when_present(sessionmaker_):
    domain = await _seed_domain(sessionmaker_, "al2.test")
    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session, "al-2", _payload("jane@al2.test", aliases=["a@al2.test", "b@al2.test"])
        )
    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session, "al-2", _payload("jane@al2.test", aliases=["b@al2.test"])
        )
        assert await _alias_local_parts(session, domain.id) == ["b"]


@pytest.mark.asyncio
async def test_omitted_alias_field_leaves_them_untouched(sessionmaker_):
    domain = await _seed_domain(sessionmaker_, "al3.test")
    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session, "al-3", _payload("jane@al3.test", aliases=["a@al3.test"])
        )
    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session, "al-3", _payload("jane@al3.test", display_name="Jane")
        )
        assert await _alias_local_parts(session, domain.id) == ["a"]


@pytest.mark.asyncio
async def test_empty_alias_list_removes_all_idm_owned_aliases(sessionmaker_):
    domain = await _seed_domain(sessionmaker_, "al4.test")
    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session, "al-4", _payload("jane@al4.test", aliases=["a@al4.test"])
        )
    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session, "al-4", _payload("jane@al4.test", aliases=[])
        )
        assert await _alias_local_parts(session, domain.id) == []


@pytest.mark.asyncio
async def test_admin_created_alias_is_never_adopted_or_removed(sessionmaker_):
    domain = await _seed_domain(sessionmaker_, "al5.test")
    async with sessionmaker_() as session:
        session.add(
            Alias(domain_id=domain.id, local_part="handmade", destination="someone@al5.test")
        )
        await session.commit()

    async with sessionmaker_() as session:
        with pytest.raises(ConflictError):
            await provisioning_service.upsert_identity(
                session, "al-5", _payload("jane@al5.test", aliases=["handmade@al5.test"])
            )

    async with sessionmaker_() as session:
        # The admin's alias survives untouched, and nothing was half-written.
        assert await _alias_local_parts(session, domain.id) == ["handmade"]
        links = (await session.execute(select(IdmIdentityAlias))).all()
        assert links == []


@pytest.mark.asyncio
async def test_alias_in_an_unhosted_domain_is_rejected(sessionmaker_):
    await _seed_domain(sessionmaker_, "al6.test")
    async with sessionmaker_() as session:
        with pytest.raises(NotFoundError):
            await provisioning_service.upsert_identity(
                session, "al-6", _payload("jane@al6.test", aliases=["j@elsewhere.test"])
            )


@pytest.mark.asyncio
async def test_a_rename_repoints_alias_destinations(sessionmaker_):
    await _seed_domain(sessionmaker_, "al7.test")
    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session, "al-7", _payload("jane@al7.test", aliases=["j@al7.test"])
        )
    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session, "al-7", _payload("jane.doe@al7.test", aliases=["j@al7.test"])
        )
        alias = (
            await session.execute(select(Alias).where(Alias.local_part == "j"))
        ).scalar_one()
        assert alias.destination == "jane.doe@al7.test"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_provisioning_aliases.py -v`
Expected: FAIL — aliases are ignored, so `test_aliases_are_created…` fails with an empty list.

- [ ] **Step 3: Implement alias convergence**

In `backend/app/services/provisioning_service.py`, add the imports:

```python
from app.models.alias import Alias
from app.models.idm import IdmIdentity, IdmIdentityAlias
```

and add this function:

```python
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

    # An omitted collection means "leave untouched" — only the destination is
    # refreshed, so a rename still repoints aliases the IdM already owns.
    if "aliases" not in payload.model_fields_set or payload.aliases is None:
        for alias in owned:
            alias.destination = destination
        return

    desired: dict[tuple[uuid.UUID, str], str] = {}
    for address in payload.aliases:
        alias_local, alias_domain_name = _split_address(address)
        alias_domain = await domain_service.get_by_name(db, alias_domain_name)
        if alias_domain is None:
            raise NotFoundError(f"Alias domain {alias_domain_name} is not hosted here.")
        desired[(alias_domain.id, alias_local)] = address

    owned_by_key = {(a.domain_id, a.local_part): a for a in owned}

    for key, alias in owned_by_key.items():
        if key not in desired:
            await db.execute(
                IdmIdentityAlias.__table__.delete().where(
                    IdmIdentityAlias.alias_id == alias.id
                )
            )
            await db.delete(alias)

    for (domain_id, alias_local), _address in desired.items():
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
            raise ConflictError(
                f"{alias_local}@… already exists and is not managed by the IdM."
            )
        if await mailbox_service.local_part_taken(db, domain_id, alias_local):
            raise ConflictError(f"{alias_local}@… already exists (mailbox or alias).")

        alias = Alias(
            domain_id=domain_id, local_part=alias_local, destination=destination
        )
        db.add(alias)
        await db.flush()
        db.add(IdmIdentityAlias(identity_id=identity.id, alias_id=alias.id))
```

- [ ] **Step 4: Call it from `upsert_identity`**

In `upsert_identity`, replace the line `await _converge_mailbox(db, identity, domain, local_part, payload)` with:

```python
    await _converge_mailbox(db, identity, domain, local_part, payload)

    mailbox = await db.get(Mailbox, identity.mailbox_id)
    if mailbox is not None:
        await _converge_aliases(db, identity, mailbox, domain.name, payload)
    elif payload.aliases:
        # An alias must forward somewhere; without a mailbox there is no
        # destination to point at.
        raise ConflictError("Cannot set aliases on an identity with no mailbox.")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_provisioning_aliases.py tests/test_provisioning_mailbox.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/provisioning_service.py backend/tests/test_provisioning_aliases.py
git commit -m "feat(idm): converge IdM-owned aliases without touching admin-created ones"
```

---

### Task 7: Admin record convergence

**Files:**
- Modify: `backend/app/services/provisioning_service.py`
- Test: `backend/tests/test_provisioning_admin.py`

**Interfaces:**
- Consumes: `User`, `UserRole`, `Domain`; `UNUSABLE_PASSWORD_HASH`.
- Produces: `provisioning_service._converge_admin(db, identity, email, payload) -> None`, called from `upsert_identity`. `email` is the lower-cased address from the payload.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_provisioning_admin.py`:

```python
"""Admin (control-plane user) convergence."""
import pytest
from sqlalchemy import select

from app.core.exceptions import NotFoundError
from app.core.security import verify_password
from app.models.domain import Domain
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.provisioning import UNUSABLE_PASSWORD_HASH, IdentityUpsert
from app.services import provisioning_service


async def _seed_domain(sessionmaker_, name):
    async with sessionmaker_() as session:
        domain = Domain(name=name)
        session.add(domain)
        await session.commit()
        await session.refresh(domain)
        return domain


def _payload(email, **overrides):
    base = {"email": email, "status": "active"}
    base.update(overrides)
    return IdentityUpsert(**base)


@pytest.mark.asyncio
async def test_admin_block_creates_a_user_with_an_unusable_password(sessionmaker_):
    domain = await _seed_domain(sessionmaker_, "ad1.test")
    async with sessionmaker_() as session:
        identity = await provisioning_service.upsert_identity(
            session,
            "ad-1",
            _payload(
                "jane@ad1.test",
                admin={"role": "domain_admin", "domains": ["ad1.test"]},
            ),
        )
        user = await session.get(User, identity.user_id)
        assert user.email == "jane@ad1.test"
        assert user.role is UserRole.DOMAIN_ADMIN
        assert user.is_active is True
        assert user.password_hash == UNUSABLE_PASSWORD_HASH
        assert verify_password("anything", user.password_hash) is False

        owned = await session.get(Domain, domain.id)
        assert owned.owner_id == user.id


@pytest.mark.asyncio
async def test_role_change_is_applied(sessionmaker_):
    await _seed_domain(sessionmaker_, "ad2.test")
    async with sessionmaker_() as session:
        identity = await provisioning_service.upsert_identity(
            session, "ad-2", _payload("jane@ad2.test", admin={"role": "domain_admin"})
        )
    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session, "ad-2", _payload("jane@ad2.test", admin={"role": "superadmin"})
        )
        user = await session.get(User, identity.user_id)
        assert user.role is UserRole.SUPERADMIN


@pytest.mark.asyncio
async def test_explicit_null_admin_deactivates_without_deleting(sessionmaker_):
    await _seed_domain(sessionmaker_, "ad3.test")
    async with sessionmaker_() as session:
        identity = await provisioning_service.upsert_identity(
            session, "ad-3", _payload("jane@ad3.test", admin={"role": "domain_admin"})
        )
        user_id = identity.user_id

    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session, "ad-3", _payload("jane@ad3.test", admin=None)
        )
        user = await session.get(User, user_id)
        assert user is not None, "the row must survive — audit FKs depend on it"
        assert user.is_active is False


@pytest.mark.asyncio
async def test_omitted_admin_leaves_the_record_alone(sessionmaker_):
    await _seed_domain(sessionmaker_, "ad4.test")
    async with sessionmaker_() as session:
        identity = await provisioning_service.upsert_identity(
            session, "ad-4", _payload("jane@ad4.test", admin={"role": "domain_admin"})
        )
    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session, "ad-4", _payload("jane@ad4.test", display_name="Jane")
        )
        user = await session.get(User, identity.user_id)
        assert user.is_active is True
        assert user.role is UserRole.DOMAIN_ADMIN


@pytest.mark.asyncio
async def test_deactivated_status_also_deactivates_the_admin_record(sessionmaker_):
    await _seed_domain(sessionmaker_, "ad5.test")
    async with sessionmaker_() as session:
        identity = await provisioning_service.upsert_identity(
            session, "ad-5", _payload("jane@ad5.test", admin={"role": "domain_admin"})
        )
    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session,
            "ad-5",
            _payload("jane@ad5.test", status="deactivated", admin={"role": "domain_admin"}),
        )
        user = await session.get(User, identity.user_id)
        assert user.is_active is False


@pytest.mark.asyncio
async def test_unknown_admin_domain_is_rejected(sessionmaker_):
    await _seed_domain(sessionmaker_, "ad6.test")
    async with sessionmaker_() as session:
        with pytest.raises(NotFoundError):
            await provisioning_service.upsert_identity(
                session,
                "ad-6",
                _payload(
                    "jane@ad6.test",
                    admin={"role": "domain_admin", "domains": ["nope.test"]},
                ),
            )


@pytest.mark.asyncio
async def test_a_rename_moves_the_admin_email(sessionmaker_):
    await _seed_domain(sessionmaker_, "ad7.test")
    async with sessionmaker_() as session:
        identity = await provisioning_service.upsert_identity(
            session, "ad-7", _payload("jane@ad7.test", admin={"role": "domain_admin"})
        )
    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session, "ad-7", _payload("jane.doe@ad7.test", admin={"role": "domain_admin"})
        )
        user = await session.get(User, identity.user_id)
        assert user.email == "jane.doe@ad7.test"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_provisioning_admin.py -v`
Expected: FAIL — `identity.user_id` is `None`.

- [ ] **Step 3: Implement admin convergence**

In `backend/app/services/provisioning_service.py`, add imports:

```python
from app.models.domain import Domain
from app.models.enums import UserRole
from app.models.user import User
```

and add:

```python
_ADMIN_ROLES = {
    "superadmin": UserRole.SUPERADMIN,
    "domain_admin": UserRole.DOMAIN_ADMIN,
}


async def _converge_admin(
    db: AsyncSession, identity: IdmIdentity, email: str, payload: IdentityUpsert
) -> None:
    """Reconcile the control-plane user record.

    Provisioned admins never get a usable password: they authenticate via SSO
    in phase 2, and this row exists so an SSO login has something to resolve
    to — the same shape the counterpart system's own console requires (a local
    row plus a role grant before authorization works).
    """
    if "admin" not in payload.model_fields_set:
        return

    user: User | None = (
        await db.get(User, identity.user_id) if identity.user_id is not None else None
    )

    if payload.admin is None:
        # Deactivate, never delete: audit entries reference this row.
        if user is not None:
            user.is_active = False
        return

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

    user.is_active = STATUS_ACTIVE_MAP[payload.status]

    for domain_name in payload.admin.domains:
        domain = await domain_service.get_by_name(db, domain_name)
        if domain is None:
            raise NotFoundError(f"Domain {domain_name} is not hosted here.")
        domain.owner_id = user.id
```

- [ ] **Step 4: Call it from `upsert_identity`**

In `upsert_identity`, after the alias block and before `identity.last_payload_hash = payload_hash`, add:

```python
    await _converge_admin(db, identity, str(payload.email).lower(), payload)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_provisioning_admin.py -v`
Expected: PASS (7 tests)

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/provisioning_service.py backend/tests/test_provisioning_admin.py
git commit -m "feat(idm): converge admin records with unusable passwords for SSO"
```

---

### Task 8: Provisioning routes, audit, and rollback guarantee

**Files:**
- Modify: `backend/app/api/v1/provisioning.py`
- Modify: `backend/app/services/provisioning_service.py`
- Test: `backend/tests/test_provisioning_api.py`

**Interfaces:**
- Consumes: everything from Tasks 4-7; `audit_service.record`, `ActorType.IDM`.
- Produces: `PUT`/`GET /api/v1/provisioning/identities/{external_id}` returning `IdentityRead`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_provisioning_api.py`:

```python
"""End-to-end provisioning routes, audit, and transactional rollback."""
import pytest
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.domain import Domain
from app.models.enums import ActorType
from app.models.idm import IdmIdentity
from app.models.mailbox import Mailbox
from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD, login_headers


async def _service_token(client, headers) -> str:
    resp = await client.post(
        "/api/v1/idm/tokens", json={"name": "connector"}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["token"]


@pytest.fixture
async def svc(client, superadmin, sessionmaker_):
    admin_headers = await login_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    raw = await _service_token(client, admin_headers)
    async with sessionmaker_() as session:
        session.add(Domain(name="api.test"))
        await session.commit()
    return {"Authorization": f"Bearer {raw}"}


@pytest.mark.asyncio
async def test_upsert_creates_and_returns_state(client, svc):
    resp = await client.put(
        "/api/v1/provisioning/identities/ext-100",
        json={"email": "jane@api.test", "status": "active", "display_name": "Jane"},
        headers=svc,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["external_id"] == "ext-100"
    assert body["email"] == "jane@api.test"
    assert body["status"] == "active"
    assert body["mailbox_id"] is not None


@pytest.mark.asyncio
async def test_upsert_is_idempotent_over_http(client, svc):
    payload = {"email": "jane@api.test", "status": "active"}
    first = await client.put(
        "/api/v1/provisioning/identities/ext-101", json=payload, headers=svc
    )
    second = await client.put(
        "/api/v1/provisioning/identities/ext-101", json=payload, headers=svc
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["mailbox_id"] == second.json()["mailbox_id"]


@pytest.mark.asyncio
async def test_credential_field_is_rejected_over_http(client, svc):
    resp = await client.put(
        "/api/v1/provisioning/identities/ext-102",
        json={"email": "jane@api.test", "status": "active", "password": "hunter2"},
        headers=svc,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_unknown_domain_is_422_not_500(client, svc):
    resp = await client.put(
        "/api/v1/provisioning/identities/ext-103",
        json={"email": "jane@unhosted.test", "status": "active"},
        headers=svc,
    )
    assert resp.status_code == 422
    assert "unhosted.test" in resp.text


@pytest.mark.asyncio
async def test_collision_is_409(client, svc):
    await client.put(
        "/api/v1/provisioning/identities/ext-104a",
        json={"email": "taken@api.test", "status": "active"},
        headers=svc,
    )
    await client.put(
        "/api/v1/provisioning/identities/ext-104b",
        json={"email": "other@api.test", "status": "active"},
        headers=svc,
    )
    resp = await client.put(
        "/api/v1/provisioning/identities/ext-104b",
        json={"email": "taken@api.test", "status": "active"},
        headers=svc,
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_get_returns_state_and_404s_for_unknown(client, svc):
    await client.put(
        "/api/v1/provisioning/identities/ext-105",
        json={"email": "jane@api.test", "status": "active"},
        headers=svc,
    )
    found = await client.get("/api/v1/provisioning/identities/ext-105", headers=svc)
    assert found.status_code == 200
    assert found.json()["external_id"] == "ext-105"

    missing = await client.get("/api/v1/provisioning/identities/nope", headers=svc)
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_delete_verb_does_not_exist(client, svc):
    """The counterpart system has no delete; offboarding is a status change."""
    resp = await client.delete("/api/v1/provisioning/identities/ext-105", headers=svc)
    assert resp.status_code == 405


@pytest.mark.asyncio
async def test_audit_entry_written_once_per_real_change(client, svc, sessionmaker_):
    payload = {"email": "audited@api.test", "status": "active"}
    await client.put(
        "/api/v1/provisioning/identities/ext-106", json=payload, headers=svc
    )
    await client.put(
        "/api/v1/provisioning/identities/ext-106", json=payload, headers=svc
    )

    async with sessionmaker_() as session:
        rows = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "idm.identity.synced")
            )
        ).scalars().all()
        assert len(rows) == 1, "a no-op push must not write an audit entry"
        assert rows[0].actor_type is ActorType.IDM


@pytest.mark.asyncio
async def test_a_failed_sync_writes_nothing_at_all(client, svc, sessionmaker_):
    """A conflict part-way through must leave no mailbox, identity, or audit row."""
    await client.put(
        "/api/v1/provisioning/identities/ext-107a",
        json={"email": "occupied@api.test", "status": "active"},
        headers=svc,
    )
    resp = await client.put(
        "/api/v1/provisioning/identities/ext-107b",
        json={"email": "occupied@api.test", "status": "active"},
        headers=svc,
    )
    assert resp.status_code == 409

    async with sessionmaker_() as session:
        identity = (
            await session.execute(
                select(IdmIdentity).where(IdmIdentity.external_id == "ext-107b")
            )
        ).scalar_one_or_none()
        assert identity is None
        mailboxes = (
            await session.execute(
                select(Mailbox).where(Mailbox.local_part == "occupied")
            )
        ).scalars().all()
        assert len(mailboxes) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "active", "suspended", "deactivated"])
async def test_status_round_trips_through_get(client, svc, status):
    """All four statuses must survive a write/read cycle. `pending` and
    `suspended` are both inactive-and-unstamped, so a derived status would
    collapse them."""
    await client.put(
        f"/api/v1/provisioning/identities/ext-st-{status}",
        json={"email": f"u{status}@api.test", "status": status},
        headers=svc,
    )
    resp = await client.get(
        f"/api/v1/provisioning/identities/ext-st-{status}", headers=svc
    )
    assert resp.json()["status"] == status


@pytest.mark.asyncio
async def test_provisioning_requires_a_token(client, svc):
    resp = await client.put(
        "/api/v1/provisioning/identities/ext-108",
        json={"email": "jane@api.test", "status": "active"},
    )
    assert resp.status_code == 403
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_provisioning_api.py -v`
Expected: FAIL — 405/404 on the identities routes, which do not exist yet.

- [ ] **Step 3: Make convergence failures roll back cleanly**

`upsert_identity` currently flushes a new `IdmIdentity` before convergence can fail. Wrap the whole body so nothing is committed unless everything succeeded. In `backend/app/services/provisioning_service.py`, change `upsert_identity` to roll back on any failure:

```python
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
        await db.rollback()
        raise
```

Rename the existing body to `_upsert_identity_inner` with the same signature.

- [ ] **Step 4: Add the audit write**

Inside `_upsert_identity_inner`, immediately before the final `await db.commit()` (the one after `identity.last_synced_at = ...`), add:

```python
    from app.models.enums import ActorType
    from app.services import audit_service

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
        },
        ip_address=ip_address,
        # Shares this transaction: the log must never outlive a rollback.
        commit=False,
    )
```

Note the no-op branch returns before reaching this, so an unchanged push writes no audit row.

- [ ] **Step 5: Map `NotFoundError` from convergence to 422 where the spec requires it**

The spec returns `422` for an unhosted domain, not `404`. Add a dedicated exception in `backend/app/core/exceptions.py`:

```python
class UnprocessableError(AppError):
    """The request is well-formed but names something that cannot be used."""

    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "unprocessable"
```

Then in `provisioning_service.py`, replace the three domain-lookup failures (`Domain … is not hosted here`, `Alias domain … is not hosted here`, and the `admin.domains` one) with `UnprocessableError`, and the "no mailbox for aliases" `ConflictError` with `UnprocessableError`. Update the imports accordingly.

Adjust `tests/test_provisioning_mailbox.py`, `tests/test_provisioning_aliases.py`, and `tests/test_provisioning_admin.py` to expect `UnprocessableError` instead of `NotFoundError` in exactly those cases. `get_identity` keeps raising `NotFoundError` — an unknown `external_id` really is a 404.

- [ ] **Step 6: Write the routes**

Replace `backend/app/api/v1/provisioning.py` with:

```python
"""IdM provisioning endpoints (service-token authenticated).

These routes are blocked at the Nginx edge and are reachable only on the
internal Docker network — see docker/nginx/templates/10-https.conf.template.

There is deliberately no DELETE: the counterpart system has no delete
operation, `deactivated` is terminal there, and offboarding arrives as an
ordinary upsert carrying that status. Adding a delete verb here would invent a
lifecycle the source of truth does not have.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from app.api.deps import DbDep, get_client_ip, require_service_token
from app.models.alias import Alias
from app.models.domain import Domain
from app.models.idm import IdmIdentity, IdmIdentityAlias, IdmServiceToken
from app.models.mailbox import Mailbox
from app.schemas.provisioning import IdentityRead, IdentityUpsert
from app.services import provisioning_service

router = APIRouter(prefix="/provisioning", tags=["provisioning"])

ServiceToken = Annotated[IdmServiceToken, Depends(require_service_token)]


@router.get("/health")
async def health(token: ServiceToken) -> dict[str, str]:
    """Validates the caller's token and touches nothing else."""
    return {"status": "ok"}


async def _to_read(db, identity: IdmIdentity) -> IdentityRead:
    email = None
    if identity.mailbox_id is not None:
        mailbox = await db.get(Mailbox, identity.mailbox_id)
        if mailbox is not None:
            domain = await db.get(Domain, mailbox.domain_id)
            email = f"{mailbox.local_part}@{domain.name}" if domain else None

    rows = (
        await db.execute(
            select(Alias, Domain.name)
            .join(IdmIdentityAlias, IdmIdentityAlias.alias_id == Alias.id)
            .join(Domain, Alias.domain_id == Domain.id)
            .where(IdmIdentityAlias.identity_id == identity.id)
        )
    ).all()
    aliases = sorted(f"{alias.local_part}@{name}" for alias, name in rows)

    return IdentityRead(
        external_id=identity.external_id,
        email=email,
        status=identity.status,
        mailbox_id=identity.mailbox_id,
        user_id=identity.user_id,
        aliases=aliases,
        last_synced_at=identity.last_synced_at,
        deactivated_at=identity.deactivated_at,
    )


@router.put("/identities/{external_id}", response_model=IdentityRead)
async def upsert_identity(
    external_id: str,
    payload: IdentityUpsert,
    request: Request,
    token: ServiceToken,
    db: DbDep,
) -> IdentityRead:
    identity = await provisioning_service.upsert_identity(
        db,
        external_id,
        payload,
        token_id=token.id,
        ip_address=get_client_ip(request),
    )
    return await _to_read(db, identity)


@router.get("/identities/{external_id}", response_model=IdentityRead)
async def get_identity(
    external_id: str, token: ServiceToken, db: DbDep
) -> IdentityRead:
    identity = await provisioning_service.get_identity(db, external_id)
    return await _to_read(db, identity)
```

**Note on rate limiting:** the spec calls for reusing `app/core/ratelimit.py`. No work is needed — `limiter` is constructed with `default_limits=[f"{RATE_LIMIT_API_PER_MINUTE}/minute"]` (`ratelimit.py:12-17`), which applies app-wide, so provisioning routes inherit it automatically. Do not add a per-route decorator.

- [ ] **Step 7: Run the full suite**

Run: `cd backend && python -m pytest -q`
Expected: PASS — every pre-existing test plus all new ones.

- [ ] **Step 8: Commit**

```bash
git add backend/app/api/v1/provisioning.py backend/app/services/provisioning_service.py \
        backend/app/core/exceptions.py backend/tests/
git commit -m "feat(idm): add provisioning routes with audit and single-transaction rollback"
```

---

### Task 9: Align Dovecot with `maildir_path` and block provisioning at the edge

**Files:**
- Modify: `docker/dovecot/dovecot-sql.conf.ext.tmpl:18-25`
- Modify: `docker/nginx/templates/10-https.conf.template`
- Test: `backend/tests/test_mail_config_templates.py`

**Why this must ship with the rename feature:** Postfix's `virtual_mailbox_maps.cf:6` reads the authoritative `m.maildir_path`, but Dovecot's `user_query` recomputes the path from `domain/local_part`. They agree today only because `maildir_path` is always derived that way at creation. The first rename would point Dovecot at a new empty directory and orphan the user's mail.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_mail_config_templates.py`:

```python
"""Regression guards on mail daemon and edge config.

These are config files rather than Python, but two properties are load-bearing
enough to pin: Dovecot must not recompute a path the database owns, and the
provisioning API must not be reachable from the internet.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOVECOT_SQL = REPO_ROOT / "docker" / "dovecot" / "dovecot-sql.conf.ext.tmpl"
NGINX_HTTPS = REPO_ROOT / "docker" / "nginx" / "templates" / "10-https.conf.template"


def test_dovecot_reads_maildir_path_rather_than_deriving_it():
    """A rename changes local_part but keeps maildir_path, so a derived path
    would send Dovecot to a new empty directory and orphan the user's mail."""
    content = DOVECOT_SQL.read_text(encoding="utf-8")
    user_query = content.split("user_query")[1]
    assert "m.maildir_path" in user_query
    assert "'/maildata/' || d.name" not in user_query


def test_provisioning_is_not_exposed_at_the_edge():
    """Provisioning is reachable only on the internal Docker network."""
    content = NGINX_HTTPS.read_text(encoding="utf-8")
    assert "/api/v1/provisioning/" in content
    provisioning_block = content.split("/api/v1/provisioning/")[1].split("}")[0]
    assert "return 404" in provisioning_block
    assert "proxy_pass" not in provisioning_block


def test_provisioning_block_precedes_the_general_api_block():
    """Nginx prefix matching takes the longest match, but ordering the block
    first keeps the intent obvious to the next reader."""
    content = NGINX_HTTPS.read_text(encoding="utf-8")
    assert content.index("/api/v1/provisioning/") < content.index("location /api/ {")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_mail_config_templates.py -v`
Expected: FAIL — `user_query` still derives the path; nginx has no provisioning block.

- [ ] **Step 3: Fix the Dovecot template**

In `docker/dovecot/dovecot-sql.conf.ext.tmpl`, replace the `user_query` block (lines 18-25) with:

```
# `maildir_path` is the single authority for where a mailbox lives — Postfix's
# virtual_mailbox_maps.cf already reads it. Deriving the path here instead
# would break renames: an IdM-driven rename changes local_part but deliberately
# keeps maildir_path so existing mail follows the user.
user_query = \
  SELECT m.maildir_path AS home, \
         'maildir:' || m.maildir_path AS mail, \
         5000 AS uid, 5000 AS gid, \
         ('*:bytes=' || (m.quota_mb::bigint * 1048576)) AS quota_rule \
  FROM mailboxes m JOIN domains d ON m.domain_id = d.id \
  WHERE m.local_part = '%n' AND d.name = '%d' \
    AND m.is_active = true AND d.is_active = true
```

- [ ] **Step 4: Block provisioning at the Nginx edge**

In `docker/nginx/templates/10-https.conf.template`, insert this block immediately before the `# --- General API ---` section:

```
    # --- IdM provisioning: internal network only -----------------------------
    # Service-token authenticated and reachable only from inside the Docker
    # network, so a leaked token is useless from the internet. Longest-prefix
    # matching means this wins over `location /api/`.
    location /api/v1/provisioning/ {
        return 404;
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_mail_config_templates.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Verify the rendered config is valid**

Run: `docker compose config -q`
Expected: no output (compose file parses).

Run: `docker compose up -d --build nginx dovecot && docker compose exec nginx nginx -t`
Expected: `syntax is ok` / `test is successful`.

If Docker is unavailable in this environment, record that this step was skipped and must be run before deploy — do not mark it complete.

- [ ] **Step 7: Run the full suite**

Run: `cd backend && python -m pytest -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add docker/dovecot/dovecot-sql.conf.ext.tmpl \
        docker/nginx/templates/10-https.conf.template \
        backend/tests/test_mail_config_templates.py
git commit -m "fix(mail): make maildir_path authoritative for Dovecot and hide provisioning from the edge"
```

---

## Definition of Done

- [ ] Three tables and the `idm` actor type exist, with a reversible migration
- [ ] Service tokens can be issued, listed, and revoked by a superadmin; the raw value appears exactly once
- [ ] A provisioning token cannot satisfy an operator endpoint, and an operator JWT cannot satisfy a provisioning one
- [ ] No credential field is accepted in any payload — asserted, not assumed
- [ ] A repeated identical push writes nothing: no row update, no audit entry
- [ ] A rename preserves `maildir_path`, and Dovecot reads that column
- [ ] All four IdM statuses map correctly; `suspended` never stamps `deactivated_at`; repeated `deactivated` never moves it; `active` clears it
- [ ] IdM-owned aliases converge; admin-created aliases are never adopted or removed
- [ ] Admin records are created with an unusable password and deactivated rather than deleted
- [ ] A failed convergence leaves no partial write of any kind
- [ ] `/api/v1/provisioning/` returns 404 at the edge
- [ ] Full suite green: `cd backend && python -m pytest -q`

## Carried forward, still open

- **Phase 2 (Keycloak OIDC + app passwords)** is what makes a provisioned mailbox usable. Until then, an admin sets mailbox passwords by hand through the existing dashboard.
- **The break-glass decision** — whether the bootstrap `ADMIN_EMAIL`/`ADMIN_PASSWORD` superadmin stays password-capable once SSO lands. Recorded in the spec; not a phase 1 decision.
- **The counterpart connector** (`D:\identity-manager`) defines the calling side. The error taxonomy is a shared agreement: `409`/`422` are permanent failures its worker should dead-letter, `5xx` is retriable. Confirm both sides agree before either ships.
- **Concurrency:** `SELECT … FOR UPDATE` on the identity row is specified in the design but not implemented here, because SQLite (the test database) does not support it. Add it with a Postgres-only integration test before running two connector workers against one mail server.
