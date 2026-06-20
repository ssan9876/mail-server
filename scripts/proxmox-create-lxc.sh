#!/usr/bin/env bash
# ============================================================================
# mail-server — Proxmox VE helper script
#
# Run this ON THE PROXMOX HOST (as root). It:
#   1. creates an Ubuntu 24.04 LXC container with the features Docker needs
#   2. installs Docker + the Compose plugin inside it
#   3. clones this repo and generates a .env with strong random secrets
#   4. builds and starts the full mail-server stack
#
# Usage (interactive prompts for the few required values):
#   bash proxmox-create-lxc.sh
#
# Or fully non-interactive via environment variables:
#   MAIL_HOSTNAME=mail.example.com WEB_HOSTNAME=admin.example.com \
#   ADMIN_EMAIL=admin@example.com bash proxmox-create-lxc.sh
#
# Common overrides (env vars, all optional):
#   CTID, CT_HOSTNAME, CORES, RAM_MB, SWAP_MB, DISK_GB, STORAGE, BRIDGE,
#   NET_IP (default dhcp), TEMPLATE_STORAGE, PRIVILEGED (0/1),
#   REPO_URL, REPO_BRANCH, CLOUDFLARE_API_TOKEN
# ============================================================================
set -euo pipefail

# --- Defaults ---------------------------------------------------------------
CTID="${CTID:-$(pvesh get /cluster/nextid 2>/dev/null || echo 200)}"
# NB: not HOSTNAME — that is the Proxmox host's own shell variable.
CT_HOSTNAME="${CT_HOSTNAME:-mailserver}"
CORES="${CORES:-2}"
RAM_MB="${RAM_MB:-4096}"
SWAP_MB="${SWAP_MB:-2048}"
DISK_GB="${DISK_GB:-16}"
STORAGE="${STORAGE:-local-lvm}"
BRIDGE="${BRIDGE:-vmbr0}"
NET_IP="${NET_IP:-dhcp}"
TEMPLATE_STORAGE="${TEMPLATE_STORAGE:-local}"
# Privileged by default for the most reliable Docker-in-LXC + mail networking.
# Set PRIVILEGED=0 for an unprivileged container (more isolated; needs PVE 8+).
PRIVILEGED="${PRIVILEGED:-1}"
REPO_URL="${REPO_URL:-https://github.com/ssan9876/mail-server.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"

RED=$'\e[31m'; GRN=$'\e[32m'; YLW=$'\e[33m'; BLU=$'\e[34m'; NC=$'\e[0m'
info() { echo "${BLU}==>${NC} $*"; }
ok()   { echo "${GRN}✓${NC} $*"; }
warn() { echo "${YLW}!${NC} $*"; }
die()  { echo "${RED}✗ $*${NC}" >&2; exit 1; }

# --- Preflight --------------------------------------------------------------
[[ $EUID -eq 0 ]] || die "Run as root on the Proxmox host."
command -v pct  >/dev/null || die "pct not found — run this on a Proxmox VE host."
command -v pveam >/dev/null || die "pveam not found — run this on a Proxmox VE host."
command -v openssl >/dev/null || die "openssl is required."

# --- Required values (prompt if interactive and unset) ----------------------
prompt_if_unset() {
  local var="$1" msg="$2" default="${3:-}"
  if [[ -z "${!var:-}" ]]; then
    if [[ -t 0 ]]; then
      read -rp "$msg${default:+ [$default]}: " val
      printf -v "$var" '%s' "${val:-$default}"
    elif [[ -n "$default" ]]; then
      printf -v "$var" '%s' "$default"
    else
      die "$var is required (set it as an environment variable)."
    fi
  fi
}
prompt_if_unset MAIL_HOSTNAME "Mail server FQDN (MX / HELO host)" "mail.example.com"
prompt_if_unset WEB_HOSTNAME  "Dashboard/API hostname"           "admin.example.com"
prompt_if_unset ADMIN_EMAIL   "First admin email"                "admin@${MAIL_HOSTNAME#*.}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"   # generated below if empty
CLOUDFLARE_API_TOKEN="${CLOUDFLARE_API_TOKEN:-}"

# --- Secret generation (on the host; pushed into the container) -------------
rand_alnum() { LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c "${1:-32}"; }
# A valid Fernet key = url-safe base64 of 32 random bytes.
fernet_key() { openssl rand -base64 32 | tr '+/' '-_'; }

POSTGRES_PASSWORD="$(rand_alnum 32)"
POSTGRES_MAIL_PASSWORD="$(rand_alnum 32)"
REDIS_PASSWORD="$(rand_alnum 32)"
RSPAMD_PASSWORD="$(rand_alnum 24)"
JWT_SECRET_KEY="$(openssl rand -hex 32)"
SECRETS_ENCRYPTION_KEY="$(fernet_key)"
[[ -n "$ADMIN_PASSWORD" ]] || ADMIN_PASSWORD="$(rand_alnum 20)"

