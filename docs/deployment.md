# Deployment guide

How to stand up mail-server on a fresh **Ubuntu 24.04** host.

> Running a public mail server requires a static IP, a **PTR (reverse DNS)**
> record from your hosting provider, and outbound port 25 unblocked. Many cloud
> providers block port 25 by default — request an unblock before you start.

---

## 1. Prerequisites

- Ubuntu 24.04 server with a public static IPv4 address.
- A domain you control, with DNS managed by **Cloudflare** (for the automation).
- Docker Engine + the Compose plugin:
  ```bash
  curl -fsSL https://get.docker.com | sh
  ```
- Ports open on the host firewall (see §6).
- A **PTR record** mapping your IP → `mail.example.com`, set at your provider.

## 2. Get the code and configure

```bash
git clone https://github.com/ssan9876/mail-server.git
cd mail-server
cp .env.example .env
```

Generate strong secrets and edit `.env`:

```bash
openssl rand -base64 32      # POSTGRES_PASSWORD, POSTGRES_MAIL_PASSWORD, REDIS_PASSWORD, RSPAMD_PASSWORD
openssl rand -hex 32         # JWT_SECRET_KEY
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"   # SECRETS_ENCRYPTION_KEY
```

Set at minimum:

| Variable | Notes |
|---|---|
| `MAIL_HOSTNAME` | FQDN of the server, e.g. `mail.example.com` (needs A + PTR) |
| `WEB_HOSTNAME` | Where the dashboard/API is served, e.g. `admin.example.com` |
| `POSTGRES_PASSWORD` / `POSTGRES_MAIL_PASSWORD` | DB passwords |
| `REDIS_PASSWORD` | Redis auth |
| `JWT_SECRET_KEY`, `SECRETS_ENCRYPTION_KEY` | crypto keys |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | first superadmin, created on first boot |
| `CLOUDFLARE_API_TOKEN` | scoped token (see §5) |
| `ACME_EMAIL` | Let's Encrypt contact |
| `CORS_ORIGINS` | `https://admin.example.com` |

## 3. Base DNS records (before first boot)

Point the hostnames at your server and set up mail routing. The app can publish
the per-domain records (SPF/DKIM/DMARC/MX) for you via Cloudflare later, but the
**server's own A record** and the **MX** must exist:

| Type | Name | Value | Notes |
|---|---|---|---|
| A | `mail` | `<server IP>` | the mail host |
| A | `admin` | `<server IP>` | the dashboard |
| MX | `example.com` | `mail.example.com` (prio 10) | per hosted domain |
| TXT | `example.com` | `v=spf1 mx ~all` | SPF |
| TXT | `_dmarc.example.com` | `v=DMARC1; p=quarantine; rua=mailto:dmarc@example.com` | DMARC |
| TXT | `mail._domainkey.example.com` | *(from the dashboard)* | DKIM public key |
| PTR | *(reverse zone)* | `mail.example.com` | set at your IP provider |

In the dashboard you can view the exact records per domain (**Domains → DNS
records**) and **Publish to Cloudflare** / **Verify** them.

## 4. Launch

```bash
make prod-up         # docker compose -f docker-compose.yml up -d --build
```

On startup the backend **waits for Postgres, runs Alembic migrations**, and
**creates the first superadmin** from `ADMIN_EMAIL`/`ADMIN_PASSWORD`. Watch:

```bash
make ps
docker compose logs -f backend
```

### TLS certificates

The edge nginx, Postfix, and Dovecot all start with a **self-signed fallback**
cert so the stack comes up immediately. To issue real Let's Encrypt certs (once
the A records resolve and port 80 is reachable):

```bash
docker compose run --rm certbot certonly --webroot -w /var/www/acme \
  -d admin.example.com -d mail.example.com \
  --email "$ACME_EMAIL" --agree-tos --no-eff-email
docker compose restart nginx postfix dovecot
```

The `certbot` service auto-renews every 12h thereafter.

## 5. Cloudflare API token

Create a token (My Profile → API Tokens) scoped to **Zone : DNS : Edit** for the
relevant zones. Put it in `CLOUDFLARE_API_TOKEN`. It is used only for the
publish/verify actions; nothing is changed without you clicking them.

## 6. Firewall (ufw)

```bash
ufw allow 22/tcp      # SSH
ufw allow 80,443/tcp  # web + ACME
ufw allow 25/tcp      # inbound SMTP
ufw allow 465,587/tcp # submission (implicit/STARTTLS)
ufw allow 993,995/tcp # IMAPS / POP3S
ufw enable
```

## 7. Create domains and mailboxes

Log in at `https://admin.example.com` with the superadmin credentials, then:

1. **Add domain** → a DKIM keypair is generated automatically.
2. **DNS records** tab → Publish to Cloudflare (or copy them to your DNS), then
   **Verify DNS**.
3. **Mailboxes** tab → add accounts. Users sign in to mail clients with their
   full email + password, and to the self-service portal at `/portal/login`.

## 8. Verify mail flow

```bash
# Inbound: send a test from an external account, then check delivery:
docker compose exec dovecot doveadm mailbox status -u john@example.com 'messages' INBOX

# Map lookups:
docker compose exec postfix \
  postmap -q "john@example.com" pgsql:/etc/postfix/pgsql/virtual_mailbox_maps.cf

# Check DKIM signing / spam scoring:
docker compose logs rspamd | grep -i dkim
```

Use [mail-tester.com](https://www.mail-tester.com) to score SPF/DKIM/DMARC/PTR.

## 9. Backups

```bash
# Database
docker compose exec -T postgres pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" > backup.sql

# Mail (Maildir volume)
docker run --rm -v mail-server_maildata:/data -v "$PWD":/backup alpine \
  tar czf /backup/maildata.tgz -C /data .
```

DKIM private keys live (encrypted) in the database, so the DB dump is sufficient
to restore signing — keep `SECRETS_ENCRYPTION_KEY` safe and backed up separately.

## 10. Operations

| Task | Command |
|---|---|
| Tail all logs | `make logs` |
| Apply new migrations | `make migrate` |
| Open psql | `make psql` |
| Re-export DKIM keys to Rspamd | `POST /api/v1/domains/dkim/sync` (admin) |
| Check Dovecot config | `docker compose exec dovecot doveconf -n` |
| Validate Postfix config | `docker compose exec postfix postfix check` |

## 11. Security checklist

- [ ] All secrets in `.env` are randomly generated; `.env` is **not** committed.
- [ ] PTR record set and matches `MAIL_HOSTNAME`.
- [ ] Real TLS certificates issued (not the self-signed fallback).
- [ ] SPF / DKIM / DMARC verified green for each domain.
- [ ] Firewall restricts to the ports in §6.
- [ ] `SECRETS_ENCRYPTION_KEY` backed up offline (loss = unrecoverable DKIM keys).
- [ ] Regular DB + Maildir backups scheduled.

See [architecture.md](architecture.md) for how the pieces fit together.
