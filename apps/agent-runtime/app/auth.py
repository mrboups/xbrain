"""JWT verification — Google OIDC ID tokens AND internal bridge service JWTs.

Mirror of memory-api/app/auth.py — kept duplicated rather than extracted to a shared
package to avoid coupling the two services' release cycles. If a third service ever
needs this, extract to packages/xbrain-auth then.
"""

import time

import httpx
from authlib.jose import JsonWebKey, jwt

from app.config import settings

GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")

_jwks_cache: dict = {"keys": None, "ts": 0.0}


async def _fetch_google_jwks() -> JsonWebKey:
    now = time.time()
    if _jwks_cache["keys"] is None or now - _jwks_cache["ts"] > 3600:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(GOOGLE_JWKS_URL)
            r.raise_for_status()
            _jwks_cache["keys"] = JsonWebKey.import_key_set(r.json())
            _jwks_cache["ts"] = now
    return _jwks_cache["keys"]


async def verify_google_id_token(token: str, client_id: str) -> dict:
    if not client_id:
        raise ValueError("GOOGLE_CLIENT_ID not configured")
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


def make_bridge_jwt(sub: str, team_scope: str, ttl_seconds: int = 300) -> str:
    """Sign a HS256 JWT the gateway and memory-api will verify with the shared secret.

    Claims:
      iss = agent-runtime
      sub = caller subject (e.g. "agent-runtime", a user OIDC sub, ...)
      team_scope = team scope to embed in token (used by gateway for routing)
      scope = "bridge" (required by gateway + memory-api auth check)
    """
    now = int(time.time())
    header = {"alg": settings.JWT_ALGORITHM}
    payload = {
        "iss": "agent-runtime",
        "sub": sub,
        "team_scope": team_scope,
        "scope": "bridge",
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return jwt.encode(header, payload, settings.BRIDGE_SHARED_SECRET).decode("ascii")


def verify_bridge_jwt(token: str, secret: str) -> dict:
    claims = jwt.decode(token, secret)
    claims.validate()
    out = dict(claims)
    if out.get("scope") != "bridge":
        raise ValueError("token scope is not 'bridge'")
    return out
