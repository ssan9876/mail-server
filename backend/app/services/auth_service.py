"""
Authentication orchestration: credential checks with brute-force lockout,
JWT issuance, Redis-backed refresh-token store + access-token blacklist, and
refresh-token rotation.

Redis key layout (all values short, all keys TTL'd):
  refresh:{jti}        -> user_id        (lives for the refresh lifetime)
  bl:{jti}             -> "1"            (access blacklist, until token expiry)
  loginfail:{email}    -> attempt count  (sliding lockout window)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
)
from app.core.exceptions import AuthenticationError, RateLimitError
from app.models.user import User
from app.schemas.auth import TokenPair
from app.services import user_service

_REFRESH_PREFIX = "refresh:"
_BLACKLIST_PREFIX = "bl:"
_LOGINFAIL_PREFIX = "loginfail:"
_LOCKOUT_WINDOW_SECONDS = 900  # 15 minutes


# --------------------------------------------------------------------------- #
# Brute-force lockout
# --------------------------------------------------------------------------- #
async def _assert_not_locked(redis: aioredis.Redis, email: str) -> None:
    attempts = await redis.get(f"{_LOGINFAIL_PREFIX}{email}")
    if attempts is not None and int(attempts) >= settings.RATE_LIMIT_LOGIN_ATTEMPTS:
        raise RateLimitError("Too many failed login attempts. Try again later.")


async def _record_failure(redis: aioredis.Redis, email: str) -> None:
    key = f"{_LOGINFAIL_PREFIX}{email}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, _LOCKOUT_WINDOW_SECONDS)


async def _clear_failures(redis: aioredis.Redis, email: str) -> None:
    await redis.delete(f"{_LOGINFAIL_PREFIX}{email}")


# --------------------------------------------------------------------------- #
# Token store
# --------------------------------------------------------------------------- #
def _ttl_seconds(expires_at: datetime) -> int:
    return max(1, int((expires_at - datetime.now(timezone.utc)).total_seconds()))


async def _issue_pair(redis: aioredis.Redis, user: User) -> TokenPair:
    access_token, _, access_exp = create_access_token(str(user.id), user.role.value)
    refresh_token, refresh_jti, refresh_exp = create_refresh_token(str(user.id))

    await redis.set(
        f"{_REFRESH_PREFIX}{refresh_jti}",
        str(user.id),
        ex=_ttl_seconds(refresh_exp),
    )
    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


# --------------------------------------------------------------------------- #
# Public operations
# --------------------------------------------------------------------------- #
async def authenticate(
    db: AsyncSession, redis: aioredis.Redis, email: str, password: str
) -> User:
    """Validate credentials, enforcing lockout. Raises on failure."""
    email = email.lower()
    await _assert_not_locked(redis, email)

    user = await user_service.get_by_email(db, email)
    # Always run a hash comparison to keep timing uniform for unknown emails.
    valid = bool(user) and user.is_active and verify_password(password, user.password_hash)
    if not valid:
        await _record_failure(redis, email)
        raise AuthenticationError("Invalid email or password.")

    await _clear_failures(redis, email)
    return user


async def login(db: AsyncSession, redis: aioredis.Redis, email: str, password: str) -> TokenPair:
    user = await authenticate(db, redis, email, password)
    return await _issue_pair(redis, user)


async def refresh(db: AsyncSession, redis: aioredis.Redis, refresh_token: str) -> TokenPair:
    """Rotate a refresh token: validate, revoke the old jti, issue a new pair."""
    from app.core.security import TokenError

    try:
        payload = decode_token(refresh_token, expected_type="refresh")
    except TokenError as exc:
        raise AuthenticationError("Invalid refresh token.") from exc

    jti = payload.get("jti", "")
    stored_user_id = await redis.get(f"{_REFRESH_PREFIX}{jti}")
    if stored_user_id is None or stored_user_id != payload.get("sub"):
        raise AuthenticationError("Refresh token is expired or revoked.")

    # One-time use: invalidate immediately (rotation).
    await redis.delete(f"{_REFRESH_PREFIX}{jti}")

    user = await user_service.get_by_id(db, uuid.UUID(payload["sub"]))
    if user is None or not user.is_active:
        raise AuthenticationError("User no longer active.")
    return await _issue_pair(redis, user)


async def logout(redis: aioredis.Redis, access_payload: dict, refresh_token: str | None) -> None:
    """Blacklist the current access token and revoke an optional refresh token."""
    access_jti = access_payload.get("jti")
    access_exp = access_payload.get("exp")
    if access_jti and access_exp:
        ttl = _ttl_seconds(datetime.fromtimestamp(access_exp, tz=timezone.utc))
        await redis.set(f"{_BLACKLIST_PREFIX}{access_jti}", "1", ex=ttl)

    if refresh_token:
        from app.core.security import TokenError

        try:
            payload = decode_token(refresh_token, expected_type="refresh")
            await redis.delete(f"{_REFRESH_PREFIX}{payload.get('jti', '')}")
        except TokenError:
            pass  # nothing to revoke


async def is_blacklisted(redis: aioredis.Redis, jti: str) -> bool:
    return bool(await redis.exists(f"{_BLACKLIST_PREFIX}{jti}"))
