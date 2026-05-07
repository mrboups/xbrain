from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = ""
    MEMORY_API_URL: str = "http://memory-api:8000"
    BRIDGE_SHARED_SECRET: str = ""
    JWT_ALGORITHM: str = "HS256"
    GRANOLA_POLL_INTERVAL_SECONDS: int = 300  # 5 minutes
    GRANOLA_API_BASE: str = "https://api.granola.ai"
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-3-5-haiku-20241022"
    FERNET_KEY: str = ""
    LOG_LEVEL: str = "INFO"

    class Config:
        env_file = ".env"


settings = Settings()
