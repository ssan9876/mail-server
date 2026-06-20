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
# Private repo? Pass a PAT with read access (and usually the PR branch):
#   GITHUB_TOKEN=ghp_xxx REPO_BRANCH=build/mail-server-platform ...
#
# Common overrides (env vars, all optional):
#   CTID, CT_HOSTNAME, CORES, RAM_MB, SWAP_MB, DISK_GB, STORAGE, BRIDGE,
#   NET_IP (default dhcp), TEMPLATE_STORAGE, PRIVILEGED (0/1),
#   REPO_URL, REPO_BRANCH, GITHUB_TOKEN, CLOUDFLARE_API_TOKEN, DEBUG=1
# ============================================================================
set -Eeuo pipefail

RED=$'\e[31m'; GRN=$'\e[32m'; YLW=$'\e[33m'; BLU=$'\e[34m'; NC=$'\e[0m'
info() { echo "${BLU}==>${NC} $*"; }
ok()   { echo "${GRN}✓${NC} $*"; }
warn() { echo "${YLW}!${NC} $*" >&2; }
die()  { echo "${RED}✗ $*${NC}" >&2; exit 1; }

# Surface exactly where a failure happened instead of exiting silently.
trap 'rc=$?; echo "${RED}✗ failed (exit $rc) at line ${BASH_LINENO[0]}: ${BASH_COMMAND}${NC}" >&2' ERR
[[ "${DEBUG:-0}" == "1" ]] && set -x

# --- Defaults ---------------------------------------------------------------
CT_HOSTNAME="${CT_HOSTNAME:-mailserver}"   # NB: not HOSTNAME (host's own var)
CORES="${CORES:-2}"
RAM_MB="${RAM_MB:-4096}"
SWAP_MB="${SWAP_MB:-2048}"
DISK_GB="${DISK_GB:-16}"
BRIDGE="${BRIDGE:-vmbr0}"
NET_IP="${NET_IP:-dhcp}"
PRIVILEGED="${PRIVILEGED:-1}"
REPO_URL="${REPO_URL:-https://github.com/ssan9876/mail-server.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"

# --- Preflight --------------------------------------------------------------
[[ $EUID -eq 0 ]] || die "Run as root on the Proxmox host."
command -v pct    >/dev/null || die "pct not found — run this on a Proxmox VE host."
command -v pveam  >/dev/null || die "pveam not found — run this on a Proxmox VE host."
command -v pvesm  >/dev/null || die "pvesm not found — run this on a Proxmox VE host."
command -v openssl >/dev/null || die "openssl is required."

# --- Pick a free CTID -------------------------------------------------------
CTID="${CTID:-$(pvesh get /cluster/nextid 2>/dev/null || echo 200)}"
if pct status "$CTID" >/dev/null 2>&1; then
  die "CTID $CTID is already in use. Set CTID=<free id> and retry."
fi

# --- Auto-detect storages ---------------------------------------------------
# A container rootfs needs a storage with content type 'rootdir'; templates
# need 'vztmpl'. Assuming 'local-lvm'/'local' is the usual cause of failure on
# hosts that use ZFS/other names, so detect from the host's actual storage.
list_storage_for() { pvesm status --content "$1" 2>/dev/null | awk 'NR>1 {print $1}'; }

pick_storage() {  # $1=content type  $2=preferred name (optional)
  local content="$1" preferred="${2:-}" available
  available="$(list_storage_for "$content")"
  [[ -n "$available" ]] || return 1
  if [[ -n "$preferred" ]] && grep -qx "$preferred" <<<"$available"; then
    echo "$preferred"; return 0
  fi
  head -n1 <<<"$available"
}

STORAGE="$(pick_storage rootdir "${STORAGE:-local-lvm}")" \
  || die "No storage supports container rootfs (content 'rootdir'). Check 'pvesm status'."
TEMPLATE_STORAGE="$(pick_storage vztmpl "${TEMPLATE_STORAGE:-local}")" \
  || die "No storage supports templates (content 'vztmpl'). Check 'pvesm status'."
ok "rootfs storage: ${STORAGE}   template storage: ${TEMPLATE_STORAGE}"

