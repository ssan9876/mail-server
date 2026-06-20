# mail-server

A self-hosted, multi-domain email platform — a lightweight, open alternative to
Google Workspace / Microsoft 365. SMTP + IMAP, virtual mailboxes, aliases,
catch-all, DKIM/SPF/DMARC, a REST API, and admin/user dashboards.


## Stack

| Layer            | Technology                          |
|------------------|-------------------------------------|
| API / control    | FastAPI (Python 3.12), JWT auth     |
| Frontend         | React + TypeScript (Vite)           |
| Database         | PostgreSQL 16                        |
| Cache / limits   | Redis 7                             |
| MTA (SMTP)       | Postfix + `postfix-pgsql`           |
| MDA (IMAP/POP3)  | Dovecot + `dovecot-pgsql`, Maildir  |
| Spam / DKIM      | Rspamd (milter)                     |
| Edge / TLS       | Nginx + Certbot (Let's Encrypt)     |
| DNS automation   | Cloudflare API                      |
| Deployment       | Docker Compose (Ubuntu 24.04 hosts) |

## Repository layout

```
backend/      FastAPI control plane (API, services, models, migrations)
frontend/     React + TypeScript dashboards (admin + user)
docker/       Per-service images + config (postfix, dovecot, nginx, rspamd, postgres)
docs/         Architecture, API, and deployment docs
tests/        Integration + end-to-end tests
docker-compose.yml          base (production) stack
docker-compose.override.yml dev overrides (hot reload, mail catcher)
```

## Quick start (development)

```bash
make env          # create .env from .env.example, then edit secrets
make up           # build + start the full stack with dev overrides
make ps           # check service health
make logs         # tail logs
```

Key dev endpoints:

| URL                      | What                              |
|--------------------------|-----------------------------------|
| http://localhost:8000/docs | FastAPI Swagger UI              |
| http://localhost:3000    | Frontend (Vite dev server)        |
| http://localhost:8025    | Mailpit — captured outbound mail  |

## Production

```bash
docker compose -f docker-compose.yml up -d --build   # or: make prod-up
```

See [docs/deployment.md](docs/deployment.md) for DNS records (MX, SPF, DKIM,
DMARC, PTR), firewall rules, and TLS setup.

## Build roadmap

1. ✅ Docker Compose skeleton + service images + env config
2. ✅ PostgreSQL schema + Alembic migrations
3. ✅ FastAPI core: config, DB, security, JWT auth
4. ✅ Domain management + Cloudflare DNS automation
5. ✅ Mailbox + alias CRUD
6. ✅ Postfix (virtual mailbox, pgsql maps, DKIM milter)
7. ✅ Dovecot (SQL auth, Maildir, TLS, LMTP)
8. ✅ Rspamd (spam filtering + DKIM signing)
9. ✅ Nginx (TLS, reverse proxy, rate limiting)
10. ✅ Admin dashboard
11. ✅ User dashboard (mailbox self-service portal)
12. ✅ Hardening, audit logs, docs, migrate-on-boot

**All steps complete.** See [docs/deployment.md](docs/deployment.md) and
[docs/architecture.md](docs/architecture.md).
