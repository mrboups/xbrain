"""GET /.well-known/oauth-authorization-server — OAuth 2.1 AS metadata (RFC 8414).

Mounted WITHOUT the /v1 prefix so the public path is exactly
``/.well-known/oauth-authorization-server`` (what Claude.ai discovers). The
metadata advertises S256 PKCE (mandatory), public-client auth method ``none``
(mandatory for DCR-registered Claude.ai clients), and the five /oauth/
endpoints. CORS for browser fetches is owned by the app-level CORSMiddleware
(claude.ai is in the allow_origin_regex) — nginx must NOT add ACAO here.
"""
from __future__ import annotations

from fastapi import APIRouter

from app.config import settings

router = APIRouter()


@router.get("/.well-known/oauth-authorization-server")
async def authorization_server_metadata() -> dict:
    issuer = settings.OAUTH_ISSUER_URL.rstrip("/")
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/oauth/authorize",
        "token_endpoint": f"{issuer}/oauth/token",
        "registration_endpoint": f"{issuer}/oauth/register",
        "introspection_endpoint": f"{issuer}/oauth/introspect",
        "revocation_endpoint": f"{issuer}/oauth/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        # "none" is required: Claude.ai registers as a public client (no secret).
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": ["brain:read", "brain:write"],
    }
