"""mcp-github — MCP sidecar exposing GitHub repo read tools.

Port 8107. Bridge-JWT auth path only: this sidecar mints a bridge JWT and
proxies requests to memory-api /v1/internal/github/{list,read}.

The GitHub App private key and installation-token machinery lives in memory-api
(centralized). This sidecar is a thin proxy — it holds NO GitHub secrets.

Tools exposed (LLM-facing):
  github_list_files(repo, path="", ref="HEAD") -> JSON list of {name, path, type, size, sha}
  github_read_file(repo, path, ref="HEAD") -> file text (up to 100 KB)
"""
from __future__ import annotations

import json

import httpx
import structlog
from mcp.server.fastmcp import FastMCP

from app.bridge_jwt import mint_bridge_jwt
from app.config import settings

log = structlog.get_logger(__name__)
mcp = FastMCP("xbrain-github", host=settings.FASTMCP_HOST, port=settings.FASTMCP_PORT)


def _auth_headers() -> dict[str, str]:
    """Mint a fresh bridge JWT and return the Authorization header dict."""
    secret = settings.BRIDGE_SHARED_SECRET
    if not secret:
        raise RuntimeError(
            "BRIDGE_SHARED_SECRET is not set — cannot authenticate with memory-api."
        )
    jwt = mint_bridge_jwt(secret=secret)
    return {"Authorization": f"Bearer {jwt}"}


@mcp.tool()
async def github_list_files(
    repo: str,
    path: str = "",
    ref: str = "HEAD",
) -> str:
    """List files and directories in a GitHub repository.

    Returns the directory listing at *path* inside *repo* at the given git *ref*.
    The GitHub App must be installed on the repository's owner (org or user account)
    with at least 'contents:read' permission.

    Args:
        repo: Repository in "owner/name" format (e.g. "myorg/myrepo").
        path: Subdirectory path within the repo (default: repository root).
        ref:  Git ref — branch name, tag, or commit SHA (default: "HEAD").

    Returns:
        JSON array of entry objects with keys: name, path, type, size, sha.
        Returns an empty array "[]" when the path does not exist.
        Returns a plain-text error message (not an exception) on failure so the
        LLM can act on it — especially the 403 "grant contents:read" message.

    Example:
        github_list_files("myorg/myrepo") → '[{"name":"README.md","path":"README.md",...}]'
        github_list_files("myorg/myrepo", "src", ref="main")
    """
    url = f"{settings.MEMORY_API_URL}/v1/internal/github/list"
    params: dict[str, str] = {"repo": repo, "path": path, "ref": ref}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(url, params=params, headers=_auth_headers())
        if r.status_code == 200:
            data = r.json()
            return json.dumps(data.get("entries", []), ensure_ascii=False)
        if r.status_code == 403:
            detail = _extract_detail(r)
            return (
                f"ERROR 403 — GitHub App lacks 'contents:read' on '{repo}'. "
                f"The org admin must grant the permission in GitHub App settings → "
                f"Permissions & events → Repository permissions → Contents → Read-only, "
                f"then approve the updated permissions on the installation. "
                f"Detail: {detail}"
            )
        if r.status_code == 404:
            detail = _extract_detail(r)
            return f"ERROR 404 — {detail}"
        return f"ERROR {r.status_code} — {_extract_detail(r)}"
    except RuntimeError as exc:
        return f"ERROR: {exc}"
    except Exception as exc:
        log.error("mcp_github.list_files.error", repo=repo, err=str(exc))
        return f"ERROR: Failed to list files in '{repo}': {exc}"


