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
