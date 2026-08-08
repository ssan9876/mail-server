# ============================================================================
# Backend image — FastAPI control plane.
# Multi-stage: `dev` target = hot-reload + dev deps; `prod` = gunicorn/uvicorn.
# ============================================================================

# ---- base ------------------------------------------------------------------
FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Runtime libs: libpq for psycopg, plus mail clients used to reload Postfix/Dovecot.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ---- builder (compiles wheels) ---------------------------------------------
FROM base AS builder
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-dev.txt ./
RUN pip wheel --wheel-dir /wheels -r requirements.txt -r requirements-dev.txt

# ---- dev -------------------------------------------------------------------
FROM base AS dev
COPY --from=builder /wheels /wheels
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-index --find-links=/wheels -r requirements.txt -r requirements-dev.txt \
    && rm -rf /wheels
COPY . .
RUN chmod +x /app/entrypoint.sh
EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ---- prod ------------------------------------------------------------------
FROM base AS prod
COPY --from=builder /wheels /wheels
COPY requirements.txt ./
RUN pip install --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

# Run as non-root.
RUN useradd --create-home --uid 10001 appuser

# Create /dkim owned by appuser BEFORE the volume is mounted there.
#
# Docker seeds a fresh named volume's ownership from whatever the image has at
# that path; with no directory here it creates one owned by root, mode 755, and
# the backend runs as uid 10001. dkim_export_service._write_key then fails with
# PermissionError on EVERY domain, so no key is ever exported — and rspamd
# signs on the presence of that file, so all outbound mail leaves UNSIGNED.
#
# It is silent on the normal path: try_sync() catches OSError and only logs a
# warning, so creating a domain reports success while the export fails. The
# explicit POST /domains/dkim/sync is the only thing that surfaces it, as a 500.
# You otherwise learn about it from recipients' DMARC reports days later.
#
# Confirmed on a real deployment: the export failed with
#   PermissionError: [Errno 13] Permission denied: '/dkim/other.example.com'
# and succeeded ({"exported": 2}) once the directory was owned by appuser.
#
# NOTE for existing deployments: this fixes NEW volumes only. Docker sets that
# ownership just once, when the volume is first created, so a stack that has
# already run needs a one-time chown — see docs/deployment.md.
RUN mkdir -p /dkim && chown 10001:10001 /dkim

COPY --chown=appuser:appuser . .
RUN chmod +x /app/entrypoint.sh
USER appuser

EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
# gunicorn manages uvicorn workers; tune workers via env at deploy time.
CMD ["gunicorn", "app.main:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--workers", "4", \
     "--bind", "0.0.0.0:8000", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
