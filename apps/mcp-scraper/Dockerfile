FROM python:3.11-slim
WORKDIR /app

# Install dependencies first (layer-cached separately from app code)
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

# Copy application code
COPY app/ ./app/

EXPOSE 8100

# Single worker — mandatory for FastMCP streamable-http session state consistency.
# Multi-worker mode causes in-memory session loss (issue #658).
# mcp.run() binds /mcp endpoint on port 8100 with streamable-http transport.
CMD ["python", "app/main.py"]
