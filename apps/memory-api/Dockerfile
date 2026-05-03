# syntax=docker/dockerfile:1.7
# Build context = repo root (xbrain/) so we can COPY packages/memory-models
# (referenced by memory-api pyproject as `xbrain-memory @ file:///app/packages/memory-models`).
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
# Stage the in-repo dep at the path the pyproject expects (file:///app/packages/memory-models)
COPY packages/memory-models /app/packages/memory-models
WORKDIR /build
COPY apps/memory-api/pyproject.toml ./
RUN pip install --upgrade pip && pip install --target=/build/deps -e .

FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/usr/local/lib/python3.12/site-packages
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -u 10001 -m -s /bin/bash xbrain
WORKDIR /app
# Source of the editable install must remain present in the runtime image
# (egg-link in deps/ points to /app/packages/memory-models)
COPY packages/memory-models /app/packages/memory-models
COPY --from=builder /build/deps /usr/local/lib/python3.12/site-packages
COPY apps/memory-api/app ./app
COPY apps/memory-api/alembic ./alembic
COPY apps/memory-api/alembic.ini ./
USER xbrain
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --retries=5 --start-period=20s \
  CMD curl -fsS http://localhost:8000/v1/healthz || exit 1
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
