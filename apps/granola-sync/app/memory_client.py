"""HTTP client posting structured Granola payloads to memory-api /v1/integrations/granola/ingest + agent invocation /v1/agents/{id}/invoke."""

import time
from typing import Any

import httpx
import structlog
from authlib.jose import jwt as jose_jwt

from app.config import settings

log = structlog.get_logger(__name__)


def _make_bridge_jwt() -> str:
    """Generate a short-lived bridge JWT for service-to-service auth."""
    payload = {
        "sub": "granola-sync",
        "scope": "bridge",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    return jose_jwt.encode(
        {"alg": settings.JWT_ALGORITHM},
        payload,
        settings.BRIDGE_SHARED_SECRET,
    ).decode()


async def post_ingest(team_scope: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    """POST a structured Granola payload to memory-api. Fail-soft (returns None on error)."""
    token = _make_bridge_jwt()
    url = f"{settings.MEMORY_API_URL}/v1/integrations/granola/ingest"
    body = {
        "team_scope": team_scope,
        **payload,  # contains "note" + "extracted"
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                url,
                json=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Team-Scope": team_scope,
                    "Content-Type": "application/json",
                },
            )
        if resp.status_code != 201:
            log.error(
                "ingest.non_201",
                team_scope=team_scope,
                status=resp.status_code,
                body=resp.text[:500],
            )
            return None
        return resp.json()
    except Exception as exc:
        log.error("ingest.exception", team_scope=team_scope, error=str(exc))
        return None


async def post_agent_invoke(
    agent_id: str,
    team_scope: str,
    content: str,
    source_ref: str | None = None,
    project_scope: str | None = None,
) -> dict[str, Any] | None:
    """POST /v1/agents/{agent_id}/invoke with bridge JWT. Fail-soft (returns None on error).

    Used by granola-sync to auto-trigger the meeting-recap agent after ingesting
    a meeting (D5 RESEARCH.md). Pitfall 1: bridge JWT carries no team_scope,
    we send team_scope in the body and the agents.py invoke handler reads it from there.
    """
    token = _make_bridge_jwt()
    url = f"{settings.MEMORY_API_URL}/v1/agents/{agent_id}/invoke"
    body = {
        "team_scope": team_scope,
        "content": content[:50000],  # cap content to match agents.py limit
        "source_ref": source_ref,
        "project_scope": project_scope,
    }
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                url,
                json=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
        if resp.status_code != 200:
            log.error(
                "agent_invoke.non_200",
                agent_id=agent_id,
                team_scope=team_scope,
                status=resp.status_code,
                body=resp.text[:500],
            )
            return None
        return resp.json()
    except Exception as exc:
        log.error(
            "agent_invoke.exception",
            agent_id=agent_id,
            team_scope=team_scope,
            error=str(exc),
        )
        return None
