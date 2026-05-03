"""Settings — read from env via pydantic-settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Postgres DSN — shared with memory-api, separate logical schema (tables prefixed by langgraph)
    DATABASE_URL: str

    # memory-api HTTP base URL — agents NEVER touch DB/Qdrant directly
    MEMORY_API_URL: str = "http://memory-api:8000"

    # Auth
    BRIDGE_SHARED_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    GOOGLE_CLIENT_ID: str = ""

    # LLM keys (optional — agents pick up only what they need)
    ANTHROPIC_API_KEY: str = ""
    OPENAI_API_KEY: str = ""

    # Logging
    LOG_LEVEL: str = "INFO"


settings = Settings()  # type: ignore[call-arg]
