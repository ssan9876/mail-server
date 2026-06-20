# VPS installation runbook

End-to-end guide to running mail-server on a public VPS — the recommended path
for a **real, internet-facing** mail server (a VPS gives you a static public IP,
the ability to open port 25, and reverse-DNS control that a home/NAT connection
can't).

The installer (`scripts/vps-install.sh`) handles Docker, the code, secret
generation, and bringing the stack up. The DNS, reverse-DNS, and TLS steps
around it are things only you can do at your provider/registrar — this document
covers all of them.

---

## TL;DR

```bash
# On a fresh Ubuntu 24.04 VPS, as root:
bash -c "$(curl -fsSL https://raw.githubusercontent.com/ssan9876/mail-server/main/scripts/vps-install.sh)"
```

It prompts for: mail hostname, dashboard hostname, admin email, and (optionally)
a Cloudflare token. Everything else (DB/Redis/JWT/Fernet secrets, admin
password) is generated for you. Then follow **After the script runs** below.

---

## 1. Before you start — choose the right VPS

A mail server has requirements many cheap/home setups can't meet:

- [ ] **Outbound port 25 open.** Most providers block it by default; you usually
      open a support ticket. The installer warns you if it's blocked. Providers
      that allow it (often on request): Hetzner, OVH, Vultr, DigitalOcean,
      Scaleway. **AWS/GCP/Azure block 25 hard** — avoid for sending.
- [ ] **You can set a PTR / reverse-DNS record** for the IP (almost all VPS
      panels let you). Required or your mail lands in spam / is rejected.
- [ ] **A clean IP** (not on blocklists). Check at multirbl.valli.org.
- [ ] **Specs:** 2 vCPU / 4 GB RAM / 25 GB+ disk is comfortable. 1 vCPU / 2 GB
      works for light use (the image build is the heaviest moment).
- [ ] **OS: Ubuntu 24.04 LTS.**

## 2. Before you start — DNS you can pre-create

Set these at your DNS host (e.g. Cloudflare). Doing it *before* you run the
script means records are propagating while you install. Replace `example.com`
and the IP.

| Type | Name | Value | Notes |
|------|------|-------|-------|
| A | `mail` | `<VPS IP>` | the mail host; **must** match PTR |
| A | `admin` | `<VPS IP>` | the dashboard |
| MX | `@` (example.com) | `mail.example.com` (priority 10) | |
| TXT | `@` | `v=spf1 mx ~all` | SPF |
| TXT | `_dmarc` | `v=DMARC1; p=quarantine; rua=mailto:dmarc@example.com` | DMARC |
| TXT | `mail._domainkey` | *(from the dashboard, after step 5)* | DKIM |

> **Cloudflare:** set the `mail`/`admin` A records to **DNS only (grey cloud)** —
> proxying (orange cloud) breaks SMTP/IMAP and direct TLS. If you use Cloudflare
> **Email Routing**, it owns the apex `MX`; disable it to run your own MX.

**PTR (reverse DNS):** in your VPS control panel, set the IP's PTR to
`mail.example.com`. This is set at the provider, not in your DNS zone.

## 3. Before you start — secrets to have ready

The script generates all infrastructure secrets automatically. The only things
*you* supply:

- **Hostnames + admin email** (prompted). The admin email domain must be real —
  reserved TLDs like `.local`/`.test` are rejected by the email validator.
- **Cloudflare API token** (optional) — only if you want the dashboard to
  publish/verify DNS for you. Scope it **Zone → DNS → Edit** for your zone
  (Cloudflare → My Profile → API Tokens → "Edit zone DNS" template).
- **Admin password** (optional) — set `ADMIN_PASSWORD=...` to choose your own;
  otherwise a strong one is generated and printed once.

## 4. Run the installer

```bash
# Interactive (recommended first time):
bash -c "$(curl -fsSL https://raw.githubusercontent.com/ssan9876/mail-server/main/scripts/vps-install.sh)"

# Or fully non-interactive:
MAIL_HOSTNAME=mail.example.com WEB_HOSTNAME=admin.example.com \
ADMIN_EMAIL=you@example.com CLOUDFLARE_API_TOKEN=xxxx SETUP_UFW=1 \
bash -c "$(curl -fsSL https://raw.githubusercontent.com/ssan9876/mail-server/main/scripts/vps-install.sh)"
```

What it does: warns if port 25 is blocked → installs Docker → clones the repo →
generates `.env` → (optionally) configures `ufw` → builds and starts all 9
services. On first boot the backend runs DB migrations and creates the admin
user. It prints the **admin password** — save it.

Re-running is safe: an existing `.env` is preserved (secrets are not rotated)
unless you pass `FORCE=1`.

## 5. After the script runs

1. **Firewall** (if you didn't let the script do it):
   ```bash
   ufw allow 22,80,443,25,465,587,993,995/tcp && ufw --force enable
   ```
2. **TLS certificates** — once the `mail`/`admin` A records resolve and ports
   80/443 are reachable:
   ```bash
   cd /opt/mail-server && make certs        # Let's Encrypt for both hostnames
   ```
   Until then the stack uses a self-signed fallback (browser/cert warnings).
3. **Log into the dashboard** at `https://admin.example.com/` with the admin
   email + password.
4. **Add your domain** → a DKIM keypair is generated. Open the domain's **DNS
   records** tab:
   - If you gave a Cloudflare token: click **Publish to Cloudflare**, then **Verify**.
   - Otherwise: copy the **DKIM** TXT record into your DNS by hand, then **Verify**.
5. **Create mailboxes** (e.g. `you@example.com`). Users connect with any mail
   client:

   | Protocol | Host | Port | Security |
   |----------|------|------|----------|
   | IMAP | `mail.example.com` | 993 | SSL/TLS |
   | SMTP (send) | `mail.example.com` | 587 | STARTTLS |

6. **Verify deliverability:** send a message to a fresh address at
   [mail-tester.com](https://www.mail-tester.com) and aim for 10/10 (it checks
   SPF, DKIM, DMARC, PTR, blocklists).

## 6. Operations

| Task | Command (run in `/opt/mail-server`) |
|------|------|
| Status | `make ps` |
| Logs | `make logs` |
| Renew/issue certs | `make certs` |
| DB backup | `make backup` |
| Apply updates | `git pull && docker compose -f docker-compose.yml up -d --build` |

## 7. Security checklist

- [ ] Change the generated **admin password** after first login.
- [ ] **Rotate the Cloudflare token** if you pasted it anywhere shared.
- [ ] `.env` stays `chmod 600`, never committed.
- [ ] Back up `SECRETS_ENCRYPTION_KEY` offline — losing it makes stored DKIM
      keys unrecoverable.
- [ ] Real TLS certs issued (not the self-signed fallback).
- [ ] SPF / DKIM / DMARC verified green; PTR matches `MAIL_HOSTNAME`.
- [ ] Schedule DB + Maildir backups (see [deployment.md](deployment.md#9-backups)).

## 8. Troubleshooting

- **Can't send mail / times out:** outbound port 25 blocked — ask the provider.
- **Mail goes to spam / rejected:** PTR missing or mismatched; or DKIM/SPF/DMARC
  not verified; or IP on a blocklist.
- **Cert issuance fails:** A records not resolving yet, or port 80 blocked, or
  Cloudflare proxy (orange cloud) on the host records — set them to DNS-only.
- **DKIM shows unverified but the record exists:** your resolver cached the
  earlier miss; wait for the negative-cache TTL or query `@1.1.1.1` to confirm
  it's actually published.
- **Dashboard rejects the admin email:** the domain is a reserved TLD
  (`.local`, `.test`, …) — use a real domain.

See [architecture.md](architecture.md) for how the components fit together and
[deployment.md](deployment.md) for the generic (non-VPS) runbook.
