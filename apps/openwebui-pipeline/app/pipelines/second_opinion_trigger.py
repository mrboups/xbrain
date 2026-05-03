"""second-opinion-trigger pipeline — `/second-opinion <prompt>` slash command.

Forwards the prompt to agent-runtime's `second-opinion` agent and returns the
formatted Claude+Grok markdown comparison straight back to the user. No
memory writes happen — this is a read-only comparison helper.
"""

from __future__ import annotations

import time
from typing import Optional

import httpx
import structlog
from authlib.jose import jwt

from app.config import settings

log = structlog.get_logger()

_CMD = "/second-opinion"


def _agent_jwt(*, user_sub: str, team_scope: str, ttl: int = 300) -> str:
    now = int(time.time())
    return jwt.encode(
        {"alg": settings.JWT_ALGORITHM},
        {
            "iss": "openwebui-pipeline",
            "sub": user_sub,
            "team_scope": team_scope,
            "scope": "bridge",
            "iat": now,
            "exp": now + ttl,
        },
        settings.BRIDGE_SHARED_SECRET,
    ).decode("ascii")


async def try_handle(
    *,
    user_message: str,
    user_sub: str,
    team_scope: str,
) -> Optional[str]:
    stripped = user_message.strip()
    if not stripped.startswith(_CMD):
        return None
    prompt = stripped[len(_CMD):].strip()
    if not prompt:
        return f"Usage: `{_CMD} <prompt>`"

    base = settings.AGENT_RUNTIME_URL.rstrip("/")
    token = _agent_jwt(user_sub=user_sub, team_scope=team_scope)
    try:
        async with httpx.AsyncClient(timeout=120.0) as c:
            r = await c.post(
                f"{base}/v1/agents/second-opinion/run",
                headers={"Authorization": f"Bearer {token}", "X-Team-Scope": team_scope},
                json={"initial_state": {"prompt": prompt}},
            )
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPStatusError as e:
        try:
            detail = e.response.json().get("detail", e.response.text[:200])
        except Exception:
            detail = e.response.text[:200]
        log.warning("second_opinion_http_error", status=e.response.status_code)
        return f"❌ agent-runtime {e.response.status_code}: {detail}"
    except httpx.HTTPError as e:
        log.warning("second_opinion_network_error", err=str(e))
        return f"❌ Could not reach agent-runtime: {e}"

    md = (data.get("state") or {}).get("final_markdown")
    if not md:
        return "❌ agent-runtime returned no final_markdown — check service logs."
    return md
