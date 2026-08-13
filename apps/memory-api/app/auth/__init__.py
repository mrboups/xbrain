"""JWT verification — Google OIDC ID tokens AND internal bridge service JWTs."""

import time
from enum import StrEnum

import httpx
import structlog
from authlib.jose import JsonWebKey, JsonWebToken, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.github_installation import get_installation_token_for_org

log = structlog.get_logger(__name__)

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


#: Bridge tokens are HS256 and nothing else. `jwt.decode` without this lets the
#: TOKEN'S OWN HEADER choose the algorithm — the classic confusion primitive,
#: where an attacker names an algorithm the verifier treats differently from
#: what the signer intended. Hardcoded rather than read from settings, because
#: an allow-list an env var can widen is not an allow-list. Mirrors the same
#: fix in mcp-gateway/app/auth.py.
_BRIDGE_JWT = JsonWebToken(["HS256"])


def verify_bridge_jwt(token: str, secret: str) -> dict:
    """Verify a service JWT signed with the shared bridge secret (HS256)."""
    # Belt and braces behind config.py's required BRIDGE_SHARED_SECRET: this
    # function also takes a caller-supplied secret, and verifying against ""
    # accepts anything an attacker signs with "".
    if not secret:
        raise ValueError("bridge secret is empty — refusing to verify")
    claims = _BRIDGE_JWT.decode(token, secret)
    claims.validate()
    out = dict(claims)
    if out.get("scope") != "bridge":
        raise ValueError("token scope is not 'bridge'")
    return out


def is_admin(sub: str) -> bool:
    """Phase 1 simplification: admin SUBs come from env. Phase 2 will use DB roles."""
    return sub in settings.admin_user_subs


# === GitHub org-membership verification (Phase 12 — Plan 12-04) ===
#
# Phase 5 (Plan 05-02) used a long-lived server PAT to check org membership.
# Phase 12 (Plan 12-04) removed the PAT — every credential in this path is now
# short-lived:
#   - user token (gho_/ghu_, ~8h, refreshes)
#   - installation token (ghs_, 1h, cached 55min, auto-refresh)
#
# The function returns one of three states (INSTALL_REQUIRED is new — surfaces
# the install URL UX when the App is not installed on the user's org).

_github_membership_cache: dict[str, tuple[float, dict]] = {}
_GITHUB_CACHE_TTL = 300       # 5 min for MEMBER / NOT_MEMBER
_GITHUB_INSTALL_REQUIRED_TTL = 60  # 60s for INSTALL_REQUIRED so retries after
                                    # install reach reality quickly

_GH_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


class OrgMembershipResult(StrEnum):
    """Three possible outcomes of a /orgs/{org}/members/{user} check.

    - MEMBER:           GitHub returned 204 → user is in the org.
    - NOT_MEMBER:       GitHub returned 404 → user is not in the org.
    - INSTALL_REQUIRED: xbrain App is not installed on the org. The caller
                        should surface the install URL (Plan 12-06 UX).
    """

    MEMBER = "member"
    NOT_MEMBER = "not_member"
    INSTALL_REQUIRED = "install_required"


async def check_github_org_membership(
    session: AsyncSession,
    github_user_token: str,
    org: str,
) -> dict:
    """Check if a GitHub user is a member of `org` using an installation token.

    Steps:
      1. Resolve the user's GitHub login + numeric id from the user token (GET /user).
      2. Look up the installation_id for `org` via the hybrid helper
         (DB lookup first, GitHub fallback). If no installation → INSTALL_REQUIRED.
      3. Mint the installation token (cached 55 min, per-id lock).
      4. GET /orgs/{org}/members/{username} with the installation token.
         - 204 → MEMBER, 404 → NOT_MEMBER, 401 → force-refresh once and retry.

    Args:
      session: AsyncSession for the installation lookup (and potential backfill).
      github_user_token: a user OAuth token. Both gho_ (LibreChat OAuth App) and
        ghu_ (GitHub App user-to-server, Plan 12-06) shapes are accepted —
        GitHub's /user endpoint validates either.
      org: org login (case-sensitive in storage; GitHub treats login case-
        insensitively).

    Returns:
      {
        "login": str,                       # GitHub login
        "github_id": int,                   # numeric GitHub user id
        "email": str | None,                # from /user (may be None if private)
        "name": str | None,                 # from /user
        "is_org_member": bool,              # True iff result == MEMBER
        "install_required": bool,           # True iff result == INSTALL_REQUIRED
        "result": OrgMembershipResult,
      }

    Cache: keyed by (token[:16], org). MEMBER/NOT_MEMBER cached 5 min;
    INSTALL_REQUIRED cached 60s so a user retrying after install gets the
    refreshed state without 4 extra minutes of stale "install required" UX.
    """
    cache_key = f"{github_user_token[:16]}:{org}"
    now = time.time()
    cached = _github_membership_cache.get(cache_key)
    if cached is not None:
        ts, result = cached
        # The cache stores the absolute expiry timestamp (ts == expiry_unix_ts)
        # so MEMBER (5min) and INSTALL_REQUIRED (60s) entries coexist correctly.
        if ts > now:
            return result

    async with httpx.AsyncClient(timeout=10.0) as client:
        # Step 1 — resolve user identity from the user token.
        user_r = await client.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {github_user_token}", **_GH_HEADERS},
        )
        user_r.raise_for_status()
        user_data = user_r.json()
        username = user_data["login"]
        github_id = int(user_data["id"])

        # Step 2 — look up the installation token for this org.
        installation_token = await get_installation_token_for_org(session, org)

        if installation_token is None:
            # App not installed on this org. Surface so caller can hand the
            # install URL to the frontend (Plan 12-06 UX).
            result = {
                "login": username,
                "github_id": github_id,
                "email": user_data.get("email"),
                "name": user_data.get("name"),
                "is_org_member": False,
                "install_required": True,
                "result": OrgMembershipResult.INSTALL_REQUIRED,
            }
            _github_membership_cache[cache_key] = (
                now + _GITHUB_INSTALL_REQUIRED_TTL, result,
            )
            return result

        # Step 3+4 — membership check with installation token.
        # RESEARCH §Pitfall 4: 204 = member, anything else = not-member.
        membership_url = (
            f"https://api.github.com/orgs/{org}/members/{username}"
        )
        org_r = await client.get(
            membership_url,
            headers={"Authorization": f"Bearer {installation_token}", **_GH_HEADERS},
        )

        # REVISION 2 (M-4 fix) — 401 retry on the membership endpoint. If the
        # installation token was revoked between mint (12-03 cache) and use,
        # GitHub returns 401. Force-refresh ONCE and retry; if still 401,
        # fall through (is_member stays False — NOT_MEMBER) so we don't loop.
        if org_r.status_code == 401:
            log.warning(
                "github_app.membership_check.401_retry",
                org=org,
                username=username,
            )
            installation_token = await get_installation_token_for_org(
                session, org, force_refresh=True,
            )
            if installation_token is not None:
                org_r = await client.get(
                    membership_url,
                    headers={
                        "Authorization": f"Bearer {installation_token}",
                        **_GH_HEADERS,
                    },
                )

        is_member = org_r.status_code == 204

    result = {
        "login": username,
        "github_id": github_id,
        "email": user_data.get("email"),
        "name": user_data.get("name"),
        "is_org_member": is_member,
        "install_required": False,
        "result": (
            OrgMembershipResult.MEMBER if is_member
            else OrgMembershipResult.NOT_MEMBER
        ),
    }
    _github_membership_cache[cache_key] = (now + _GITHUB_CACHE_TTL, result)
    return result
