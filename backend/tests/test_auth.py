"""Integration tests for the auth flow against the live ASGI app."""
import pytest

from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD

LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
LOGOUT = "/api/v1/auth/logout"
ME = "/api/v1/auth/me"


async def _login(client, email=ADMIN_EMAIL, password=ADMIN_PASSWORD):
    return await client.post(LOGIN, json={"email": email, "password": password})


@pytest.mark.usefixtures("superadmin")
async def test_login_success_returns_token_pair(client):
    resp = await _login(client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"] and body["refresh_token"]
    assert body["expires_in"] > 0


@pytest.mark.usefixtures("superadmin")
async def test_me_returns_profile_with_valid_token(client):
    tokens = (await _login(client)).json()
    resp = await client.get(ME, headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert resp.status_code == 200
    assert resp.json()["email"] == ADMIN_EMAIL
    assert resp.json()["role"] == "superadmin"


async def test_me_without_token_is_unauthorized(client):
    resp = await client.get(ME)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "authentication_error"


@pytest.mark.usefixtures("superadmin")
async def test_login_wrong_password_is_unauthorized(client):
    resp = await _login(client, password="nope")
    assert resp.status_code == 401


@pytest.mark.usefixtures("superadmin")
async def test_login_lockout_after_repeated_failures(client):
    # Default RATE_LIMIT_LOGIN_ATTEMPTS = 5 → 6th attempt is locked out.
    for _ in range(5):
        assert (await _login(client, password="wrong")).status_code == 401
    locked = await _login(client, password="wrong")
    assert locked.status_code == 429
    assert locked.json()["error"]["code"] == "rate_limited"


@pytest.mark.usefixtures("superadmin")
async def test_refresh_rotates_and_invalidates_old_token(client):
    tokens = (await _login(client)).json()
    first = await client.post(REFRESH, json={"refresh_token": tokens["refresh_token"]})
    assert first.status_code == 200
    # Old refresh token is now revoked (one-time use).
    replay = await client.post(REFRESH, json={"refresh_token": tokens["refresh_token"]})
    assert replay.status_code == 401


@pytest.mark.usefixtures("superadmin")
async def test_logout_blacklists_access_token(client):
    tokens = (await _login(client)).json()
    auth = {"Authorization": f"Bearer {tokens['access_token']}"}

    assert (await client.get(ME, headers=auth)).status_code == 200

    logout = await client.post(LOGOUT, headers=auth, json={"refresh_token": tokens["refresh_token"]})
    assert logout.status_code == 204

    # Access token is now revoked.
    assert (await client.get(ME, headers=auth)).status_code == 401
    # Refresh token is also revoked.
    replay = await client.post(REFRESH, json={"refresh_token": tokens["refresh_token"]})
    assert replay.status_code == 401
