from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    LIBRECHAT_MONGO_URI: str
    MEMORY_API_URL: str = "http://memory-api:8000"
    BRIDGE_SHARED_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    BRIDGE_DEFAULT_TEAM_SCOPE: str = "default"
    BRIDGE_BACKFILL_FROM: str = "startup"  # "startup" | "never"
    LOG_LEVEL: str = "INFO"
    BRIDGE_HEARTBEAT_PATH: str = "/tmp/bridge-alive"

    # Phase 7 plan 07-09 — Task intent detection (D5 trigger 3)
    TASK_INTENT_DETECTION: bool = False  # opt-in — set TASK_INTENT_DETECTION=true to enable
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_TASK_INTENT_MODEL: str = "claude-haiku-4-5-20251001"

    # Phase 8 plan 08-06 — Contact extraction from LibreChat messages (D3 RESEARCH.md)
    CONTACT_EXTRACTION: bool = False  # opt-in — set CONTACT_EXTRACTION=true to enable
    ANTHROPIC_CONTACT_MODEL: str = "claude-haiku-4-5-20251001"

    # Phase 13 plan 13-04 — Chat -> Brain ingestion (MEM-04 / CHAT-03)
    BRAIN_INGEST_ENABLED: bool = True  # kill-switch for the LibreChat brain ingest hook

    # Phase 13 plan 13-05 — Chat enrichment (CHAT-07)
    CHAT07_TOP_K: int = 5
    CHAT07_TRUTH_FILTER_MIN_LEVEL: str = "VALIDATED"  # VALIDATED + CANONICAL + PUBLIC per >= semantics


settings = Settings()  # type: ignore[call-arg]
