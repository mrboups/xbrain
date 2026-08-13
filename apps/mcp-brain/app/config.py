from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    MEMORY_API_URL: str = "http://memory-api:8000"
    FASTMCP_HOST: str = "0.0.0.0"
    FASTMCP_PORT: int = 8104
    LOG_LEVEL: str = "INFO"
    # Tokenless email path — gated by shared secret (internal LibreChat calls only).
    # Same value as BRIDGE_SHARED_SECRET everywhere else, so LibreChat can use the
    # email path without an xbt_ token.
    #
    # NO DEFAULT — mirrors memory-api's app/config.py:16. The old `= ""` made an
    # unconfigured deploy look healthy while `_resolve`'s `bool(secret)` guard
    # silently refused every internal call; and the same value is handed to
    # oauth_verify as X-Internal-Secret. A secret that is allowed to be absent is
    # a secret nobody notices is absent — refuse to boot instead. docker-compose
    # passes it. INTERNAL_EMAIL_PATH_ENABLED below is the kill-switch for turning
    # the path off deliberately.
    BRIDGE_SHARED_SECRET: str
    # Kill-switch: set to false to disable the email path entirely without clearing the secret.
    INTERNAL_EMAIL_PATH_ENABLED: bool = True

    # Quick task 260604-glo — Claude.ai Custom Connector (Protected Resource).
    # oat_ access tokens are validated by introspecting against memory-api at
    # MEMORY_API_OAUTH_INTROSPECT_URL (X-Internal-Secret = BRIDGE_SHARED_SECRET).
    # OAUTH_ISSUER_URL + OAUTH_RESOURCE_URL back the protected-resource metadata
    # and the audience (resource) check on introspected tokens.
    # Empty by default (D-03) — required, validated below. main.py computes
    # _PROTECTED_RESOURCE_METADATA_URL from OAUTH_RESOURCE_URL at MODULE-IMPORT
    # time; this validator must crash Settings() construction first so that
    # malformed constant is never built (14-RESEARCH.md "Fail-Fast Risk").
    OAUTH_ISSUER_URL: str = ""
    OAUTH_RESOURCE_URL: str = ""
    MEMORY_API_OAUTH_INTROSPECT_URL: str = "http://memory-api:8000/oauth/introspect"

    @field_validator("OAUTH_ISSUER_URL", "OAUTH_RESOURCE_URL")
    @classmethod
    def _require_oauth_urls(cls, v: str, info) -> str:
        if not v:
            raise ValueError(
                f"{info.field_name} is required — set it in .env "
                f"(e.g. OAUTH_ISSUER_URL=https://api.yourdomain.com)"
            )
        return v


settings = Settings()
