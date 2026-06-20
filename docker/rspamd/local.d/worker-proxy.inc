# Milter worker — Postfix connects here (smtpd_milters = inet:rspamd:11332).
# Self-scan mode: the proxy scans messages itself rather than forwarding.
bind_socket = "*:11332";
milter = yes;
timeout = 120s;
upstream "local" {
  default = yes;
  self_scan = yes;
}
