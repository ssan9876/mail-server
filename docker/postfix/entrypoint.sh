#!/usr/bin/env bash
# ============================================================================
# Postfix entrypoint:
#   1. render PostgreSQL map templates with the read-only lookup credentials
#   2. set host-specific config (hostname, TLS cert paths) via postconf
#   3. fall back to a self-signed cert until Let's Encrypt issues a real one
#   4. wait for PostgreSQL, then run Postfix in the foreground
# ============================================================================
set -euo pipefail

: "${MAIL_HOSTNAME:=mail.example.com}"
: "${POSTGRES_HOST:=postgres}"
: "${POSTGRES_PORT:=5432}"
: "${POSTGRES_DB:=mailserver}"
: "${POSTGRES_MAIL_USER:=mail_lookup}"
: "${POSTGRES_MAIL_PASSWORD:=changeme}"

echo "[postfix] rendering PostgreSQL map templates"
mkdir -p /etc/postfix/pgsql
for tpl in /etc/postfix/pgsql-templates/*.cf; do
    envsubst '${POSTGRES_HOST} ${POSTGRES_DB} ${POSTGRES_MAIL_USER} ${POSTGRES_MAIL_PASSWORD}' \
        < "$tpl" > "/etc/postfix/pgsql/$(basename "$tpl")"
done
chmod 640 /etc/postfix/pgsql/*.cf

echo "[postfix] applying host-specific configuration"
postconf -e "myhostname=${MAIL_HOSTNAME}"

# Prefer a real Let's Encrypt cert; otherwise generate a self-signed fallback
# so Postfix can start before certbot has run.
LE_DIR="/etc/letsencrypt/live/${MAIL_HOSTNAME}"
if [ -f "${LE_DIR}/fullchain.pem" ] && [ -f "${LE_DIR}/privkey.pem" ]; then
    echo "[postfix] using Let's Encrypt certificate"
    postconf -e "smtpd_tls_cert_file=${LE_DIR}/fullchain.pem"
    postconf -e "smtpd_tls_key_file=${LE_DIR}/privkey.pem"
else
    echo "[postfix] no LE cert found — generating self-signed fallback"
    mkdir -p /etc/postfix/tls
    if [ ! -f /etc/postfix/tls/snakeoil.pem ]; then
        openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
            -subj "/CN=${MAIL_HOSTNAME}" \
            -keyout /etc/postfix/tls/snakeoil.key \
            -out /etc/postfix/tls/snakeoil.pem 2>/dev/null
        chmod 600 /etc/postfix/tls/snakeoil.key
    fi
    postconf -e "smtpd_tls_cert_file=/etc/postfix/tls/snakeoil.pem"
    postconf -e "smtpd_tls_key_file=/etc/postfix/tls/snakeoil.key"
fi

echo "[postfix] waiting for PostgreSQL at ${POSTGRES_HOST}:${POSTGRES_PORT}"
until (echo > "/dev/tcp/${POSTGRES_HOST}/${POSTGRES_PORT}") 2>/dev/null; do
    sleep 1
done
echo "[postfix] PostgreSQL reachable"

# Validate config before launching; surfaces typos early.
postfix check

echo "[postfix] starting"
exec postfix start-fg
