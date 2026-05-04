FROM python:3.12-slim
WORKDIR /app

# Install system deps for python-pptx (lxml needs libxml2)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2-dev \
    libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first (layer-cached separately from app code)
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

# Copy application code
COPY app/ ./app/

EXPOSE 8103

# Single worker — mandatory for FastMCP streamable-http session state consistency.
# Multi-worker mode causes in-memory session loss (issue #658).
# mcp.run() binds /mcp endpoint on port 8103 with streamable-http transport.
CMD ["python", "app/main.py"]
