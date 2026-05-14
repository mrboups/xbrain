"""POST /v1/auth/github/signin — Phase 10 GHA-01.

Public endpoint (no Authorization header). Accepts a GitHub OAuth `code`
returned from the user's redirect, exchanges it for a `gho_` token server-side
(client_secret never crosses the network to the browser), then:

  1. Calls GET /user, /user/emails, /user/orgs.
  2. Resolves canonical user row with auto-merge (GHA-06):
       a. Lookup by github_id → if active, follow merge pointer.
       b. Else lookup by primary verified email → if present and no github_id,
          attach github_id to that row (implicit merge).
       c. Else lookup orphan by source_user_id = "github:{login}".
       d. If both a single Google user (by email) AND an orphan github-only row
          exist for the same identity, call merge_user_rows to consolidate
          (survivor = Google row, orphan = github-only row).
       e. Else create a fresh row with source_user_id = "github:{login}".
  3. Runs auto_grant_via_org_match for org-derived team memberships (GHA-02).
  4. Fires fail-soft admin emails via background task (GHA-05).
  5. Mints an `xbt_` token (team_scope = empty-string sentinel → multi-team)
     and returns it.

CSRF / state: the caller verifies the `state` param against sessionStorage
BEFORE calling this endpoint. The endpoint does not validate state itself
(per locked decision — the client-side verification before the POST is the
boundary). The endpoint requires a non-empty `state` to be present in the
body, treats it as opaque, and logs it for audit purposes.

Security:
- client_secret stays server-side (env GITHUB_CLIENT_SECRET).
- The endpoint is rate-limited by the gateway/nginx layer (per IP).
- Failed code exchanges return 400 with no GitHub error details (avoid info leak).
"""

import hashlib
import secrets
from typing import Any

import httpx
import sqlalchemy as sa
import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import async_session_factory
from app.deps import get_session
from app.models.user import User
from app.repos import users as users_repo
from app.repos.merge import merge_user_rows
from app.services.team_autogrant import (
    auto_grant_via_org_match,
    emit_autogrant_notifications,
)

router = APIRouter()
log = structlog.get_logger(__name__)

_GH_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


class SigninGithubBody(BaseModel):
    code: str = Field(..., min_length=1)
    redirect_uri: str = Field(..., min_length=1)
    state: str = Field(..., min_length=1, max_length=128)


class SigninGithubOut(BaseModel):
    xbt_token: str
    user: dict[str, Any]


async def _exchange_code_for_token(code: str, redirect_uri: str) -> str:
    """Exchange GitHub OAuth code for an access token. Raises HTTPException on failure."""
    if not settings.GITHUB_CLIENT_ID or not settings.GITHUB_CLIENT_SECRET:
        raise HTTPException(503, "GitHub OAuth not configured on memory-api")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={
                    "client_id": settings.GITHUB_CLIENT_ID,
                    "client_secret": settings.GITHUB_CLIENT_SECRET,
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
            )
    except httpx.RequestError as exc:
        raise HTTPException(502, "Could not reach GitHub token endpoint") from exc
    if r.status_code != 200:
        raise HTTPException(400, "GitHub token exchange failed")
    body = r.json()
    token = body.get("access_token")
    if not token:
        raise HTTPException(400, "GitHub token exchange failed: no access_token")
    return token


