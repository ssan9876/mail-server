"""Payload validation — most importantly, that no credential can be sent."""
import pytest
from pydantic import ValidationError

from app.core.dovecot_password import verify_dovecot
from app.core.security import verify_password
from app.schemas.provisioning import (
    UNUSABLE_PASSWORD_HASH,
    IdentityUpsert,
    canonical_hash,
)


def _valid(**overrides):
    base = {"email": "jane@example.com", "status": "active"}
    base.update(overrides)
    return base


def test_minimal_payload_is_valid():
    payload = IdentityUpsert(**_valid())
    assert payload.email == "jane@example.com"
    assert payload.status == "active"


@pytest.mark.parametrize(
    "field", ["password", "password_hash", "credential", "secret", "passwd"]
)
def test_any_credential_field_is_rejected(field):
    """The closed schema is what makes 'no credentials' structural."""
    with pytest.raises(ValidationError):
        IdentityUpsert(**_valid(**{field: "hunter2"}))


def test_unknown_field_is_rejected():
    with pytest.raises(ValidationError):
        IdentityUpsert(**_valid(department="Finance"))


@pytest.mark.parametrize("status", ["pending", "active", "suspended", "deactivated"])
def test_all_four_idm_statuses_are_accepted(status):
    assert IdentityUpsert(**_valid(status=status)).status == status


def test_deprovisioned_is_not_a_status():
    """The counterpart system has no such state — reject it loudly rather than
    silently coercing it."""
    with pytest.raises(ValidationError):
        IdentityUpsert(**_valid(status="deprovisioned"))


def test_absent_collection_is_distinguishable_from_empty_one():
    absent = IdentityUpsert(**_valid())
    empty = IdentityUpsert(**_valid(aliases=[]))
    assert "aliases" not in absent.model_fields_set
    assert "aliases" in empty.model_fields_set
    assert empty.aliases == []


def test_absent_admin_is_distinguishable_from_explicit_null():
    absent = IdentityUpsert(**_valid())
    explicit = IdentityUpsert(**_valid(admin=None))
    assert "admin" not in absent.model_fields_set
    assert "admin" in explicit.model_fields_set


def test_canonical_hash_is_stable_and_order_independent():
    a = IdentityUpsert(**_valid(display_name="Jane", quota_mb=1024))
    b = IdentityUpsert(**{"quota_mb": 1024, "display_name": "Jane", **_valid()})
    assert canonical_hash(a) == canonical_hash(b)


def test_canonical_hash_changes_with_content():
    a = IdentityUpsert(**_valid(quota_mb=1024))
    b = IdentityUpsert(**_valid(quota_mb=2048))
    assert canonical_hash(a) != canonical_hash(b)


def test_canonical_hash_distinguishes_absent_from_null():
    absent = IdentityUpsert(**_valid())
    explicit = IdentityUpsert(**_valid(display_name=None))
    assert canonical_hash(absent) != canonical_hash(explicit)


def test_unusable_password_hash_cannot_be_authenticated_against():
    for candidate in ["", "password", UNUSABLE_PASSWORD_HASH, "!"]:
        assert verify_password(candidate, UNUSABLE_PASSWORD_HASH) is False
        assert verify_dovecot(candidate, UNUSABLE_PASSWORD_HASH) is False


@pytest.mark.parametrize(
    "field", ["password", "password_hash", "credential", "secret", "passwd"]
)
def test_any_credential_field_nested_in_admin_is_rejected(field):
    """Nested smuggling: `extra="forbid"` inside `AdminSpec` must reject
    credential-shaped fields nested deep."""
    with pytest.raises(ValidationError):
        IdentityUpsert(
            **_valid(admin={"role": "domain_admin", field: "hunter2"})
        )


def test_unknown_field_nested_in_admin_is_rejected():
    """Verify `extra="forbid"` on `AdminSpec` rejects non-credential unknown
    fields too, so it's not a name-specific rule."""
    with pytest.raises(ValidationError):
        IdentityUpsert(
            **_valid(admin={"role": "domain_admin", "department": "Finance"})
        )


def test_canonical_hash_distinguishes_absent_from_set_in_nested_admin():
    """Nested `exclude_unset=True` behavior: `admin` with `domains` from
    `default_factory` must hash differently from explicit empty list."""
    absent_domains = IdentityUpsert(
        **_valid(admin={"role": "domain_admin"})
    )
    explicit_empty = IdentityUpsert(
        **_valid(admin={"role": "domain_admin", "domains": []})
    )
    assert canonical_hash(absent_domains) != canonical_hash(explicit_empty)


