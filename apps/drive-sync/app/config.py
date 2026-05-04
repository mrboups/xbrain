from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = ""
    MEMORY_API_URL: str = "http://memory-api:8000"
    AGENT_RUNTIME_URL: str = "http://agent-runtime:9100"
    BRIDGE_SHARED_SECRET: str = ""
    JWT_ALGORITHM: str = "HS256"
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    OAUTH_CREDENTIALS_ENCRYPTION_KEY: str = ""
    POLL_INTERVAL_SECONDS: int = 300  # 5 minutes
    LOG_LEVEL: str = "INFO"

    class Config:
        env_file = ".env"


settings = Settings()
