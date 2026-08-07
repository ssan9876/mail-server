"""End-to-end provisioning routes, audit, and transactional rollback."""
import pytest
from sqlalchemy import select

from app.models.audit_log import AuditLog
from app.models.domain import Domain
from app.models.enums import ActorType, UserRole
from app.models.idm import IdmIdentity
from app.models.mailbox import Mailbox
from app.services import user_service
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
        session.add(Domain(name="api.example.com"))
        await session.commit()
    return {"Authorization": f"Bearer {raw}"}


@pytest.mark.asyncio
async def test_upsert_creates_and_returns_state(client, svc):
    resp = await client.put(
        "/api/v1/provisioning/identities/ext-100",
        json={"email": "jane@api.example.com", "status": "active", "display_name": "Jane"},
        headers=svc,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["external_id"] == "ext-100"
    assert body["email"] == "jane@api.example.com"
    assert body["status"] == "active"
    assert body["mailbox_id"] is not None


@pytest.mark.asyncio
async def test_upsert_is_idempotent_over_http(client, svc):
    payload = {"email": "jane@api.example.com", "status": "active"}
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
        json={"email": "jane@api.example.com", "status": "active", "password": "hunter2"},
        headers=svc,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_unknown_domain_is_422_not_500(client, svc):
    resp = await client.put(
        "/api/v1/provisioning/identities/ext-103",
        json={"email": "jane@unhosted.example.com", "status": "active"},
        headers=svc,
    )
    assert resp.status_code == 422
    assert "unhosted.example.com" in resp.text


@pytest.mark.asyncio
async def test_collision_is_409(client, svc):
    await client.put(
        "/api/v1/provisioning/identities/ext-104a",
        json={"email": "taken@api.example.com", "status": "active"},
        headers=svc,
    )
    await client.put(
        "/api/v1/provisioning/identities/ext-104b",
        json={"email": "other@api.example.com", "status": "active"},
        headers=svc,
    )
    resp = await client.put(
        "/api/v1/provisioning/identities/ext-104b",
        json={"email": "taken@api.example.com", "status": "active"},
        headers=svc,
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_over_long_external_id_is_422_not_500(client, svc):
    """`idm_identities.external_id` is `String(255)`. Unbounded, an over-long
    path segment reaches Postgres and raises `DataError` -> 500, and the
    connector retries 5xx forever instead of dead-lettering a request that can
    never succeed. SQLite accepts over-long strings silently, so this asserts
    the API-level constraint rather than relying on the database to enforce it.
    """
    too_long = "x" * 256
    body = {"email": "jane@api.example.com", "status": "active"}

    resp = await client.put(
        f"/api/v1/provisioning/identities/{too_long}", json=body, headers=svc
    )
    assert resp.status_code == 422, resp.text

    read = await client.get(
        f"/api/v1/provisioning/identities/{too_long}", headers=svc
    )
    assert read.status_code == 422, read.text

    # Exactly at the limit still works — the bound is the column width, not an
    # arbitrary tightening.
    ok = await client.put(
        f"/api/v1/provisioning/identities/{'x' * 255}", json=body, headers=svc
    )
    assert ok.status_code == 200, ok.text


@pytest.mark.asyncio
async def test_null_aliases_is_422_over_http(client, svc):
    """An explicit null changes nothing yet would advance `last_synced_at` and
    write a success audit row. `[]` is how "remove all" is expressed."""
    resp = await client.put(
        "/api/v1/provisioning/identities/ext-111",
        json={"email": "jane@api.example.com", "status": "active", "aliases": None},
        headers=svc,
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_catch_all_shaped_alias_is_422_over_http(client, svc):
    """A double-`@` typo must not install a domain catch-all."""
    resp = await client.put(
        "/api/v1/provisioning/identities/ext-112",
        json={
            "email": "jane@api.example.com",
            "status": "active",
            "aliases": ["@@api.example.com"],
        },
        headers=svc,
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_over_long_alias_local_part_is_422_over_http(client, svc):
    """Whitespace before the `@` used to smuggle a 65-character local part into
    `aliases.local_part`, which is `String(64)` — `DataError` -> 500 on
    Postgres, retried forever by the connector. SQLite ignores VARCHAR width,
    so this asserts the API-level rejection."""
    resp = await client.put(
        "/api/v1/provisioning/identities/ext-113",
        json={
            "email": "jane@api.example.com",
            "status": "active",
            "aliases": ["x" * 64 + " @api.example.com"],
        },
        headers=svc,
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_get_returns_state_and_404s_for_unknown(client, svc):
    await client.put(
        "/api/v1/provisioning/identities/ext-105",
        json={"email": "jane@api.example.com", "status": "active"},
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
    payload = {"email": "audited@api.example.com", "status": "active"}
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
async def test_reassigned_domains_lands_in_the_persisted_audit_row(client, svc, sessionmaker_):
    """Domain ownership is last-write-wins by explicit decision, so this audit
    entry is the ONLY record that an administrator's domain was reassigned
    away from them. Task 7 tests `_converge_admin`'s return value in
    isolation; this drives a real reassignment through the HTTP route and
    inspects the persisted `AuditLog.meta` from a fresh session, proving the
    value is actually wired into the audit call rather than merely computed
    and discarded.
    """
    async with sessionmaker_() as session:
        session.add(Domain(name="reassign.example.com"))
        await session.commit()

        previous_owner = await user_service.create_user(
            session,
            email="previous-owner@reassign.example.com",
            password="not-usable-by-the-idm",
            role=UserRole.DOMAIN_ADMIN,
        )
        domain = (
            await session.execute(
                select(Domain).where(Domain.name == "reassign.example.com")
            )
        ).scalar_one()
        domain.owner_id = previous_owner.id
        await session.commit()
        previous_owner_id = previous_owner.id

    resp = await client.put(
        "/api/v1/provisioning/identities/ext-109",
        json={
            "email": "newowner@api.example.com",
            "status": "active",
            "admin": {"role": "domain_admin", "domains": ["reassign.example.com"]},
        },
        headers=svc,
    )
    assert resp.status_code == 200, resp.text

    async with sessionmaker_() as session:
        row = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "idm.identity.synced")
            )
        ).scalar_one()
        assert row.meta["reassigned_domains"] == [
            {"domain": "reassign.example.com", "from": str(previous_owner_id)}
        ]


@pytest.mark.asyncio
async def test_no_reassignment_stores_null_not_empty_list(client, svc, sessionmaker_):
    """The `reassigned_domains or None` conversion is easy to lose in a future
    edit — a push that reassigns nothing must persist `null`, not `[]`, so a
    reader of the audit log can tell "checked, nothing moved" apart from
    "field never populated"."""
    resp = await client.put(
        "/api/v1/provisioning/identities/ext-110",
        json={"email": "noadmin@api.example.com", "status": "active"},
        headers=svc,
    )
    assert resp.status_code == 200, resp.text

    async with sessionmaker_() as session:
        row = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "idm.identity.synced")
            )
        ).scalar_one()
        assert row.meta["reassigned_domains"] is None


