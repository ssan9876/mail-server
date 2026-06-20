"""Unit tests for the security primitives (no I/O)."""
import pytest

from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_opaque_token,
    hash_opaque_token,
    hash_password,
    verify_password,
    TokenError,
)


def test_password_hash_roundtrip():
    h = hash_password("correct horse battery staple")
    assert h != "correct horse battery staple"
    assert verify_password("correct horse battery staple", h)
    assert not verify_password("wrong", h)


def test_verify_password_handles_garbage_hash():
    # Must never raise on a malformed stored hash.
    assert verify_password("anything", "not-a-real-hash") is False


def test_access_token_roundtrip():
    token, jti, _exp = create_access_token("user-123", "superadmin")
    payload = decode_token(token, expected_type="access")
    assert payload["sub"] == "user-123"
    assert payload["role"] == "superadmin"
    assert payload["jti"] == jti
    assert payload["type"] == "access"


def test_decode_rejects_wrong_token_type():
    refresh, _jti, _exp = create_refresh_token("user-123")
    with pytest.raises(TokenError):
        decode_token(refresh, expected_type="access")


def test_decode_rejects_tampered_token():
    token, _jti, _exp = create_access_token("user-123", "user")
    with pytest.raises(TokenError):
        decode_token(token + "tampered", expected_type="access")


def test_opaque_token_hash_is_deterministic():
    raw = generate_opaque_token()
    assert hash_opaque_token(raw) == hash_opaque_token(raw)
    assert hash_opaque_token(raw) != raw
