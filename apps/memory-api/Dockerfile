# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install --target=/build/deps -e .

FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/usr/local/lib/python3.12/site-packages
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -u 10001 -m -s /bin/bash xbrain
WORKDIR /app
COPY --from=builder /build/deps /usr/local/lib/python3.12/site-packages
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
USER xbrain
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --retries=5 --start-period=20s \
  CMD curl -fsS http://localhost:8000/v1/healthz || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
