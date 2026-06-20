#!/usr/bin/env bash
# ============================================================================
# Rspamd entrypoint:
#   1. generate Redis config (with optional password)
#   2. generate controller config (hashed web password, if provided)
#   3. wait for Redis, validate config, run Rspamd in the foreground
# ============================================================================
set -euo pipefail

: "${REDIS_HOST:=redis}"
: "${REDIS_PORT:=6379}"
: "${REDIS_PASSWORD:=}"
: "${RSPAMD_PASSWORD:=}"

mkdir -p /etc/rspamd/local.d

echo "[rspamd] configuring Redis backend (${REDIS_HOST}:${REDIS_PORT})"
{
    echo "servers = \"${REDIS_HOST}:${REDIS_PORT}\";"
    if [ -n "${REDIS_PASSWORD}" ]; then
        echo "password = \"${REDIS_PASSWORD}\";"
    fi
} > /etc/rspamd/local.d/redis.conf

echo "[rspamd] configuring controller (web UI on :11334)"
{
    echo 'bind_socket = "*:11334";'
    if [ -n "${RSPAMD_PASSWORD}" ]; then
        # rspamadm pw produces a salted PBKDF hash; never store the plaintext.
        hashed="$(rspamadm pw -q -p "${RSPAMD_PASSWORD}")"
        echo "password = \"${hashed}\";"
    fi
} > /etc/rspamd/local.d/worker-controller.inc

echo "[rspamd] waiting for Redis"
until (echo > "/dev/tcp/${REDIS_HOST}/${REDIS_PORT}") 2>/dev/null; do
    sleep 1
done
echo "[rspamd] Redis reachable"

# Validate configuration before launch.
rspamadm configtest

echo "[rspamd] starting"
exec rspamd -f -u _rspamd -g _rspamd
