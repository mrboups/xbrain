"""GitHub Contents service — read files from GitHub repos via the App installation token.

Uses the existing installation-token machinery (github_installation.py) so that
the GitHub App private key stays in one place. No per-user PATs or static tokens.

Public API
----------
list_repo_files(session, repo, path="", ref="HEAD") -> list[dict]
read_repo_file(session, repo, path, ref="HEAD") -> dict

Typed exceptions
----------------
GithubAppNotInstalled  — raised when no installation can be found for the owner.
                         Mapped to HTTP 404 by the route layer.
GithubPermissionDenied — raised on GitHub 403 (App lacks contents:read).
                         Mapped to HTTP 403 by the route layer with a clear grant message.
"""

from __future__ import annotations

import base64
import re

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.github_app_jwt import mint_app_jwt
from app.services.github_installation import (
    get_installation_token,
    get_installation_token_for_org,
)

log = structlog.get_logger(__name__)

_GH_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
_CONTENT_SIZE_CAP = 100_000  # 100 KB hard cap for read_repo_file

_REPO_RE = re.compile(r"^[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+$")


# ---------------------------------------------------------------------------
# Typed exceptions
# ---------------------------------------------------------------------------


class GithubAppNotInstalled(Exception):
    """No active installation found for the owner (org or user)."""


class GithubPermissionDenied(Exception):
    """GitHub returned 403 — the App installation lacks contents:read.

    The org admin must grant the permission at:
      https://github.com/settings/apps/<app-slug>
    → Permissions & events → Repository permissions → Contents → Read-only.
    Then each installation must approve the updated permissions.
    """


# ---------------------------------------------------------------------------
# Internal: resolve an installation token for an owner (org or user)
# ---------------------------------------------------------------------------


async def _resolve_installation_token(
    session: AsyncSession,
    owner: str,
) -> str:
    """Return an installation token for *owner*, trying org then user installation.

    1. Try `get_installation_token_for_org(session, owner)` — covers orgs that
       have the App installed via the organizations endpoint.
    2. If that returns None, fall back to a USER installation:
       - Mint an App JWT.
       - GET https://api.github.com/users/{owner}/installation to get installation_id.
       - Call `get_installation_token(installation_id)`.

    Returns the token string on success.
    Raises GithubAppNotInstalled if neither path finds an installation.
    """
    # --- Path 1: org installation (DB-cached + GitHub reconciliation) ----------
    token = await get_installation_token_for_org(session, owner)
    if token is not None:
        log.debug("github_contents.resolve_token.org", owner=owner)
        return token

    # --- Path 2: user installation (personal repos / Claude Code) -------------
    log.debug("github_contents.resolve_token.user_fallback", owner=owner)
    app_jwt = mint_app_jwt()
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(
            f"https://api.github.com/users/{owner}/installation",
            headers={"Authorization": f"Bearer {app_jwt}", **_GH_HEADERS},
        )

    if r.status_code == 404:
        raise GithubAppNotInstalled(
            f"GitHub App is not installed on '{owner}'. "
            "Ask an org/account admin to install the xbrain GitHub App."
        )
    r.raise_for_status()
    installation_id = int(r.json()["id"])
    token = await get_installation_token(installation_id)
    log.debug(
        "github_contents.resolve_token.user_installation",
        owner=owner,
        installation_id=installation_id,
    )
    return token


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def list_repo_files(
    session: AsyncSession,
    repo: str,
    path: str = "",
    ref: str = "HEAD",
) -> list[dict]:
    """List files/dirs at *path* in *repo* at *ref*.

    Args:
        session: AsyncSession (passed to token resolver).
        repo:    "owner/name" — raises ValueError for invalid format.
        path:    Relative path inside the repo (default: repo root).
        ref:     Git ref (branch, tag, SHA). Default "HEAD".

    Returns:
        List of dicts with keys: name, path, type, size, sha.
        Returns [] for 404 (path doesn't exist, or repo doesn't exist).

    Raises:
        ValueError:             if *repo* is not "owner/name".
        GithubAppNotInstalled:  if the App is not installed on the owner.
        GithubPermissionDenied: if GitHub returns 403 (missing contents:read).
    """
    if not _REPO_RE.match(repo):
        raise ValueError(
            f"Invalid repo format '{repo}' — expected 'owner/name' "
            "(e.g. 'myorg/myrepo')."
        )
    owner, name = repo.split("/", 1)
    token = await _resolve_installation_token(session, owner)

    url = f"https://api.github.com/repos/{owner}/{name}/contents/{path.lstrip('/')}"
    params: dict[str, str] = {}
    if ref and ref != "HEAD":
        params["ref"] = ref

    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(
            url,
            headers={"Authorization": f"Bearer {token}", **_GH_HEADERS},
            params=params or None,
        )

    if r.status_code == 404:
        log.debug("github_contents.list.not_found", repo=repo, path=path, ref=ref)
        return []

    if r.status_code == 403:
        raise GithubPermissionDenied(
            f"GitHub App lacks 'contents:read' permission on '{repo}'. "
            "The org admin must grant it: GitHub App settings → "
            "Permissions & events → Repository permissions → Contents → Read-only, "
            "then each installation must approve the updated permissions."
        )

    r.raise_for_status()
    data = r.json()

    # GitHub returns a list for directories, a single dict for a file path.
    if isinstance(data, dict):
        data = [data]

    entries = [
        {
            "name": item.get("name"),
            "path": item.get("path"),
            "type": item.get("type"),   # "file" | "dir" | "submodule" | "symlink"
            "size": item.get("size"),
            "sha": item.get("sha"),
        }
        for item in data
    ]
    log.debug(
        "github_contents.list.ok",
        repo=repo,
        path=path,
        ref=ref,
        count=len(entries),
    )
    return entries


