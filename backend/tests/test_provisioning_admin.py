"""Admin (control-plane user) convergence."""
import pytest
from sqlalchemy import select

from app.core.exceptions import ConflictError, UnprocessableError
from app.core.security import verify_password
from app.models.domain import Domain
from app.models.enums import UserRole
from app.models.idm import IdmIdentity
from app.models.user import User
from app.schemas.provisioning import UNUSABLE_PASSWORD_HASH, IdentityUpsert
from app.services import provisioning_service, user_service


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
        # Revocation must also land in the ROLE dimension, not only in
        # `is_active` — see the next test for the sequence that proves why.
        assert user.role is UserRole.USER


@pytest.mark.asyncio
async def test_null_admin_revocation_survives_a_later_push_that_omits_admin(sessionmaker_):
    """Revocation must not be undone by an ordinary, unrelated later push.

    Regression test for a real hole. `admin: null` used to set only
    `is_active = False`, leaving `role` at SUPERADMIN/DOMAIN_ADMIN and
    `identity.user_id` still linked. But `is_active` is ALSO written
    unconditionally from the status mapping on every push, so the next
    ordinary payload that omitted `admin` and carried `status: "active"` —
    here a quota change, but equally a display-name edit or the connector
    dropping a now-absent `mail_admin_role` attribute — restored the operator
    at their previous role. Neither pre-existing test caught it:
    `test_explicit_null_admin_deactivates_without_deleting` stops at the
    revocation, and `test_omitted_admin_leaves_the_record_alone` omits `admin`
    after a GRANT rather than after a REVOKE.
    """
    await _seed_domain(sessionmaker_, "ad9.example.com")
    async with sessionmaker_() as session:
        identity = await provisioning_service.upsert_identity(
            session, "ad-9", _payload("jane@ad9.example.com", admin={"role": "superadmin"})
        )
        user_id = identity.user_id

    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session, "ad-9", _payload("jane@ad9.example.com", admin=None)
        )

    # An ordinary push, unrelated to admin: different payload hash, so the
    # no-op short circuit does not fire and convergence really runs.
    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session, "ad-9", _payload("jane@ad9.example.com", quota_mb=8192)
        )
        user = await session.get(User, user_id)
        assert user is not None
        assert user.role is UserRole.USER, "a revoked operator must not regain their role"
        assert user.role is not UserRole.SUPERADMIN


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
@pytest.mark.parametrize("status", ["deactivated", "suspended", "pending"])
async def test_inactive_status_revokes_operator_access_when_admin_is_omitted(
    sessionmaker_, status
):
    """`is_active` is derived from `status`, never from `admin`.

    A real offboarding push is `{"email": ..., "status": "deactivated"}` with
    no `admin` key at all — the IdM has no reason to restate an admin block for
    someone who just left. `test_deactivated_status_also_deactivates_the_admin_record`
    re-sends `admin`, so it never covered this path, and the absent-collection
    rule ("a minimal payload cannot wipe aliases") was wrongly extended to a
    field that is not a collection and was not absent.
    """
    domain_name = f"off-{status}.example.com"
    email = f"jane@{domain_name}"
    external_id = f"off-{status}"
    await _seed_domain(sessionmaker_, domain_name)

    async with sessionmaker_() as session:
        identity = await provisioning_service.upsert_identity(
            session, external_id, _payload(email, admin={"role": "superadmin"})
        )
        user_id = identity.user_id
        assert (await session.get(User, user_id)).is_active is True

    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session, external_id, _payload(email, status=status)
        )

    async with sessionmaker_() as session:
        user = await session.get(User, user_id)
        assert user.is_active is False, "an offboarded employee keeps mail-admin access"
        assert user.role is UserRole.SUPERADMIN, (
            "role is governed by `admin`, which was absent — only `is_active` moves"
        )


@pytest.mark.asyncio
async def test_reactivation_restores_operator_access_when_admin_is_omitted(sessionmaker_):
    """The status mapping is symmetric: if an absent `admin` cannot stop
    `deactivated` from revoking access, it must not stop `active` from
    restoring it either."""
    await _seed_domain(sessionmaker_, "reon.example.com")
    email = "jane@reon.example.com"

    async with sessionmaker_() as session:
        identity = await provisioning_service.upsert_identity(
            session, "reon-1", _payload(email, admin={"role": "domain_admin"})
        )
        user_id = identity.user_id

    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session, "reon-1", _payload(email, status="deactivated")
        )
    async with sessionmaker_() as session:
        assert (await session.get(User, user_id)).is_active is False

    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session, "reon-1", _payload(email, status="active")
        )

    async with sessionmaker_() as session:
        user = await session.get(User, user_id)
        assert user.is_active is True
        assert user.role is UserRole.DOMAIN_ADMIN


@pytest.mark.asyncio
async def test_explicit_null_admin_beats_an_active_status(sessionmaker_):
    """Ordering guard for the fix above: the status mapping now runs for every
    linked user, but `"admin": null` must still win — it means "no longer an
    operator", independent of whether the person is still employed."""
    await _seed_domain(sessionmaker_, "nulladm.example.com")
    email = "jane@nulladm.example.com"

    async with sessionmaker_() as session:
        identity = await provisioning_service.upsert_identity(
            session, "nulladm-1", _payload(email, admin={"role": "superadmin"})
        )
        user_id = identity.user_id

    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session, "nulladm-1", _payload(email, status="active", admin=None)
        )

    async with sessionmaker_() as session:
        assert (await session.get(User, user_id)).is_active is False


