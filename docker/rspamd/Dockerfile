# ============================================================================
# Rspamd image — spam filtering + DKIM signing milter for Postfix.
# ============================================================================
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        gnupg2 \
        wget \
    && wget -qO- https://rspamd.com/apt-stable/gpg.key | gpg --dearmor > /usr/share/keyrings/rspamd.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/rspamd.gpg] http://rspamd.com/apt-stable/ noble main" \
        > /etc/apt/sources.list.d/rspamd.list \
    && apt-get update && apt-get install -y --no-install-recommends rspamd \
    && rm -rf /var/lib/apt/lists/*

# Static module overrides (Redis + controller are generated at startup).
COPY local.d/ /etc/rspamd/local.d/

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# 11332 = milter (proxy worker), 11334 = controller/web UI.
EXPOSE 11332 11334

ENTRYPOINT ["/entrypoint.sh"]
