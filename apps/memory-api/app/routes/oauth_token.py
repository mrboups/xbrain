"""POST /oauth/token — OAuth 2.1 token endpoint (authorization_code + refresh).

Mounted WITHOUT the /v1 prefix (public path /oauth/token). Form-encoded per the
OAuth 2.1 spec. Public clients (auth method "none") are accepted — no
client_secret. This is the riskiest surface in the plan, so the order of checks
is deliberate and each failure mints NO token:

  1. authorization_code grant:
     a. one-time-consume the code (replay → invalid_grant)
     b. PKCE: base64url(SHA256(code_verifier)) == stored code_challenge
        (mismatch → invalid_grant)
     c. redirect_uri + client_id match the code's stored values (mismatch → 400)
     d. resource (normalized) == the code's stored resource
        (mismatch → invalid_target)
     e. only then mint + store the oat_/ort_ pair bound to team_scope + aud
  2. refresh_token grant: validate + rotate (old refresh invalidated).
"""
from __future__ import annotations

import base64
import hashlib
import hmac

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import oauth_store
from app.auth.oauth_tokens import normalize_resource
from app.deps import get_session

router = APIRouter()
log = structlog.get_logger(__name__)


def verify_pkce_s256(code_verifier: str, code_challenge: str) -> bool:
    """True iff base64url(SHA256(code_verifier)) == code_challenge (constant-time).

    Returns False for any empty input so a missing verifier or challenge can
    never accidentally pass.
    """
    if not code_verifier or not code_challenge:
        return False
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return hmac.compare_digest(computed, code_challenge)


def _error(status: int, error: str, description: str | None = None) -> JSONResponse:
    body = {"error": error}
    if description:
        body["error_description"] = description
    return JSONResponse(status_code=status, content=body)


@router.post("/oauth/token")
async def token(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    form = await request.form()
    grant_type = form.get("grant_type")

    # ── refresh_token grant ───────────────────────────────────────────────
    if grant_type == "refresh_token":
        raw_refresh = form.get("refresh_token")
        if not raw_refresh:
            return _error(400, "invalid_request", "missing refresh_token")
        rotated = await oauth_store.rotate_refresh_token(
            session, raw_refresh=str(raw_refresh)
        )
        if rotated is None:
            return _error(400, "invalid_grant", "refresh token invalid or expired")
        await session.commit()
        return JSONResponse(
            {
                "access_token": rotated["access_token"],
                "token_type": "Bearer",
                "expires_in": rotated["expires_in"],
                "refresh_token": rotated["refresh_token"],
                "scope": rotated["scope"],
            }
        )

    # ── authorization_code grant ──────────────────────────────────────────
    if grant_type != "authorization_code":
        return _error(400, "unsupported_grant_type")

    code = form.get("code")
    redirect_uri = form.get("redirect_uri")
    client_id = form.get("client_id")
    code_verifier = form.get("code_verifier")
    resource = form.get("resource")

    if not code or not redirect_uri or not client_id or not code_verifier:
        return _error(400, "invalid_request", "missing required parameter")

    # (a) one-time consume — replay returns None.
    code_row = await oauth_store.consume_auth_code(session, str(code))
    if code_row is None:
        return _error(400, "invalid_grant", "authorization code invalid, used, or expired")

    # (b) PKCE S256 verification.
    if not verify_pkce_s256(str(code_verifier), code_row["code_challenge"]):
        await session.commit()  # persist the used_at marking (code is now spent)
        return _error(400, "invalid_grant", "PKCE verification failed")

    # (c) redirect_uri + client_id must match the code's stored values.
    if str(redirect_uri) != code_row["redirect_uri"] or str(client_id) != code_row["client_id"]:
        await session.commit()
        return _error(400, "invalid_grant", "redirect_uri or client_id mismatch")

    # (d) resource / audience must match (normalized).
    if normalize_resource(str(resource or "")) != normalize_resource(code_row["resource"]):
        await session.commit()
        return _error(400, "invalid_target", "resource does not match the authorization request")

    # (e) all checks passed — mint + store the token pair.
    bundle = await oauth_store.mint_and_store_access_token(
        session,
        client_id=code_row["client_id"],
        user_id=code_row["user_id"],
        team_scope=code_row["team_scope"],
        resource=code_row["resource"],
        scope=code_row["scope"],
        source="claude.ai-connector",
    )
    await session.commit()
    log.info("oauth.token.issued", team_scope=code_row["team_scope"])
    return JSONResponse(
        {
            "access_token": bundle["access_token"],
            "token_type": "Bearer",
            "expires_in": bundle["expires_in"],
            "refresh_token": bundle["refresh_token"],
            "scope": bundle["scope"],
        }
    )
