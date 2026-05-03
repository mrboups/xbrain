"""JWT verification — Google OIDC ID tokens AND internal bridge service JWTs.

Mirror of memory-api/app/auth.py — kept duplicated rather than extracted to a shared
package to avoid coupling the two services' release cycles. If a third service ever
needs this, extract to packages/xbrain-auth then.
"""

import time

import httpx
from authlib.jose import JsonWebKey, jwt

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


def verify_bridge_jwt(token: str, secret: str) -> dict:
    claims = jwt.decode(token, secret)
    claims.validate()
    out = dict(claims)
    if out.get("scope") != "bridge":
        raise ValueError("token scope is not 'bridge'")
    return out
