"""Unscoped mailbox primitives used by provisioning."""
import pytest

from app.services import domain_service, mailbox_service
from app.models.domain import Domain


@pytest.mark.asyncio
async def test_create_mailbox_unscoped_needs_no_user(sessionmaker_):
    async with sessionmaker_() as session:
        domain = Domain(name="acme.test")
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
        assert mailbox.maildir_path == "/maildata/acme.test/jane/"
        assert mailbox.is_active is True


@pytest.mark.asyncio
async def test_local_part_taken_can_exclude_self(sessionmaker_):
    async with sessionmaker_() as session:
        domain = Domain(name="acme2.test")
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
        session.add(Domain(name="acme3.test"))
        await session.commit()

        found = await domain_service.get_by_name(session, "acme3.test")
        assert found is not None and found.name == "acme3.test"
        assert await domain_service.get_by_name(session, "nope.test") is None
