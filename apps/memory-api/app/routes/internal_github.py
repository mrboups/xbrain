"""Internal GitHub endpoints — not exposed to end users.

Exposes:
  GET /internal/github/list?repo=owner/name&path=&ref=HEAD
    List files/dirs at a path in a GitHub repo via the App installation token.

  GET /internal/github/read?repo=owner/name&path=path/to/file&ref=HEAD
    Read the text content of a single file from a GitHub repo.

  POST /internal/github/sync
    Index all text files from a repo into the team brain (on-demand, background).
    Body: {repo, team_scope, project_scope?, ref?}
    Returns 202 immediately; sync runs in the background.

  GET /internal/github/catalog?team_scope=<slug>
    (or header X-Team-Scope: <slug>)
    Return the exact catalog of repos indexed for a team (brain-only, direct
    memory_items SELECT, no Qdrant). Used by mcp-github github_list_repos.
    # memory_items.source is free-form VARCHAR(128), no CHECK (migration 0002);
    # idx_memory_source_team_unique dropped (migration 0020) so many catalog
    # items with the same source per team are allowed.

Auth: Depends(get_current_principal) — any authenticated principal.

list/read are team-neutral: they return GitHub content the App installation is
already sanctioned to read, and they write nothing.

sync and catalog are NOT team-neutral and are gated by `_authorize_team_scope`:
sync WRITES a whole repository into a team's brain, catalog READS that team's
indexed repo list. Both take the team from the caller (body field / header /
query param), which is a claim and not a grant — see the helper's docstring.
"""

import json
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import async_session_factory
from app.deps import get_current_principal, get_memory_provider, get_session
from app.repos.teams import get_membership
from app.services import background
from app.services.github_contents import (
    GithubAppNotInstalled,
    GithubPermissionDenied,
    list_repo_files,
    read_repo_file,
)
from app.services.github_sync import sync_repo

log = structlog.get_logger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Request model for POST /internal/github/sync
# ---------------------------------------------------------------------------


class GithubSyncRequest(BaseModel):
    repo: str = Field(..., description="Repository in 'owner/name' format")
    team_scope: str = Field(..., min_length=1, max_length=64, description="Team slug")
    project_scope: str | None = Field(
        default=None,
        max_length=64,
        description="Project slug (defaults to repo name when absent)",
    )
    ref: str = Field(default="HEAD", description="Git ref (branch, tag, SHA)")


# ---------------------------------------------------------------------------
# Team-scope authorisation for the two non-team-neutral endpoints
# ---------------------------------------------------------------------------


async def _authorize_team_scope(
    session: AsyncSession,
    principal: dict[str, Any],
    team_scope: str,
) -> None:
    """Prove the caller may act inside *team_scope*, however that slug arrived.

    Both callers put the slug somewhere the client controls — a JSON field on
    sync, the X-Team-Scope header (or a query param) on catalog. Neither of
    those is evidence. Before this gate, `POST /internal/github/sync` would
    index an entire repository into any team named in the body, and `catalog`
    would read back any team's repo list, for any account that could sign in.

    The two principal shapes are checked the two different ways they can be
    checked, and there is no third branch that falls through:

    * ``kind='bridge'`` — the JWT's OWN ``team_scope`` claim must equal the
      requested slug. mcp-github's LibreChat/email path mints exactly such a
      JWT after resolving the caller's team server-side, so the claim is the
      resolved answer and the comparison is free. A bridge JWT with no claim
      at all fails: an unnamed team is a mismatch, never a wildcard.
    * a user (``kind='user'`` / ``'user_api_token'``) — team_members must hold
      an unblocked row. A token pinned to another team is rejected before the
      lookup, which is what stops mcp-github's xbt_ token path from being a
      way around the pin.
    """
    if principal.get("kind") == "bridge":
        claim = principal.get("team_scope")
        if not claim or claim != team_scope:
            raise HTTPException(
                status_code=403,
                detail="bridge JWT team_scope does not match the requested team",
            )
        return

    user = principal.get("user")
    if user is None:
        # Any future principal kind that carries no user identity lands here
        # rather than sliding past the gate.
        raise HTTPException(status_code=403, detail="not a member of this team")

    pinned = principal.get("api_token_team_scope")
    if pinned and pinned != team_scope:
        raise HTTPException(
            status_code=403,
            detail="API token team_scope mismatch with the requested team",
        )

    membership = await get_membership(session, user_id=user.id, team_slug=team_scope)
    if membership is None or membership.blocked_at is not None:
        raise HTTPException(status_code=403, detail="not a member of this team")


