from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    MEMORY_API_URL: str = "http://memory-api:8000"
    FASTMCP_HOST: str = "0.0.0.0"
    FASTMCP_PORT: int = 8104
    LOG_LEVEL: str = "INFO"


settings = Settings()
