# Architecture

## Components

```
                          Internet
                             │
        ┌────────────────────┼─────────────────────┐
        │ :80/:443           │ :25/:587/:465        │ :993/:995/:143
        ▼                    ▼                      ▼
   ┌─────────┐         ┌──────────┐           ┌──────────┐
   │  nginx  │  proxy  │ postfix  │  milter   │ dovecot  │
   │  (edge) ├────┐    │  (MTA)   ├──────────▶│ (IMAP/   │
   └────┬────┘    │    └────┬─────┘  :11332   │  LMTP)   │
        │         │         │  LMTP :24  ▲    └────┬─────┘
        │ /api    │ /       │            │         │ Maildir
        ▼         ▼         ▼         ┌──┴────┐    ▼
   ┌─────────┐ ┌──────┐  ┌──────────┐│rspamd │ [maildata vol]
   │ backend │ │ SPA  │  │ SASL:12345││(spam+ │
   │(FastAPI)│ │(React)│ │  dovecot  ││ DKIM) │
   └────┬────┘ └──────┘  └──────────┘└───┬───┘
        │                                 │ reads /dkim
        ▼                                 ▼
   ┌──────────┐   ┌───────┐         [dkim_keys vol]
   │ postgres │   │ redis │              ▲
   └────▲─────┘   └───────┘              │ exports keys
        │  read-only lookups             │
        └── postfix/dovecot (mail_lookup role)
                                  backend ┘
```

## Data plane vs control plane

- **Control plane** — the FastAPI backend + React SPA behind nginx. Manages
  domains, mailboxes, aliases, DNS automation, and audit logs. Authenticated
  with JWTs. Talks to Postgres (full access) and Redis.
- **Data plane** — Postfix ⇄ Rspamd ⇄ Dovecot moving actual mail. These read
  Postgres **read-only** via the least-privilege `mail_lookup` role; they never
  use the API.

The two planes share only the database (as the source of truth) and two named
volumes: `maildata` (Maildir storage) and `dkim_keys` (signing keys the backend
exports for Rspamd).

## Mail flow

**Inbound:** Internet → Postfix:25 → (Rspamd milter scores/greylists) → if
accepted, LMTP → Dovecot → Maildir. Postfix decides acceptance from the
`virtual_mailbox_domains` / `virtual_mailbox_maps` SQL lookups; aliases and
catch-all are resolved by `virtual_alias_*` maps.

**Outbound:** authenticated client → Postfix submission (587/465, SASL via
Dovecot) → Rspamd milter **DKIM-signs** using the domain's key → delivered to
the remote MTA.

## Authentication

Two JWT principal kinds, distinguished by a `principal` claim:

- `user` — operator accounts (`superadmin` / `domain_admin` / `user`) for the
  dashboard + REST API.
- `mailbox` — end users on the self-service portal.

Access tokens are short-lived; refresh tokens are stored in Redis with one-time
rotation, and a Redis blacklist revokes access tokens on logout. The two
principals have separate login/refresh endpoints and cannot cross over.

## Security boundaries

- Datastores sit on the `internal` Docker network; the mail daemons sit on
  `mail`. Neither Postgres nor Redis is published to the host.
- The mail daemons authenticate to Postgres with a read-only role.
- DKIM private keys are encrypted at rest (Fernet) in the DB; decrypted copies
  exist only on the internal `dkim_keys` volume for Rspamd.
- nginx terminates TLS and applies rate limits (tight on auth endpoints); the
  backend additionally enforces per-account login lockout and JWT revocation.
- Every state-changing operation writes an append-only audit log.

See [deployment.md](deployment.md) for operational details.
