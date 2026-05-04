"""/v1/admin/drive-mapping — admin endpoints for Drive folder → team mapping.

Flow:
  1. Admin calls POST /v1/admin/drive-mapping with {team_scope, folder_id}
  2. Endpoint creates/updates row in team_drive_mappings and returns {authorization_url}
  3. Admin (or user on behalf of team) visits authorization_url in browser
  4. Google redirects to /v1/admin/drive-mapping/oauth-callback?code=...&state=team_scope
  5. Callback exchanges code for tokens, encrypts with Fernet, stores in team_drive_mappings

Security:
  POST/GET admin endpoints require bridge JWT (kind=service) or a sub listed in
  ADMIN_USER_SUBS. The OAuth callback is intentionally unauthenticated — Google
  calls it with a single-use code that expires in 10 min (T-03-10-01 accepted).
  Stored credentials are Fernet-encrypted at rest (T-03-10-02 mitigated).
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.deps import get_current_principal, get_session

log = structlog.get_logger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_fernet():
    """Return a Fernet instance or raise 500 if key not configured."""
    if not settings.OAUTH_CREDENTIALS_ENCRYPTION_KEY:
        raise HTTPException(
            500,
            "OAUTH_CREDENTIALS_ENCRYPTION_KEY not configured — cannot store OAuth credentials",
        )
    from cryptography.fernet import Fernet  # lazy import — cryptography is optional at boot

    return Fernet(settings.OAUTH_CREDENTIALS_ENCRYPTION_KEY.encode())


def _is_admin(principal: dict[str, Any]) -> bool:
    """Return True for bridge service JWTs (kind=service/bridge) or listed admin subs.

    Bridge tokens (kind='bridge') are emitted by internal services and implicitly
    trusted as admin for configuration endpoints. Real-user tokens (kind='user')
    require the caller's sub to appear in ADMIN_USER_SUBS.
    """
    if principal.get("kind") in ("service", "bridge"):
        return True
    sub = principal.get("sub", "")
    admin_subs = [s.strip() for s in (settings.ADMIN_USER_SUBS or "").split(",") if s.strip()]
    return sub in admin_subs


def _build_authorization_url(team_scope: str) -> str:
    """Construct the Google OAuth incremental-auth URL for drive.readonly."""
    redirect_uri = f"{settings.MEMORY_API_EXTERNAL_URL}/v1/admin/drive-mapping/oauth-callback"
    return (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={settings.GOOGLE_CLIENT_ID}"
        f"&redirect_uri={redirect_uri}"
        "&scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fdrive.readonly"
        "&include_granted_scopes=true"
        "&access_type=offline"
        "&response_type=code"
        f"&state={team_scope}"
        "&prompt=consent"
    )


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class DriveMappingBody(BaseModel):
    team_scope: str
    folder_id: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/admin/drive-mapping", status_code=201)
async def create_drive_mapping(
    body: DriveMappingBody,
    session=Depends(get_session),
    principal: dict[str, Any] = Depends(get_current_principal),
):
    """Create or update a Drive folder → team mapping.

    Returns an authorization_url the admin must visit to grant drive.readonly
    OAuth consent on behalf of the team. The mapping row is created immediately;
    oauth_credentials_enc is populated later by the oauth-callback endpoint.
    """
    if not _is_admin(principal):
        raise HTTPException(403, "Admin access required")
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(500, "GOOGLE_CLIENT_ID not configured")

    # Upsert: create or refresh folder_id for existing team mapping
    await session.execute(
        """INSERT INTO team_drive_mappings(team_scope, folder_id)
           VALUES(:team_scope, :folder_id)
           ON CONFLICT(team_scope) DO UPDATE
           SET folder_id=EXCLUDED.folder_id, updated_at=now()""",
        {"team_scope": body.team_scope, "folder_id": body.folder_id},
    )
    await session.commit()

    authorization_url = _build_authorization_url(body.team_scope)
    log.info("drive_mapping.created", team=body.team_scope, folder=body.folder_id)
    return {
        "team_scope": body.team_scope,
        "folder_id": body.folder_id,
        "authorization_url": authorization_url,
        "next_step": (
            "Visit authorization_url in a browser to grant Drive read access. "
            "Google will redirect to /v1/admin/drive-mapping/oauth-callback, "
            "which stores the encrypted credentials automatically."
        ),
    }


@router.get("/admin/drive-mapping/{team_scope}")
async def get_drive_mapping(
    team_scope: str,
    session=Depends(get_session),
    principal: dict[str, Any] = Depends(get_current_principal),
):
    """Get the current Drive mapping for a team.

    Intentionally omits oauth_credentials_enc — raw encrypted bytes are not
    useful to the caller and would widen the information-disclosure surface
    (T-03-10-02).
    """
    if not _is_admin(principal):
        raise HTTPException(403, "Admin access required")

    row = await session.execute(
        "SELECT team_scope, folder_id, change_token, oauth_credentials_enc, updated_at"
        " FROM team_drive_mappings WHERE team_scope=:ts",
        {"ts": team_scope},
    )
    result = row.fetchone()
    if result is None:
        raise HTTPException(404, f"No Drive mapping for team '{team_scope}'")

    return {
        "team_scope": result.team_scope,
        "folder_id": result.folder_id,
        "change_token": result.change_token,
        "updated_at": str(result.updated_at),
        # True only when encrypted credentials are stored (OAuth flow completed)
        "oauth_configured": result.oauth_credentials_enc is not None,
    }


@router.get("/admin/drive-mapping/oauth-callback")
async def drive_oauth_callback(
    code: str,
    state: str,
    session=Depends(get_session),
):
    """Google OAuth callback — exchange authorization code for tokens and store encrypted.

    This endpoint is called by Google after the user grants consent in the browser.
    It is intentionally unauthenticated (Google cannot send a Bearer token here).
    The `code` param is single-use and expires in ~10 minutes (T-03-10-01 accepted).

    `state` = team_scope (set in the authorization URL by create_drive_mapping).
    """
    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
        raise HTTPException(500, "Google OAuth client not configured (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET)")

    redirect_uri = f"{settings.MEMORY_API_EXTERNAL_URL}/v1/admin/drive-mapping/oauth-callback"
    fernet = _require_fernet()

    # Exchange authorization code for access + refresh tokens
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if resp.status_code != 200:
            log.error(
                "drive_oauth.token_exchange_failed",
                status=resp.status_code,
                body=resp.text[:500],
            )
            raise HTTPException(
                502,
                f"Google token exchange failed (HTTP {resp.status_code}). "
                "Check GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, and the authorized redirect URIs in Google Cloud Console.",
            )
        tokens = resp.json()

    # Encrypt credentials (Fernet AES-128-CBC + HMAC-SHA256) and persist
    encrypted = fernet.encrypt(json.dumps(tokens).encode()).decode()
    team_scope = state

    await session.execute(
        "UPDATE team_drive_mappings"
        " SET oauth_credentials_enc=:enc, updated_at=now()"
        " WHERE team_scope=:ts",
        {"enc": encrypted, "ts": team_scope},
    )
    await session.commit()

    log.info("drive_oauth.callback_success", team=team_scope)
    return {
        "status": "OAuth credentials stored successfully",
        "team_scope": team_scope,
        "note": "Drive sync will use these credentials on its next polling cycle.",
    }