async def read_repo_file(
    session: AsyncSession,
    repo: str,
    path: str,
    ref: str = "HEAD",
) -> dict:
    """Read the text content of a single file from *repo*.

    Args:
        session: AsyncSession.
        repo:    "owner/name".
        path:    Path to the file inside the repo.
        ref:     Git ref. Default "HEAD".

    Returns:
        Dict with keys: repo, path, ref, size, truncated (bool), content (str).

    Raises:
        ValueError:             Invalid *repo* format, or the path is a directory
                                (use list_repo_files for dirs).
        GithubAppNotInstalled:  App not installed on owner.
        GithubPermissionDenied: GitHub 403 (missing contents:read).
    """
    if not _REPO_RE.match(repo):
        raise ValueError(
            f"Invalid repo format '{repo}' — expected 'owner/name'."
        )
    owner, name = repo.split("/", 1)
    token = await _resolve_installation_token(session, owner)

    url = f"https://api.github.com/repos/{owner}/{name}/contents/{path.lstrip('/')}"
    params: dict[str, str] = {}
    if ref and ref != "HEAD":
        params["ref"] = ref

    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(
            url,
            headers={"Authorization": f"Bearer {token}", **_GH_HEADERS},
            params=params or None,
        )

    if r.status_code == 404:
        # Treat 404 as "file not found" rather than empty — callers expect content.
        raise ValueError(f"Path '{path}' not found in {repo}@{ref}")

    if r.status_code == 403:
        raise GithubPermissionDenied(
            f"GitHub App lacks 'contents:read' permission on '{repo}'. "
            "The org admin must grant it: GitHub App settings → "
            "Permissions & events → Repository permissions → Contents → Read-only."
        )

    r.raise_for_status()
    item = r.json()

    if item.get("type") == "dir":
        raise ValueError(
            f"Path '{path}' is a directory — use github_list_files to browse it."
        )

    # GitHub encodes file content as base64 with embedded newlines.
    raw_b64: str = (item.get("content") or "").replace("\n", "")
    raw_bytes = base64.b64decode(raw_b64)
    size = len(raw_bytes)
    truncated = size > _CONTENT_SIZE_CAP

    chunk = raw_bytes[:_CONTENT_SIZE_CAP]
    try:
        content = chunk.decode("utf-8")
    except UnicodeDecodeError:
        content = chunk.decode("latin-1")

    log.debug(
        "github_contents.read.ok",
        repo=repo,
        path=path,
        ref=ref,
        size=size,
        truncated=truncated,
    )
    return {
        "repo": repo,
        "path": path,
        "ref": ref,
        "size": size,
        "truncated": truncated,
        "content": content,
    }