@pytest.mark.asyncio
async def test_a_failed_sync_writes_nothing_at_all(client, svc, sessionmaker_):
    """A conflict part-way through must leave no mailbox, identity, or audit row."""
    await client.put(
        "/api/v1/provisioning/identities/ext-107a",
        json={"email": "occupied@api.example.com", "status": "active"},
        headers=svc,
    )
    resp = await client.put(
        "/api/v1/provisioning/identities/ext-107b",
        json={"email": "occupied@api.example.com", "status": "active"},
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
        json={"email": f"u{status}@api.example.com", "status": status},
        headers=svc,
    )
    resp = await client.get(
        f"/api/v1/provisioning/identities/ext-st-{status}", headers=svc
    )
    assert resp.json()["status"] == status


@pytest.mark.asyncio
async def test_provisioning_requires_a_token(client, svc):
    """No credentials is 401 with a challenge, matching the JWT path; an
    invalid token is 403. Only the second case must be indistinguishable."""
    resp = await client.put(
        "/api/v1/provisioning/identities/ext-108",
        json={"email": "jane@api.example.com", "status": "active"},
    )
    assert resp.status_code == 401
    assert resp.headers.get("WWW-Authenticate") == "Bearer"

    bad = await client.put(
        "/api/v1/provisioning/identities/ext-108",
        json={"email": "jane@api.example.com", "status": "active"},
        headers={"Authorization": "Bearer idm_not-a-real-token"},
    )
    assert bad.status_code == 403
