"""IdM table shape and constraint tests."""
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.enums import ActorType
from app.models.idm import IdmIdentity, IdmIdentityAlias, IdmServiceToken


def test_actor_type_has_idm():
    assert ActorType.IDM.value == "idm"


@pytest.mark.asyncio
async def test_external_id_is_unique(sessionmaker_):
    async with sessionmaker_() as session:
        session.add(IdmIdentity(external_id="user-1"))
        await session.commit()

    async with sessionmaker_() as session:
        session.add(IdmIdentity(external_id="user-1"))
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_identity_defaults_are_null(sessionmaker_):
    async with sessionmaker_() as session:
        session.add(IdmIdentity(external_id="user-2", idm_username="jdoe"))
        await session.commit()

    async with sessionmaker_() as session:
        identity = (
            await session.execute(
                select(IdmIdentity).where(IdmIdentity.external_id == "user-2")
            )
        ).scalar_one()
        assert identity.mailbox_id is None
        assert identity.user_id is None
        assert identity.last_payload_hash is None
        assert identity.deactivated_at is None
        assert identity.idm_username == "jdoe"
        assert identity.status == "pending"


@pytest.mark.asyncio
async def test_identity_alias_link_is_composite_pk(sessionmaker_):
    identity_id = uuid.uuid4()
    alias_id = uuid.uuid4()
    async with sessionmaker_() as session:
        session.add(IdmIdentity(id=identity_id, external_id="user-3"))
        await session.commit()

    async with sessionmaker_() as session:
        session.add(IdmIdentityAlias(identity_id=identity_id, alias_id=alias_id))
        await session.commit()

    async with sessionmaker_() as session:
        session.add(IdmIdentityAlias(identity_id=identity_id, alias_id=alias_id))
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_service_token_stores_hash_not_raw(sessionmaker_):
    async with sessionmaker_() as session:
        session.add(
            IdmServiceToken(name="idm", token_hash="a" * 64, prefix="idm_abcd")
        )
        await session.commit()

    async with sessionmaker_() as session:
        token = (await session.execute(select(IdmServiceToken))).scalar_one()
        assert token.is_active is True
        assert token.expires_at is None
        assert token.last_used_at is None
        assert not hasattr(token, "token")