@mcp.tool()
async def github_read_file(
    repo: str,
    path: str,
    ref: str = "HEAD",
) -> str:
    """Read the text content of a file in a GitHub repository.

    Fetches and returns the decoded text of *path* from *repo* at the given
    git *ref*. Content is capped at 100 KB; if the file is larger, the content
    is truncated and the response includes a notice at the end.

    The GitHub App must be installed on the repository's owner with at least
    'contents:read' permission.

    Args:
        repo: Repository in "owner/name" format (e.g. "myorg/myrepo").
        path: Path to the file within the repo (e.g. "src/main.py").
        ref:  Git ref — branch name, tag, or commit SHA (default: "HEAD").

    Returns:
        The file's text content as a string.
        Returns a plain-text error message (not an exception) on failure so
        the LLM can act on it — especially the 403 "grant contents:read" message.

    Example:
        github_read_file("myorg/myrepo", "README.md")
        github_read_file("myorg/myrepo", "src/app.py", ref="develop")
    """
    url = f"{settings.MEMORY_API_URL}/v1/internal/github/read"
    params: dict[str, str] = {"repo": repo, "path": path, "ref": ref}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(url, params=params, headers=_auth_headers())
        if r.status_code == 200:
            data = r.json()
            content: str = data.get("content", "")
            if data.get("truncated"):
                content += (
                    f"\n\n[TRUNCATED: file is {data.get('size', '?')} bytes, "
                    f"only first 100 KB shown]"
                )
            return content
        if r.status_code == 403:
            detail = _extract_detail(r)
            return (
                f"ERROR 403 — GitHub App lacks 'contents:read' on '{repo}'. "
                f"The org admin must grant the permission in GitHub App settings → "
                f"Permissions & events → Repository permissions → Contents → Read-only, "
                f"then approve the updated permissions on the installation. "
                f"Detail: {detail}"
            )
        if r.status_code == 404:
            detail = _extract_detail(r)
            return f"ERROR 404 — {detail}"
        if r.status_code == 400:
            return f"ERROR 400 — {_extract_detail(r)}"
        return f"ERROR {r.status_code} — {_extract_detail(r)}"
    except RuntimeError as exc:
        return f"ERROR: {exc}"
    except Exception as exc:
        log.error("mcp_github.read_file.error", repo=repo, path=path, err=str(exc))
        return f"ERROR: Failed to read '{path}' from '{repo}': {exc}"


@mcp.tool()
async def github_sync_repo(
    repo: str,
    team_scope: str = "default",
    project_scope: str = "",
) -> str:
    """Index all text files from a GitHub repository into the team brain.

    Walks the repository tree and upserts every indexable text/code file as a
    searchable memory item scoped to *team_scope*. The operation runs in the
    background on memory-api — this call returns as soon as the sync is
    accepted (202). The brain becomes searchable shortly after.

    Hard caps (enforced server-side): MAX_FILES=200, MAX_CHUNKS_TOTAL=2000.
    Files larger than 100 KB or with binary/non-text extensions are skipped.

    The GitHub App must be installed on the repository's owner with at least
    'contents:read' permission.

    Args:
        repo:          Repository in "owner/name" format (e.g. "myorg/myrepo").
        team_scope:    Team slug the indexed content will be scoped to
                       (default: "default"). Use your actual team slug so recall
                       is properly scoped.
        project_scope: Optional project slug for fine-grained filtering.
                       Defaults to the repo name when left empty.

    Returns:
        Human-readable confirmation that the sync has started, or a plain-text
        error message so the LLM can act on it.

    Example:
        github_sync_repo("myorg/myrepo", team_scope="engineering")
        github_sync_repo("myorg/myrepo", team_scope="engineering", project_scope="backend")
    """
    url = f"{settings.MEMORY_API_URL}/v1/internal/github/sync"
    payload: dict = {"repo": repo, "team_scope": team_scope, "ref": "HEAD"}
    if project_scope:
        payload["project_scope"] = project_scope
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(url, json=payload, headers=_auth_headers())
        if r.status_code == 202:
            data = r.json()
            return (
                f"Sync started for {data.get('repo', repo)} "
                f"(ref: {data.get('ref', 'HEAD')}, team: {team_scope}). "
                "Ask me about it in a moment — the files will be searchable via memory_search."
            )
        if r.status_code == 403:
            detail = _extract_detail(r)
            return (
                f"ERROR 403 — GitHub App lacks 'contents:read' on '{repo}'. "
                "The org admin must grant it in GitHub App settings → "
                "Permissions & events → Repository permissions → Contents → Read-only. "
                f"Detail: {detail}"
            )
        if r.status_code == 404:
            detail = _extract_detail(r)
            return f"ERROR 404 — {detail}"
        if r.status_code == 400:
            return f"ERROR 400 — {_extract_detail(r)}"
        return f"ERROR {r.status_code} — {_extract_detail(r)}"
    except RuntimeError as exc:
        return f"ERROR: {exc}"
    except Exception as exc:
        log.error("mcp_github.sync_repo.error", repo=repo, err=str(exc))
        return f"ERROR: Failed to start sync for '{repo}': {exc}"


def _extract_detail(response: httpx.Response) -> str:
    """Extract a human-readable detail string from an error response."""
    try:
        body = response.json()
        return body.get("detail") or body.get("message") or str(body)
    except Exception:
        return response.text[:300] or f"HTTP {response.status_code}"


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
