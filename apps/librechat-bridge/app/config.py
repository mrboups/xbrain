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
    ANTHROPIC_TASK_INTENT_MODEL: str = "claude-3-5-haiku-20241022"


settings = Settings()  # type: ignore[call-arg]
