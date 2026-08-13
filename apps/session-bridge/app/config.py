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
    # POST /v1/me/external-sessions, and to VERIFY the bridge JWTs backend
    # services present on the chat and status routes (app/auth.py).
    #
    # The `= ""` default is KEPT here, alone among the six sidecars, and it is
    # not an oversight: app/auth.py:75 refuses every bridge JWT outright when the
    # secret is empty (`return None` before any decode), so an unconfigured
    # session-bridge verifies nothing rather than verifying against "". Making
    # the field required would additionally break tests/ at import time — its
    # conftest constructs Settings() with no env and monkeypatches afterwards.
    # Change both together or neither.
    BRIDGE_SHARED_SECRET: str = ""

    # Algorithm for the bridge JWT. Must match memory-api's accepted alg list.
    JWT_ALGORITHM: str = "HS256"

    # xbt_ token validation cache TTL (seconds). Mirrors mcp-brain.
    TOKEN_TTL_S: float = 60.0

    LOG_LEVEL: str = "info"


settings = Settings()
