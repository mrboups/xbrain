"""POST /v1/me/link-github — link a GitHub account to the current Google-authenticated user.

Endpoint flow:
1. Caller must be authenticated with a Google ID token (standard Authorization header).
2. Caller provides their GitHub OAuth token (gho_... obtained from LibreChat GitHub login).
3. memory-api verifies the GitHub token via GitHub API and checks org membership.
4. github_username + github_id are stored on the users row.

Security notes (T-05-02-01):
- The GitHub API call with the user's own token proves they own that account.
  An attacker cannot link someone else's GitHub account without possessing their token.
- Phase 12 (Plan 12-04): org-membership check now uses an installation token
  minted from the GitHub App (replaces the prior server PAT). The user's gho_
  token never leaves memory-api; the installation token is server-side only.
"""

from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import check_github_org_membership
from app.config import settings
from app.deps import get_current_principal, get_session
from app.models.user import User

router = APIRouter()


class LinkGithubBody(BaseModel):
    github_token: str  # GitHub OAuth token (gho_...) from the user's LibreChat session


@router.post("/me/link-github")
async def link_github(
    body: LinkGithubBody,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Link a GitHub account to the currently authenticated user.

    Returns the github_username and whether the account is in the configured org.
    The caller must already be authenticated (Google ID token in Authorization header).
    Bridge tokens (service principals) are not allowed to link GitHub accounts.
    """
    if principal.get("kind") not in ("user", "user_api_token"):
        raise HTTPException(403, "Only user principals can link a GitHub account")

    # Fetch github username + org membership.
    # Phase 12 (Plan 12-04) — new signature: (session, user_token, org). The
    # helper returns github_id directly so we no longer need a second /user call.
    try:
        gh = await check_github_org_membership(
            session,
            body.github_token,
            settings.GITHUB_ORG,
        )
    except httpx.HTTPStatusError as exc:
        # GitHub API rejected the token — invalid or expired
        raise HTTPException(
            status_code=400,
            detail=f"GitHub token validation failed: HTTP {exc.response.status_code}",
        ) from exc
    except httpx.RequestError as exc:
        # Network-level error reaching GitHub API
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach GitHub API: {exc}",
        ) from exc

    username: str = gh["login"]
    github_id: int = gh["github_id"]

    user = principal["user"]

    # Check that this github_id is not already linked to a different xbrain user
    existing = await session.execute(
        select(User).where(User.github_id == github_id, User.id != user.id)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=409,
            detail="This GitHub account is already linked to another xbrain user",
        )

    # Update the user row with the GitHub account info
    await session.execute(
        update(User)
        .where(User.id == user.id)
        .values(github_username=username, github_id=github_id)
    )
    await session.commit()

    return {
        "github_username": username,
        "github_id": github_id,
        "is_org_member": gh["is_org_member"],
        # Phase 12 (Plan 12-04) — surface install_required so the frontend
        # can show an "install the xbrain App on your org" banner when the
        # user's org doesn't yet have the App installed.
        "install_required": gh.get("install_required", False),
    }


class LinkGithubWithCodeBody(BaseModel):
    code: str  # `code` returned by GitHub's OAuth /authorize redirect
    redirect_uri: str  # Must match the redirect_uri sent at /authorize time


@router.post("/me/link-github-with-code")
async def link_github_with_code(
    body: LinkGithubWithCodeBody,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Chrome extension entry point — exchange a GitHub OAuth `code` for a
    `gho_` token server-side (so the client_secret never leaves memory-api)
    then run the standard link flow.

    Returns the same payload as POST /v1/me/link-github.

    Auth: same — caller must be authenticated as `kind=user` (Google).
    """
    if principal.get("kind") not in ("user", "user_api_token"):
        raise HTTPException(403, "Only user principals can link a GitHub account")
    if not settings.GITHUB_CLIENT_ID or not settings.GITHUB_CLIENT_SECRET:
        raise HTTPException(
            503,
            "GitHub OAuth not configured on memory-api "
            "(GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET missing)",
        )

    # Exchange the code for an access token.
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            tok_r = await client.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={
                    "client_id": settings.GITHUB_CLIENT_ID,
                    "client_secret": settings.GITHUB_CLIENT_SECRET,
                    "code": body.code,
                    "redirect_uri": body.redirect_uri,
                },
            )
    except httpx.RequestError as exc:
        raise HTTPException(502, f"Could not reach GitHub token endpoint: {exc}") from exc

    if tok_r.status_code != 200:
        raise HTTPException(400, f"GitHub token exchange failed: HTTP {tok_r.status_code}")
    tok_json = tok_r.json()
    access_token = tok_json.get("access_token")
    if not access_token:
        # GitHub returns 200 + {"error": "..."} on bad code
        err = tok_json.get("error_description") or tok_json.get("error") or "no access_token"
        raise HTTPException(400, f"GitHub token exchange failed: {err}")

    # Delegate to the existing link flow using the freshly-exchanged token.
    return await link_github(
        LinkGithubBody(github_token=access_token),
        principal=principal,
        session=session,
    )