# --- Resolve + download the Ubuntu 24.04 template ---------------------------
info "Resolving Ubuntu 24.04 LXC template…"
pveam update >/dev/null 2>&1 || true
TEMPLATE="$(pveam available --section system 2>/dev/null \
  | awk '/ubuntu-24.04-standard/ {print $2}' | sort -V | tail -1)"
[[ -n "$TEMPLATE" ]] || die "Could not find an ubuntu-24.04-standard template via pveam."

if ! pveam list "$TEMPLATE_STORAGE" 2>/dev/null | grep -q "$TEMPLATE"; then
  info "Downloading $TEMPLATE to $TEMPLATE_STORAGE…"
  pveam download "$TEMPLATE_STORAGE" "$TEMPLATE"
fi
ok "Template: $TEMPLATE"

# --- Create the container ---------------------------------------------------
info "Creating LXC $CTID ($CT_HOSTNAME): ${CORES} cores, ${RAM_MB}MB RAM, ${DISK_GB}GB disk"
pct create "$CTID" "${TEMPLATE_STORAGE}:vztmpl/${TEMPLATE}" \
  --hostname "$CT_HOSTNAME" \
  --cores "$CORES" --memory "$RAM_MB" --swap "$SWAP_MB" \
  --rootfs "${STORAGE}:${DISK_GB}" \
  --net0 "name=eth0,bridge=${BRIDGE},ip=${NET_IP}" \
  --features "nesting=1,keyctl=1" \
  --unprivileged "$([[ "$PRIVILEGED" == "1" ]] && echo 0 || echo 1)" \
  --onboot 1 \
  --ostype ubuntu
ok "Container created."

info "Starting container…"
pct start "$CTID"

# Wait for working networking + DNS inside the container.
info "Waiting for network…"
for _ in $(seq 1 60); do
  if pct exec "$CTID" -- getent hosts github.com >/dev/null 2>&1; then break; fi
  sleep 2
done
pct exec "$CTID" -- getent hosts github.com >/dev/null 2>&1 \
  || die "Container has no network/DNS — check bridge ${BRIDGE} and IP config."
ok "Network is up."

# --- Provision inside the container -----------------------------------------
info "Installing base packages…"
pct exec "$CTID" -- bash -c \
  "export DEBIAN_FRONTEND=noninteractive; apt-get update -qq && apt-get install -y -qq ca-certificates curl git >/dev/null"

info "Installing Docker…"
pct exec "$CTID" -- bash -c "curl -fsSL https://get.docker.com | sh >/dev/null 2>&1"
pct exec "$CTID" -- systemctl enable --now docker >/dev/null 2>&1 || true

info "Cloning ${REPO_URL} (${REPO_BRANCH})…"
pct exec "$CTID" -- bash -c \
  "rm -rf /opt/mail-server && git clone --depth 1 --branch '${REPO_BRANCH}' '${REPO_URL}' /opt/mail-server"

# --- Generate .env on the host and push it in -------------------------------
info "Writing .env with generated secrets…"
ENV_TMP="$(mktemp)"
trap 'rm -f "$ENV_TMP"' EXIT
cat > "$ENV_TMP" <<EOF
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
pct push "$CTID" "$ENV_TMP" /opt/mail-server/.env --perms 600
rm -f "$ENV_TMP"; trap - EXIT
ok ".env written."

# --- Build + launch ---------------------------------------------------------
info "Building images and starting the stack (this takes a few minutes)…"
pct exec "$CTID" -- bash -c \
  "cd /opt/mail-server && docker compose -f docker-compose.yml up -d --build"

CT_IP="$(pct exec "$CTID" -- hostname -I 2>/dev/null | awk '{print $1}')"

# --- Summary ----------------------------------------------------------------
cat <<EOF

${GRN}========================================================================${NC}
${GRN} mail-server deployed in LXC ${CTID} (${CT_HOSTNAME})${NC}
${GRN}========================================================================${NC}

  Container IP : ${CT_IP:-<check: pct exec $CTID -- hostname -I>}
  Dashboard    : https://${WEB_HOSTNAME}/   (point this at the container IP)
  Admin login  : ${ADMIN_EMAIL}
  Admin pass   : ${ADMIN_PASSWORD}

  ${YLW}Save the admin password now — it is not stored anywhere else.${NC}

Next steps:
  1. DNS: A records for ${MAIL_HOSTNAME} and ${WEB_HOSTNAME} -> ${CT_IP:-container IP},
     plus an MX for each hosted domain and a PTR (reverse DNS) for ${MAIL_HOSTNAME}.
  2. TLS: once DNS resolves and ports 80/443 are reachable:
       pct exec ${CTID} -- bash -c 'cd /opt/mail-server && make certs'
  3. Add domains/mailboxes in the dashboard; publish + verify SPF/DKIM/DMARC.

Manage:  pct exec ${CTID} -- bash -c 'cd /opt/mail-server && make ps'
Logs:    pct exec ${CTID} -- bash -c 'cd /opt/mail-server && make logs'

See docs/deployment.md for the full runbook.
EOF
