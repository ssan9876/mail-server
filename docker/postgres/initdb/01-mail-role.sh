#!/bin/bash
# ============================================================================
# Provision the least-privilege read-only role used by Postfix/Dovecot SQL
# lookups. Runs once on first cluster init. Table-level GRANTs are applied
# later by Alembic migration 0002 (after the app owner creates the tables).
#
# NOTE: only runs when the data directory is empty (fresh volume). To rotate
# the password on an existing cluster, ALTER ROLE manually or recreate.
# ============================================================================
set -euo pipefail

: "${POSTGRES_MAIL_USER:=mail_lookup}"
: "${POSTGRES_MAIL_PASSWORD:=changeme}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${POSTGRES_MAIL_USER}') THEN
            CREATE ROLE ${POSTGRES_MAIL_USER} LOGIN PASSWORD '${POSTGRES_MAIL_PASSWORD}';
        ELSE
            ALTER ROLE ${POSTGRES_MAIL_USER} WITH LOGIN PASSWORD '${POSTGRES_MAIL_PASSWORD}';
        END IF;
    END
    \$\$;
EOSQL

echo "[postgres-init] role ${POSTGRES_MAIL_USER} ready"
