"""IdM-owned alias convergence."""
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.core.exceptions import ConflictError, UnprocessableError
from app.models.alias import Alias
from app.models.domain import Domain
from app.models.idm import IdmIdentityAlias
from app.models.mailbox import Mailbox
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


@pytest.mark.asyncio
async def test_a_rename_repoints_owned_aliases_when_aliases_field_is_omitted(sessionmaker_):
    """Distinct from `test_a_rename_repoints_alias_destinations`: that test
    sends `aliases` present on both pushes, so it only exercises the update
    branch inside the "collection present" loop. This test omits `aliases`
    entirely on the renaming push, so it can only pass if the early-return
    ("collection absent, leave membership untouched") branch itself refreshes
    `alias.destination` for already-owned aliases.
    """
    await _seed_domain(sessionmaker_, "al8.example.com")
    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session, "al-8", _payload("jane@al8.example.com", aliases=["j@al8.example.com"])
        )
    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session, "al-8", _payload("jane.doe@al8.example.com")
        )
    async with sessionmaker_() as session:
        alias = (
            await session.execute(select(Alias).where(Alias.local_part == "j"))
        ).scalar_one()
        assert alias.destination == "jane.doe@al8.example.com"


@pytest.mark.asyncio
async def test_alias_collision_with_an_existing_mailbox_is_rejected(sessionmaker_):
    domain = await _seed_domain(sessionmaker_, "al9.example.com")
    async with sessionmaker_() as session:
        # A separate identity's mailbox occupies "taken@al9.example.com".
        await provisioning_service.upsert_identity(
            session, "al-9-mbox", _payload("taken@al9.example.com")
        )

    async with sessionmaker_() as session:
        with pytest.raises(ConflictError):
            await provisioning_service.upsert_identity(
                session, "al-9", _payload("jane@al9.example.com", aliases=["taken@al9.example.com"])
            )

    async with sessionmaker_() as session:
        # No alias got created for the identity that requested the collision.
        assert await _alias_local_parts(session, domain.id) == []


@pytest.mark.asyncio
async def test_multi_alias_collision_rolls_back_the_earlier_created_alias(sessionmaker_):
    domain = await _seed_domain(sessionmaker_, "al10.example.com")
    async with sessionmaker_() as session:
        session.add(
            Alias(domain_id=domain.id, local_part="collides", destination="someone@al10.example.com")
        )
        await session.commit()

    async with sessionmaker_() as session:
        with pytest.raises(ConflictError):
            await provisioning_service.upsert_identity(
                session,
                "al-10",
                _payload(
                    "jane@al10.example.com",
                    aliases=["fresh@al10.example.com", "collides@al10.example.com"],
                ),
            )

    async with sessionmaker_() as session:
        # "fresh" was created and flushed before "collides" raised — proving
        # the whole request rolled back rather than leaving it behind.
        assert await _alias_local_parts(session, domain.id) == ["collides"]
        links = (await session.execute(select(IdmIdentityAlias))).all()
        assert links == []


@pytest.mark.asyncio
async def test_no_alias_with_a_catch_all_local_part_can_ever_be_provisioned(
    sessionmaker_,
):
    """Regression guard on the whole path, not just the regex.

    `_split_address` splits on the LAST `@`, so `"@@d"` yields `local_part ==
    "@"` — the domain catch-all convention Postfix's
    `virtual_alias_catchall.cf` matches. A double-`@` typo would silently
    divert every unrouted address in the domain into one mailbox. Nothing that
    reaches the alias table through provisioning may carry that local part.
    """
    domain = await _seed_domain(sessionmaker_, "catchall.example.com")

    for bad in ["@@catchall.example.com", "@catchall.example.com", "@"]:
        with pytest.raises(ValidationError):
            _payload("jane@catchall.example.com", aliases=[bad])

    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session,
            "al-catchall",
            _payload("jane@catchall.example.com", aliases=["j@catchall.example.com"]),
        )

    async with sessionmaker_() as session:
        catch_alls = (
            await session.execute(
                select(Alias).where(
                    Alias.domain_id == domain.id, Alias.local_part == "@"
                )
            )
        ).scalars().all()
        assert catch_alls == []


