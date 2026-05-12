"""Settings for the session-bridge service.

Loaded once at import time. Fields can be overridden via environment variables
(case-insensitive) or a local `.env` file.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    # memory-api base URL (used for xbt_ token validation + external-sessions upsert).
    MEMORY_API_URL: str = "http://memory-api:8000"

    # HMAC secret used to sign the bridge JWT carried in calls to memory-api
    # POST /v1/me/external-sessions. Empty in dev/test; required in prod.
    BRIDGE_SHARED_SECRET: str = ""

    # Algorithm for the bridge JWT. Must match memory-api's accepted alg list.
    JWT_ALGORITHM: str = "HS256"

    # xbt_ token validation cache TTL (seconds). Mirrors mcp-brain.
    TOKEN_TTL_S: float = 60.0

    LOG_LEVEL: str = "info"


settings = Settings()
