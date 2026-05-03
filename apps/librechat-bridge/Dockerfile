# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install --target=/build/deps -e .

FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/usr/local/lib/python3.12/site-packages
RUN useradd -u 10002 -m -s /bin/bash bridge
WORKDIR /app
COPY --from=builder /build/deps /usr/local/lib/python3.12/site-packages
COPY app ./app
USER bridge
HEALTHCHECK --interval=15s --timeout=5s --retries=4 --start-period=30s \
  CMD test -f /tmp/bridge-alive && [ $(($(date +%s) - $(stat -c %Y /tmp/bridge-alive))) -lt 60 ] || exit 1
CMD ["python", "-m", "app.main"]
