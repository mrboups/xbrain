# Build context: REPO ROOT (not apps/drive-sync) -- needed for packages/memory-models
# In docker-compose: context: ..   dockerfile: apps/drive-sync/Dockerfile
FROM python:3.11-slim
WORKDIR /app
# Install shared package first (repo root context required)
COPY packages/memory-models/ ./packages/memory-models/
RUN pip install --no-cache-dir -e packages/memory-models/
# Install drive-sync dependencies
COPY apps/drive-sync/pyproject.toml ./apps/drive-sync/pyproject.toml
RUN pip install --no-cache-dir -e apps/drive-sync/
# Copy application code
COPY apps/drive-sync/app/ ./apps/drive-sync/app/
WORKDIR /app/apps/drive-sync
CMD ["python", "-m", "app.main"]
