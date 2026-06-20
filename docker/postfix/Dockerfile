# ============================================================================
# Postfix MTA image (Ubuntu 24.04 base, matching the target host OS).
# Virtual mailboxes backed by PostgreSQL; Maildir delivery via Dovecot LMTP.
# ============================================================================
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        postfix \
        postfix-pgsql \
        ca-certificates \
        gettext-base \
        openssl \
    && rm -rf /var/lib/apt/lists/*

# Static configuration.
COPY main.cf /etc/postfix/main.cf
COPY master.cf /etc/postfix/master.cf

# PostgreSQL lookup-map templates (rendered to /etc/postfix/pgsql at startup).
COPY pgsql/ /etc/postfix/pgsql-templates/

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# 25 = inbound SMTP, 587 = submission (STARTTLS), 465 = smtps (implicit TLS).
EXPOSE 25 465 587

ENTRYPOINT ["/entrypoint.sh"]
