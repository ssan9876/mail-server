# Hybrid deployment (home / Proxmox, no public MX)

How to run mail-server on a box **without a public IP or with port 25 blocked**
(a home connection, a NAT'd Proxmox LXC, or a cloud that blocks outbound 25).

A mail server normally needs a public IP, outbound **port 25**, and a **PTR**
record. Home/residential links almost never have these. This setup works around
all three by splitting the job:

| Function | Handled by | Notes |
|----------|-----------|-------|
| **Inbound** mail | **Cloudflare Email Routing** | Receives at Cloudflare's MX and *forwards* to an address you choose. Does **not** deliver into your Dovecot. |
| **Outbound** mail | **SMTP relay (smarthost)** | Postfix sends through e.g. Brevo instead of direct on :25. |
| **Dashboard + Webmail** | **Cloudflare Tunnel** | Exposes the HTTP services publicly with no open ports. |
| **Mailboxes / IMAP / SMTP** | Dovecot / Postfix on the box | Reachable on the LAN (or via VPN/WARP). |

> **Limitation:** with this setup mailboxes can **send** (via the relay) and be
> **managed/read** (dashboard + webmail), but they do **not receive** internet
> mail into Dovecot — Email Routing forwards it elsewhere. For full send *and*
> receive into your own mailboxes, use the [VPS path](deployment.md). See
> [Migrating to a VPS](#migrating-to-a-vps) below.

---

## 1. Deploy the stack

On a Proxmox host, the helper script provisions an LXC and deploys everything:

```bash
bash scripts/proxmox-create-lxc.sh        # see scripts/README.md for options
```

Or on any Ubuntu 24.04 box, `docker compose -f docker-compose.yml up -d --build`.

The container/host gets a LAN IP (e.g. `192.168.88.167`) referenced below.

## 2. Outbound — SMTP relay

Pick a relay (Brevo's free tier = 300/day; SMTP2GO, Mailjet, Amazon SES also
work), then set in `.env`:

```ini
RELAYHOST=[smtp-relay.brevo.com]:587
RELAY_USERNAME=<relay login>
RELAY_PASSWORD=<relay SMTP key>
```

Rebuild Postfix: `docker compose -f docker-compose.yml up -d --build postfix`.

Provider gotchas seen in practice:
- **Authorize your sending IP** in the relay (Brevo: *Security → Authorized IPs*).
  Your egress IP is what the relay sees — find it with `curl ifconfig.me` from
  the box. Home IPs are dynamic, so re-add it if it changes (or allow all).
- For inbox placement, **authenticate the domain** in the relay (adds its DKIM
  so DMARC aligns), in addition to the stack's own Rspamd DKIM.

Verify: `docker compose exec postfix sendmail -f you@yourdomain dest@example.com`
then check `docker compose logs postfix` for `status=sent`.

## 3. Inbound — Cloudflare Email Routing

Enable **Email Routing** for the domain in the Cloudflare dashboard and add
routes that forward `you@yourdomain` to an inbox you control. Cloudflare manages
the MX automatically (you can't also point MX at this box — it has no public IP).

> Email Routing owns the apex **MX** record. Trying to publish your own MX via
> the app's "Publish to Cloudflare" returns `1046: managed by Email Routing` —
> that's expected here. The app's **DKIM** record can still be published.

## 4. Dashboard + Webmail — Cloudflare Tunnel

With `cloudflared` running (token mode), add **Public Hostnames** in
**Zero Trust → Networks → Tunnels → your tunnel → Public Hostnames**:

| Hostname | Service | Extra |
|----------|---------|-------|
| `admin.<domain>` | `HTTPS` → `<box-ip>:443` | TLS → **No TLS Verify: ON** (origin is self-signed) |
| `webmail.<domain>` | `HTTP` → `<box-ip>:8080` | plain HTTP origin; no toggle needed |

Then align the app's hostname so links/CORS match:

```ini
WEB_HOSTNAME=admin.<domain>
CORS_ORIGINS=https://admin.<domain>
```
`docker compose -f docker-compose.yml up -d backend nginx`

## 5. Logging in

- **Admin dashboard** (`https://admin.<domain>`): the operator account
  (`ADMIN_EMAIL` / `ADMIN_PASSWORD`).
- **Webmail** (`https://webmail.<domain>`) and **mailbox portal**
  (`/portal/login`): a mailbox account — **use the full email address** as the
  username (e.g. `you@yourdomain`, not `you`). This is multi-domain, so a bare
  username won't resolve.

## Operational notes / gotchas

- **Full email as username** for all mailbox logins (IMAP, webmail, portal).
- **Reserved TLDs** like `.local` are rejected by the email validator — use a
  real domain for mailbox/admin addresses.
- **Docker-in-LXC** (Proxmox): the container needs `nesting=1,keyctl=1` **and**
  `lxc.apparmor.profile: unconfined` (+ device/cgroup allowances) or image
  builds fail with "unable to apply apparmor profile". The helper script does
  this automatically.
- The Ubuntu template ships **Postfix on :25**; the script disables it so the
  Dockerised Postfix can bind. (Handled automatically.)
- IMAP/SMTP are **not** exposed through the tunnel (it only carries HTTP). Use a
  mail client on the LAN, or a VPN/WARP, for direct IMAP/SMTP access.

## Migrating to a VPS

When you get a VPS with a public IP, port 25, and PTR (see
[deployment.md](deployment.md)), switch to full self-hosting:

1. Deploy the stack on the VPS ([docs/vps-install.md](vps-install.md)).
2. Set **PTR** (reverse DNS) for the VPS IP → `mail.<domain>`.
3. **Disable Cloudflare Email Routing** for the domain.
4. Publish **MX → mail.<domain> → VPS IP**, plus SPF/DKIM/DMARC (the dashboard's
   *Publish to Cloudflare* now works once Email Routing is off).
5. Optionally drop the relay (`RELAYHOST=` blank) to send directly on :25, or
   keep it.
6. Repoint the `admin.`/`webmail.` tunnel hostnames (or serve them directly from
   the VPS behind real TLS).

You now receive mail directly into your own mailboxes.
