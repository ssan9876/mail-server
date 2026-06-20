#!/usr/bin/env bash
# Backend entrypoint: wait for PostgreSQL, apply migrations, then run the app.
# Works for both the dev (uvicorn --reload) and prod (gunicorn) commands, which
# are passed as arguments.
set -euo pipefail

: "${POSTGRES_HOST:=postgres}"
: "${POSTGRES_PORT:=5432}"

echo "[backend] waiting for PostgreSQL at ${POSTGRES_HOST}:${POSTGRES_PORT}"
until (echo > "/dev/tcp/${POSTGRES_HOST}/${POSTGRES_PORT}") 2>/dev/null; do
    sleep 1
done
echo "[backend] PostgreSQL reachable"

echo "[backend] applying database migrations"
alembic upgrade head

echo "[backend] starting: $*"
exec "$@"
