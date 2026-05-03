"""ingestion-trigger pipeline — slash commands for the ingestion agent.

Supported commands (must be the first non-whitespace token of the *last user* message):

    /ingest <url>
        Start a new ingestion thread on the given URL. Returns thread_id +
        the list of extracted facts (numbered) waiting for approval.

    /approve-thread <thread_id> <indices>
        Resume the thread, approving only the listed fact indices.
        `indices` is a comma-separated list (e.g. `0,2,5`) or `all`.

    /reject-thread <thread_id>
        Resume with empty approval — write nothing, close the thread.

If the command is recognized, returns assistant content. Otherwise returns
None and main.py routes the message to the LLM as usual.
"""

from __future__ import annotations

import re
import time
from typing import Any, Optional

import httpx
import structlog
from authlib.jose import jwt

from app.config import settings

log = structlog.get_logger()

_TOKEN_RE = re.compile(r"\s+")


def _agent_jwt(*, user_sub: str, team_scope: str, ttl: int = 300) -> str:
    """JWT recognized by agent-runtime (HS256, scope=bridge)."""
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


async def _agent_post(
    path: str, *, user_sub: str, team_scope: str, body: dict[str, Any]
) -> dict[str, Any]:
    base = settings.AGENT_RUNTIME_URL.rstrip("/")
    token = _agent_jwt(user_sub=user_sub, team_scope=team_scope)
    async with httpx.AsyncClient(timeout=120.0) as c:
        r = await c.post(
            f"{base}{path}",
            headers={"Authorization": f"Bearer {token}", "X-Team-Scope": team_scope},
            json=body,
        )
        r.raise_for_status()
        return r.json()


def _parse_indices(raw: str, fact_count: int) -> list[int]:
    if raw.strip().lower() == "all":
        return list(range(fact_count))
    out: list[int] = []
    for tok in raw.split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            out.append(int(tok))
        except ValueError:
            continue
    return out


def _format_extracted_facts(thread_id: str, facts: list[dict]) -> str:
    if not facts:
        return (
            f"📭 Thread `{thread_id[:8]}…` opened but no facts could be extracted "
            f"from this source. Use `/reject-thread {thread_id}` to close it."
        )
    lines = [
        f"📥 Thread `{thread_id[:8]}…` — **{len(facts)} fact(s) extracted**, "
        f"awaiting your approval:",
        "",
    ]
    for i, f in enumerate(facts):
        lvl = f.get("suggested_truth_level", "WORKING")
        conf = f.get("confidence", 0.7)
        content = f.get("content", "")
        if len(content) > 140:
            content = content[:137] + "…"
        lines.append(f"`{i}` — _{lvl}_ (conf={conf:.2f}): {content}")
    lines.extend(
        [
            "",
            f"Approve all: `/approve-thread {thread_id} all`",
            f"Approve some: `/approve-thread {thread_id} 0,2,5`",
            f"Reject: `/reject-thread {thread_id}`",
        ]
    )
    return "\n".join(lines)


async def try_handle(
    *,
    user_message: str,
    user_sub: str,
    team_scope: str,
) -> Optional[str]:
    stripped = user_message.strip()
    if not stripped.startswith("/"):
        return None
    parts = _TOKEN_RE.split(stripped, maxsplit=2)
    cmd = parts[0].lower()

    try:
        if cmd == "/ingest":
            if len(parts) < 2:
                return "Usage: `/ingest <url>`"
            url = parts[1].strip()
            if not (url.startswith("http://") or url.startswith("https://")):
                return f"❌ Invalid URL `{url}` — must start with http:// or https://"
            run = await _agent_post(
                "/v1/agents/ingestion/run",
                user_sub=user_sub,
                team_scope=team_scope,
                body={
                    "initial_state": {
                        "source_type": "url",
                        "source_url": url,
                        "team_scope": team_scope,
                        "sub": user_sub,
                    }
                },
            )
            facts = (run.get("state") or {}).get("extracted_facts") or []
            return _format_extracted_facts(run["thread_id"], facts)

        if cmd == "/approve-thread":
            if len(parts) < 3:
                return "Usage: `/approve-thread <thread_id> <indices|all>`"
            thread_id, raw = parts[1].strip(), parts[2].strip()
            # We don't know fact_count here, so resolve indices in two phases:
            # /resume sees the persisted state and ignores out-of-range indexes.
            indices = _parse_indices(raw, fact_count=10_000)
            res = await _agent_post(
                f"/v1/agents/{thread_id}/resume",
                user_sub=user_sub,
                team_scope=team_scope,
                body={
                    "agent_name": "ingestion",
                    "state_update": {"approved_indexes": indices},
                },
            )
            written = (res.get("state") or {}).get("written_ids") or []
            skipped = (res.get("state") or {}).get("skipped_count", 0)
            return (
                f"✅ Thread `{thread_id[:8]}…` resolved — **{len(written)} fact(s)** "
                f"written to memory ({skipped} skipped). New items have "
                f"validation_status='pending' until promotion (4-eyes for CANONICAL)."
            )

        if cmd == "/reject-thread":
            if len(parts) < 2:
                return "Usage: `/reject-thread <thread_id>`"
            thread_id = parts[1].strip()
            await _agent_post(
                f"/v1/agents/{thread_id}/resume",
                user_sub=user_sub,
                team_scope=team_scope,
                body={
                    "agent_name": "ingestion",
                    "state_update": {"approved_indexes": []},
                },
            )
            return f"🚫 Thread `{thread_id[:8]}…` rejected — no facts written."

    except httpx.HTTPStatusError as e:
        try:
            detail = e.response.json().get("detail", e.response.text[:200])
        except Exception:
            detail = e.response.text[:200]
        log.warning("ingestion_http_error", cmd=cmd, status=e.response.status_code)
        return f"❌ agent-runtime {e.response.status_code}: {detail}"
    except httpx.HTTPError as e:
        log.warning("ingestion_network_error", cmd=cmd, err=str(e))
        return (
            "⚠️ **Cette commande dépend de `agent-runtime` (xbrain Phase 2) "
            "qui n'est pas encore déployé.**\n\n"
            "`/ingest`, `/approve-thread`, `/reject-thread` seront actifs "
            "une fois la stack Phase 2 ship'd.\n\n"
            "En attendant, envoie un message normal pour parler à l'LLM."
        )

    return None
