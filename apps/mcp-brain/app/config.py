from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    MEMORY_API_URL: str = "http://memory-api:8000"
    FASTMCP_HOST: str = "0.0.0.0"
    FASTMCP_PORT: int = 8104
    LOG_LEVEL: str = "INFO"
    # Tokenless email path — gated by shared secret (internal LibreChat calls only).
    # Empty string = disabled (fail-closed). Set to same value as BRIDGE_SHARED_SECRET
    # in docker-compose so LibreChat can use email path without an xbt_ token.
    BRIDGE_SHARED_SECRET: str = ""
    # Kill-switch: set to false to disable the email path entirely without clearing the secret.
    INTERNAL_EMAIL_PATH_ENABLED: bool = True


settings = Settings()
