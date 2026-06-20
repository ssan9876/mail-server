#!/usr/bin/env bash
# ============================================================================
# Dovecot entrypoint:
#   1. render the SQL config from its template (read-only lookup credentials)
#   2. generate 99-runtime.conf with hostname, postmaster, and TLS cert paths
#   3. fall back to a self-signed cert until Let's Encrypt issues a real one
#   4. prepare the Maildir volume, wait for PostgreSQL, run Dovecot
# ============================================================================
set -euo pipefail

: "${MAIL_HOSTNAME:=mail.example.com}"
: "${POSTGRES_HOST:=postgres}"
: "${POSTGRES_PORT:=5432}"
: "${POSTGRES_DB:=mailserver}"
: "${POSTGRES_MAIL_USER:=mail_lookup}"
: "${POSTGRES_MAIL_PASSWORD:=changeme}"
: "${MAILDIR_ROOT:=/maildata}"

echo "[dovecot] rendering SQL configuration"
envsubst '${POSTGRES_HOST} ${POSTGRES_DB} ${POSTGRES_MAIL_USER} ${POSTGRES_MAIL_PASSWORD}' \
    < /etc/dovecot/dovecot-sql.conf.ext.tmpl \
    > /etc/dovecot/dovecot-sql.conf.ext
chmod 600 /etc/dovecot/dovecot-sql.conf.ext

echo "[dovecot] selecting TLS certificate"
LE_DIR="/etc/letsencrypt/live/${MAIL_HOSTNAME}"
if [ -f "${LE_DIR}/fullchain.pem" ] && [ -f "${LE_DIR}/privkey.pem" ]; then
    CERT="${LE_DIR}/fullchain.pem"
    KEY="${LE_DIR}/privkey.pem"
    echo "[dovecot] using Let's Encrypt certificate"
else
    echo "[dovecot] no LE cert found — generating self-signed fallback"
    mkdir -p /etc/dovecot/tls
    if [ ! -f /etc/dovecot/tls/snakeoil.pem ]; then
        openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
            -subj "/CN=${MAIL_HOSTNAME}" \
            -keyout /etc/dovecot/tls/snakeoil.key \
            -out /etc/dovecot/tls/snakeoil.pem 2>/dev/null
        chmod 600 /etc/dovecot/tls/snakeoil.key
    fi
    CERT="/etc/dovecot/tls/snakeoil.pem"
    KEY="/etc/dovecot/tls/snakeoil.key"
fi

echo "[dovecot] writing runtime configuration"
cat > /etc/dovecot/conf.d/99-runtime.conf <<EOF
hostname = ${MAIL_HOSTNAME}
postmaster_address = postmaster@${MAIL_HOSTNAME}
ssl_cert = <${CERT}
ssl_key = <${KEY}
EOF

echo "[dovecot] preparing Maildir volume at ${MAILDIR_ROOT}"
mkdir -p "${MAILDIR_ROOT}"
chown vmail:vmail "${MAILDIR_ROOT}"

echo "[dovecot] waiting for PostgreSQL at ${POSTGRES_HOST}:${POSTGRES_PORT}"
until (echo > "/dev/tcp/${POSTGRES_HOST}/${POSTGRES_PORT}") 2>/dev/null; do
    sleep 1
done
echo "[dovecot] PostgreSQL reachable"

# Validate config; -c prints errors and exits non-zero on problems.
doveconf -n > /dev/null

echo "[dovecot] starting"
exec dovecot -F
