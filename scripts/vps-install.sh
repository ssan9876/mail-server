#!/usr/bin/env bash
# ============================================================================
# mail-server — VPS installer (Ubuntu 24.04)
#
# Run this ON THE VPS, as root. It installs Docker, clones this repo, generates
# a .env with strong random secrets, and starts the full stack.
#
# Interactive:
#   bash vps-install.sh
#
# Non-interactive:
#   MAIL_HOSTNAME=mail.example.com WEB_HOSTNAME=admin.example.com \
#   ADMIN_EMAIL=admin@example.com bash vps-install.sh
#
# Private repo (until PR #1 is merged to main):
#   GITHUB_TOKEN=ghp_xxx REPO_BRANCH=build/mail-server-platform bash vps-install.sh
# ============================================================================
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/mail-server}"
REPO_URL="${REPO_URL:-https://github.com/ssan9876/mail-server.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"

RED=$'\e[31m'; GRN=$'\e[32m'; YLW=$'\e[33m'; BLU=$'\e[34m'; NC=$'\e[0m'
info() { echo "${BLU}==>${NC} $*"; }
ok()   { echo "${GRN}✓${NC} $*"; }
warn() { echo "${YLW}!${NC} $*"; }
die()  { echo "${RED}✗ $*${NC}" >&2; exit 1; }
trap 'rc=$?; echo "${RED}✗ aborted at line ${LINENO}: ${BASH_COMMAND} (exit ${rc})${NC}" >&2' ERR

# --- Preflight --------------------------------------------------------------
[[ $EUID -eq 0 ]] || die "Run as root."
command -v openssl >/dev/null || { apt-get update -qq && apt-get install -y -qq openssl; }

prompt_if_unset() {
  local var="$1" msg="$2" default="${3:-}"
  if [[ -z "${!var:-}" ]]; then
    if [[ -t 0 ]]; then
      read -rp "$msg${default:+ [$default]}: " val
      printf -v "$var" '%s' "${val:-$default}"
    elif [[ -n "$default" ]]; then printf -v "$var" '%s' "$default"
    else die "$var is required (set it as an environment variable)."; fi
  fi
}
prompt_if_unset MAIL_HOSTNAME "Mail server FQDN (MX / HELO host)" "mail.example.com"
prompt_if_unset WEB_HOSTNAME  "Dashboard/API hostname"           "admin.example.com"
prompt_if_unset ADMIN_EMAIL   "First admin email"                "admin@${MAIL_HOSTNAME#*.}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"
CLOUDFLARE_API_TOKEN="${CLOUDFLARE_API_TOKEN:-}"

# --- Secrets (SIGPIPE-safe) -------------------------------------------------
rand_alnum() { local n="${1:-32}" s; s="$(openssl rand -hex "$n")"; printf '%s' "${s:0:n}"; }
fernet_key() { openssl rand -base64 32 | tr '+/' '-_'; }   # valid Fernet key
POSTGRES_PASSWORD="$(rand_alnum 32)"
POSTGRES_MAIL_PASSWORD="$(rand_alnum 32)"
REDIS_PASSWORD="$(rand_alnum 32)"
RSPAMD_PASSWORD="$(rand_alnum 24)"
JWT_SECRET_KEY="$(openssl rand -hex 32)"
SECRETS_ENCRYPTION_KEY="$(fernet_key)"
[[ -n "$ADMIN_PASSWORD" ]] || ADMIN_PASSWORD="$(rand_alnum 20)"

# --- Docker -----------------------------------------------------------------
if ! command -v docker >/dev/null; then
  info "Installing Docker…"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq && apt-get install -y -qq ca-certificates curl git >/dev/null
  curl -fsSL https://get.docker.com | sh >/dev/null 2>&1
  systemctl enable --now docker >/dev/null 2>&1 || true
fi
ok "Docker present."

