# Dovecot service

IMAP/POP3 access + LMTP delivery for virtual mailboxes, authenticated against
PostgreSQL. Maildir++ storage on the shared `maildata` volume, owned by a single
`vmail` (uid/gid 5000) user. Per-user quota comes from `mailboxes.quota_mb`.

## Ports

| Port  | Purpose                                              |
|-------|------------------------------------------------------|
| 143   | IMAP (STARTTLS; plaintext auth refused before TLS)   |
| 993   | IMAPS (implicit TLS)                                  |
| 110   | POP3 (STARTTLS)                                       |
| 995   | POP3S (implicit TLS)                                  |
| 24    | LMTP — Postfix delivers accepted mail here           |
| 12345 | Dovecot SASL — Postfix submission auth (internal net)|

## How it ties into the rest of the stack

- **Postfix → Dovecot (delivery):** `virtual_transport = lmtp:inet:dovecot:24`.
- **Postfix → Dovecot (auth):** `smtpd_sasl_path = inet:dovecot:12345`.
- **Passwords:** stored as `{ARGON2ID}…`; Dovecot reads the inline scheme prefix.
- **Mail location:** `maildir:/maildata/%d/%n`, matching `mailboxes.maildir_path`.

## Validating

```bash
# Print the effective (non-default) config.
docker compose exec dovecot doveconf -n

# Look up a user via the SQL userdb (home, quota, uid/gid).
docker compose exec dovecot doveadm user john@example.com

# Test a password against the SQL passdb.
docker compose exec dovecot doveadm auth test john@example.com

# Inspect a mailbox's current quota usage.
docker compose exec dovecot doveadm quota get -u john@example.com
```

## Notes

- TLS certs are chosen at startup: Let's Encrypt under
  `/etc/letsencrypt/live/$MAIL_HOSTNAME/` if present, otherwise a self-signed
  fallback so the service starts before certbot has run.
- `doveconf -n` runs in the entrypoint before launch as a config sanity check.
