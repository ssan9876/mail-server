"""Unscoped mailbox primitives used by provisioning."""
import pytest

from app.services import domain_service, mailbox_service
from app.models.domain import Domain


@pytest.mark.asyncio
async def test_create_mailbox_unscoped_needs_no_user(sessionmaker_):
    async with sessionmaker_() as session:
        domain = Domain(name="acme.example.com")
        session.add(domain)
        await session.commit()
        await session.refresh(domain)

        mailbox = await mailbox_service.create_mailbox_unscoped(
            session,
            domain,
            local_part="jane",
            password_hash="!unusable",
            display_name="Jane",
            quota_mb=2048,
        )
        assert mailbox.local_part == "jane"
        assert mailbox.maildir_path == "/maildata/acme.example.com/jane/"
        assert mailbox.is_active is True


@pytest.mark.asyncio
async def test_local_part_taken_can_exclude_self(sessionmaker_):
    async with sessionmaker_() as session:
        domain = Domain(name="acme2.example.com")
        session.add(domain)
        await session.commit()
        await session.refresh(domain)

        mailbox = await mailbox_service.create_mailbox_unscoped(
            session, domain, local_part="jane", password_hash="!unusable"
        )
        assert await mailbox_service.local_part_taken(session, domain.id, "jane") is True
        assert (
            await mailbox_service.local_part_taken(
                session, domain.id, "jane", exclude_mailbox_id=mailbox.id
            )
            is False
        )


@pytest.mark.asyncio
async def test_domain_get_by_name(sessionmaker_):
    async with sessionmaker_() as session:
        session.add(Domain(name="acme3.example.com"))
        await session.commit()

        found = await domain_service.get_by_name(session, "acme3.example.com")
        assert found is not None and found.name == "acme3.example.com"
        assert await domain_service.get_by_name(session, "nope.example.com") is None


from datetime import datetime, timezone

from app.models.domain import Domain
from app.models.mailbox import Mailbox
from app.schemas.provisioning import UNUSABLE_PASSWORD_HASH, IdentityUpsert
from app.services import provisioning_service
from app.core.exceptions import ConflictError, NotFoundError, UnprocessableError
from sqlalchemy import select
import pytest


async def _seed_domain(sessionmaker_, name="corp.example.com"):
    async with sessionmaker_() as session:
        domain = Domain(name=name)
        session.add(domain)
        await session.commit()
        await session.refresh(domain)
        return domain


def _payload(**overrides):
    base = {"email": "jane@corp.example.com", "status": "active"}
    base.update(overrides)
    return IdentityUpsert(**base)


@pytest.mark.asyncio
async def test_creates_a_mailbox_with_an_unusable_password(sessionmaker_):
    await _seed_domain(sessionmaker_)
    async with sessionmaker_() as session:
        identity = await provisioning_service.upsert_identity(
            session, "ext-1", _payload(display_name="Jane", quota_mb=4096)
        )
        mailbox = await session.get(Mailbox, identity.mailbox_id)
        assert mailbox.local_part == "jane"
        assert mailbox.display_name == "Jane"
        assert mailbox.quota_mb == 4096
        assert mailbox.is_active is True
        assert mailbox.password_hash == UNUSABLE_PASSWORD_HASH
        assert identity.idm_username is None


@pytest.mark.asyncio
async def test_unknown_domain_is_rejected(sessionmaker_):
    """422, not 404: the request is well-formed, it just names a domain this
    server does not host."""
    async with sessionmaker_() as session:
        with pytest.raises(UnprocessableError):
            await provisioning_service.upsert_identity(
                session, "ext-2", _payload(email="jane@nowhere.example.com")
            )


@pytest.mark.asyncio
async def test_inactive_domain_is_accepted(sessionmaker_):
    async with sessionmaker_() as session:
        session.add(Domain(name="paused.example.com", is_active=False))
        await session.commit()

    async with sessionmaker_() as session:
        identity = await provisioning_service.upsert_identity(
            session, "ext-3", _payload(email="jane@paused.example.com")
        )
        assert identity.mailbox_id is not None


@pytest.mark.asyncio
async def test_repeated_identical_push_is_a_no_op(sessionmaker_):
    await _seed_domain(sessionmaker_, "noop.example.com")
    payload = _payload(email="jane@noop.example.com", display_name="Jane")

    async with sessionmaker_() as session:
        identity = await provisioning_service.upsert_identity(session, "ext-4", payload)
        mailbox_id = identity.mailbox_id
        first_updated = (await session.get(Mailbox, mailbox_id)).updated_at
        first_hash = identity.last_payload_hash

    async with sessionmaker_() as session:
        again = await provisioning_service.upsert_identity(session, "ext-4", payload)
        mailbox = await session.get(Mailbox, mailbox_id)
        assert again.last_payload_hash == first_hash
        assert mailbox.updated_at == first_updated
        assert again.last_synced_at is not None


@pytest.mark.asyncio
async def test_rename_preserves_maildir_path(sessionmaker_):
    await _seed_domain(sessionmaker_, "rename.example.com")
    async with sessionmaker_() as session:
        identity = await provisioning_service.upsert_identity(
            session, "ext-5", _payload(email="jane@rename.example.com")
        )
        original_path = (await session.get(Mailbox, identity.mailbox_id)).maildir_path

    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session, "ext-5", _payload(email="jane.doe@rename.example.com")
        )
        mailbox = await session.get(Mailbox, identity.mailbox_id)
        assert mailbox.local_part == "jane.doe"
        assert mailbox.maildir_path == original_path


