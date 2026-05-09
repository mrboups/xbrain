# Build context: REPO ROOT (not apps/granola-sync) -- needed for packages/memory-models
# In docker-compose: context: ..   dockerfile: apps/granola-sync/Dockerfile
FROM python:3.12-slim
WORKDIR /app
# Install shared package first (repo root context required)
COPY packages/memory-models/ ./packages/memory-models/
RUN pip install --no-cache-dir -e packages/memory-models/
# Install granola-sync dependencies
COPY apps/granola-sync/ ./apps/granola-sync/
RUN pip install --no-cache-dir -e apps/granola-sync/
WORKDIR /app/apps/granola-sync
CMD ["python", "-m", "app.main"]