@router.get("/internal/github/list")
async def github_list(
    repo: str = Query(..., description="Repository in 'owner/name' format"),
    path: str = Query("", description="Path within the repo (default: root)"),
    ref: str = Query("HEAD", description="Git ref (branch, tag, SHA)"),
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """List files and directories at *path* in *repo* at *ref*.

    Returns:
        {"entries": [{name, path, type, size, sha}, ...]}
        Returns {"entries": []} when the path does not exist (GitHub 404).

    Auth: any authenticated principal (including kind='bridge').
    """
    try:
        entries = await list_repo_files(session, repo, path=path, ref=ref)
        return {"entries": entries}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GithubAppNotInstalled as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except GithubPermissionDenied as exc:
        raise HTTPException(
            status_code=403,
            detail=str(exc),
        ) from exc


@router.get("/internal/github/read")
async def github_read(
    repo: str = Query(..., description="Repository in 'owner/name' format"),
    path: str = Query(..., description="Path to the file within the repo"),
    ref: str = Query("HEAD", description="Git ref (branch, tag, SHA)"),
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Read the text content of a single file from *repo*.

    Returns:
        {repo, path, ref, size (bytes), truncated (bool), content (str)}
        Content is capped at 100 KB; truncated=true when the file is larger.

    Auth: any authenticated principal (including kind='bridge').
    """
    try:
        result = await read_repo_file(session, repo, path=path, ref=ref)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GithubAppNotInstalled as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except GithubPermissionDenied as exc:
        raise HTTPException(
            status_code=403,
            detail=str(exc),
        ) from exc


@router.post("/internal/github/sync", status_code=202)
async def github_sync(
    body: GithubSyncRequest,
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Index all text files from a GitHub repo into the team brain.

    Performs synchronous validation (repo format, GitHub App installation check,
    permission check) before accepting the request. The actual walk-and-upsert
    runs in a background asyncio task so large repos do not time out the HTTP call.

    Body:
        repo         (required) — "owner/name"
        team_scope   (required) — team slug
        project_scope (optional) — project slug; defaults to repo name
        ref          (optional, default "HEAD") — git ref

    Returns 202 {"status": "started", "repo": "...", "ref": "..."}
    immediately after launching the background task.

    Auth: a bridge JWT whose team_scope claim equals body.team_scope, or a user
    who is an unblocked member of it. See `_authorize_team_scope`.
    """
    # Authorise the destination team BEFORE the GitHub round-trip below: the
    # reachability probe is the one part of this handler an unauthorised caller
    # could otherwise use as an oracle for which repos the App can read.
    await _authorize_team_scope(session, principal, body.team_scope)

    # --- Synchronous validation: repo format + GitHub App reachability ---
    # list_repo_files raises ValueError (bad format), GithubAppNotInstalled (404),
    # GithubPermissionDenied (403) — map these before kicking off the background task.
    try:
        await list_repo_files(session, body.repo, path="", ref=body.ref)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except GithubAppNotInstalled as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except GithubPermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    # --- Launch background task with its OWN session (do not reuse request session) ---
    repo = body.repo
    team_scope = body.team_scope
    project_scope = body.project_scope
    ref = body.ref

    async def _run_sync() -> None:
        try:
            async with async_session_factory() as bg_session:
                provider = get_memory_provider()
                result = await sync_repo(
                    bg_session,
                    provider,
                    repo=repo,
                    team_scope=team_scope,
                    project_scope=project_scope,
                    ref=ref,
                )
                log.info("github_sync.background.done", **result)
        except Exception as exc:
            log.warning(
                "github_sync.background.error",
                repo=repo,
                team_scope=team_scope,
                error=str(exc),
            )

    background.spawn(_run_sync(), name="github_sync.repo")

    return {"status": "started", "repo": repo, "ref": ref}


@router.get("/internal/github/catalog")
async def github_catalog(
    request: Request,
    team_scope: str | None = Query(default=None, description="Team slug"),
    principal: dict[str, Any] = Depends(get_current_principal),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Return the exact catalog of GitHub repos indexed for a team.

    Performs a direct ``memory_items`` SELECT filtered by
    ``source = 'github:repo-catalog' AND team_scope = $1 AND deleted_at IS NULL``
    — no Qdrant, no new table.  Results are ordered by ``updated_at DESC``.

    Team scope resolution order:
      1. Header ``X-Team-Scope``  (preferred — what mcp-github sends).
      2. Query param ``team_scope``.
    Returns 400 if neither is provided.

    Auth: a bridge JWT whose team_scope claim equals the resolved team, or a
    user who is an unblocked member of it. See `_authorize_team_scope` — the
    header being "what mcp-github sends" says nothing about who sent it.
    """
    # Resolve team scope from header (preferred) or query param.
    resolved_team = request.headers.get("X-Team-Scope") or team_scope
    if not resolved_team:
        raise HTTPException(status_code=400, detail="team_scope is required (header X-Team-Scope or query param)")

    await _authorize_team_scope(session, principal, resolved_team)

    provider = get_memory_provider()
    pool = await provider._ensure_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, content, metadata, project_scope, created_at, updated_at
            FROM memory_items
            WHERE source = 'github:repo-catalog'
              AND team_scope = $1
              AND deleted_at IS NULL
            ORDER BY updated_at DESC
            """,
            resolved_team,
        )

    catalog_repos = []
    for r in rows:
        # asyncpg may return metadata as a string (JSONB) or already a dict.
        raw_meta = r["metadata"]
        if isinstance(raw_meta, str):
            try:
                meta = json.loads(raw_meta)
            except (json.JSONDecodeError, TypeError):
                meta = {}
        else:
            meta = raw_meta or {}

        catalog_repos.append(
            {
                "id": str(r["id"]),
                "content": r["content"],
                "project_scope": r["project_scope"],
                "full_name": meta.get("full_name"),
                "html_url": meta.get("html_url"),
                "primary_language": meta.get("primary_language"),
                "topics": meta.get("topics"),
                "visibility": meta.get("visibility"),
                "readme_summarized": meta.get("readme_summarized"),
                "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
            }
        )

    return {
        "team_scope": resolved_team,
        "count": len(catalog_repos),
        "repos": catalog_repos,
    }
