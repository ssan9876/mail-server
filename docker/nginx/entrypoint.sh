#!/usr/bin/env sh
# ============================================================================
# Nginx edge entrypoint:
#   1. choose a TLS certificate (Let's Encrypt if present, else self-signed)
#   2. render server-block templates with hostnames + cert paths
#   3. validate config, then exec nginx
# ============================================================================
set -eu

: "${WEB_HOSTNAME:=admin.example.com}"
: "${MAIL_HOSTNAME:=mail.example.com}"

LE_DIR="/etc/letsencrypt/live/${WEB_HOSTNAME}"
if [ -f "${LE_DIR}/fullchain.pem" ] && [ -f "${LE_DIR}/privkey.pem" ]; then
    echo "[nginx] using Let's Encrypt certificate for ${WEB_HOSTNAME}"
    TLS_CERT="${LE_DIR}/fullchain.pem"
    TLS_KEY="${LE_DIR}/privkey.pem"
else
    echo "[nginx] no LE cert — generating self-signed fallback"
    mkdir -p /etc/nginx/tls
    if [ ! -f /etc/nginx/tls/snakeoil.pem ]; then
        openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
            -subj "/CN=${WEB_HOSTNAME}" \
            -keyout /etc/nginx/tls/snakeoil.key \
            -out /etc/nginx/tls/snakeoil.pem 2>/dev/null
    fi
    TLS_CERT="/etc/nginx/tls/snakeoil.pem"
    TLS_KEY="/etc/nginx/tls/snakeoil.key"
fi
export WEB_HOSTNAME MAIL_HOSTNAME TLS_CERT TLS_KEY

echo "[nginx] rendering server templates"
mkdir -p /etc/nginx/conf.d /var/www/acme
for tpl in /etc/nginx/templates/*.template; do
    [ -e "$tpl" ] || continue
    out="/etc/nginx/conf.d/$(basename "$tpl" .template)"
    envsubst '${WEB_HOSTNAME} ${MAIL_HOSTNAME} ${TLS_CERT} ${TLS_KEY}' < "$tpl" > "$out"
done

echo "[nginx] validating configuration"
nginx -t

echo "[nginx] starting"
exec "$@"