@pytest.mark.asyncio
async def test_unknown_admin_domain_is_rejected(sessionmaker_):
    await _seed_domain(sessionmaker_, "ad6.example.com")
    async with sessionmaker_() as session:
        # The message must name the specific domain: it is what an operator
        # sees when triaging a dead-lettered sync event.
        with pytest.raises(UnprocessableError, match="nope.example.com"):
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


@pytest.mark.asyncio
async def test_admin_email_collision_with_a_non_idm_user_is_rejected(sessionmaker_):
    """`users.email` is unique, and not every row is IdM-managed.

    The bootstrap superadmin (and any operator created by hand) has a `users`
    row with no mailbox at all, so `_converge_mailbox`'s local_part/alias
    check never sees it — a colliding admin push would otherwise sail past
    that check and hit the raw unique constraint on `users.email` directly.
    """
    domain = await _seed_domain(sessionmaker_, "ad8.example.com")
    async with sessionmaker_() as session:
        await user_service.create_user(
            session, email="jane@ad8.example.com", password="not-usable-by-the-idm"
        )

    async with sessionmaker_() as session:
        with pytest.raises(ConflictError, match="jane@ad8.example.com"):
            await provisioning_service.upsert_identity(
                session,
                "ad-8",
                _payload(
                    "jane@ad8.example.com",
                    admin={"role": "domain_admin", "domains": ["ad8.example.com"]},
                ),
            )

    async with sessionmaker_() as session:
        users = (
            await session.execute(select(User).where(User.email == "jane@ad8.example.com"))
        ).scalars().all()
        assert len(users) == 1, "no duplicate/partial user row from the failed push"
        owned = await session.get(Domain, domain.id)
        assert owned.owner_id is None, "domain ownership must not have been applied"


@pytest.mark.asyncio
async def test_admin_rename_collision_with_a_different_pre_existing_user_is_rejected(
    sessionmaker_,
):
    """The email-collision guard in `_converge_admin` covers both the create
    path (tested above) and the rename path, by its placement before the
    create-vs-update branch. This exercises the rename path specifically: an
    existing IdM-managed admin renamed onto an email already held by a
    DIFFERENT pre-existing user must raise ConflictError (409), not an
    IntegrityError from the raw unique constraint on `users.email`.
    """
    await _seed_domain(sessionmaker_, "ad11.example.com")

    async with sessionmaker_() as session:
        # A pre-existing, non-IdM-managed user occupying the target email.
        await user_service.create_user(
            session, email="occupied@ad11.example.com", password="not-usable-by-the-idm"
        )

    async with sessionmaker_() as session:
        # A separate, already-provisioned IdM-managed admin.
        identity = await provisioning_service.upsert_identity(
            session,
            "ad-11",
            _payload("mover@ad11.example.com", admin={"role": "domain_admin"}),
        )
        mover_user_id = identity.user_id

    async with sessionmaker_() as session:
        with pytest.raises(ConflictError, match="occupied@ad11.example.com"):
            await provisioning_service.upsert_identity(
                session,
                "ad-11",
                _payload("occupied@ad11.example.com", admin={"role": "domain_admin"}),
            )

    async with sessionmaker_() as session:
        mover = await session.get(User, mover_user_id)
        assert mover.email == "mover@ad11.example.com", "the rename must not have applied"
        occupied_users = (
            await session.execute(
                select(User).where(User.email == "occupied@ad11.example.com")
            )
        ).scalars().all()
        assert len(occupied_users) == 1, "no duplicate/partial user row from the failed rename"


@pytest.mark.asyncio
async def test_422_on_unknown_admin_domain_leaves_the_database_unchanged(sessionmaker_):
    await _seed_domain(sessionmaker_, "ad10.example.com")
    async with sessionmaker_() as session:
        with pytest.raises(UnprocessableError):
            await provisioning_service.upsert_identity(
                session,
                "ad-10",
                _payload(
                    "jane@ad10.example.com",
                    admin={
                        "role": "domain_admin",
                        # A known domain first, so the loop applies it in
                        # memory before the unknown one aborts the call.
                        "domains": ["ad10.example.com", "nope.example.com"],
                    },
                ),
            )

    async with sessionmaker_() as session:
        users = (await session.execute(select(User))).scalars().all()
        assert users == [], "no half-created user row must survive a 422"
        owned = (
            await session.execute(
                select(Domain).where(Domain.name == "ad10.example.com")
            )
        ).scalar_one()
        assert owned.owner_id is None, "domain ownership must not have been partially applied"


@pytest.mark.asyncio
async def test_domain_reassignment_from_another_owner_is_reported(sessionmaker_):
    """Domain ownership is last-write-wins by deliberate ruling, but never
    silently: `_converge_admin` reports every reassignment it performs so
    Task 8 can fold it into the audit entry."""
    domain = await _seed_domain(sessionmaker_, "ad9.example.com")
    async with sessionmaker_() as session:
        await provisioning_service.upsert_identity(
            session,
            "ad-9a",
            _payload(
                "alice@ad9.example.com",
                admin={"role": "domain_admin", "domains": ["ad9.example.com"]},
            ),
        )

    async with sessionmaker_() as session:
        alice = await user_service.get_by_email(session, "alice@ad9.example.com")

        identity_b = IdmIdentity(external_id="ad-9b")
        session.add(identity_b)
        await session.flush()

        reassigned = await provisioning_service._converge_admin(
            session,
            identity_b,
            "bob@ad9.example.com",
            _payload(
                "bob@ad9.example.com",
                admin={"role": "domain_admin", "domains": ["ad9.example.com"]},
            ),
        )

        assert reassigned == [{"domain": "ad9.example.com", "from": str(alice.id)}]

        owned = await session.get(Domain, domain.id)
        assert owned.owner_id == identity_b.user_id
        assert owned.owner_id != alice.id
