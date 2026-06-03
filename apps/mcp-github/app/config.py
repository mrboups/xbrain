from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    MEMORY_API_URL: str = "http://memory-api:8000"
    BRIDGE_SHARED_SECRET: str = ""
    FASTMCP_HOST: str = "0.0.0.0"
    FASTMCP_PORT: int = 8107
    LOG_LEVEL: str = "INFO"
    # EMAIL PATH (LibreChat-internal): resolve the caller's team from
    # X-LibreChat-User-Email + X-Internal-Secret (== BRIDGE_SHARED_SECRET).
    INTERNAL_EMAIL_PATH_ENABLED: bool = True


settings = Settings()
