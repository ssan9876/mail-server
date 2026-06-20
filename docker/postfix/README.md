# Postfix service

Virtual-mailbox MTA backed by PostgreSQL. Domains, mailboxes, and aliases are
resolved live from the database via `postfix-pgsql` lookup maps — no flat files,
no `postmap` reload after changes.

## Ports

| Port | Service    | Auth | TLS              |
|------|------------|------|------------------|
| 25   | smtp       | none | opportunistic    |
| 587  | submission | SASL | STARTTLS (required) |
| 465  | smtps      | SASL | implicit (wrapper)  |

Inbound (25) is protected from being an open relay by
`reject_unauth_destination`; it only accepts mail for domains present in
`virtual_mailbox_domains`. Submission (587/465) requires Dovecot SASL auth.

## Lookup maps

Templates live in `pgsql/` and are rendered to `/etc/postfix/pgsql/` at startup
using the **read-only** `mail_lookup` role. Resolution order for recipients:

1. `virtual_alias_maps.cf` — specific aliases (`user@domain` → destinations)
2. `virtual_mailbox_self.cf` — maps a real mailbox to itself (shields it from
   the catch-all)
3. `virtual_alias_catchall.cf` — `@domain` catch-all for everything else

`virtual_mailbox_maps.cf` then confirms the (possibly rewritten) recipient is a
real mailbox before handing off to Dovecot via LMTP.

## Validating the maps

From the host, exec into the running container and query a map directly with
`postmap -q "<key>" pgsql:<map>`:

```bash
# Is the domain accepted?
docker compose exec postfix \
  postmap -q "example.com" pgsql:/etc/postfix/pgsql/virtual_mailbox_domains.cf

# Does the mailbox exist? (returns the Maildir path)
docker compose exec postfix \
  postmap -q "john@example.com" pgsql:/etc/postfix/pgsql/virtual_mailbox_maps.cf

# Where does an alias deliver?
docker compose exec postfix \
  postmap -q "team@example.com" pgsql:/etc/postfix/pgsql/virtual_alias_maps.cf

# Catch-all for the domain (note the leading @):
docker compose exec postfix \
  postmap -q "@example.com" pgsql:/etc/postfix/pgsql/virtual_alias_catchall.cf
```

An empty result means "no match" (and, for the domains map, that the domain is
not accepted). Use `postconf -n` to print the active non-default settings and
`postfix check` to validate configuration.

## Logging

Postfix logs to stdout (`maillog_file = /dev/stdout`), so `docker compose logs
postfix` shows live mail flow.
