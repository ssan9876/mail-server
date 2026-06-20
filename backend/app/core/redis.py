"""Async Redis client lifecycle helpers."""
from __future__ import annotations

import redis.asyncio as aioredis

from app.core.config import settings


def create_redis_pool() -> aioredis.Redis:
    """Create a Redis client backed by a connection pool (decoded strings)."""
    return aioredis.from_url(
        settings.REDIS_URL,
        encoding="utf-8",
        decode_responses=True,
        health_check_interval=30,
    )
