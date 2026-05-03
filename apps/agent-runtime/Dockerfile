# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install --target=/build/deps -e .

FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/usr/local/lib/python3.12/site-packages
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -u 10004 -m -s /bin/bash agent
WORKDIR /app
COPY --from=builder /build/deps /usr/local/lib/python3.12/site-packages
COPY app ./app
USER agent
EXPOSE 9100
HEALTHCHECK --interval=15s --timeout=5s --retries=5 --start-period=30s \
  CMD curl -fsS http://localhost:9100/healthz || exit 1
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9100", "--workers", "1"]