@pytest.mark.asyncio
async def test_rename_keeping_the_old_address_as_an_alias(sessionmaker_):
    """A name change that keeps the previous address reachable.

    Sessions are `autoflush=False` in production, so the rename
    `_converge_mailbox` applies in memory is invisible to `_converge_aliases`'
    collision query unless the service flushes in between. Without that flush
    this raised a false `409 jane@... already exists` — and 409 is a permanent
    dead-letter, not a retry, so the rename would never land.
    """
    domain = await _seed_domain(sessionmaker_, "keepold.example.com")
    async with sessionmaker_() as session:
        identity = await provisioning_service.upsert_identity(
            session, "al-11", _payload("jane@keepold.example.com")
        )
        mailbox_id = identity.mailbox_id

    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session,
            "al-11",
            _payload(
                "jsmith@keepold.example.com", aliases=["jane@keepold.example.com"]
            ),
        )

    async with sessionmaker_() as session:
        mailbox = await session.get(Mailbox, mailbox_id)
        assert mailbox.local_part == "jsmith"
        assert await _alias_local_parts(session, domain.id) == ["jane"]
        alias = (
            await session.execute(select(Alias).where(Alias.local_part == "jane"))
        ).scalar_one()
        assert alias.destination == "jsmith@keepold.example.com"


@pytest.mark.asyncio
async def test_swapping_the_primary_address_with_an_owned_alias(sessionmaker_):
    """`jane` <-> `j.doe`, both already owned by this identity.

    `_converge_mailbox` runs before `_converge_aliases`, so the new primary
    address collides with an alias that the very same transaction is about to
    reconcile away. Treating that as a conflict left the IdM unable to express
    a name change over an address it already owns — and 409 dead-letters.
    """
    domain = await _seed_domain(sessionmaker_, "swap.example.com")
    async with sessionmaker_() as session:
        identity = await provisioning_service.upsert_identity(
            session,
            "al-12",
            _payload("jane@swap.example.com", aliases=["j.doe@swap.example.com"]),
        )
        mailbox_id = identity.mailbox_id

    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session,
            "al-12",
            _payload("j.doe@swap.example.com", aliases=["jane@swap.example.com"]),
        )

    async with sessionmaker_() as session:
        mailbox = await session.get(Mailbox, mailbox_id)
        assert mailbox.local_part == "j.doe"
        assert await _alias_local_parts(session, domain.id) == ["jane"]
        alias = (
            await session.execute(select(Alias).where(Alias.local_part == "jane"))
        ).scalar_one()
        assert alias.destination == "j.doe@swap.example.com"
        links = (await session.execute(select(IdmIdentityAlias))).scalars().all()
        assert len(links) == 1, "exactly one owned alias survives the swap"


@pytest.mark.asyncio
async def test_primary_address_still_collides_with_another_identity_alias(
    sessionmaker_,
):
    """The exclusion above is narrow: only aliases owned by THIS identity."""
    await _seed_domain(sessionmaker_, "swap2.example.com")
    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session,
            "al-13a",
            _payload("alice@swap2.example.com", aliases=["shared@swap2.example.com"]),
        )
        await provisioning_service.upsert_identity(
            session, "al-13b", _payload("bob@swap2.example.com")
        )

    async with sessionmaker_() as session:
        with pytest.raises(ConflictError, match="shared@swap2.example.com"):
            await provisioning_service.upsert_identity(
                session, "al-13b", _payload("shared@swap2.example.com")
            )


