"""JWT verification — Google OIDC ID tokens AND internal bridge service JWTs."""

import time

import httpx
from authlib.jose import JsonWebKey, jwt

from app.config import settings

GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")

_jwks_cache: dict = {"keys": None, "ts": 0.0}


async def _fetch_google_jwks() -> JsonWebKey:
    """Fetch + cache Google's public JWKs (1 hour TTL)."""
    now = time.time()
    if _jwks_cache["keys"] is None or now - _jwks_cache["ts"] > 3600:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(GOOGLE_JWKS_URL)
            r.raise_for_status()
            _jwks_cache["keys"] = JsonWebKey.import_key_set(r.json())
            _jwks_cache["ts"] = now
    return _jwks_cache["keys"]


async def verify_google_id_token(token: str, client_id: str) -> dict:
    """Verify a Google OIDC ID token. Returns the claims dict (sub, email, name, ...)."""
    if not client_id:
        raise ValueError("GOOGLE_CLIENT_ID not configured — cannot verify Google tokens")
    keys = await _fetch_google_jwks()
    claims = jwt.decode(
        token,
        keys,
        claims_options={
            "iss": {"essential": True, "values": list(GOOGLE_ISSUERS)},
            "aud": {"essential": True, "value": client_id},
        },
    )
    claims.validate()
    return dict(claims)


def verify_bridge_jwt(token: str, secret: str) -> dict:
    """Verify a service JWT signed with the shared bridge secret (HS256)."""
    claims = jwt.decode(token, secret)
    claims.validate()
    out = dict(claims)
    if out.get("scope") != "bridge":
        raise ValueError("token scope is not 'bridge'")
    return out


def is_admin(sub: str) -> bool:
    """Phase 1 simplification: admin SUBs come from env. Phase 2 will use DB roles."""
    return sub in settings.admin_user_subs
