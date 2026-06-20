"""Tests for the audit-log read API."""
import pytest

from app.models.enums import UserRole
from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD, login_headers

AUDIT = "/api/v1/audit"


@pytest.mark.usefixtures("superadmin")
async def test_superadmin_sees_audit_entries(client):
    headers = await login_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    # Generate an auditable action.
    await client.post("/api/v1/domains", json={"name": "audited.com"}, headers=headers)

    resp = await client.get(AUDIT, headers=headers)
    assert resp.status_code == 200
    actions = [e["action"] for e in resp.json()]
    assert "domain.created" in actions
    # Login is also audited.
    assert "auth.login" in actions


@pytest.mark.usefixtures("superadmin")
async def test_audit_filter_by_action(client):
    headers = await login_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    await client.post("/api/v1/domains", json={"name": "filtered.com"}, headers=headers)
    resp = await client.get(AUDIT, params={"action": "domain.created"}, headers=headers)
    assert resp.status_code == 200
    assert all(e["action"] == "domain.created" for e in resp.json())


async def test_domain_admin_cannot_read_audit(client, make_user):
    await make_user("da@example.com", role=UserRole.DOMAIN_ADMIN)
    headers = await login_headers(client, "da@example.com", "password123456")
    assert (await client.get(AUDIT, headers=headers)).status_code == 403