# --- Clone ------------------------------------------------------------------
info "Cloning ${REPO_URL} (${REPO_BRANCH})…"
CLONE_URL="$REPO_URL"
if [[ -n "${GITHUB_TOKEN:-}" ]]; then
  CLONE_URL="${REPO_URL/https:\/\//https://x-access-token:${GITHUB_TOKEN}@}"
fi
rm -rf "$INSTALL_DIR"
git clone --depth 1 --branch "$REPO_BRANCH" "$CLONE_URL" "$INSTALL_DIR"
# Don't leave the token in the stored remote.
git -C "$INSTALL_DIR" remote set-url origin "$REPO_URL"
ok "Cloned to ${INSTALL_DIR}."

# --- .env -------------------------------------------------------------------
info "Writing .env…"
cat > "${INSTALL_DIR}/.env" <<EOF
MAIL_HOSTNAME=${MAIL_HOSTNAME}
WEB_HOSTNAME=${WEB_HOSTNAME}
ENVIRONMENT=production

POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=mailserver
POSTGRES_USER=mailserver
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
POSTGRES_MAIL_USER=mail_lookup
POSTGRES_MAIL_PASSWORD=${POSTGRES_MAIL_PASSWORD}

REDIS_HOST=redis
REDIS_PORT=6379
REDIS_PASSWORD=${REDIS_PASSWORD}

JWT_SECRET_KEY=${JWT_SECRET_KEY}
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=15
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7
SECRETS_ENCRYPTION_KEY=${SECRETS_ENCRYPTION_KEY}

ADMIN_EMAIL=${ADMIN_EMAIL}
ADMIN_PASSWORD=${ADMIN_PASSWORD}

CLOUDFLARE_API_TOKEN=${CLOUDFLARE_API_TOKEN}
CLOUDFLARE_ACCOUNT_ID=

ACME_EMAIL=${ADMIN_EMAIL}
ACME_STAGING=0

RSPAMD_PASSWORD=${RSPAMD_PASSWORD}

DEFAULT_MAILBOX_QUOTA_MB=2048
MAILDIR_ROOT=/maildata
DKIM_KEYS_PATH=/dkim

RATE_LIMIT_API_PER_MINUTE=120
RATE_LIMIT_LOGIN_ATTEMPTS=5

CORS_ORIGINS=https://${WEB_HOSTNAME}
EOF
chmod 600 "${INSTALL_DIR}/.env"
ok ".env written."

# --- Launch -----------------------------------------------------------------
info "Building images and starting the stack (a few minutes)…"
( cd "$INSTALL_DIR" && docker compose -f docker-compose.yml up -d --build )

PUB_IP="$(curl -fsSL https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')"

cat <<EOF

${GRN}========================================================================${NC}
${GRN} mail-server is up${NC}
${GRN}========================================================================${NC}

  Public IP   : ${PUB_IP}
  Dashboard   : https://${WEB_HOSTNAME}/
  Admin login : ${ADMIN_EMAIL}
  Admin pass  : ${ADMIN_PASSWORD}      ${YLW}<-- save this now${NC}

Next:
  1. DNS (Cloudflare, all "DNS only"/grey cloud):
       A   mail   -> ${PUB_IP}
       A   admin  -> ${PUB_IP}
       MX  @      -> ${MAIL_HOSTNAME} (priority 10)
       TXT @      -> v=spf1 mx ~all
       TXT _dmarc -> v=DMARC1; p=quarantine; rua=mailto:dmarc@${MAIL_HOSTNAME#*.}
       (DKIM TXT comes from the dashboard once you add the domain)
  2. PTR / reverse DNS: set ${PUB_IP} -> ${MAIL_HOSTNAME} in your VPS control panel.
  3. Firewall:
       ufw allow 22,80,443,25,465,587,993,995/tcp && ufw enable
  4. TLS once DNS resolves:  (cd ${INSTALL_DIR} && make certs)
  5. mail-tester.com to confirm SPF/DKIM/DMARC/PTR.

Manage: (cd ${INSTALL_DIR} && make ps | make logs)
Full runbook: ${INSTALL_DIR}/docs/deployment.md
EOF
