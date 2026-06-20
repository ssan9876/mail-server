"""Shared slowapi limiter instance.

Lives in its own module so both `main` and individual routers can import it
without creating an import cycle. Uses Redis storage in production and an
in-process store otherwise (tests, local single-process dev).
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[f"{settings.RATE_LIMIT_API_PER_MINUTE}/minute"],
    storage_uri=settings.REDIS_URL if settings.is_production else "memory://",
    headers_enabled=True,
)
