"""Application settings — read from env via pydantic-settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str
    QDRANT_URL: str = "http://qdrant:6333"
    QDRANT_API_KEY: str = ""
    GOOGLE_CLIENT_ID: str = ""
    BRIDGE_SHARED_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    LOG_LEVEL: str = "INFO"

    # Comma-separated list of OIDC subs that have admin powers (Phase 1 simplification —
    # Phase 2 will move to a proper role/permission table).
    ADMIN_USER_SUBS: str = ""

    @property
    def admin_user_subs(self) -> set[str]:
        return {s.strip() for s in self.ADMIN_USER_SUBS.split(",") if s.strip()}


settings = Settings()  # type: ignore[call-arg]
