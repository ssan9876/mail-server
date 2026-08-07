"""IdM-owned alias convergence."""
import pytest
from sqlalchemy import select

from app.core.exceptions import ConflictError, UnprocessableError
from app.models.alias import Alias
from app.models.domain import Domain
from app.models.idm import IdmIdentityAlias
from app.schemas.provisioning import IdentityUpsert
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


async def _alias_local_parts(session, domain_id):
    rows = await session.execute(
        select(Alias.local_part).where(Alias.domain_id == domain_id)
    )
    return sorted(r[0] for r in rows)


@pytest.mark.asyncio
async def test_aliases_are_created_and_pointed_at_the_mailbox(sessionmaker_):
    domain = await _seed_domain(sessionmaker_, "al1.example.com")
    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session,
            "al-1",
            _payload("jane@al1.example.com", aliases=["j.doe@al1.example.com", "jd@al1.example.com"]),
        )
        assert await _alias_local_parts(session, domain.id) == ["j.doe", "jd"]
        alias = (
            await session.execute(select(Alias).where(Alias.local_part == "j.doe"))
        ).scalar_one()
        assert alias.destination == "jane@al1.example.com"


@pytest.mark.asyncio
async def test_alias_set_is_authoritative_when_present(sessionmaker_):
    domain = await _seed_domain(sessionmaker_, "al2.example.com")
    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session, "al-2", _payload("jane@al2.example.com", aliases=["a@al2.example.com", "b@al2.example.com"])
        )
    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session, "al-2", _payload("jane@al2.example.com", aliases=["b@al2.example.com"])
        )
        assert await _alias_local_parts(session, domain.id) == ["b"]


@pytest.mark.asyncio
async def test_omitted_alias_field_leaves_them_untouched(sessionmaker_):
    domain = await _seed_domain(sessionmaker_, "al3.example.com")
    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session, "al-3", _payload("jane@al3.example.com", aliases=["a@al3.example.com"])
        )
    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session, "al-3", _payload("jane@al3.example.com", display_name="Jane")
        )
        assert await _alias_local_parts(session, domain.id) == ["a"]


@pytest.mark.asyncio
async def test_empty_alias_list_removes_all_idm_owned_aliases(sessionmaker_):
    domain = await _seed_domain(sessionmaker_, "al4.example.com")
    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session, "al-4", _payload("jane@al4.example.com", aliases=["a@al4.example.com"])
        )
    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session, "al-4", _payload("jane@al4.example.com", aliases=[])
        )
        assert await _alias_local_parts(session, domain.id) == []


@pytest.mark.asyncio
async def test_admin_created_alias_is_never_adopted_or_removed(sessionmaker_):
    domain = await _seed_domain(sessionmaker_, "al5.example.com")
    async with sessionmaker_() as session:
        session.add(
            Alias(domain_id=domain.id, local_part="handmade", destination="someone@al5.example.com")
        )
        await session.commit()

    async with sessionmaker_() as session:
        with pytest.raises(ConflictError):
            await provisioning_service.upsert_identity(
                session, "al-5", _payload("jane@al5.example.com", aliases=["handmade@al5.example.com"])
            )

    async with sessionmaker_() as session:
        # The admin's alias survives untouched, and nothing was half-written.
        assert await _alias_local_parts(session, domain.id) == ["handmade"]
        links = (await session.execute(select(IdmIdentityAlias))).all()
        assert links == []


@pytest.mark.asyncio
async def test_alias_in_an_unhosted_domain_is_rejected(sessionmaker_):
    await _seed_domain(sessionmaker_, "al6.example.com")
    async with sessionmaker_() as session:
        with pytest.raises(UnprocessableError):
            await provisioning_service.upsert_identity(
                session, "al-6", _payload("jane@al6.example.com", aliases=["j@elsewhere.example.com"])
            )


@pytest.mark.asyncio
async def test_a_rename_repoints_alias_destinations(sessionmaker_):
    await _seed_domain(sessionmaker_, "al7.example.com")
    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session, "al-7", _payload("jane@al7.example.com", aliases=["j@al7.example.com"])
        )
    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session, "al-7", _payload("jane.doe@al7.example.com", aliases=["j@al7.example.com"])
        )
        alias = (
            await session.execute(select(Alias).where(Alias.local_part == "j"))
        ).scalar_one()
        assert alias.destination == "jane.doe@al7.example.com"
