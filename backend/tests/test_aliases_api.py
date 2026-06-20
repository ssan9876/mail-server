"""Integration tests for alias CRUD, catch-all, and collision checks."""
import pytest

from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD, login_headers

DOMAINS = "/api/v1/domains"


async def _admin_headers(client):
    return await login_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)


async def _make_domain(client, headers, name) -> str:
    return (await client.post(DOMAINS, json={"name": name}, headers=headers)).json()["id"]


@pytest.mark.usefixtures("superadmin")
async def test_create_multi_destination_alias(client):
    headers = await _admin_headers(client)
    domain_id = await _make_domain(client, headers, "ali.com")
    resp = await client.post(
        f"{DOMAINS}/{domain_id}/aliases",
        json={"local_part": "team", "destinations": ["a@x.com", "b@y.com"]},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["destinations"] == ["a@x.com", "b@y.com"]


@pytest.mark.usefixtures("superadmin")
async def test_create_catch_all_alias(client):
    headers = await _admin_headers(client)
    domain_id = await _make_domain(client, headers, "catch.com")
    resp = await client.post(
        f"{DOMAINS}/{domain_id}/aliases",
        json={"local_part": "@", "destinations": ["inbox@catch.com"]},
        headers=headers,
    )
    assert resp.status_code == 201
    assert resp.json()["local_part"] == "@"


@pytest.mark.usefixtures("superadmin")
async def test_alias_mailbox_collision_conflicts(client):
    headers = await _admin_headers(client)
    domain_id = await _make_domain(client, headers, "clash.com")
    await client.post(
        f"{DOMAINS}/{domain_id}/mailboxes",
        json={"local_part": "sales", "password": "hunter2hunter2"},
        headers=headers,
    )
    resp = await client.post(
        f"{DOMAINS}/{domain_id}/aliases",
        json={"local_part": "sales", "destinations": ["elsewhere@x.com"]},
        headers=headers,
    )
    assert resp.status_code == 409


@pytest.mark.usefixtures("superadmin")
async def test_update_and_delete_alias(client):
    headers = await _admin_headers(client)
    domain_id = await _make_domain(client, headers, "aliupd.com")
    created = (
        await client.post(
            f"{DOMAINS}/{domain_id}/aliases",
            json={"local_part": "info", "destinations": ["one@x.com"]},
            headers=headers,
        )
    ).json()
    alias_id = created["id"]

    resp = await client.patch(
        f"/api/v1/aliases/{alias_id}",
        json={"destinations": ["one@x.com", "two@x.com"], "is_active": False},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["destinations"] == ["one@x.com", "two@x.com"]
    assert resp.json()["is_active"] is False

    assert (await client.delete(f"/api/v1/aliases/{alias_id}", headers=headers)).status_code == 204
