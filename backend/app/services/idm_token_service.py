"""
Service tokens for the IdM provisioning API.

Deliberately separate from every user/mailbox credential path: these tokens are
not JWTs and are never accepted by `get_current_user`, so a provisioning
credential can never satisfy an operator endpoint (and vice versa) by
construction rather than by a role check.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, PermissionDeniedError
from app.models.idm import IdmServiceToken

TOKEN_PREFIX = "idm_"
_TOKEN_BYTES = 32
_PREFIX_LEN = 16


def generate_token() -> str:
    """A high-entropy, url-safe token with an identifying prefix."""
    return f"{TOKEN_PREFIX}{secrets.token_urlsafe(_TOKEN_BYTES)}"


def hash_token(raw_token: str) -> str:
    """Deterministic SHA-256 used for lookup. See IdmServiceToken's docstring
    for why this is not a slow hash."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


async def create_token(
    db: AsyncSession,
    *,
    name: str,
    created_by: uuid.UUID | None,
    expires_at: datetime | None = None,
) -> tuple[IdmServiceToken, str]:
    """Issue a token. The raw value is returned once and never stored."""
    raw = generate_token()
    token = IdmServiceToken(
        name=name,
        token_hash=hash_token(raw),
        prefix=raw[:_PREFIX_LEN],
        created_by=created_by,
        expires_at=expires_at,
    )
    db.add(token)
    await db.commit()
    await db.refresh(token)
    return token, raw


async def verify_token(db: AsyncSession, raw_token: str) -> IdmServiceToken:
    """Resolve a raw token, or raise PermissionDeniedError.

    Every rejection raises the same error with the same message: a caller must
    not be able to tell an unknown token from a revoked or expired one.
    """
    digest = hash_token(raw_token)
    result = await db.execute(
        select(IdmServiceToken).where(IdmServiceToken.token_hash == digest)
    )
    token = result.scalar_one_or_none()

    if token is None or not secrets.compare_digest(token.token_hash, digest):
        raise PermissionDeniedError("Invalid service token.")
    if not token.is_active:
        raise PermissionDeniedError("Invalid service token.")
    if token.expires_at is not None:
        expires_at = token.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            raise PermissionDeniedError("Invalid service token.")

    token.last_used_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(token)
    return token


async def list_tokens(db: AsyncSession) -> list[IdmServiceToken]:
    result = await db.execute(
        select(IdmServiceToken).order_by(IdmServiceToken.created_at.desc())
    )
    return list(result.scalars().all())


async def revoke_token(db: AsyncSession, token_id: uuid.UUID) -> IdmServiceToken:
    token = await db.get(IdmServiceToken, token_id)
    if token is None:
        raise NotFoundError("Service token not found.")
    token.is_active = False
    await db.commit()
    await db.refresh(token)
    return token
