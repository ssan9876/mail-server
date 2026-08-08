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


@pytest.mark.asyncio
async def test_sync_all_prunes_keys_for_deleted_domains(sessionmaker_, tmp_path):
    """A deleted domain's DECRYPTED private key must not survive on the volume.

    Security audit finding. ``delete_domain`` removes the database row —
    including the only *encrypted* copy of the key — but the export is a
    *decrypted* PEM on a shared volume, and nothing used to remove it. That
    matters because Rspamd resolves signing keys by filesystem path
    (``path = "/dkim/$domain/$selector.key"``) with a fallback
    ``selector = "mail"``, so the presence of the FILE is what makes a domain
    signable; ``selectors.map`` is not the gate. A deleted domain therefore
    stayed signable indefinitely, with no rotation path left.
    """
    async with sessionmaker_() as db:
        admin = await user_service.create_user(
            db, email="prune@example.com", password="password123456", role=UserRole.SUPERADMIN
        )
        keep = await domain_service.create_domain(db, admin, name="keep.com")
        doomed = await domain_service.create_domain(db, admin, name="doomed.com")

        await dkim_export_service.sync_all(db, tmp_path)
        doomed_key = tmp_path / "doomed.com" / f"{doomed.dkim_selector}.key"
        assert doomed_key.exists(), "precondition: the key was exported"

        await domain_service.delete_domain(db, admin, doomed.id)
        await dkim_export_service.sync_all(db, tmp_path)

        assert not doomed_key.exists(), "deleted domain's private key still on disk"
        assert not doomed_key.parent.exists(), "empty domain directory left behind"

        # The surviving domain is untouched, and the map no longer advertises
        # the deleted one.
        assert (tmp_path / "keep.com" / f"{keep.dkim_selector}.key").exists()
        mapping = (tmp_path / "selectors.map").read_text().strip().splitlines()
        assert mapping == [f"keep.com {keep.dkim_selector}"]


@pytest.mark.asyncio
async def test_prune_never_touches_files_it_did_not_write(sessionmaker_, tmp_path):
    """The prune is scoped to ``*.key`` and to directories it thereby empties.

    A misconfigured ``DKIM_KEYS_PATH`` must not turn a routine re-export into
    an arbitrary-delete primitive, so this pins the conservative scope rather
    than trusting the implementation to stay conservative.
    """
    async with sessionmaker_() as db:
        admin = await user_service.create_user(
            db, email="scope@example.com", password="password123456", role=UserRole.SUPERADMIN
        )
        await domain_service.create_domain(db, admin, name="live.com")

        stray_dir = tmp_path / "not-a-domain"
        stray_dir.mkdir()
        bystander = stray_dir / "important.txt"
        bystander.write_text("do not delete me")

        await dkim_export_service.sync_all(db, tmp_path)

        assert bystander.exists(), "prune deleted a file it did not write"
        assert stray_dir.exists(), "prune removed a directory that was not empty"