async def _fetch_github_profile(token: str) -> dict[str, Any]:
    """Fetch /user + /user/emails + /user/orgs (single client).

    Returns:
      {github_id, login, display_name, email (primary verified), org_logins: [..]}.
    """
    auth = {"Authorization": f"Bearer {token}", **_GH_HEADERS}
    async with httpx.AsyncClient(timeout=10.0) as client:
        u_r = await client.get("https://api.github.com/user", headers=auth)
        u_r.raise_for_status()
        u = u_r.json()

        e_r = await client.get("https://api.github.com/user/emails", headers=auth)
        # If the user denied user:email scope, /user/emails returns 404.
        primary_email = None
        if e_r.status_code == 200:
            primary_email = next(
                (e["email"] for e in e_r.json() if e.get("primary") and e.get("verified")),
                None,
            )

        # /user/orgs — paginate until <100 returned.
        org_logins: list[str] = []
        page = 1
        while True:
            o_r = await client.get(
                f"https://api.github.com/user/orgs?per_page=100&page={page}",
                headers=auth,
            )
            if o_r.status_code != 200:
                break
            page_orgs = o_r.json()
            org_logins.extend(o["login"] for o in page_orgs)
            if len(page_orgs) < 100:
                break
            page += 1

    return {
        "github_id": u["id"],
        "login": u["login"],
        "display_name": u.get("name") or u["login"],
        "email": primary_email or f"{u['login']}@users.noreply.github.com",
        "org_logins": org_logins,
    }


async def _resolve_or_merge_user(
    session: AsyncSession,
    *,
    github_id: int,
    login: str,
    display_name: str,
    email: str,
) -> User:
    """Identity resolution per RESEARCH.md Q3 + Pitfall 4. See route docstring
    for the full state machine.

    Phase 10 — B-2 fix (REVISION 1): Step B does **not** assign
    `github_id` to the survivor before clearing it on the orphan. The
    `users.github_id` column has a UNIQUE constraint (per migration 0016 +
    pre-existing Phase 5 schema); assigning the survivor first and then
    relying on a later step to clear the orphan causes both rows to hold
    the same `github_id` at `session.flush()` time, raising
    `sqlalchemy.exc.IntegrityError → UniqueViolationError` and rolling
    back the entire transaction (including the team_members migration in
    `merge_user_rows`). The fix sequences the operations inside a single
    transaction as:

      1. Clear orphan.github_id (release the unique index slot).
         await session.flush()  # index now empty for github_id.
      2. Assign survivor.github_id = github_id; survivor.github_username = login.
         await session.flush()  # safe to assign on survivor.
      3. Call merge_user_rows(orphan, survivor) — migrates FKs (team_members,
         user_api_tokens, conversations, etc.) and soft-deletes the orphan.
      4. Set orphan.merged_into_user_id = survivor.id (this is part of
         merge_user_rows already — kept here for explicitness).
         await session.flush()
      5. await session.commit() — at the route level after this function returns.

    The migration of FKs (step 3) is the heavy operation. Sequencing the
    `github_id` swap before it guarantees we never hold a unique-constraint
    conflict for any wall-clock duration.
    """

    # Step A — find active row by github_id (excludes already-merged rows).
    by_gh = await users_repo.find_user_by_github_id(session, github_id)
    if by_gh is not None:
        # Refresh display fields opportunistically.
        if email and not email.endswith("@users.noreply.github.com") and by_gh.email != email:
            by_gh.email = email
        if display_name and by_gh.display_name != display_name:
            by_gh.display_name = display_name
        if by_gh.github_username != login:
            by_gh.github_username = login
        await session.flush()
        return by_gh

    # Step B — find a Google user by primary verified email with no github_id.
    if email and not email.endswith("@users.noreply.github.com"):
        by_email = await users_repo.get_user_by_email(session, email)
        if (
            by_email is not None
            and by_email.github_id is None
            and by_email.merged_into_user_id is None
        ):
            # Look for an orphan github-only row to merge into this Google user.
            orphan_result = await session.execute(
                select(User).where(
                    User.source_user_id == f"github:{login}",
                    User.merged_into_user_id.is_(None),
                )
            )
            orphan = orphan_result.scalar_one_or_none()

            # === REVISION 1 B-2 FIX: explicit ordering ===
            #
            # If the orphan exists AND already carries github_id (because it
            # was created via a prior GitHub-only sign-in), we MUST clear it
            # before assigning on the survivor — otherwise the UNIQUE index
            # on users.github_id is violated at flush.
            if orphan is not None and orphan.id != by_email.id:
                # Step B.1 — release the unique index slot on the orphan.
                orphan.github_id = None
                await session.flush()
                # Step B.2 — now safe to assign on survivor.
                by_email.github_id = github_id
                by_email.github_username = login
                if display_name and not by_email.display_name:
                    by_email.display_name = display_name
                await session.flush()
                # Step B.3 — re-parent FKs and soft-delete the orphan.
                # merge_user_rows itself sets orphan.merged_into_user_id = survivor.id.
                await merge_user_rows(
                    session, orphan_id=orphan.id, survivor_id=by_email.id
                )
                # Step B.4 — final flush; commit happens at the route layer.
                await session.flush()
            else:
                # No orphan to merge — simple github_id attach on the survivor.
                by_email.github_id = github_id
                by_email.github_username = login
                if display_name and not by_email.display_name:
                    by_email.display_name = display_name
                await session.flush()
            return by_email

    # Step C — orphan github-only row exists.
    orphan_result = await session.execute(
        select(User).where(
            User.source_user_id == f"github:{login}",
            User.merged_into_user_id.is_(None),
        )
    )
    orphan = orphan_result.scalar_one_or_none()
    if orphan is not None:
        # Patch github_id + refresh email on the orphan, return as the survivor itself.
        orphan.github_id = github_id
        orphan.github_username = login
        if email:
            orphan.email = email
        if display_name:
            orphan.display_name = display_name
        await session.flush()
        return orphan

    # Step D — fresh row.
    new_user = User(
        source_user_id=f"github:{login}",
        email=email,
        display_name=display_name,
        github_username=login,
        github_id=github_id,
    )
    session.add(new_user)
    await session.flush()
    return new_user


