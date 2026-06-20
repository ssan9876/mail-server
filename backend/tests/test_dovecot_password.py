"""Unit tests for Dovecot-compatible password hashing."""
from app.core.dovecot_password import DOVECOT_SCHEME, hash_for_dovecot, verify_dovecot


def test_hash_has_scheme_prefix():
    h = hash_for_dovecot("s3cret-passw0rd")
    assert h.startswith(f"{{{DOVECOT_SCHEME}}}")
    assert "$argon2id$" in h  # PHC string Dovecot can parse


def test_verify_roundtrip():
    h = hash_for_dovecot("s3cret-passw0rd")
    assert verify_dovecot("s3cret-passw0rd", h)
    assert not verify_dovecot("wrong", h)


def test_verify_tolerates_missing_prefix():
    h = hash_for_dovecot("abc12345").split("}", 1)[1]  # strip {ARGON2ID}
    assert verify_dovecot("abc12345", h)
