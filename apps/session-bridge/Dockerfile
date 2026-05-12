FROM python:3.12-slim
WORKDIR /app

# Install curl for healthcheck (docker-compose calls /healthz)
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY app/ ./app/

EXPOSE 8105

# Single worker — USER_SOCKETS pool is in-memory and must not split across processes
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8105", "--log-level", "info"]
