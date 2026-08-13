from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = ""
    MEMORY_API_URL: str = "http://memory-api:8000"
    # NO DEFAULT — mirrors memory-api's app/config.py:16. An empty shared secret
    # is never a working degraded mode: it signs bridge JWTs any third party can
    # forge, and any service verifying with "" accepts them. Missing var =>
    # refuse to boot, where an operator sees it. docker-compose passes it.
    BRIDGE_SHARED_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    GRANOLA_POLL_INTERVAL_SECONDS: int = 300  # 5 minutes
    GRANOLA_API_BASE: str = "https://api.granola.ai"
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-haiku-4-5-20251001"
    FERNET_KEY: str = ""
    LOG_LEVEL: str = "INFO"

    class Config:
        env_file = ".env"


settings = Settings()
