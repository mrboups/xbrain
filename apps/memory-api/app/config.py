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

    # Quick task 260512-tcr — team chat realtime
    # HMAC secret for client JWT tokens issued by memory-api so the Chrome
    # extension / PWA can connect to Centrifugo. MUST match Centrifugo's
    # client.token.hmac_secret_key (set via CENTRIFUGO_CLIENT_TOKEN_HMAC_SECRET_KEY).
    CENTRIFUGO_TOKEN_HMAC_SECRET: str = ""
    # API key for server-side publish/presence/history calls from memory-api +
    # agent-runtime. MUST match Centrifugo's http_api.key.
    CENTRIFUGO_API_KEY: str = ""
    # Internal Docker network URL (memory-api → centrifugo container).
    CENTRIFUGO_HTTP_URL_INTERNAL: str = "http://centrifugo:8000"
    # Public WSS URL the Chrome extension + PWA connect to.
    CENTRIFUGO_WS_URL_PUBLIC: str = "wss://centrifugo.grooveos.app/connection/websocket"
    # TTL for the team memory context bundle cached in-process. 5 minutes
    # matches the Anthropic prompt cache window (cache_control: ephemeral
    # holds ~5min before eviction in practice).
    TEAM_CONTEXT_CACHE_TTL_S: int = 300
    # How many memory items to include in each context bundle. Phase 2 swaps
    # this for Qdrant top-K retrieval; for v1 we send the latest 100
    # truth_level>=WORKING items in reverse chronological order.
    TEAM_CONTEXT_MAX_ITEMS: int = 100
    # TTL on the client connection token issued by /v1/me/centrifugo-token.
    # 1h matches our other JWTs.
    CENTRIFUGO_CLIENT_TOKEN_TTL_S: int = 3600

    @property
    def admin_user_subs(self) -> set[str]:
        return {s.strip() for s in self.ADMIN_USER_SUBS.split(",") if s.strip()}


settings = Settings()  # type: ignore[call-arg]