@pytest.mark.asyncio
async def test_primary_address_still_collides_with_an_admin_created_alias(
    sessionmaker_,
):
    domain = await _seed_domain(sessionmaker_, "swap3.example.com")
    async with sessionmaker_() as session:
        session.add(
            Alias(
                domain_id=domain.id,
                local_part="handmade",
                destination="someone@swap3.example.com",
            )
        )
        await session.commit()

    async with sessionmaker_() as session:
        with pytest.raises(ConflictError, match="handmade@swap3.example.com"):
            await provisioning_service.upsert_identity(
                session, "al-14", _payload("handmade@swap3.example.com")
            )


@pytest.mark.asyncio
async def test_alias_clash_names_the_owning_idm_identity(sessionmaker_):
    """"... is not managed by the IdM" is untrue when the clash is with another
    IdM identity's alias, and it points the operator at the wrong system to fix
    it. Name the owning `external_id` instead."""
    await _seed_domain(sessionmaker_, "clashmsg.example.com")
    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session,
            "clash-owner",
            _payload(
                "alice@clashmsg.example.com", aliases=["shared@clashmsg.example.com"]
            ),
        )

    async with sessionmaker_() as session:
        with pytest.raises(ConflictError, match="managed by IdM identity clash-owner"):
            await provisioning_service.upsert_identity(
                session,
                "clash-other",
                _payload(
                    "bob@clashmsg.example.com", aliases=["shared@clashmsg.example.com"]
                ),
            )


@pytest.mark.asyncio
async def test_alias_clash_with_an_admin_created_alias_says_not_managed(sessionmaker_):
    """The other half of the same message: an alias with no IdM owner really is
    unmanaged, and the operator fixes it here rather than in the IdM."""
    domain = await _seed_domain(sessionmaker_, "clashmsg2.example.com")
    async with sessionmaker_() as session:
        session.add(
            Alias(
                domain_id=domain.id,
                local_part="handmade",
                destination="someone@clashmsg2.example.com",
            )
        )
        await session.commit()

    async with sessionmaker_() as session:
        with pytest.raises(ConflictError, match="is not managed by the IdM"):
            await provisioning_service.upsert_identity(
                session,
                "clash-admin",
                _payload(
                    "jane@clashmsg2.example.com",
                    aliases=["handmade@clashmsg2.example.com"],
                ),
            )


@pytest.mark.asyncio
async def test_rename_onto_an_owned_alias_leaves_no_self_referential_alias(
    sessionmaker_,
):
    """Promoting an owned alias to the primary address must retire the alias.

    `exclude_alias_ids` makes this rename legal, but nothing else reconciles
    the alias away: with `aliases` omitted — an ordinary minimal payload — the
    omitted-collection branch repoints owned aliases at the new primary,
    leaving an alias whose destination is its own address. Delivery survives
    that (`virtual_mailbox_self.cf` terminates self-mappings), but while the
    mailbox is deactivated the still-active alias short-circuits
    `virtual_alias_catchall.cf` and mail that should have reached the domain
    catch-all bounces instead.
    """
    domain = await _seed_domain(sessionmaker_, "selfref.example.com")
    async with sessionmaker_() as session:
        identity = await provisioning_service.upsert_identity(
            session,
            "al-15",
            _payload(
                "jane@selfref.example.com", aliases=["j.doe@selfref.example.com"]
            ),
        )
        mailbox_id = identity.mailbox_id

    async with sessionmaker_() as session:
        # `aliases` omitted entirely: the minimal payload a rename really sends.
        await provisioning_service.upsert_identity(
            session, "al-15", _payload("j.doe@selfref.example.com")
        )

    async with sessionmaker_() as session:
        mailbox = await session.get(Mailbox, mailbox_id)
        assert mailbox.local_part == "j.doe"
        assert await _alias_local_parts(session, domain.id) == []
        links = (await session.execute(select(IdmIdentityAlias))).scalars().all()
        assert links == [], "the ownership link must go with the alias"

        aliases = (await session.execute(select(Alias))).scalars().all()
        assert not [
            a
            for a in aliases
            if f"{a.local_part}@selfref.example.com" == a.destination
        ], "no alias may point at its own address"
