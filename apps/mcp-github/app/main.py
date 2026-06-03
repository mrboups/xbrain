"""mcp-github — MCP sidecar exposing GitHub repo read + sync tools.

Port 8107. Connected DIRECTLY by LibreChat (like mcp-brain) so it receives
``X-LibreChat-User-Email`` + ``X-Internal-Secret`` and can resolve the caller's
real team — this is what makes ``github_sync_repo`` land in the right brain.

Auth (mirrors mcp-brain ``_resolve``):
  - TOKEN PATH (public): ``Authorization: Bearer xbt_...`` → validated via /v1/me.
  - EMAIL PATH (LibreChat internal): ``X-LibreChat-User-Email`` + ``X-Internal-Secret``
    matching ``BRIDGE_SHARED_SECRET`` → team resolved server-side. Fail-closed.

Reads (``github_list_files`` / ``github_read_file``) are team-neutral — they only
need an authenticated caller. ``github_sync_repo`` needs the resolved team_scope.

The GitHub App private key + installation/fallback-token machinery lives in
memory-api (centralized). This sidecar holds NO GitHub secrets — it mints a
bridge JWT and proxies to memory-api /v1/internal/github/{list,read,sync}.
"""
from __future__ import annotations

import hmac
import json

import httpx
import structlog
from mcp.server.fastmcp import Context, FastMCP

from app import memory_client
from app.bridge_jwt import mint_bridge_jwt
from app.config import settings

log = structlog.get_logger(__name__)
mcp = FastMCP("xbrain-github", host=settings.FASTMCP_HOST, port=settings.FASTMCP_PORT)


# ---------------------------------------------------------------------------
# Auth resolution (mirrors mcp-brain)
# ---------------------------------------------------------------------------


async def _verify_caller(ctx: Context) -> tuple[str, str | None]:
    """Verify the caller and return ``(bearer, email_or_None)``.

    ``bearer`` is an ``xbt_`` token (token path) OR a bridge JWT scoped to
    "default" (email path) — usable for the team-neutral read endpoints.
    ``email`` is the LibreChat user email on the email path, else None.

    Raises ValueError (fail-closed) on any auth failure.
    """
    headers = ctx.request_context.request.headers
    authorization = headers.get("authorization") or ""
    email = headers.get("x-librechat-user-email") or ""
    x_internal_secret = headers.get("x-internal-secret") or ""
    raw = (
        authorization.removeprefix("Bearer ").strip()
        if authorization.startswith("Bearer ")
        else ""
    )

    if raw.startswith("xbt_"):
        return raw, None

    secret = settings.BRIDGE_SHARED_SECRET
    ok = (
        settings.INTERNAL_EMAIL_PATH_ENABLED
        and bool(secret)
        and bool(x_internal_secret)
        and hmac.compare_digest(x_internal_secret, secret)
        and bool(email)
    )
    if not ok:
        raise ValueError(
            "Authentication required: call from an authorized frontend "
            "or set an xbt_ token."
        )
    return mint_bridge_jwt(secret=secret, team_scope="default", sub=f"email:{email}"), email


async def _resolve_team(ctx: Context) -> tuple[str, str]:
    """Return ``(bearer_with_team, team_scope)`` — REQUIRES a team. For sync.

    Resolves the caller's primary team via memory-api so synced content lands
    in the right brain. Raises ValueError if the caller has no team.
    """
    bearer, email = await _verify_caller(ctx)

    # Token path — team_scope comes from the token's /v1/me.
    if email is None:
        me = await memory_client.get_me(bearer)
        team_scope = me.get("api_token_team_scope") or me.get("team_scope")
        if not team_scope:
            raise ValueError("Your token is not associated with a team.")
        return bearer, team_scope

    # Email path — resolve via the internal endpoint (try prefixed then bare).
    secret = settings.BRIDGE_SHARED_SECRET
    sub = f"email:{email}"
    team_scope = await memory_client.resolve_team_scope_internal(bearer, sub)
    if not team_scope:
        team_scope = await memory_client.resolve_team_scope_internal(bearer, email)
    if not team_scope:
        raise ValueError(
            "No team is associated with your account yet — sign in to a team first."
        )
    jwt1 = mint_bridge_jwt(secret=secret, team_scope=team_scope, sub=sub)
    log.info("mcp_github.resolve", team=team_scope)
    return jwt1, team_scope


def _extract_detail(response: httpx.Response) -> str:
    """Extract a human-readable detail string from an error response."""
    try:
        body = response.json()
        return body.get("detail") or body.get("message") or str(body)
    except Exception:
        return response.text[:300] or f"HTTP {response.status_code}"


