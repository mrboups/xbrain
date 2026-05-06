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


# === GitHub OAuth membership verification (Phase 5 — plan 05-02) ===

_github_membership_cache: dict[str, tuple[float, dict]] = {}
_GITHUB_CACHE_TTL = 300  # 5 minutes — reduces GitHub API calls, well within 5000 req/h rate limit


async def check_github_org_membership(
    github_token: str,
    org: str,
    server_pat: str,
) -> dict:
    """Verify if a GitHub OAuth token belongs to a member of the given org.

    Uses two-step approach:
    1. GET /user with the user's own OAuth token to get their username.
    2. GET /orgs/{org}/members/{username} with the server PAT to check membership
       (server PAT is required to see private org members — pitfall documented in
       RESEARCH.md Q3, Pitfall 4).

    Returns: {"login": str, "email": str|None, "is_org_member": bool}
    Caches result for _GITHUB_CACHE_TTL seconds per token[:16]+org key.
    """
    # Truncate token prefix for cache key — avoids excessively long keys (T-05-02-03)
    cache_key = f"{github_token[:16]}:{org}"
    now = time.time()
    if cache_key in _github_membership_cache:
        ts, result = _github_membership_cache[cache_key]
        if now - ts < _GITHUB_CACHE_TTL:
            return result

    async with httpx.AsyncClient(timeout=10.0) as client:
        # Step 1: resolve the GitHub username from the OAuth token
        user_r = await client.get(
            "https://api.github.com/user",
            headers={
                "Authorization": f"Bearer {github_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        user_r.raise_for_status()
        user_data = user_r.json()
        username = user_data["login"]

        # Step 2: check org membership using the server PAT
        # Using PAT (not user token) so private members are visible (204 = member, else not)
        org_r = await client.get(
            f"https://api.github.com/orgs/{org}/members/{username}",
            headers={
                "Authorization": f"Bearer {server_pat}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        is_member = org_r.status_code == 204

    result = {
        "login": username,
        "email": user_data.get("email"),
        "name": user_data.get("name"),
        "is_org_member": is_member,
    }
    _github_membership_cache[cache_key] = (now, result)
    return result
