"""
Mailbox self-service authentication (the second principal type).

Mirrors auth_service but for mailbox accounts: credentials are verified against
the Dovecot-format password hash, and tokens carry principal="mailbox". Uses
separate Redis namespaces for the refresh store and lockout counter; the access
blacklist is shared with the user flow (jti is globally unique).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.dovecot_password import verify_dovecot
from app.core.exceptions import AuthenticationError, RateLimitError
from app.core.security import (
    TokenError,
    create_mailbox_access_token,
    create_refresh_token,
    decode_token,
)
from app.models.mailbox import Mailbox
from app.schemas.auth import TokenPair
from app.services import mailbox_service

_MREFRESH_PREFIX = "mrefresh:"
_BLACKLIST_PREFIX = "bl:"
_MLOGINFAIL_PREFIX = "mloginfail:"
_LOCKOUT_WINDOW_SECONDS = 900


async def _assert_not_locked(redis: aioredis.Redis, email: str) -> None:
    attempts = await redis.get(f"{_MLOGINFAIL_PREFIX}{email}")
    if attempts is not None and int(attempts) >= settings.RATE_LIMIT_LOGIN_ATTEMPTS:
        raise RateLimitError("Too many failed login attempts. Try again later.")


async def _record_failure(redis: aioredis.Redis, email: str) -> None:
    key = f"{_MLOGINFAIL_PREFIX}{email}"
    if await redis.incr(key) == 1:
        await redis.expire(key, _LOCKOUT_WINDOW_SECONDS)


def _ttl_seconds(expires_at: datetime) -> int:
    return max(1, int((expires_at - datetime.now(timezone.utc)).total_seconds()))


async def _issue_pair(redis: aioredis.Redis, mailbox: Mailbox) -> TokenPair:
    access_token, _, _ = create_mailbox_access_token(str(mailbox.id))
    refresh_token, refresh_jti, refresh_exp = create_refresh_token(str(mailbox.id))
    await redis.set(
        f"{_MREFRESH_PREFIX}{refresh_jti}", str(mailbox.id), ex=_ttl_seconds(refresh_exp)
    )
    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


async def authenticate(
    db: AsyncSession, redis: aioredis.Redis, email: str, password: str
) -> Mailbox:
    email = email.lower()
    await _assert_not_locked(redis, email)

    mailbox = await mailbox_service.get_by_address(db, email)
    valid = bool(mailbox) and mailbox.is_active and verify_dovecot(password, mailbox.password_hash)
    if not valid:
        await _record_failure(redis, email)
        raise AuthenticationError("Invalid email or password.")

    await redis.delete(f"{_MLOGINFAIL_PREFIX}{email}")
    return mailbox


async def login(db: AsyncSession, redis: aioredis.Redis, email: str, password: str) -> TokenPair:
    mailbox = await authenticate(db, redis, email, password)
    return await _issue_pair(redis, mailbox)


async def refresh(db: AsyncSession, redis: aioredis.Redis, refresh_token: str) -> TokenPair:
    try:
        payload = decode_token(refresh_token, expected_type="refresh")
    except TokenError as exc:
        raise AuthenticationError("Invalid refresh token.") from exc

    jti = payload.get("jti", "")
    stored = await redis.get(f"{_MREFRESH_PREFIX}{jti}")
    if stored is None or stored != payload.get("sub"):
        raise AuthenticationError("Refresh token is expired or revoked.")
    await redis.delete(f"{_MREFRESH_PREFIX}{jti}")

    mailbox = await mailbox_service.get_by_id(db, uuid.UUID(payload["sub"]))
    if mailbox is None or not mailbox.is_active:
        raise AuthenticationError("Mailbox no longer active.")
    return await _issue_pair(redis, mailbox)


async def logout(
    redis: aioredis.Redis, access_payload: dict, refresh_token: str | None
) -> None:
    access_jti = access_payload.get("jti")
    access_exp = access_payload.get("exp")
    if access_jti and access_exp:
        ttl = _ttl_seconds(datetime.fromtimestamp(access_exp, tz=timezone.utc))
        await redis.set(f"{_BLACKLIST_PREFIX}{access_jti}", "1", ex=ttl)
    if refresh_token:
        try:
            payload = decode_token(refresh_token, expected_type="refresh")
            await redis.delete(f"{_MREFRESH_PREFIX}{payload.get('jti', '')}")
        except TokenError:
            pass


async def change_password(
    db: AsyncSession, mailbox: Mailbox, current_password: str, new_password: str
) -> None:
    from app.core.dovecot_password import hash_for_dovecot

    if not verify_dovecot(current_password, mailbox.password_hash):
        raise AuthenticationError("Current password is incorrect.")
    mailbox.password_hash = hash_for_dovecot(new_password)
    await db.commit()
