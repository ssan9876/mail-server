#!/usr/bin/env bash
# ============================================================================
# mail-server — VPS installer (Ubuntu 24.04)
#
# Run this ON THE VPS, as root. It installs Docker, clones this repo, generates
# a .env with strong random secrets, and starts the full stack.
#
# Interactive (prompts for the few required values + optional Cloudflare token):
#   bash vps-install.sh
#
# Non-interactive:
#   MAIL_HOSTNAME=mail.example.com WEB_HOSTNAME=admin.example.com \
#   ADMIN_EMAIL=you@example.com CLOUDFLARE_API_TOKEN=... bash vps-install.sh
#
# Useful env overrides:
#   ADMIN_PASSWORD (else random)   INSTALL_DIR (default /opt/mail-server)
#   REPO_URL  REPO_BRANCH (default main)   GITHUB_TOKEN (private repo)
#   SETUP_UFW=1 (configure firewall non-interactively)   FORCE=1 (regen .env)
#
# Re-running is safe: an existing .env is preserved (secrets are NOT rotated)
# unless you pass FORCE=1.
# ============================================================================
set -Eeuo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/mail-server}"
REPO_URL="${REPO_URL:-https://github.com/ssan9876/mail-server.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"

RED=$'\e[31m'; GRN=$'\e[32m'; YLW=$'\e[33m'; BLU=$'\e[34m'; NC=$'\e[0m'
info() { echo "${BLU}==>${NC} $*"; }
ok()   { echo "${GRN}✓${NC} $*"; }
warn() { echo "${YLW}!${NC} $*" >&2; }
die()  { echo "${RED}✗ $*${NC}" >&2; exit 1; }
trap 'rc=$?; echo "${RED}✗ aborted at line ${LINENO}: ${BASH_COMMAND} (exit ${rc})${NC}" >&2' ERR

# --- Preflight --------------------------------------------------------------
[[ $EUID -eq 0 ]] || die "Run as root."
. /etc/os-release 2>/dev/null || true
[[ "${ID:-}" == "ubuntu" ]] || warn "Designed for Ubuntu 24.04; '${ID:-unknown}' may work but is untested."
command -v openssl >/dev/null || { apt-get update -qq && apt-get install -y -qq openssl; }

# Outbound SMTP (port 25) is blocked by default on most VPS providers — flag it
# early since it's the #1 reason a new mail server can't send.
if timeout 5 bash -c '>/dev/tcp/aspmx.l.google.com/25' 2>/dev/null; then
  ok "Outbound SMTP (port 25) is reachable."
else
  warn "Outbound port 25 looks BLOCKED. Ask your provider to open it, or outbound mail will fail."
fi

# --- Required values (prompt if interactive, else require env) ---------------
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
prompt_if_unset MAIL_HOSTNAME "Mail server FQDN (MX / HELO host), e.g. mail.example.com" ""
prompt_if_unset WEB_HOSTNAME  "Dashboard/API hostname, e.g. admin.example.com" ""
prompt_if_unset ADMIN_EMAIL   "First admin email" "admin@${MAIL_HOSTNAME#*.}"
# Optional secrets the operator may want to supply.
if [[ -z "${CLOUDFLARE_API_TOKEN:-}" && -t 0 ]]; then
  read -rp "Cloudflare API token (Zone:DNS:Edit) — optional, blank to skip: " CLOUDFLARE_API_TOKEN || true
fi
CLOUDFLARE_API_TOKEN="${CLOUDFLARE_API_TOKEN:-}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"

[[ "$MAIL_HOSTNAME" == *.*.* || "$MAIL_HOSTNAME" == *.* ]] || die "MAIL_HOSTNAME must be a FQDN."
case "${ADMIN_EMAIL##*@}" in
  *.local|*.localhost|localhost|*.test|*.example|*.invalid)
    die "ADMIN_EMAIL domain '${ADMIN_EMAIL##*@}' is a reserved TLD the validator rejects — use a real domain." ;;
esac

# --- Docker -----------------------------------------------------------------
if ! command -v docker >/dev/null; then
  info "Installing Docker…"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq && apt-get install -y -qq ca-certificates curl git >/dev/null
  curl -fsSL https://get.docker.com | sh >/dev/null 2>&1
  systemctl enable --now docker >/dev/null 2>&1 || true
fi
docker compose version >/dev/null 2>&1 || die "Docker Compose plugin missing."
ok "Docker present."

# --- Clone / update (preserves an existing .env) ----------------------------
CLONE_URL="$REPO_URL"
[[ -n "${GITHUB_TOKEN:-}" ]] && CLONE_URL="${REPO_URL/https:\/\//https://x-access-token:${GITHUB_TOKEN}@}"

if [[ -d "$INSTALL_DIR/.git" ]]; then
  info "Updating existing checkout in ${INSTALL_DIR}…"
  git -C "$INSTALL_DIR" remote set-url origin "$CLONE_URL"
  git -C "$INSTALL_DIR" fetch --depth 1 origin "$REPO_BRANCH"
  git -C "$INSTALL_DIR" checkout -B "$REPO_BRANCH" FETCH_HEAD
