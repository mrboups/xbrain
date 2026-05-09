FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .
COPY app/ ./app/
EXPOSE 8101
# Single worker — FastMCP session state is in-memory per process.
# Do NOT override with --workers N (issue #658).
CMD ["python", "-m", "app.main"]