@pytest.mark.asyncio
async def test_rename_onto_an_existing_address_conflicts(sessionmaker_):
    domain = await _seed_domain(sessionmaker_, "clash.example.com")
    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session, "ext-6a", _payload(email="taken@clash.example.com")
        )
        await provisioning_service.upsert_identity(
            session, "ext-6b", _payload(email="jane@clash.example.com")
        )

    async with sessionmaker_() as session:
        with pytest.raises(ConflictError):
            await provisioning_service.upsert_identity(
                session, "ext-6b", _payload(email="taken@clash.example.com")
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,expected_active",
    [("pending", False), ("active", True), ("suspended", False), ("deactivated", False)],
)
async def test_status_maps_to_is_active(sessionmaker_, status, expected_active):
    await _seed_domain(sessionmaker_, f"st-{status}.example.com")
    async with sessionmaker_() as session:
        identity = await provisioning_service.upsert_identity(
            session,
            f"ext-st-{status}",
            _payload(email=f"jane@st-{status}.example.com", status=status),
        )
        mailbox = await session.get(Mailbox, identity.mailbox_id)
        assert mailbox.is_active is expected_active


@pytest.mark.asyncio
async def test_suspended_does_not_stamp_deactivated_at(sessionmaker_):
    """A suspension is not an offboarding and must not start the retention clock."""
    await _seed_domain(sessionmaker_, "susp.example.com")
    async with sessionmaker_() as session:
        identity = await provisioning_service.upsert_identity(
            session, "ext-7", _payload(email="jane@susp.example.com", status="suspended")
        )
        assert identity.deactivated_at is None


@pytest.mark.asyncio
async def test_repeated_deactivation_does_not_move_the_stamp(sessionmaker_):
    await _seed_domain(sessionmaker_, "deact.example.com")
    async with sessionmaker_() as session:
        identity = await provisioning_service.upsert_identity(
            session, "ext-8", _payload(email="jane@deact.example.com", status="deactivated")
        )
        first_stamp = identity.deactivated_at
        assert first_stamp is not None

    async with sessionmaker_() as session:
        # Vary an unrelated field so the no-op short circuit does not hide the
        # behaviour under test.
        again = await provisioning_service.upsert_identity(
            session,
            "ext-8",
            _payload(email="jane@deact.example.com", status="deactivated", display_name="J"),
        )
        assert again.deactivated_at == first_stamp


@pytest.mark.asyncio
async def test_reactivation_clears_the_stamp(sessionmaker_):
    await _seed_domain(sessionmaker_, "react.example.com")
    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session, "ext-9", _payload(email="jane@react.example.com", status="deactivated")
        )

    async with sessionmaker_() as session:
        identity = await provisioning_service.upsert_identity(
            session, "ext-9", _payload(email="jane@react.example.com", status="active")
        )
        mailbox = await session.get(Mailbox, identity.mailbox_id)
        assert identity.deactivated_at is None
        assert mailbox.is_active is True


@pytest.mark.asyncio
async def test_absent_scalar_leaves_the_value_unchanged(sessionmaker_):
    await _seed_domain(sessionmaker_, "scalar.example.com")
    async with sessionmaker_() as session:
        identity = await provisioning_service.upsert_identity(
            session, "ext-10", _payload(email="jane@scalar.example.com", display_name="Jane")
        )

    async with sessionmaker_() as session:
        # display_name omitted entirely — must not be cleared.
        await provisioning_service.upsert_identity(
            session, "ext-10", _payload(email="jane@scalar.example.com", quota_mb=8192)
        )
        mailbox = await session.get(Mailbox, identity.mailbox_id)
        assert mailbox.display_name == "Jane"
        assert mailbox.quota_mb == 8192


@pytest.mark.asyncio
async def test_explicit_null_clears_a_nullable_scalar(sessionmaker_):
    await _seed_domain(sessionmaker_, "clear.example.com")
    async with sessionmaker_() as session:
        identity = await provisioning_service.upsert_identity(
            session, "ext-11", _payload(email="jane@clear.example.com", display_name="Jane")
        )

    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session, "ext-11", _payload(email="jane@clear.example.com", display_name=None)
        )
        mailbox = await session.get(Mailbox, identity.mailbox_id)
        assert mailbox.display_name is None


@pytest.mark.asyncio
async def test_username_is_captured_when_sent(sessionmaker_):
    await _seed_domain(sessionmaker_, "uname.example.com")
    async with sessionmaker_() as session:
        identity = await provisioning_service.upsert_identity(
            session, "ext-12", _payload(email="jane@uname.example.com", username="jdoe")
        )
        assert identity.idm_username == "jdoe"


@pytest.mark.asyncio
async def test_get_identity_raises_for_unknown_external_id(sessionmaker_):
    async with sessionmaker_() as session:
        with pytest.raises(NotFoundError):
            await provisioning_service.get_identity(session, "never-seen")


def test_split_address_strips_a_trailing_dot_from_the_domain():
    """`jane@acme.example.com.` (trailing dot, valid FQDN syntax) must resolve
    the same domain as `jane@acme.example.com`. `domain_service.get_by_name`
    normalizes with `.strip().lower()` but NOT `.rstrip(".")`, so this has to
    be handled here — Pydantic's `EmailStr` already rejects a trailing dot at
    the schema layer, so this function is the only place that can see one
    (e.g. a caller invoking it directly, or a future relaxation of the schema).
    """
    local_part, domain_name = provisioning_service._split_address(
        "jane@acme.example.com."
    )
    assert local_part == "jane"
    assert domain_name == "acme.example.com"
