"""Integration tests for the mailbox self-service portal + principal isolation."""
import pytest

from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD, login_headers

DOMAINS = "/api/v1/domains"
M_LOGIN = "/api/v1/mailbox/login"
M_REFRESH = "/api/v1/mailbox/refresh"
M_ME = "/api/v1/mailbox/me"
M_PASSWORD = "/api/v1/mailbox/password"


async def _provision_mailbox(client, local="bob", password="mailboxpass1"):
    """Admin creates a domain + mailbox; returns the mailbox email."""
    headers = await login_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    domain_id = (
        await client.post(DOMAINS, json={"name": "portal.com"}, headers=headers)
    ).json()["id"]
    await client.post(
        f"{DOMAINS}/{domain_id}/mailboxes",
        json={"local_part": local, "password": password},
        headers=headers,
    )
    return f"{local}@portal.com"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.usefixtures("superadmin")
async def test_mailbox_login_and_me(client):
    email = await _provision_mailbox(client)
    resp = await client.post(M_LOGIN, json={"email": email, "password": "mailboxpass1"})
    assert resp.status_code == 200, resp.text
    tokens = resp.json()

    me = await client.get(M_ME, headers=_bearer(tokens["access_token"]))
    assert me.status_code == 200
    assert me.json()["address"] == email


@pytest.mark.usefixtures("superadmin")
async def test_mailbox_wrong_password(client):
    email = await _provision_mailbox(client)
    resp = await client.post(M_LOGIN, json={"email": email, "password": "wrong"})
    assert resp.status_code == 401


@pytest.mark.usefixtures("superadmin")
async def test_admin_token_rejected_on_mailbox_portal(client):
    await _provision_mailbox(client)
    headers = await login_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    # Operator (user-principal) token must not access mailbox endpoints.
    assert (await client.get(M_ME, headers=headers)).status_code == 401


@pytest.mark.usefixtures("superadmin")
async def test_mailbox_token_rejected_on_admin_api(client):
    email = await _provision_mailbox(client)
    tokens = (await client.post(M_LOGIN, json={"email": email, "password": "mailboxpass1"})).json()
    # Mailbox-principal token must not access operator endpoints.
    resp = await client.get(DOMAINS, headers=_bearer(tokens["access_token"]))
    assert resp.status_code == 401


@pytest.mark.usefixtures("superadmin")
async def test_mailbox_change_password(client):
    email = await _provision_mailbox(client)
    tokens = (await client.post(M_LOGIN, json={"email": email, "password": "mailboxpass1"})).json()
    auth = _bearer(tokens["access_token"])

    # Wrong current password is rejected.
    bad = await client.post(
        M_PASSWORD, json={"current_password": "nope", "new_password": "newsecret22"}, headers=auth
    )
    assert bad.status_code == 401

    ok = await client.post(
        M_PASSWORD,
        json={"current_password": "mailboxpass1", "new_password": "newsecret22"},
        headers=auth,
    )
    assert ok.status_code == 200

    # New password works, old one no longer does.
    assert (await client.post(M_LOGIN, json={"email": email, "password": "newsecret22"})).status_code == 200
    assert (await client.post(M_LOGIN, json={"email": email, "password": "mailboxpass1"})).status_code == 401


@pytest.mark.usefixtures("superadmin")
async def test_mailbox_refresh_rotation(client):
    email = await _provision_mailbox(client)
    tokens = (await client.post(M_LOGIN, json={"email": email, "password": "mailboxpass1"})).json()
    first = await client.post(M_REFRESH, json={"refresh_token": tokens["refresh_token"]})
    assert first.status_code == 200
    replay = await client.post(M_REFRESH, json={"refresh_token": tokens["refresh_token"]})
    assert replay.status_code == 401
