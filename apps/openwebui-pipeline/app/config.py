from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    MEMORY_API_URL: str = "http://memory-api:8000"
    BRIDGE_SHARED_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    PIPELINE_API_KEY: str  # the API key Open WebUI sends to authenticate against this service
    PIPELINE_DEFAULT_TEAM_SCOPE: str = "default"
    ANTHROPIC_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    LOG_LEVEL: str = "INFO"


settings = Settings()  # type: ignore[call-arg]
