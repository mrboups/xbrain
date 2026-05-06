FROM python:3.12-slim
WORKDIR /app

# Install system deps for graphiti-core (lxml needs libxml2/libxslt1)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2-dev \
    libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first (layer-cached separately from app code)
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

# Copy application code
COPY app/ ./app/

EXPOSE 8300

# Single worker — graphiti-core maintains persistent Neo4j connections in the event loop.
# Multi-worker mode would create separate Graphiti instances per worker (wasteful + race on indices).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8300", "--workers", "1"]