# --- Validate bridge --------------------------------------------------------
[[ -d "/sys/class/net/${BRIDGE}" ]] \
  || die "Network bridge '${BRIDGE}' not found. Set BRIDGE=<your bridge> (see 'ip link')."

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
ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"
CLOUDFLARE_API_TOKEN="${CLOUDFLARE_API_TOKEN:-}"

# --- Secret generation (on the host; pushed into the container) -------------
# Note: avoid `tr </dev/urandom | head` — head closing the pipe sends SIGPIPE
# to tr, which under `set -o pipefail` aborts the script. Source finite bytes
# from openssl and trim with cut (which consumes all input) instead.
rand_alnum() { local n="${1:-32}"; openssl rand -base64 "$(( n * 2 ))" | tr -dc 'A-Za-z0-9' | cut -c "1-${n}"; }
fernet_key() { openssl rand -base64 32 | tr '+/' '-_'; }  # valid Fernet key
POSTGRES_PASSWORD="$(rand_alnum 32)"
POSTGRES_MAIL_PASSWORD="$(rand_alnum 32)"
REDIS_PASSWORD="$(rand_alnum 32)"
RSPAMD_PASSWORD="$(rand_alnum 24)"
JWT_SECRET_KEY="$(openssl rand -hex 32)"
SECRETS_ENCRYPTION_KEY="$(fernet_key)"
[[ -n "$ADMIN_PASSWORD" ]] || ADMIN_PASSWORD="$(rand_alnum 20)"

# --- Resolve + download the Ubuntu 24.04 template ---------------------------
info "Resolving Ubuntu 24.04 LXC template…"
pveam update >/dev/null 2>&1 || warn "pveam update failed (offline?); using cached list."
TEMPLATE="$(pveam available --section system 2>/dev/null \
  | awk '/ubuntu-24\.04-standard/ {print $2}' | sort -V | tail -1)"
# Fall back to an already-downloaded template if the index is unavailable.
if [[ -z "$TEMPLATE" ]]; then
  TEMPLATE="$(pveam list "$TEMPLATE_STORAGE" 2>/dev/null \
    | awk '/ubuntu-24\.04-standard/ {print $1}' | sed 's#.*/##' | sort -V | tail -1)"
fi
[[ -n "$TEMPLATE" ]] || die "No ubuntu-24.04-standard template found. Run 'pveam available --section system' to check."

if ! pveam list "$TEMPLATE_STORAGE" 2>/dev/null | grep -q "$TEMPLATE"; then
  info "Downloading template $TEMPLATE to ${TEMPLATE_STORAGE}…"
  pveam download "$TEMPLATE_STORAGE" "$TEMPLATE"
fi
ok "Template: $TEMPLATE"

# --- Create the container ---------------------------------------------------
UNPRIV="$([[ "$PRIVILEGED" == "1" ]] && echo 0 || echo 1)"
info "Creating LXC ${CTID} (${CT_HOSTNAME}): ${CORES} cores, ${RAM_MB}MB RAM, ${DISK_GB}GB disk on ${STORAGE}"
pct create "$CTID" "${TEMPLATE_STORAGE}:vztmpl/${TEMPLATE}" \
  --hostname "$CT_HOSTNAME" \
  --cores "$CORES" --memory "$RAM_MB" --swap "$SWAP_MB" \
  --rootfs "${STORAGE}:${DISK_GB}" \
  --net0 "name=eth0,bridge=${BRIDGE},ip=${NET_IP}" \
  --features "nesting=1,keyctl=1" \
  --unprivileged "$UNPRIV" \
  --onboot 1 \
  --ostype ubuntu
ok "Container ${CTID} created."

# Docker-in-LXC needs the container to be AppArmor-unconfined and to have
# device/cgroup access; nesting/keyctl alone aren't enough (image build RUN
# steps fail with "unable to apply apparmor profile" otherwise).
info "Enabling Docker-in-LXC (AppArmor unconfined + device access)…"
cat >> "/etc/pve/lxc/${CTID}.conf" <<'LXCCONF'
lxc.apparmor.profile: unconfined
lxc.cgroup2.devices.allow: a
lxc.cap.drop:
lxc.mount.auto: proc:rw sys:rw
LXCCONF

info "Starting container…"
pct start "$CTID"
sleep 3
pct status "$CTID" | grep -q running || die "Container failed to start (see 'pct status $CTID')."

