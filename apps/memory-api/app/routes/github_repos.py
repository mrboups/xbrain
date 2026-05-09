"""GET /v1/github/repos — proxy to LibreChat /api/xbrain/github-repos."""

from typing import Any

import httpx
import structlog
from fastapi import APIRouter, HTTPException, Request

from app.config import settings

log = structlog.get_logger(__name__)
router = APIRouter()


@router.get("/github/repos")
async def list_github_repos(request: Request) -> dict[str, Any]:
    """Proxy the caller's Bearer token to LibreChat /api/xbrain/github-repos."""
    librechat_url = getattr(settings, "LIBRECHAT_INTERNAL_URL", "") or ""
    if not librechat_url:
        log.warning("github_repos.proxy_disabled", reason="LIBRECHAT_INTERNAL_URL not set")
        raise HTTPException(503, "github_repos proxy disabled (LIBRECHAT_INTERNAL_URL not configured)")

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(401, "Bearer token required (LibreChat session JWT)")

    upstream_url = f"{librechat_url.rstrip('/')}/api/xbrain/github-repos"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                upstream_url,
                headers={
                    "Authorization": auth_header,
                    "Accept": "application/json",
                    "User-Agent": "xbrain-memory-api-proxy",
                },
            )
    except httpx.RequestError as exc:
        log.error("github_repos.upstream_unreachable", error=str(exc))
        raise HTTPException(502, "LibreChat upstream unreachable")

    if resp.status_code == 401:
        raise HTTPException(401, "GitHub not connected or LibreChat session expired")
    if resp.status_code == 403:
        raise HTTPException(403, "Forbidden upstream")
    if not resp.is_success:
        log.warning("github_repos.upstream_error", status=resp.status_code, body=resp.text[:200])
        raise HTTPException(resp.status_code, f"LibreChat proxy error: {resp.status_code}")

    try:
        return resp.json()
    except ValueError:
        log.error("github_repos.upstream_not_json", body=resp.text[:200])
        raise HTTPException(502, "LibreChat upstream returned non-JSON")
