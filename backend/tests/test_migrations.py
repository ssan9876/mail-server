"""Alembic migrations against a real PostgreSQL database.

Skipped unless TEST_MIGRATION_DSN is set — see the module docstring's run
instructions. The rest of the suite builds schema with `create_all` on SQLite
and never invokes Alembic, so this is the only coverage the migrations have.

Run it with a disposable Postgres:

    docker network create mailtest-net
    docker run -d --name mailtest-pg --network mailtest-net \
        -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test -e POSTGRES_DB=mailtest \
        postgres:16
    docker run --rm --network mailtest-net --entrypoint python \
        -v "D:/mail-server/backend:/app" \
        -e TEST_MIGRATION_DSN=postgresql://test:test@mailtest-pg:5432/mailtest \
        mail-server-backend-dev -m pytest tests/test_migrations.py -v
    docker rm -f mailtest-pg && docker network rm mailtest-net
"""
import os
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine.url import make_url

DSN = os.getenv("TEST_MIGRATION_DSN")
BACKEND_DIR = Path(__file__).resolve().parents[1]

IDM_TABLES = {"idm_service_tokens", "idm_identities", "idm_identity_aliases"}

pytestmark = pytest.mark.skipif(
    not DSN,
    reason="Set TEST_MIGRATION_DSN to a disposable PostgreSQL to run migration tests.",
)


def _subprocess_env() -> dict[str, str]:
    """Alembic reads the URL from Settings, so override the pieces it builds from."""
    url = make_url(DSN)
    return {
        **os.environ,
        "POSTGRES_HOST": url.host or "localhost",
        "POSTGRES_PORT": str(url.port or 5432),
        "POSTGRES_DB": url.database or "postgres",
        "POSTGRES_USER": url.username or "postgres",
        "POSTGRES_PASSWORD": url.password or "",
    }


def _alembic(*args: str) -> None:
    result = subprocess.run(
        ["alembic", *args],
        cwd=BACKEND_DIR,
        env=_subprocess_env(),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"`alembic {' '.join(args)}` failed with {result.returncode}\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )


def _table_names() -> set[str]:
    engine = create_engine(DSN)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def _clean_database():
    """Every test starts from an empty database."""
    _alembic("downgrade", "base")
    yield
    _alembic("downgrade", "base")


def test_upgrade_to_head_creates_the_idm_tables():
    _alembic("upgrade", "head")
    assert IDM_TABLES <= _table_names()


def test_downgrade_removes_the_idm_tables():
    _alembic("upgrade", "head")
    _alembic("downgrade", "base")
    assert IDM_TABLES.isdisjoint(_table_names())


def test_migrations_are_reversible_and_repeatable():
    """upgrade -> downgrade -> upgrade must land in the same place. A migration
    that only works on a fresh database is not reversible."""
    _alembic("upgrade", "head")
    first = _table_names()
    _alembic("downgrade", "base")
    _alembic("upgrade", "head")
    assert _table_names() == first


def test_idm_actor_type_is_accepted_by_the_audit_log():
    """The reason no schema change is needed for ActorType.IDM: actor_type is a
    plain VARCHAR with no CHECK constraint. Prove it against real Postgres
    rather than trusting the claim."""
    _alembic("upgrade", "head")
    engine = create_engine(DSN)
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO audit_logs (action, actor_type) VALUES ('t.est', 'idm')"
            )
            value = conn.exec_driver_sql(
                "SELECT actor_type FROM audit_logs WHERE action = 't.est'"
            ).scalar_one()
        assert value == "idm"
    finally:
        engine.dispose()