else
  info "Cloning ${REPO_URL} (${REPO_BRANCH})…"
  rm -rf "$INSTALL_DIR"
  git clone --depth 1 --branch "$REPO_BRANCH" "$CLONE_URL" "$INSTALL_DIR"
fi
git -C "$INSTALL_DIR" remote set-url origin "$REPO_URL"   # never persist the token
ok "Repo ready at ${INSTALL_DIR}."

# --- .env (generate once; preserve on re-run) -------------------------------
if [[ -f "${INSTALL_DIR}/.env" && "${FORCE:-0}" != "1" ]]; then
  warn "Existing .env found — keeping it (secrets unchanged). Use FORCE=1 to regenerate."
  ADMIN_PASSWORD="$(grep -oP '^ADMIN_PASSWORD=\K.*' "${INSTALL_DIR}/.env" || echo '(see .env)')"
else
  info "Generating secrets and writing .env…"
  rand_alnum() { local n="${1:-32}" s; s="$(openssl rand -hex "$n")"; printf '%s' "${s:0:n}"; }
  fernet_key() { openssl rand -base64 32 | tr '+/' '-_'; }   # valid Fernet key
  [[ -n "$ADMIN_PASSWORD" ]] || ADMIN_PASSWORD="$(rand_alnum 20)"
  cat > "${INSTALL_DIR}/.env" <<EOF
MAIL_HOSTNAME=${MAIL_HOSTNAME}
WEB_HOSTNAME=${WEB_HOSTNAME}
ENVIRONMENT=production

POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=mailserver
POSTGRES_USER=mailserver
POSTGRES_PASSWORD=$(rand_alnum 32)
POSTGRES_MAIL_USER=mail_lookup
POSTGRES_MAIL_PASSWORD=$(rand_alnum 32)

REDIS_HOST=redis
REDIS_PORT=6379
REDIS_PASSWORD=$(rand_alnum 32)

JWT_SECRET_KEY=$(openssl rand -hex 32)
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=15
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7
SECRETS_ENCRYPTION_KEY=$(fernet_key)

ADMIN_EMAIL=${ADMIN_EMAIL}
ADMIN_PASSWORD=${ADMIN_PASSWORD}

CLOUDFLARE_API_TOKEN=${CLOUDFLARE_API_TOKEN}
CLOUDFLARE_ACCOUNT_ID=

ACME_EMAIL=${ADMIN_EMAIL}
ACME_STAGING=0

RSPAMD_PASSWORD=$(rand_alnum 24)

DEFAULT_MAILBOX_QUOTA_MB=2048
MAILDIR_ROOT=/maildata
DKIM_KEYS_PATH=/dkim

RATE_LIMIT_API_PER_MINUTE=120
RATE_LIMIT_LOGIN_ATTEMPTS=5

CORS_ORIGINS=https://${WEB_HOSTNAME}
EOF
  chmod 600 "${INSTALL_DIR}/.env"
  ok ".env written."
fi

# --- Optional firewall (SSH-safe ordering) ----------------------------------
setup_ufw() {
  command -v ufw >/dev/null || { apt-get install -y -qq ufw >/dev/null; }
  ufw allow 22/tcp >/dev/null            # SSH first, so we don't lock ourselves out
  for p in 80 443 25 465 587 993 995; do ufw allow ${p}/tcp >/dev/null; done
  ufw --force enable >/dev/null
  ok "Firewall configured (22,80,443,25,465,587,993,995)."
}
if [[ "${SETUP_UFW:-0}" == "1" ]]; then
  setup_ufw
elif [[ -t 0 ]]; then
  read -rp "Configure ufw firewall now (allows SSH + mail/web ports)? [y/N]: " a
  [[ "${a,,}" == "y" ]] && setup_ufw || warn "Skipped firewall — configure it before going live (see docs)."
fi

# --- Launch -----------------------------------------------------------------
info "Building images and starting the stack (a few minutes on first run)…"
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
  1. DNS (at your provider; in Cloudflare set these records to "DNS only"/grey):
       A    mail    -> ${PUB_IP}
       A    admin   -> ${PUB_IP}
       MX   @       -> ${MAIL_HOSTNAME}   (priority 10)
       TXT  @       -> v=spf1 mx ~all
       TXT  _dmarc  -> v=DMARC1; p=quarantine; rua=mailto:dmarc@${MAIL_HOSTNAME#*.}
       (DKIM TXT is shown in the dashboard after you add the domain.)
  2. PTR / reverse DNS: set ${PUB_IP} -> ${MAIL_HOSTNAME} in your VPS panel.
  3. TLS once the A records resolve:   (cd ${INSTALL_DIR} && make certs)
  4. Add your domain in the dashboard, then Publish + Verify; check mail-tester.com.

Manage:  (cd ${INSTALL_DIR} && make ps)   logs: (cd ${INSTALL_DIR} && make logs)
Runbook: ${INSTALL_DIR}/docs/vps-install.md
EOF
