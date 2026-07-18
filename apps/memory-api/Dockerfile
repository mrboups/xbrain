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
# Phase 19 (EMBED-01, D-19-01): bake BAAI/bge-small-en-v1.5 into a KNOWN cache dir while the
# network is available at build time. fastembed was installed into /build/deps by the editable
# install above, so point PYTHONPATH at it for the import. The runtime stage COPYs this cache and
# sets HF_HUB_OFFLINE=1 so a zero-network OSS install never fetches from HuggingFace at run time.
RUN PYTHONPATH=/build/deps python -c "from fastembed import TextEmbedding; \
    TextEmbedding(model_name='BAAI/bge-small-en-v1.5', cache_dir='/build/model_cache')"

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
# Phase 19 (EMBED-01, D-19-01): the baked model cache. --chown is MANDATORY — the runtime user
# is non-root (uid 10001); a root-owned cache would be unreadable and silently trigger a network
# re-download, defeating the zero-network guarantee. HF_HUB_OFFLINE=1 forces fastembed /
# huggingface-hub into cache-only mode. Path MUST equal settings.EMBEDDING_CACHE_DIR (/app/model_cache).
COPY --from=builder --chown=xbrain:xbrain /build/model_cache /app/model_cache
ENV HF_HUB_OFFLINE=1
USER xbrain
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --retries=5 --start-period=20s \
  CMD curl -fsS http://localhost:8000/v1/healthz || exit 1
# Phase 19 (EMBED-01, D-19-04): worker count is env-configurable so the in-process local model
# isn't duplicated in RAM. Default 2 (SaaS concurrency — nothing regresses). OSS-light overrides
# UVICORN_WORKERS=1 in .env so the ~256-366 MB model loads ONCE (fits the e2-medium budget). Shell
# form + `exec` so the substituted uvicorn becomes PID 1 and receives SIGTERM/SIGINT directly.
ENV UVICORN_WORKERS=2
CMD exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS}
