"""/v1/admin/drive-mapping — admin endpoints for Drive folder → team mapping.

Flow:
  1. Admin calls POST /v1/admin/drive-mapping with {team_scope, folder_id, project_scope?}
  2. Endpoint creates or upserts a row in team_drive_mappings and returns {id, authorization_url}
  3. Admin (or user on behalf of team) visits authorization_url in browser
  4. Google redirects to /v1/admin/drive-mapping/oauth-callback?code=...&state=<mapping_id>
  5. Callback exchanges code for tokens, encrypts with Fernet, stores in team_drive_mappings

Multi-folder support (migration 0005):
  - UNIQUE(team_scope) constraint replaced by UNIQUE(team_scope, folder_id).
  - A team can have N folder mappings, each with an optional project_scope.
  - GET /admin/drive-mapping?team_scope=... returns a list of mappings.
  - DELETE /admin/drive-mapping/{mapping_id} removes a specific mapping.

Security:
  POST/GET/DELETE admin endpoints require bridge JWT (kind=service) or a sub listed in
  ADMIN_USER_SUBS. The OAuth callback is intentionally unauthenticated — Google
  calls it with a single-use code that expires in 10 min (T-03-10-01 accepted).
  Stored credentials are Fernet-encrypted at rest (T-03-10-02 mitigated).
  OAuth state parameter now uses mapping_id (UUID) instead of team_scope to support
  multiple mappings per team (T-04-06-SEC-02 accepted).
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import sqlalchemy as sa
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

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


def _build_authorization_url(mapping_id: str) -> str:
    """Construct the Google OAuth incremental-auth URL for drive.readonly.

    Uses mapping_id (UUID) as OAuth state parameter so the callback can
    target the exact row when multiple mappings exist for the same team.
    """
    redirect_uri = f"{settings.MEMORY_API_EXTERNAL_URL}/v1/admin/drive-mapping/oauth-callback"
    return (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={settings.GOOGLE_CLIENT_ID}"
        f"&redirect_uri={redirect_uri}"
        "&scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fdrive.readonly"
        "&include_granted_scopes=true"
        "&access_type=offline"
        "&response_type=code"
        f"&state={mapping_id}"
        "&prompt=consent"
    )


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class DriveMappingBody(BaseModel):
    team_scope: str
    folder_id: str
    project_scope: str | None = Field(default=None, max_length=64)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/admin/drive-mapping", status_code=201)
async def create_drive_mapping(
    body: DriveMappingBody,
    session=Depends(get_session),
    principal: dict[str, Any] = Depends(get_current_principal),
):
    """Create or upsert a Drive folder → team mapping.

    After migration 0005: UNIQUE(team_scope, folder_id) replaces UNIQUE(team_scope).
    Sending the same (team_scope, folder_id) pair updates project_scope (upsert).
    Sending a new folder_id for the same team creates an additional mapping row.

    Returns {id, team_scope, folder_id, project_scope, authorization_url}. The
    admin must visit authorization_url to grant drive.readonly OAuth consent.
    """
    if not _is_admin(principal):
        raise HTTPException(403, "Admin access required")
    if not settings.GOOGLE_CLIENT_ID:
        raise HTTPException(500, "GOOGLE_CLIENT_ID not configured")

    # Upsert: insert or update project_scope for an existing (team_scope, folder_id) pair
    result = await session.execute(
        sa.text(
            """INSERT INTO team_drive_mappings(team_scope, folder_id, project_scope)
               VALUES(:team_scope, :folder_id, :project_scope)
               ON CONFLICT(team_scope, folder_id) DO UPDATE
               SET project_scope=EXCLUDED.project_scope, updated_at=now()
               RETURNING id"""
        ),
        {
            "team_scope": body.team_scope,
            "folder_id": body.folder_id,
            "project_scope": body.project_scope,
        },
    )
    row = result.fetchone()
    mapping_id = str(row.id)
    await session.commit()

    authorization_url = _build_authorization_url(mapping_id)
    log.info(
        "drive_mapping.created",
        team=body.team_scope,
        folder=body.folder_id,
        mapping_id=mapping_id,
        project_scope=body.project_scope,
    )
    return {
        "id": mapping_id,
        "team_scope": body.team_scope,
        "folder_id": body.folder_id,
        "project_scope": body.project_scope,
        "authorization_url": authorization_url,
        "next_step": (
            "Visit authorization_url in a browser to grant Drive read access. "
            "Google will redirect to /v1/admin/drive-mapping/oauth-callback, "
            "which stores the encrypted credentials automatically."
        ),
    }


@router.get("/admin/drive-mapping")
async def list_drive_mappings(
    team_scope: str = Query(..., max_length=64),
    session=Depends(get_session),
    principal: dict[str, Any] = Depends(get_current_principal),
):
    """List all Drive mappings for a team.

    After migration 0005, a team can have multiple folder mappings.
    Returns an array of mapping objects ordered by updated_at ascending.

    Intentionally omits oauth_credentials_enc — raw encrypted bytes are not
    useful to the caller and would widen the information-disclosure surface
    (T-03-10-02).
    """
    if not _is_admin(principal):
        raise HTTPException(403, "Admin access required")

    rows = await session.execute(
        sa.text(
            "SELECT id, team_scope, folder_id, project_scope, change_token,"
            " oauth_credentials_enc, updated_at"
            " FROM team_drive_mappings WHERE team_scope=:ts ORDER BY updated_at"
        ),
        {"ts": team_scope},
    )
    results = rows.fetchall()
    if not results:
        raise HTTPException(404, f"No Drive mappings for team '{team_scope}'")
    return [
        {
            "id": str(r.id),
            "team_scope": r.team_scope,
            "folder_id": r.folder_id,
            "project_scope": r.project_scope,
            "change_token": r.change_token,
            "updated_at": str(r.updated_at),
            "oauth_configured": r.oauth_credentials_enc is not None,
        }
        for r in results
    ]


@router.delete("/admin/drive-mapping/{mapping_id}", status_code=204)
async def delete_drive_mapping(
    mapping_id: str,
    session=Depends(get_session),
    principal: dict[str, Any] = Depends(get_current_principal),
):
    """Delete a specific Drive mapping by UUID.

    Removes the mapping row and any associated watch channels (FK CASCADE
    will handle drive_watch_channels rows once migration 0006 is applied).
    Admin-only. The polling loop will stop syncing this folder on the next tick.

    T-04-06-SEC-01 accepted: admin-only, deletion is intentional stop-sync.
    """
    if not _is_admin(principal):
        raise HTTPException(403, "Admin access required")
    result = await session.execute(
        sa.text("DELETE FROM team_drive_mappings WHERE id=:id RETURNING id"),
        {"id": mapping_id},
    )
    if result.fetchone() is None:
        raise HTTPException(404, f"Mapping '{mapping_id}' not found")
    await session.commit()
    log.info("drive_mapping.deleted", mapping_id=mapping_id)
    # 204 No Content — FastAPI returns empty body automatically


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

    `state` = mapping_id (UUID) — set in the authorization URL by create_drive_mapping.
    Using mapping_id instead of team_scope supports multiple folders per team
    (T-04-06-SEC-02 accepted — same risk profile as T-03-10-01).
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
    mapping_id = state  # state param now carries mapping UUID (not team_scope)

    result = await session.execute(
        sa.text(
            "UPDATE team_drive_mappings"
            " SET oauth_credentials_enc=:enc, updated_at=now()"
            " WHERE id=:mapping_id"
            " RETURNING team_scope"
        ),
        {"enc": encrypted, "mapping_id": mapping_id},
    )
    updated = result.fetchone()
    if updated is None:
        log.error("drive_oauth.mapping_not_found", mapping_id=mapping_id)
        raise HTTPException(404, f"Mapping '{mapping_id}' not found — OAuth callback state is stale or invalid.")

    await session.commit()

    log.info("drive_oauth.callback_success", mapping_id=mapping_id, team=updated.team_scope)
    return {
        "status": "OAuth credentials stored successfully",
        "mapping_id": mapping_id,
        "team_scope": updated.team_scope,
        "note": "Drive sync will use these credentials on its next polling cycle.",
    }
