"""Tests for exporting DKIM keys from the DB to the shared volume."""
import pytest

from app.core import crypto
from app.models.enums import UserRole
from app.services import dkim_export_service, domain_service, user_service


@pytest.mark.asyncio
async def test_sync_all_writes_keys_and_map(sessionmaker_, tmp_path):
    async with sessionmaker_() as db:
        admin = await user_service.create_user(
            db, email="su@example.com", password="password123456", role=UserRole.SUPERADMIN
        )
        d1 = await domain_service.create_domain(db, admin, name="one.com")
        d2 = await domain_service.create_domain(db, admin, name="two.com")

        count = await dkim_export_service.sync_all(db, tmp_path)
        assert count == 2

        # Key files exist, are private PEM, and decrypt-match the stored key.
        key1 = tmp_path / "one.com" / f"{d1.dkim_selector}.key"
        assert key1.exists()
        assert "BEGIN PRIVATE KEY" in key1.read_text()
        assert key1.read_text() == crypto.decrypt(d1.dkim_private_key)

        # selectors.map lists both domains, sorted.
        mapping = (tmp_path / "selectors.map").read_text().strip().splitlines()
        assert mapping == [f"one.com {d1.dkim_selector}", f"two.com {d2.dkim_selector}"]


@pytest.mark.asyncio
async def test_try_sync_is_safe_when_path_unwritable(sessionmaker_, monkeypatch, tmp_path):
    from app.core.config import settings

    # A regular file used as a parent directory makes mkdir fail on every OS.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    monkeypatch.setattr(settings, "DKIM_KEYS_PATH", str(blocker / "dkim"))

    async with sessionmaker_() as db:
        admin = await user_service.create_user(
            db, email="su2@example.com", password="password123456", role=UserRole.SUPERADMIN
        )
        await domain_service.create_domain(db, admin, name="x.com")
        # Must not raise, just return False.
        assert await dkim_export_service.try_sync(db) is False


@pytest.mark.asyncio
async def test_sync_endpoint(client, superadmin):
    from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD, login_headers

    headers = await login_headers(client, ADMIN_EMAIL, ADMIN_PASSWORD)
    await client.post("/api/v1/domains", json={"name": "synced.com"}, headers=headers)
    resp = await client.post("/api/v1/domains/dkim/sync", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["exported"] >= 1
