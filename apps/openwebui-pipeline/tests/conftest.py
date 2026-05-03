"""Shared fixtures for openwebui-pipeline tests."""

import os

# --- Defaults so app.config import doesn't blow up ---
os.environ.setdefault("BRIDGE_SHARED_SECRET", "test-bridge-secret-do-not-use-in-prod")
os.environ.setdefault("PIPELINE_API_KEY", "test-pipeline-api-key")
os.environ.setdefault("MEMORY_API_URL", "http://memory-api.test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("PIPELINE_DEFAULT_TEAM_SCOPE", "default")

import httpx
import pytest_asyncio


@pytest_asyncio.fixture
async def client():
    """ASGI httpx client bound to the FastAPI app."""
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def auth_headers():
    return {"Authorization": "Bearer test-pipeline-api-key"}