def test_explicit_null_quota_is_rejected():
    """`mailboxes.quota_mb` is NOT NULL — there is no cleared state to
    express, so an explicit null must fail validation rather than being
    silently treated as absent (which would report convergence while leaving
    the quota stale)."""
    with pytest.raises(ValidationError):
        IdentityUpsert(**_valid(quota_mb=None))


def test_omitted_quota_is_valid_and_absent_from_fields_set():
    payload = IdentityUpsert(**_valid())
    assert payload.quota_mb is None
    assert "quota_mb" not in payload.model_fields_set


@pytest.mark.parametrize(
    "alias",
    [
        "@@acme.example.com",
        "@acme.example.com",
        "no-at-sign",
        "",
        "jane@",
        "jane doe@acme.example.com",
        "jane/doe@acme.example.com",
        ".jane@acme.example.com",
        "jane.@acme.example.com",
    ],
)
def test_malformed_alias_is_rejected_and_named(alias):
    """`_split_address` splits on the LAST `@`, so `"@@acme.example.com"`
    yields a local part of `"@"` — precisely the domain catch-all convention
    `docker/postfix/pgsql/virtual_alias_catchall.cf` matches. One typo would
    route every unrouted address in the domain into a single mailbox, with
    nothing in the audit trail marking it as different from a normal alias.
    The error must also name the offending entry: it is what the operator sees
    when triaging the dead-lettered event.
    """
    with pytest.raises(ValidationError) as excinfo:
        IdentityUpsert(**_valid(aliases=[alias]))
    assert repr(alias) in str(excinfo.value)


@pytest.mark.parametrize(
    "alias",
    [
        "j.doe@acme.example.com",
        "j+tag@acme.example.com",
        "j_d-1@acme.example.com",
        "jane@sub.acme.example.com",
    ],
)
def test_ordinary_alias_addresses_are_accepted(alias):
    assert IdentityUpsert(**_valid(aliases=[alias])).aliases == [alias]


def test_one_bad_entry_rejects_the_whole_alias_list():
    with pytest.raises(ValidationError):
        IdentityUpsert(
            **_valid(aliases=["good@acme.example.com", "@@acme.example.com"])
        )


def test_explicit_null_aliases_is_rejected():
    """`[]` already expresses "remove all IdM-owned aliases", so null is
    redundant rather than a third meaning. Accepted, it would be a silent
    divergence: present in `model_fields_set`, hashing as a distinct payload,
    advancing `last_synced_at` and writing a success audit row — while
    changing nothing. Same ruling as `quota_mb`."""
    with pytest.raises(ValidationError):
        IdentityUpsert(**_valid(aliases=None))


def test_omitted_aliases_is_valid_and_absent_from_fields_set():
    payload = IdentityUpsert(**_valid())
    assert payload.aliases is None
    assert "aliases" not in payload.model_fields_set


def test_empty_alias_list_is_valid_and_present_in_fields_set():
    payload = IdentityUpsert(**_valid(aliases=[]))
    assert payload.aliases == []
    assert "aliases" in payload.model_fields_set


def test_unusable_password_hash_names_an_unmatchable_scheme():
    """Dovecot reads `mailboxes.password_hash` directly and interprets a
    prefix-less value under `default_pass_scheme`. A bare placeholder is inert
    only by accident — it fails today because Dovecot 2.3 defaults to MD5 and
    the string is not valid MD5, but `default_pass_scheme = PLAIN` (plausible
    once app passwords arrive) would turn it into a working password shared by
    every active provisioned mailbox. An explicit scheme prefix removes the
    dependency on that setting entirely, and `*` is the conventional crypt(3)
    "no password" marker that no crypt implementation can produce."""
    assert UNUSABLE_PASSWORD_HASH.startswith("{CRYPT}")
    assert UNUSABLE_PASSWORD_HASH[len("{CRYPT}")] == "*"


def test_canonical_hash_nested_admin_is_order_independent():
    """Verify that nested `AdminSpec` fields in different order hash
    identically."""
    a = IdentityUpsert(
        **_valid(admin={"role": "superadmin", "domains": ["acme.com"]})
    )
    b = IdentityUpsert(
        **_valid(admin={"domains": ["acme.com"], "role": "superadmin"})
    )
    assert canonical_hash(a) == canonical_hash(b)
