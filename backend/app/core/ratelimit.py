"""Shared slowapi limiter instance.

Lives in its own module so both `main` and individual routers can import it
without creating an import cycle. Uses Redis storage in production and an
in-process store otherwise (tests, local single-process dev).
"""
from slowapi import Limiter

from app.core.client_ip import client_ip_or_unknown
from app.core.config import settings

# Key on the X-Forwarded-For hop appended by our own nginx: behind the proxy,
# request.client.host is always the proxy's address, which would collapse every
# client into a single shared bucket (one abuser exhausts the limit for all).
limiter = Limiter(
    key_func=client_ip_or_unknown,
    default_limits=[f"{settings.RATE_LIMIT_API_PER_MINUTE}/minute"],
    storage_uri=settings.REDIS_URL if settings.is_production else "memory://",
    headers_enabled=True,
)
