"""
Resolve the real client IP behind the trusted edge proxy.

The backend is only reachable through our own nginx (internal Docker network),
which *appends* the connecting address to X-Forwarded-For
(`$proxy_add_x_forwarded_for`). That means the LAST entry is the address nginx
actually saw, while earlier entries are client-supplied and trivially spoofed.
Trusting the first entry would let an attacker forge audit-log IPs and evade
per-client rate limiting by rotating a fake header value.
"""
from __future__ import annotations

from starlette.requests import Request


def client_ip(request: Request) -> str | None:
    """Best-effort real client IP: last X-Forwarded-For hop, else the peer."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        last_hop = forwarded.split(",")[-1].strip()
        if last_hop:
            return last_hop
    return request.client.host if request.client else None


def client_ip_or_unknown(request: Request) -> str:
    """Rate-limiter key function: never returns None."""
    return client_ip(request) or "unknown"
