"""
Security primitives: password hashing (Argon2), JWT issuance/verification,
and one-way token hashing for password-reset tokens.

Pure functions with no I/O — Redis-backed revocation lives in the auth service.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

# Argon2id — memory-hard, the current OWASP-recommended default.
_pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"


class TokenError(Exception):
    """Raised when a JWT is malformed, expired, or of the wrong type."""


# --------------------------------------------------------------------------- #
# Passwords
# --------------------------------------------------------------------------- #
def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _pwd_context.verify(password, password_hash)
    except ValueError:
        # Malformed/unknown hash format — treat as a non-match, never raise.
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _pwd_context.needs_update(password_hash)


# --------------------------------------------------------------------------- #
# JWTs
# --------------------------------------------------------------------------- #
def _create_token(
    subject: str,
    token_type: str,
    expires_delta: timedelta,
    extra_claims: dict | None = None,
) -> tuple[str, str, datetime]:
    """Return (encoded_token, jti, expires_at)."""
    now = datetime.now(timezone.utc)
    expires_at = now + expires_delta
    jti = str(uuid4())
    payload: dict = {
        "sub": str(subject),
        "type": token_type,
        "jti": jti,
        "iat": now,
        "exp": expires_at,
    }
    if extra_claims:
        payload.update(extra_claims)
    token = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return token, jti, expires_at


# Principal kinds carried in the access token's "principal" claim so a single
# decode path can distinguish admin/operator users from mailbox accounts.
PRINCIPAL_USER = "user"
PRINCIPAL_MAILBOX = "mailbox"


def create_access_token(subject: str, role: str) -> tuple[str, str, datetime]:
    return _create_token(
        subject,
        ACCESS_TOKEN_TYPE,
        timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES),
        extra_claims={"principal": PRINCIPAL_USER, "role": role},
    )


def create_mailbox_access_token(subject: str) -> tuple[str, str, datetime]:
    return _create_token(
        subject,
        ACCESS_TOKEN_TYPE,
        timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES),
        extra_claims={"principal": PRINCIPAL_MAILBOX},
    )


def create_refresh_token(subject: str) -> tuple[str, str, datetime]:
    return _create_token(
        subject,
        REFRESH_TOKEN_TYPE,
        timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS),
    )


def decode_token(token: str, expected_type: str | None = None) -> dict:
    """Decode + validate a JWT. Raises TokenError on any problem."""
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except JWTError as exc:
        raise TokenError(str(exc)) from exc

    if expected_type is not None and payload.get("type") != expected_type:
        raise TokenError(f"expected {expected_type} token, got {payload.get('type')}")
    return payload


# --------------------------------------------------------------------------- #
# Opaque tokens (password reset)
# --------------------------------------------------------------------------- #
def generate_opaque_token() -> str:
    """A high-entropy URL-safe token to email to the user (never stored raw)."""
    return secrets.token_urlsafe(32)


def hash_opaque_token(raw_token: str) -> str:
    """Deterministic SHA-256 hash stored in the DB for lookup/verification."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
