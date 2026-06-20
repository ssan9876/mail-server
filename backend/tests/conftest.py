"""
Pytest fixtures.

Test isolation strategy:
  - SQLite in-memory DB (StaticPool so every connection sees the same data),
    created fresh per test.
  - fakeredis for the Redis-backed token store / lockout.
  - FastAPI dependency overrides swap the real DB/Redis for the test doubles.

Environment variables are set BEFORE importing any app module so the cached
Settings singleton picks them up.
"""
import base64
import os
import tempfile

# Export DKIM keys to a throwaway dir during tests, never a real /dkim volume.
os.environ.setdefault("DKIM_KEYS_PATH", tempfile.mkdtemp(prefix="dkim-test-"))
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("REDIS_PASSWORD", "")
os.environ.setdefault("ADMIN_EMAIL", "admin@example.com")
os.environ.setdefault("ADMIN_PASSWORD", "supersecret-admin-pw")
# A valid 32-byte url-safe base64 Fernet key for the crypto module.
os.environ.setdefault(
    "SECRETS_ENCRYPTION_KEY", base64.urlsafe_b64encode(b"0" * 32).decode()
)

import fakeredis.aioredis  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.api.deps import get_redis  # noqa: E402
from app.core.database import get_db  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Base  # noqa: E402
from app.models.enums import UserRole  # noqa: E402
from app.services import user_service  # noqa: E402

ADMIN_EMAIL = "admin@example.com"
ADMIN_PASSWORD = "supersecret-admin-pw"


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def sessionmaker_(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def redis_client():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest_asyncio.fixture
async def app(sessionmaker_, redis_client):
    application = create_app()

    async def _override_get_db():
        async with sessionmaker_() as session:
            yield session

    application.dependency_overrides[get_db] = _override_get_db
    application.dependency_overrides[get_redis] = lambda: redis_client
    return application


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def superadmin(sessionmaker_):
    async with sessionmaker_() as session:
        return await user_service.create_user(
            session,
            email=ADMIN_EMAIL,
            password=ADMIN_PASSWORD,
            role=UserRole.SUPERADMIN,
        )


@pytest_asyncio.fixture
async def make_user(sessionmaker_):
    """Factory to create users with arbitrary roles."""

    async def _make(email: str, password: str = "password123456", role=UserRole.USER):
        async with sessionmaker_() as session:
            return await user_service.create_user(
                session, email=email, password=password, role=role
            )

    return _make


async def login_headers(client, email: str, password: str) -> dict[str, str]:
    """Log in and return an Authorization header dict."""
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}
