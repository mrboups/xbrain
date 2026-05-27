from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    MEMORY_API_URL: str = "http://memory-api:8000"
    AGENT_RUNTIME_URL: str = "http://agent-runtime:9100"  # 02-07 ingestion + 02-08 second-opinion
    BRIDGE_SHARED_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    PIPELINE_API_KEY: str  # the API key Open WebUI sends to authenticate against this service
    PIPELINE_DEFAULT_TEAM_SCOPE: str = "default"
    ANTHROPIC_API_KEY: str = ""
    OPENAI_API_KEY: str = ""

    # Observability — Langfuse (Plan 02-09). Empty values = instrumentation off.
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_HOST: str = "http://langfuse:3000"

    LOG_LEVEL: str = "INFO"

    # Phase 13 — Chat → Brain Ingestion + Retrieval Enrichment (MEM-04 / CHAT-03 / CHAT-07)
    BRAIN_INGEST_ENABLED: bool = True
    CHAT07_TOP_K: int = 5
    CHAT07_TRUTH_FILTER_MIN_LEVEL: str = "VALIDATED"  # >= VALIDATED → includes VALIDATED+CANONICAL+PUBLIC


settings = Settings()  # type: ignore[call-arg]
