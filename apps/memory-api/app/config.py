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

    # Phase 2: memory backend selection
    MEMORY_BACKEND: str = "stub"  # "mem0" | "native" | "stub"
    OPENAI_API_KEY: str = ""
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"

    # Neo4j (optional — graceful degrade if not set)
    NEO4J_URI: str = ""          # e.g. bolt://neo4j:7687
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = ""

    # Drive OAuth config (Phase 3 — plan 03-10)
    GOOGLE_CLIENT_SECRET: str = ""
    # Fernet key — generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    OAUTH_CREDENTIALS_ENCRYPTION_KEY: str = ""
    # Used to build OAuth redirect_uri returned to Google
    MEMORY_API_EXTERNAL_URL: str = "https://chat.grooveos.app"

    # Phase 5 — GitHub OAuth (plan 05-02)
    GITHUB_CLIENT_ID: str = ""
    GITHUB_CLIENT_SECRET: str = ""
    GITHUB_ORG: str = "your-github-org"
    # Fine-grained PAT with scope read:org — required to see private org members (pitfall Q3)
    GITHUB_API_PAT: str = ""

    # Phase 7 — CRM + Granola + Tasks
    ANTHROPIC_API_KEY: str = ""
    # FERNET_KEY: réutilise la même clé que OAUTH_CREDENTIALS_ENCRYPTION_KEY
    # mais explicitement nommée pour l'usage Granola. Si FERNET_KEY vide,
    # fall-back sur OAUTH_CREDENTIALS_ENCRYPTION_KEY.
    FERNET_KEY: str = ""
    # SMTP for task notifications (fail-soft when SMTP_HOST not set)
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = "noreply@grooveos.app"
    SMTP_TLS: bool = True

    # Phase 8 — GitHub repos proxy (plan 08-05)
    # Internal Docker network URL to LibreChat. Used by /v1/github/repos to proxy
    # the caller's Bearer token to /api/xbrain/github-repos on LibreChat.
    # Set to http://librechat:3080 in .env on the VM. Without it, /v1/github/repos returns 503.
    LIBRECHAT_INTERNAL_URL: str = ""

    @property
    def admin_user_subs(self) -> set[str]:
        return {s.strip() for s in self.ADMIN_USER_SUBS.split(",") if s.strip()}


settings = Settings()  # type: ignore[call-arg]
