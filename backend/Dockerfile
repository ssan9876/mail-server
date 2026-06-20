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
