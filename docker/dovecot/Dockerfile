# ============================================================================
# Dovecot IMAP/POP3 + LMTP image (Ubuntu 24.04 base).
# SQL auth against PostgreSQL; Maildir++ storage; per-user quota.
# ============================================================================
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        dovecot-core \
        dovecot-imapd \
        dovecot-pop3d \
        dovecot-lmtpd \
        dovecot-pgsql \
        ca-certificates \
        gettext-base \
        openssl \
    && rm -rf /var/lib/apt/lists/*

# Single virtual-mail user that owns all Maildirs.
RUN groupadd -g 5000 vmail \
    && useradd -g vmail -u 5000 vmail -d /maildata -s /usr/sbin/nologin

# Replace the distro config tree with ours.
RUN rm -rf /etc/dovecot/conf.d /etc/dovecot/dovecot.conf
COPY dovecot.conf /etc/dovecot/dovecot.conf
COPY conf.d/ /etc/dovecot/conf.d/
COPY dovecot-sql.conf.ext.tmpl /etc/dovecot/dovecot-sql.conf.ext.tmpl

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# 143 imap, 993 imaps, 110 pop3, 995 pop3s, 24 lmtp, 12345 sasl-auth.
EXPOSE 143 993 110 995 24 12345

ENTRYPOINT ["/entrypoint.sh"]
