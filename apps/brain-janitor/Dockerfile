# Build context: REPO ROOT (not apps/brain-janitor) -- needed for packages/memory-models
# In docker-compose: context: ..   dockerfile: apps/brain-janitor/Dockerfile
FROM python:3.12-slim
WORKDIR /app
# Install shared package first (repo root context required)
COPY packages/memory-models/ ./packages/memory-models/
RUN pip install --no-cache-dir -e packages/memory-models/
# Install brain-janitor dependencies
COPY apps/brain-janitor/ ./apps/brain-janitor/
RUN pip install --no-cache-dir -e apps/brain-janitor/
WORKDIR /app/apps/brain-janitor
CMD ["python", "-m", "app.main"]
