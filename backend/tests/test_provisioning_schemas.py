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
