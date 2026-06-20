# Deployment scripts

- **`vps-install.sh`** — run on a fresh **Ubuntu 24.04 VPS** (as root) to install
  Docker, clone the repo, generate a `.env` with strong secrets, and launch the
  stack. This is the recommended path for a *public* mail server (a VPS gives you
  the real IP, open port 25, and PTR control that a home connection can't).
- **`proxmox-create-lxc.sh`** — run on a **Proxmox VE host** to auto-create an
  LXC and deploy into it. Best for a home lab / internal mail.

> A mail server cannot run behind a Cloudflare Tunnel or NAT-only home link:
> external servers deliver over port 25 to your real IP, and you need a PTR
> record. Residential ISPs almost always block port 25.

## VPS quick start

```bash
# Public repo / after PR #1 is merged to main:
MAIL_HOSTNAME=mail.example.com WEB_HOSTNAME=admin.example.com \
ADMIN_EMAIL=admin@example.com bash vps-install.sh

# Private repo on the PR branch (token = a PAT with repo read access):
GITHUB_TOKEN=ghp_xxx REPO_BRANCH=build/mail-server-platform \
MAIL_HOSTNAME=mail.example.com WEB_HOSTNAME=admin.example.com \
ADMIN_EMAIL=admin@example.com bash vps-install.sh
```

Pick a VPS provider that allows port 25 (often needs a quick support ticket) and
lets you set reverse DNS — e.g. Hetzner, OVH, Vultr, DigitalOcean.

**Full step-by-step runbook (before / run / after):**
[../docs/vps-install.md](../docs/vps-install.md).

The installer prompts for the mail/dashboard hostnames, admin email, and an
optional Cloudflare token; everything else (DB/Redis/JWT/Fernet secrets, admin
password) is generated. It also warns if outbound port 25 is blocked, and can
configure the firewall. Useful flags:

| Var | Effect |
|---|---|
| `ADMIN_PASSWORD` | choose the admin password (else generated) |
| `CLOUDFLARE_API_TOKEN` | enable dashboard DNS publish/verify |
| `SETUP_UFW=1` | configure the firewall non-interactively |
| `FORCE=1` | regenerate `.env` (rotates secrets) on a re-run |
| `REPO_BRANCH` / `GITHUB_TOKEN` | source branch / private-repo clone |

Re-running is safe — an existing `.env` is preserved unless `FORCE=1`.

---

## Proxmox helper script

`proxmox-create-lxc.sh` provisions the entire mail-server stack into a fresh
Ubuntu 24.04 LXC container on a Proxmox VE host — create container → install
Docker → clone repo → generate secrets → launch.

## Usage

Run **on the Proxmox host, as root**. One-liner:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/ssan9876/mail-server/main/scripts/proxmox-create-lxc.sh)"
```

…or copy the script over and run it. It prompts for the three required values
(mail FQDN, dashboard hostname, admin email) when run interactively, or take
them from the environment for a non-interactive run:

```bash
MAIL_HOSTNAME=mail.example.com \
WEB_HOSTNAME=admin.example.com \
ADMIN_EMAIL=admin@example.com \
bash proxmox-create-lxc.sh
```

> Until PR #1 is merged to `main`, set `REPO_BRANCH=build/mail-server-platform`
> (and use that branch's raw URL in the one-liner above).

## What it does

1. Creates an LXC (defaults: 2 cores, 4 GB RAM, 16 GB disk) with
   `nesting=1,keyctl=1` so Docker runs inside it.
2. Installs Docker + the Compose plugin.
3. Clones the repo to `/opt/mail-server`.
4. Generates a `.env` with strong random secrets (DB/Redis/Rspamd passwords,
   JWT secret, a valid Fernet `SECRETS_ENCRYPTION_KEY`) and a random admin
   password (printed once at the end).
5. Builds and starts the stack with `docker compose`.

## Configuration (environment variables)

| Var | Default | Notes |
|---|---|---|
| `CTID` | next free id | container id |
| `CT_HOSTNAME` | `mailserver` | container hostname |
| `CORES` / `RAM_MB` / `SWAP_MB` / `DISK_GB` | `2` / `4096` / `2048` / `16` | sizing |
| `STORAGE` | auto-detect (prefers `local-lvm`) | rootfs storage — falls back to the first storage with `rootdir` content |
| `BRIDGE` | `vmbr0` | network bridge (validated against `ip link`) |
| `NET_IP` | `dhcp` | e.g. `192.168.1.50/24,gw=192.168.1.1` for static |
| `TEMPLATE_STORAGE` | auto-detect (prefers `local`) | first storage with `vztmpl` content |
| `PRIVILEGED` | `1` | `0` for an unprivileged container (PVE 8+) |
| `REPO_URL` / `REPO_BRANCH` | this repo / `main` | source to deploy |
| `GITHUB_TOKEN` | empty | PAT for cloning a **private** repo |
| `CLOUDFLARE_API_TOKEN` | empty | optional; enables DNS automation |
| `ADMIN_PASSWORD` | random | set to choose your own |
| `DEBUG` | `0` | `1` for `set -x` tracing |

## Troubleshooting "it didn't create the container"

The refactored script auto-detects storage, validates the bridge/CTID, and
prints the exact failing step (with line number) instead of exiting silently.
The usual causes:

- **Storage name** — older versions assumed `local-lvm`. It now detects a
  storage with `rootdir` content; override with `STORAGE=` if needed
  (`pvesm status` lists them).
- **Private repo** — if the clone fails, pass `GITHUB_TOKEN=<PAT>` and (until
  PR #1 is merged) `REPO_BRANCH=build/mail-server-platform`.
- **Curl one-liner 404** — the `main` raw URL only works once the script is on
  `main`; before then, copy the script from the PR branch and run it locally.
- Re-run with `DEBUG=1` to see every command.

## After it runs

The script prints the container IP, dashboard URL, and admin credentials, then
the remaining manual steps: point DNS (A/MX/PTR) at the container, issue TLS
certs (`pct exec <CTID> -- bash -c 'cd /opt/mail-server && make certs'`), and add
domains/mailboxes in the dashboard. See [../docs/deployment.md](../docs/deployment.md).

## Notes

- **Privileged by default** for the most reliable Docker-in-LXC and mail
  networking. Set `PRIVILEGED=0` for stronger isolation on Proxmox VE 8+; if
  Docker's overlay2 storage misbehaves in an unprivileged container, fall back
  to privileged.
- Running a public mail server still requires outbound port 25 open and a PTR
  record — neither of which this script can do for you.
