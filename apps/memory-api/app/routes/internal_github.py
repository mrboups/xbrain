"""Internal GitHub endpoints — not exposed to end users.

Exposes:
  GET /internal/github/list?repo=owner/name&path=&ref=HEAD
    List files/dirs at a path in a GitHub repo via the App installation token.

  GET /internal/github/read?repo=owner/name&path=path/to/file&ref=HEAD
    Read the text content of a single file from a GitHub repo.

Auth: Depends(get_current_principal) — accepts kind='bridge' (same as internal.py).
NOT team-scoped: access is org-sanctioned via the GitHub App installation, not per-team.
"""

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_current_principal, get_session
from app.services.github_contents import (
    GithubAppNotInstalled,
    GithubPermissionDenied,
    list_repo_files,
    read_repo_file,
)

log = structlog.get_logger(__name__)
router = APIRouter()


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