async def _mint_xbt_for_user(session: AsyncSession, user_id) -> str:
    """Insert into user_api_tokens with empty-string team_scope sentinel.

    Phase 10 NOTE — the plan's original spec said `team_scope = NULL` for
    multi-team tokens, but `user_api_tokens.team_scope` is declared
    `TEXT NOT NULL` per migration 0013. We use the empty string `''` as the
    sentinel for "multi-team token, scope resolved at request time". The
    deps.py:get_team_scope check is patched (this same plan, in Task 5's
    deps.py edits) to treat an empty `api_token_team_scope` as 'multi-team OK'
    so any X-Team-Scope passes.
    """
    raw = "xbt_" + secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    await session.execute(sa.text("""
        INSERT INTO user_api_tokens (id, user_id, token_hash, team_scope, name, created_at)
        VALUES (gen_random_uuid(), :user_id, :hash, '', 'github-signin', now())
    """), {"user_id": user_id, "hash": token_hash})
    return raw


@router.post("/auth/github/signin", response_model=SigninGithubOut)
async def signin_github(
    body: SigninGithubBody,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> SigninGithubOut:
    log.info("auth.github.signin.start", state_len=len(body.state))

    token = await _exchange_code_for_token(body.code, body.redirect_uri)
    try:
        profile = await _fetch_github_profile(token)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(400, "GitHub profile fetch failed") from exc

    user = await _resolve_or_merge_user(
        session,
        github_id=profile["github_id"],
        login=profile["login"],
        display_name=profile["display_name"],
        email=profile["email"],
    )

    newly_joined = await auto_grant_via_org_match(
        session,
        user=user,
        github_login=profile["login"],
        github_org_logins=profile["org_logins"],
    )

    xbt = await _mint_xbt_for_user(session, user.id)
    await session.commit()

    # Fire-and-forget admin notifications.
    if newly_joined:
        background_tasks.add_task(
            emit_autogrant_notifications,
            newly_joined=newly_joined,
            new_member_login=profile["login"],
            new_member_display=profile["display_name"],
            session_factory=async_session_factory,
        )

    log.info(
        "auth.github.signin.ok",
        user_id=str(user.id),
        new_member=bool(newly_joined),
        joined_count=len(newly_joined),
    )

    return SigninGithubOut(
        xbt_token=xbt,
        user={
            "id": str(user.id),
            "email": user.email,
            "display_name": user.display_name,
            "github_username": user.github_username,
            "teams_joined": [t.slug for t in newly_joined],
        },
    )
