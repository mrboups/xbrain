FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .
COPY app/ ./app/
EXPOSE 8102
# Single worker — FastMCP session state must not be split across processes
CMD ["python", "-m", "app.main"]
