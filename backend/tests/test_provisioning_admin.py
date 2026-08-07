"""Admin (control-plane user) convergence."""
import pytest
from sqlalchemy import select

from app.core.exceptions import UnprocessableError
from app.core.security import verify_password
from app.models.domain import Domain
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.provisioning import UNUSABLE_PASSWORD_HASH, IdentityUpsert
from app.services import provisioning_service


async def _seed_domain(sessionmaker_, name):
    async with sessionmaker_() as session:
        domain = Domain(name=name)
        session.add(domain)
        await session.commit()
        await session.refresh(domain)
        return domain


def _payload(email, **overrides):
    base = {"email": email, "status": "active"}
    base.update(overrides)
    return IdentityUpsert(**base)


@pytest.mark.asyncio
async def test_admin_block_creates_a_user_with_an_unusable_password(sessionmaker_):
    domain = await _seed_domain(sessionmaker_, "ad1.example.com")
    async with sessionmaker_() as session:
        identity = await provisioning_service.upsert_identity(
            session,
            "ad-1",
            _payload(
                "jane@ad1.example.com",
                admin={"role": "domain_admin", "domains": ["ad1.example.com"]},
            ),
        )
        user = await session.get(User, identity.user_id)
        assert user.email == "jane@ad1.example.com"
        assert user.role is UserRole.DOMAIN_ADMIN
        assert user.is_active is True
        assert user.password_hash == UNUSABLE_PASSWORD_HASH
        assert verify_password("anything", user.password_hash) is False

        owned = await session.get(Domain, domain.id)
        assert owned.owner_id == user.id


@pytest.mark.asyncio
async def test_role_change_is_applied(sessionmaker_):
    await _seed_domain(sessionmaker_, "ad2.example.com")
    async with sessionmaker_() as session:
        identity = await provisioning_service.upsert_identity(
            session, "ad-2", _payload("jane@ad2.example.com", admin={"role": "domain_admin"})
        )
    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session, "ad-2", _payload("jane@ad2.example.com", admin={"role": "superadmin"})
        )
        user = await session.get(User, identity.user_id)
        assert user.role is UserRole.SUPERADMIN


@pytest.mark.asyncio
async def test_explicit_null_admin_deactivates_without_deleting(sessionmaker_):
    await _seed_domain(sessionmaker_, "ad3.example.com")
    async with sessionmaker_() as session:
        identity = await provisioning_service.upsert_identity(
            session, "ad-3", _payload("jane@ad3.example.com", admin={"role": "domain_admin"})
        )
        user_id = identity.user_id

    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session, "ad-3", _payload("jane@ad3.example.com", admin=None)
        )
        user = await session.get(User, user_id)
        assert user is not None, "the row must survive — audit FKs depend on it"
        assert user.is_active is False


@pytest.mark.asyncio
async def test_omitted_admin_leaves_the_record_alone(sessionmaker_):
    await _seed_domain(sessionmaker_, "ad4.example.com")
    async with sessionmaker_() as session:
        identity = await provisioning_service.upsert_identity(
            session, "ad-4", _payload("jane@ad4.example.com", admin={"role": "domain_admin"})
        )
    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session, "ad-4", _payload("jane@ad4.example.com", display_name="Jane")
        )
        user = await session.get(User, identity.user_id)
        assert user.is_active is True
        assert user.role is UserRole.DOMAIN_ADMIN


@pytest.mark.asyncio
async def test_deactivated_status_also_deactivates_the_admin_record(sessionmaker_):
    await _seed_domain(sessionmaker_, "ad5.example.com")
    async with sessionmaker_() as session:
        identity = await provisioning_service.upsert_identity(
            session, "ad-5", _payload("jane@ad5.example.com", admin={"role": "domain_admin"})
        )
    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session,
            "ad-5",
            _payload("jane@ad5.example.com", status="deactivated", admin={"role": "domain_admin"}),
        )
        user = await session.get(User, identity.user_id)
        assert user.is_active is False


@pytest.mark.asyncio
async def test_unknown_admin_domain_is_rejected(sessionmaker_):
    await _seed_domain(sessionmaker_, "ad6.example.com")
    async with sessionmaker_() as session:
        with pytest.raises(UnprocessableError):
            await provisioning_service.upsert_identity(
                session,
                "ad-6",
                _payload(
                    "jane@ad6.example.com",
                    admin={"role": "domain_admin", "domains": ["nope.example.com"]},
                ),
            )


@pytest.mark.asyncio
async def test_a_rename_moves_the_admin_email(sessionmaker_):
    await _seed_domain(sessionmaker_, "ad7.example.com")
    async with sessionmaker_() as session:
        identity = await provisioning_service.upsert_identity(
            session, "ad-7", _payload("jane@ad7.example.com", admin={"role": "domain_admin"})
        )
    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session, "ad-7", _payload("jane.doe@ad7.example.com", admin={"role": "domain_admin"})
        )
        user = await session.get(User, identity.user_id)
        assert user.email == "jane.doe@ad7.example.com"
