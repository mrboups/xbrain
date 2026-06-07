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

Auth: Depends(get_current_principal) — accepts kind='bridge' (same as internal.py).
NOT team-scoped for list/read/sync: access is org-sanctioned via the GitHub App
installation, not per-team.  /catalog IS team-scoped (requires X-Team-Scope or
query param team_scope).
"""

import asyncio
import json
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import async_session_factory
from app.deps import get_current_principal, get_memory_provider, get_session
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

    Auth: any authenticated principal (including kind='bridge').
    """
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

    asyncio.create_task(_run_sync())

    return {"status": "started", "repo": repo, "ref": ref}


@router.get("/internal/github/catalog")
async def github_catalog(
    request: Request,
    team_scope: str | None = Query(default=None, description="Team slug"),
    principal: dict[str, Any] = Depends(get_current_principal),
) -> dict[str, Any]:
    """Return the exact catalog of GitHub repos indexed for a team.

    Performs a direct ``memory_items`` SELECT filtered by
    ``source = 'github:repo-catalog' AND team_scope = $1 AND deleted_at IS NULL``
    — no Qdrant, no new table.  Results are ordered by ``updated_at DESC``.

    Team scope resolution order:
      1. Header ``X-Team-Scope``  (preferred — what mcp-github sends).
      2. Query param ``team_scope``.
    Returns 400 if neither is provided.

    Auth: any authenticated principal including kind='bridge'.
    """
    # Resolve team scope from header (preferred) or query param.
    resolved_team = request.headers.get("X-Team-Scope") or team_scope
    if not resolved_team:
        raise HTTPException(status_code=400, detail="team_scope is required (header X-Team-Scope or query param)")

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
