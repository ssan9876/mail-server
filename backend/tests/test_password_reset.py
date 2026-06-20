"""Integration tests for the mailbox password-reset flow."""
import uuid

import pytest

from app.core.dovecot_password import verify_dovecot
from app.models.mailbox import Mailbox
from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD, login_headers

DOMAINS = "/api/v1/domains"
REQUEST = "/api/v1/password-reset/request"
CONFIRM = "/api/v1/password-reset/confirm"


async def _setup_mailbox(client) -> str:
    headers = await login_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    domain_id = (await client.post(DOMAINS, json={"name": "reset.com"}, headers=headers)).json()["id"]
    created = (
        await client.post(
            f"{DOMAINS}/{domain_id}/mailboxes",
            json={"local_part": "alice", "password": "oldpassword11"},
            headers=headers,
        )
    ).json()
    return created["id"]


@pytest.mark.usefixtures("superadmin")
async def test_full_reset_flow(client, sessionmaker_):
    mbox_id = await _setup_mailbox(client)

    req = await client.post(REQUEST, json={"email": "alice@reset.com"})
    assert req.status_code == 200
    token = req.json()["debug_token"]
    assert token  # surfaced outside production

    confirm = await client.post(CONFIRM, json={"token": token, "new_password": "newpassword22"})
    assert confirm.status_code == 200

    async with sessionmaker_() as s:
        mb = await s.get(Mailbox, uuid.UUID(mbox_id))
        assert verify_dovecot("newpassword22", mb.password_hash)
        assert not verify_dovecot("oldpassword11", mb.password_hash)


@pytest.mark.usefixtures("superadmin")
async def test_token_is_single_use(client):
    await _setup_mailbox(client)
    token = (await client.post(REQUEST, json={"email": "alice@reset.com"})).json()["debug_token"]

    first = await client.post(CONFIRM, json={"token": token, "new_password": "newpassword22"})
    assert first.status_code == 200
    replay = await client.post(CONFIRM, json={"token": token, "new_password": "anotherpass33"})
    assert replay.status_code == 401


async def test_unknown_email_does_not_enumerate(client):
    resp = await client.post(REQUEST, json={"email": "nobody@nowhere.com"})
    assert resp.status_code == 200
    assert resp.json()["debug_token"] is None  # no token issued, but generic 200


async def test_bogus_token_is_rejected(client):
    resp = await client.post(CONFIRM, json={"token": "not-a-real-token", "new_password": "whatever12"})
    assert resp.status_code == 401