def _perm_error(repo: str, detail: str) -> str:
    return (
        f"ERROR 403 — GitHub App lacks 'contents:read' on '{repo}' (and no fallback "
        f"token can read it). Install the xbrain GitHub App on the owner + grant "
        f"Contents:Read-only, or configure a fallback token. Detail: {detail}"
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def github_list_files(
    repo: str,
    path: str = "",
    ref: str = "HEAD",
    ctx: Context = None,
) -> str:
    """List files and directories in a GitHub repository.

    Returns the directory listing at *path* inside *repo* at the given git *ref*.

    Args:
        repo: Repository in "owner/name" format (e.g. "myorg/myrepo").
        path: Subdirectory path within the repo (default: repository root).
        ref:  Git ref — branch name, tag, or commit SHA (default: "HEAD").

    Returns:
        JSON array of entry objects with keys: name, path, type, size, sha.
        Returns "[]" when the path does not exist. Returns a plain-text error
        message (not an exception) on failure so the LLM can act on it.
    """
    try:
        bearer, _ = await _verify_caller(ctx)
    except ValueError as exc:
        return f"ERROR: {exc}"

    url = f"{settings.MEMORY_API_URL}/v1/internal/github/list"
    params = {"repo": repo, "path": path, "ref": ref}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(url, params=params, headers={"Authorization": f"Bearer {bearer}"})
        if r.status_code == 200:
            return json.dumps(r.json().get("entries", []), ensure_ascii=False)
        if r.status_code == 403:
            return _perm_error(repo, _extract_detail(r))
        if r.status_code == 404:
            return f"ERROR 404 — {_extract_detail(r)}"
        return f"ERROR {r.status_code} — {_extract_detail(r)}"
    except Exception as exc:
        log.error("mcp_github.list_files.error", repo=repo, err=str(exc))
        return f"ERROR: Failed to list files in '{repo}': {exc}"


@mcp.tool()
async def github_read_file(
    repo: str,
    path: str,
    ref: str = "HEAD",
    ctx: Context = None,
) -> str:
    """Read the text content of a file in a GitHub repository.

    Args:
        repo: Repository in "owner/name" format (e.g. "myorg/myrepo").
        path: Path to the file within the repo (e.g. "src/main.py").
        ref:  Git ref — branch name, tag, or commit SHA (default: "HEAD").

    Returns:
        The file's text content (capped at 100 KB). Returns a plain-text error
        message on failure so the LLM can act on it.
    """
    try:
        bearer, _ = await _verify_caller(ctx)
    except ValueError as exc:
        return f"ERROR: {exc}"

    url = f"{settings.MEMORY_API_URL}/v1/internal/github/read"
    params = {"repo": repo, "path": path, "ref": ref}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(url, params=params, headers={"Authorization": f"Bearer {bearer}"})
        if r.status_code == 200:
            data = r.json()
            content = data.get("content", "")
            if data.get("truncated"):
                content += f"\n\n[TRUNCATED: file is {data.get('size', '?')} bytes, first 100 KB shown]"
            return content
        if r.status_code == 403:
            return _perm_error(repo, _extract_detail(r))
        if r.status_code in (400, 404):
            return f"ERROR {r.status_code} — {_extract_detail(r)}"
        return f"ERROR {r.status_code} — {_extract_detail(r)}"
    except Exception as exc:
        log.error("mcp_github.read_file.error", repo=repo, path=path, err=str(exc))
        return f"ERROR: Failed to read '{path}' from '{repo}': {exc}"


@mcp.tool()
async def github_sync_repo(
    repo: str,
    project_scope: str = "",
    ctx: Context = None,
) -> str:
    """Index a GitHub repository's files into YOUR team's brain (recall/search).

    Walks the repo, chunks its text/code/doc files, and stores them as team
    memory so you can later ask questions and get answers grounded in the repo.
    The team is resolved automatically from your signed-in account.

    Args:
        repo:          Repository in "owner/name" format (e.g. "myorg/myrepo").
        project_scope: Optional label to scope recall to this repo
                       (defaults to the repo name).

    Returns:
        A confirmation that the sync started, or a clear error message.
    """
    try:
        bearer, team_scope = await _resolve_team(ctx)
    except ValueError as exc:
        return f"ERROR: {exc}"

    url = f"{settings.MEMORY_API_URL}/v1/internal/github/sync"
    body = {"repo": repo, "team_scope": team_scope}
    if project_scope:
        body["project_scope"] = project_scope
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(url, json=body, headers={"Authorization": f"Bearer {bearer}"})
        if r.status_code == 202:
            return (
                f"Sync started for {repo} into team '{team_scope}'. "
                "Indexing runs in the background — ask me about the repo in a moment "
                "and recall/memory_search will use its files."
            )
        if r.status_code == 403:
            return _perm_error(repo, _extract_detail(r))
        if r.status_code in (400, 404):
            return f"ERROR {r.status_code} — {_extract_detail(r)}"
        return f"ERROR {r.status_code} — {_extract_detail(r)}"
    except Exception as exc:
        log.error("mcp_github.sync_repo.error", repo=repo, err=str(exc))
        return f"ERROR: Failed to start sync for '{repo}': {exc}"


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
