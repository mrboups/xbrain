from pydantic import field_validator
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