# Wait for working networking + DNS inside the container.
info "Waiting for network/DNS inside the container…"
net_ok=0
for _ in $(seq 1 60); do
  if pct exec "$CTID" -- getent hosts github.com >/dev/null 2>&1; then net_ok=1; break; fi
  sleep 2
done
[[ "$net_ok" == "1" ]] \
  || die "Container has no network/DNS. Check bridge '${BRIDGE}' and NET_IP='${NET_IP}'."
ok "Network is up."

# --- Provision inside the container -----------------------------------------
info "Installing base packages…"
pct exec "$CTID" -- bash -c \
  "export DEBIAN_FRONTEND=noninteractive; apt-get update -qq && apt-get install -y -qq ca-certificates curl git"

info "Installing Docker (this can take a minute)…"
pct exec "$CTID" -- bash -c "curl -fsSL https://get.docker.com | sh"
pct exec "$CTID" -- systemctl enable --now docker
pct exec "$CTID" -- docker version >/dev/null \
  || die "Docker did not start in the container (try PRIVILEGED=1, or check kernel features)."
ok "Docker installed."

# Build the clone URL, embedding the token for a private repo (never printed).
CLONE_URL="$REPO_URL"
[[ -n "${GITHUB_TOKEN:-}" ]] && CLONE_URL="https://${GITHUB_TOKEN}@${REPO_URL#https://}"

info "Cloning repo (branch ${REPO_BRANCH})…"
if ! pct exec "$CTID" -- bash -c \
  "rm -rf /opt/mail-server && git clone --depth 1 --branch '${REPO_BRANCH}' '${CLONE_URL}' /opt/mail-server"; then
  die "git clone failed. If the repo is private, pass GITHUB_TOKEN=<PAT>; also confirm REPO_BRANCH='${REPO_BRANCH}' exists."
fi
# Scrub the token from the saved remote so it isn't left on disk.
pct exec "$CTID" -- bash -c "cd /opt/mail-server && git remote set-url origin '${REPO_URL}'" || true
ok "Repo cloned to /opt/mail-server."

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
rm -f "$ENV_TMP"; trap 'rm -f "$ENV_TMP"' EXIT
ok ".env written."

# The Ubuntu template ships Postfix listening on :25, which collides with the
# Dockerized Postfix. Disable it so the container's port 25 is free.
info "Freeing port 25 (disabling the template's built-in Postfix)…"
pct exec "$CTID" -- bash -c \
  "systemctl disable --now postfix >/dev/null 2>&1 || true; systemctl mask postfix >/dev/null 2>&1 || true"

# --- Build + launch (streamed so failures are visible) ----------------------
info "Building images and starting the stack (several minutes)…"
pct exec "$CTID" -- bash -c \
  "cd /opt/mail-server && docker compose -f docker-compose.yml up -d --build"

info "Service status:"
pct exec "$CTID" -- bash -c "cd /opt/mail-server && docker compose ps" || true

CT_IP="$(pct exec "$CTID" -- hostname -I 2>/dev/null | awk '{print $1}')"

# --- Summary ----------------------------------------------------------------
cat <<EOF

${GRN}========================================================================${NC}
${GRN} mail-server deployed in LXC ${CTID} (${CT_HOSTNAME})${NC}
${GRN}========================================================================${NC}

  Container IP : ${CT_IP:-<run: pct exec $CTID -- hostname -I>}
  Dashboard    : https://${WEB_HOSTNAME}/   (point this at the container IP)
  Admin login  : ${ADMIN_EMAIL}
  Admin pass   : ${ADMIN_PASSWORD}

  ${YLW}Save the admin password now — it is not stored anywhere else.${NC}

Next steps:
  1. DNS: A records for ${MAIL_HOSTNAME} and ${WEB_HOSTNAME} -> ${CT_IP:-container IP},
     an MX for each hosted domain, and a PTR (reverse DNS) for ${MAIL_HOSTNAME}.
  2. TLS: once DNS resolves and ports 80/443 are reachable:
       pct exec ${CTID} -- bash -c 'cd /opt/mail-server && make certs'
  3. Add domains/mailboxes in the dashboard; publish + verify SPF/DKIM/DMARC.

Manage:  pct exec ${CTID} -- bash -c 'cd /opt/mail-server && make ps'
Logs:    pct exec ${CTID} -- bash -c 'cd /opt/mail-server && make logs'

See docs/deployment.md for the full runbook.
EOF
