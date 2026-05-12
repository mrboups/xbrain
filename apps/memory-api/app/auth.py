"""JWT verification — Google OIDC ID tokens AND internal bridge service JWTs."""

import time

import httpx
from authlib.jose import JsonWebKey, jwt

from app.config import settings

GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")

_jwks_cache: dict = {"keys": None, "ts": 0.0}

# Cache resolved Google access tokens for 5 minutes so a typical request burst
# (popup open → mint → /v1/me → mint again on race) doesn't hit Google's
# userinfo endpoint repeatedly. Key = access token; value = (expires_at, claims).
_google_userinfo_cache: dict[str, tuple[float, dict]] = {}
_GOOGLE_USERINFO_TTL = 300.0


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


async def verify_google_access_token(token: str) -> dict:
    """Resolve a Google OAuth2 access token to a userinfo claims dict.

    Used by the Chrome extension's chrome.identity.getAuthToken flow (silent
    auth when the user is already signed into Chrome). The access token is
    opaque (not a JWT), so we must call Google's userinfo endpoint to retrieve
    the user identity.

    Returns a dict shaped like an OIDC ID token claims payload — at minimum
    {sub, email, email_verified, name} — so callers in deps.py can treat it
    interchangeably with a verified ID token.

    Raises ValueError on any failure (404, 401, network error, unverified email,
    missing sub). Never returns a partial result.
    """
    if not token:
        raise ValueError("empty access token")

    now = time.time()
    cached = _google_userinfo_cache.get(token)
    if cached and cached[0] > now:
        return cached[1]

    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {token}"},
        )
    if r.status_code != 200:
        raise ValueError(f"google userinfo failed: {r.status_code}")
    data = r.json()
    sub = data.get("sub")
    if not sub:
        raise ValueError("google userinfo missing sub")
    # The userinfo endpoint only returns verified email accounts, but be defensive:
    # if the field is explicitly false, reject so the principal lookup doesn't
    # mint a row for a spoofable email.
    if data.get("email") and data.get("email_verified") is False:
        raise ValueError("google userinfo email is not verified")
    claims = {
        "sub": sub,
        "email": data.get("email"),
        "email_verified": data.get("email_verified", True),
        "name": data.get("name") or data.get("given_name"),
        "given_name": data.get("given_name"),
        "picture": data.get("picture"),
        "iss": "https://accounts.google.com",
    }
    _google_userinfo_cache[token] = (now + _GOOGLE_USERINFO_TTL, claims)
    return claims


def _reset_google_userinfo_cache_for_tests() -> None:
    """Test helper — clear the module-level userinfo cache between cases."""
    _google_userinfo_cache.clear()


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
