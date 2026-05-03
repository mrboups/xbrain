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


settings = Settings()  # type: ignore[call-arg]
