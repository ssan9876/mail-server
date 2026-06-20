"""Integration tests for mailbox CRUD + scoping."""
import pytest

from app.models.enums import UserRole
from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD, login_headers

DOMAINS = "/api/v1/domains"


async def _admin_headers(client):
    return await login_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)


async def _make_domain(client, headers, name) -> str:
    resp = await client.post(DOMAINS, json={"name": name}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


@pytest.mark.usefixtures("superadmin")
async def test_create_mailbox(client):
    headers = await _admin_headers(client)
    domain_id = await _make_domain(client, headers, "mbox.com")

    resp = await client.post(
        f"{DOMAINS}/{domain_id}/mailboxes",
        json={"local_part": "John", "password": "hunter2hunter2", "quota_mb": 500},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["local_part"] == "john"  # normalized
    assert body["quota_mb"] == 500
    assert body["maildir_path"] == "/maildata/mbox.com/john/"
    assert "password_hash" not in body


@pytest.mark.usefixtures("superadmin")
async def test_create_mailbox_uses_default_quota(client):
    headers = await _admin_headers(client)
    domain_id = await _make_domain(client, headers, "q.com")
    resp = await client.post(
        f"{DOMAINS}/{domain_id}/mailboxes",
        json={"local_part": "a", "password": "hunter2hunter2"},
        headers=headers,
    )
    assert resp.json()["quota_mb"] == 2048  # DEFAULT_MAILBOX_QUOTA_MB


@pytest.mark.usefixtures("superadmin")
async def test_duplicate_mailbox_conflicts(client):
    headers = await _admin_headers(client)
    domain_id = await _make_domain(client, headers, "dupmb.com")
    payload = {"local_part": "dup", "password": "hunter2hunter2"}
    await client.post(f"{DOMAINS}/{domain_id}/mailboxes", json=payload, headers=headers)
    resp = await client.post(f"{DOMAINS}/{domain_id}/mailboxes", json=payload, headers=headers)
    assert resp.status_code == 409


@pytest.mark.usefixtures("superadmin")
async def test_update_and_delete_mailbox(client, sessionmaker_):
    from app.core.dovecot_password import verify_dovecot
    from app.models.mailbox import Mailbox

    headers = await _admin_headers(client)
    domain_id = await _make_domain(client, headers, "upd.com")
    created = (
        await client.post(
            f"{DOMAINS}/{domain_id}/mailboxes",
            json={"local_part": "u", "password": "originalpass1"},
            headers=headers,
        )
    ).json()
    mbox_id = created["id"]

    # Update quota + password.
    resp = await client.patch(
        f"/api/v1/mailboxes/{mbox_id}",
        json={"quota_mb": 999, "password": "brandnewpass2"},
        headers=headers,
    )
    assert resp.status_code == 200 and resp.json()["quota_mb"] == 999

    # New password hash actually verifies.
    async with sessionmaker_() as s:
        mb = await s.get(Mailbox, __import__("uuid").UUID(mbox_id))
        assert verify_dovecot("brandnewpass2", mb.password_hash)

    # Delete.
    assert (await client.delete(f"/api/v1/mailboxes/{mbox_id}", headers=headers)).status_code == 204
    assert (await client.get(f"/api/v1/mailboxes/{mbox_id}", headers=headers)).status_code == 404


async def test_mailbox_scoping_across_domain_admins(client, make_user):
    await make_user("o1@example.com", role=UserRole.DOMAIN_ADMIN)
    await make_user("o2@example.com", role=UserRole.DOMAIN_ADMIN)
    h1 = await login_headers(client, "o1@example.com", "password123456")
    h2 = await login_headers(client, "o2@example.com", "password123456")

    domain_id = await _make_domain(client, h1, "owned1.com")
    # o2 cannot create a mailbox in o1's domain (domain hidden → 404).
    resp = await client.post(
        f"{DOMAINS}/{domain_id}/mailboxes",
        json={"local_part": "x", "password": "hunter2hunter2"},
        headers=h2,
    )
    assert resp.status_code == 404
