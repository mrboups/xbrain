"""Application settings — read from env via pydantic-settings."""

from pydantic import field_validator
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
    MEMORY_API_EXTERNAL_URL: str = "http://localhost:8000"

    # === Phase 5 — LibreChat OAuth App (still active — separate from xbrain auth) ===
    # GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET below identify the
    # `xbrain LibreChat` OAuth App (Client ID Ov23li0XHV3NL8Git7Dk),
    # used ONLY by apps/memory-api/app/routes/me_github.py for the
    # LibreChat account-linking flow. Out of Phase 12 scope per
    # 12-CONTEXT.md. Do NOT confuse with GITHUB_APP_CLIENT_ID below.
    GITHUB_CLIENT_ID: str = ""
    GITHUB_CLIENT_SECRET: str = ""
    GITHUB_ORG: str = ""
    # Org-membership checks use installation tokens minted by the xbrain
    # GitHub App (see app/services/github_installation.py + Plan 12-04).

    # === Phase 12 — GitHub App (NEW) ===
    # The GitHub App "xbrain" owned by mrboups account. Replaces the OAuth App
    # for xbrain auth (LibreChat OAuth App `xbrain LibreChat` is out of scope —
    # see 12-CONTEXT.md). Consumed by:
    #   - app/services/github_app_jwt.py (Plan 12-02): RS256 JWT signing
    #   - app/services/github_installation.py (Plan 12-03): installation token cache
    #   - app/routes/github_webhooks.py (Plan 12-05): X-Hub-Signature-256 verify
    #   - app/services/github_user_token.py (Plan 12-06): user-to-server refresh flow
    GITHUB_APP_ID: int = 0                          # numeric App ID, e.g. 3743573
    GITHUB_APP_SLUG: str = "xbrain-auth"            # URL slug for the install URL
                                                    # (https://github.com/apps/<slug>/installations/new)
    GITHUB_APP_CLIENT_ID: str = ""                  # 'Iv23li…' — issued by GitHub on App creation
    GITHUB_APP_CLIENT_SECRET: str = ""              # paired secret (server-side only)
    GITHUB_APP_PRIVATE_KEY_B64: str = ""            # base64-encoded PEM (single-line).
                                                    # Decode at use: base64.b64decode(...).decode().
                                                    # Generated once on App creation; rotation
                                                    # documented in Plan 12-11 verify script.
    GITHUB_APP_WEBHOOK_SECRET: str = ""             # HMAC-SHA256 secret for
                                                    # /v1/webhooks/github/installation
    # Stopgap read credential for the github-read tool when the GitHub App is
    # NOT installed on a repo owner. A user OAuth token (gho_, scope `repo`) used
    # as a server-wide fallback Bearer for the Contents API. Reads at THAT user's
    # access level (shared) — the proper multi-team model is the App installation
    # (contents:read). Empty = no fallback (App-only). See github_contents.py.
    GITHUB_FALLBACK_TOKEN: str = ""

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
    SMTP_FROM: str = "noreply@example.com"
    SMTP_TLS: bool = True
    # Public base URL of the deployment; used in outbound email links (D-05).
    APP_PUBLIC_URL: str = "http://localhost:8000"

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
    CENTRIFUGO_WS_URL_PUBLIC: str = "ws://localhost:8000/connection/websocket"
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
    # Internal URL to agent-runtime — POSTed on @claude mentions to enqueue
    # an agent task. Fire-and-forget; failures must NOT block the message
    # insert response.
    AGENT_RUNTIME_INTERNAL_URL: str = "http://agent-runtime:8200"

    # Phase 11 plan 11-10 — Brain Monitor superadmin dashboard storage metrics.
    # MinIO/S3 credentials for the /v1/admin/brain/storage endpoint to report
    # bytes per team. Defaults empty -> get_minio_client() returns None
    # (fail-soft -> dashboard column shows "N/A").
    MINIO_URL: str = ""
    MINIO_ACCESS_KEY: str = ""
    MINIO_SECRET_KEY: str = ""
    MINIO_BUCKET: str = "xbrain-media"
    # Qdrant collection name used by /v1/admin/brain/storage. Defaults to
    # "messages" (the name xbrain actually uses in qdrant_setup.py:11). The
    # docker-compose env QDRANT_COLLECTION can override per-environment.
    QDRANT_COLLECTION: str = "messages"

    # Quick task 260601-3is — URL pre-fetch via mcp-gateway scraper
    MCP_GATEWAY_URL: str = "http://mcp-gateway:8080"

    # Quick task 260604-glo — OAuth 2.1 Authorization Server (Custom Connector).
    # OAUTH_ISSUER_URL is the public base for AS metadata + /oauth/* endpoints.
    # OAUTH_RESOURCE_URL is the protected-resource (mcp-brain) audience the
    # issued tokens are bound to; normalized (no trailing slash) on every store.
    # Empty by default (D-03) — required, validated below; fails boot loudly
    # rather than minting tokens bound to a misleading hardcoded default.
    OAUTH_ISSUER_URL: str = ""
    OAUTH_RESOURCE_URL: str = ""

    # Browser origins allowed to call this API (FastAPI CORSMiddleware allow_origin_regex).
    # Set to your own front-end origins in .env. Wired in main.py (amended ROADMAP SC#4).
    CORS_ALLOWED_ORIGIN_REGEX: str = r"(chrome-extension://.*|http://localhost(:\d+)?)"

    @field_validator("OAUTH_ISSUER_URL", "OAUTH_RESOURCE_URL")
    @classmethod
    def _require_oauth_urls(cls, v: str, info) -> str:
        if not v:
            raise ValueError(
                f"{info.field_name} is required — set it in .env "
                f"(e.g. OAUTH_ISSUER_URL=https://api.yourdomain.com)"
            )
        return v

    # Phase 13 — Chat → Brain Ingestion + Retrieval Enrichment
    RELEVANCE_HAIKU_ENABLED: bool = True
    RELEVANCE_DAILY_TOKEN_CAP_PER_TEAM: int = 50_000  # input tokens per team per day
    RELEVANCE_HAIKU_MODEL: str = "claude-haiku-4-5-20251001"
    RELEVANCE_HAIKU_TIMEOUT_S: float = 3.0

    # GitHub repo-catalog auto-indexer (quick 260607-267)
    # Safe defaults — no .env change required.
    GITHUB_CATALOG_ENABLED: bool = True
    GITHUB_CATALOG_CONCURRENCY: int = 5   # max parallel upserts per backfill batch
    GITHUB_CATALOG_README_CHARS: int = 8000  # README input cap before Haiku call

    @property
    def admin_user_subs(self) -> set[str]:
        return {s.strip() for s in self.ADMIN_USER_SUBS.split(",") if s.strip()}


settings = Settings()  # type: ignore[call-arg]
