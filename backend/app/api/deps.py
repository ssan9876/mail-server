"""Shared FastAPI dependencies: DB session, Redis, current user, role guards."""
from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated

import redis.asyncio as aioredis
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.exceptions import AuthenticationError, PermissionDeniedError
from app.core.security import TokenError, decode_token
from app.models.enums import UserRole
from app.models.user import User
from app.services import auth_service, user_service

# auto_error=False so we can raise our own typed AuthenticationError.
_bearer = HTTPBearer(auto_error=False)


async def get_redis(request: Request) -> aioredis.Redis:
    """Return the app-wide Redis client created during lifespan startup."""
    return request.app.state.redis


def get_client_ip(request: Request) -> str | None:
    """Best-effort client IP, honoring the edge proxy's X-Forwarded-For."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


DbDep = Annotated[AsyncSession, Depends(get_db)]
RedisDep = Annotated[aioredis.Redis, Depends(get_redis)]


async def get_token_payload(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    redis: RedisDep,
) -> dict:
    if credentials is None:
        raise AuthenticationError("Not authenticated.")
    try:
        payload = decode_token(credentials.credentials, expected_type="access")
    except TokenError as exc:
        raise AuthenticationError("Invalid or expired token.") from exc

    if await auth_service.is_blacklisted(redis, payload.get("jti", "")):
        raise AuthenticationError("Token has been revoked.")
    return payload


async def get_current_user(
    payload: Annotated[dict, Depends(get_token_payload)],
    db: DbDep,
) -> User:
    # A mailbox-principal token must never satisfy an operator endpoint.
    if payload.get("principal") == "mailbox":
        raise AuthenticationError("This endpoint requires an operator account.")
    user = await user_service.get_by_id(db, uuid.UUID(payload["sub"]))
    if user is None or not user.is_active:
        raise AuthenticationError("User not found or inactive.")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_current_mailbox(
    payload: Annotated[dict, Depends(get_token_payload)],
    db: DbDep,
):
    """Resolve the authenticated mailbox from a mailbox-principal token."""
    from app.models.mailbox import Mailbox  # local import avoids a cycle
    from app.services import mailbox_service

    if payload.get("principal") != "mailbox":
        raise AuthenticationError("This endpoint requires a mailbox account.")
    mailbox: Mailbox | None = await mailbox_service.get_by_id(db, uuid.UUID(payload["sub"]))
    if mailbox is None or not mailbox.is_active:
        raise AuthenticationError("Mailbox not found or inactive.")
    return mailbox


def require_roles(*roles: UserRole) -> Callable[[User], Awaitable[User]]:
    """Dependency factory enforcing that the current user has one of `roles`."""

    async def _guard(current_user: CurrentUser) -> User:
        if current_user.role not in roles:
            raise PermissionDeniedError("Insufficient privileges for this action.")
        return current_user

    return _guard


# Convenience guards.
require_superadmin = require_roles(UserRole.SUPERADMIN)
require_admin = require_roles(UserRole.SUPERADMIN, UserRole.DOMAIN_ADMIN)


async def get_cloudflare_client():
    """Yield a Cloudflare API client and close it after the request."""
    from app.services.dns_service import CloudflareClient

    client = CloudflareClient()
    try:
        yield client
    finally:
        await client.aclose()


def get_dns_resolver():
    """Return the default dnspython-backed resolver (overridable in tests)."""
    from app.services.dns_verify_service import DefaultDnsResolver

    return DefaultDnsResolver()
