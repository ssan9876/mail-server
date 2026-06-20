"""
Mailbox password-reset flow (self-service, unauthenticated).

Security properties:
  - Only the SHA-256 hash of the token is stored; the raw token is emailed.
  - `request_reset` never reveals whether an address exists (no enumeration).
  - Tokens are single-use and expire after `TOKEN_TTL`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dovecot_password import hash_for_dovecot
from app.core.exceptions import AuthenticationError
from app.core.security import generate_opaque_token, hash_opaque_token
from app.models.password_reset import PasswordResetToken
from app.services import mailbox_service

TOKEN_TTL = timedelta(hours=1)


async def request_reset(db: AsyncSession, email: str) -> str | None:
    """Create a reset token for the mailbox, if it exists. Returns the raw
    token (to be emailed), or None when no mailbox matches."""
    mailbox = await mailbox_service.get_by_address(db, email)
    if mailbox is None:
        return None

    raw_token = generate_opaque_token()
    db.add(
        PasswordResetToken(
            mailbox_id=mailbox.id,
            token_hash=hash_opaque_token(raw_token),
            expires_at=datetime.now(timezone.utc) + TOKEN_TTL,
        )
    )
    await db.commit()
    return raw_token


async def confirm_reset(db: AsyncSession, raw_token: str, new_password: str) -> None:
    """Validate the token and set the mailbox's new password. Raises on any
    invalid/expired/used token."""
    token_hash = hash_opaque_token(raw_token)
    result = await db.execute(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
    )
    token = result.scalar_one_or_none()

    now = datetime.now(timezone.utc)
    expires_at = token.expires_at if token else None
    # Normalize to aware datetime for comparison (SQLite may return naive).
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if token is None or token.used or expires_at is None or expires_at < now:
        raise AuthenticationError("Invalid or expired reset token.")

    from app.models.mailbox import Mailbox

    mailbox = await db.get(Mailbox, token.mailbox_id)
    if mailbox is None:
        raise AuthenticationError("Invalid or expired reset token.")

    mailbox.password_hash = hash_for_dovecot(new_password)
    token.used = True
    await db.commit()
