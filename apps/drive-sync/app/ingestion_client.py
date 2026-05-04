"""HTTP client for agent-runtime ingestion endpoint.

drive-sync -> POST /v1/agents/ingest -> ingestion LangGraph graph (extract + HITL).
Auth: bridge JWT (short-lived, scope=bridge).
"""
from __future__ import annotations

import datetime
import time
import structlog
import httpx
from authlib.jose import jwt as jose_jwt
from app.config import settings

log = structlog.get_logger(__name__)


def _make_bridge_jwt() -> str:
    """Generate a short-lived bridge service JWT."""
    payload = {
        "sub": "drive-sync",
        "scope": "bridge",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    return jose_jwt.encode(
        {"alg": settings.JWT_ALGORITHM},
        payload,
        settings.BRIDGE_SHARED_SECRET,
    ).decode()


async def send_to_ingestion_agent(
    text: str,
    file_id: str,
    file_name: str,
    team_scope: str,
    project_scope: str | None = None,
) -> str | None:
    """Send extracted text to agent-runtime for ingestion.

    Returns the thread_id of the created ingestion job, or None on error.

    The ingestion agent (plan 02-07) runs extract_facts() + HITL gate and
    calls memory-api /v1/memory/upsert with the extracted facts.
    source will be set to "drive:{file_id}" by the caller convention.
    """
    token = _make_bridge_jwt()
    source = f"drive:{file_id}"
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{settings.AGENT_RUNTIME_URL}/v1/agents/ingest",
                json={
                    "document_text": text,
                    "source_url": source,
                    "team_scope": team_scope,
                    "project_scope": project_scope,
                    "metadata": {
                        "drive_file_id": file_id,
                        "drive_file_name": file_name,
                    },
                },
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Team-Scope": team_scope,
                },
            )
        if resp.status_code in (200, 201, 202):
            data = resp.json()
            thread_id = data.get("thread_id")
            log.info("ingestion.submitted", file_id=file_id, thread_id=thread_id)
            return thread_id
        else:
            log.error(
                "ingestion.error",
                file_id=file_id,
                status=resp.status_code,
                body=resp.text[:200],
            )
            return None
    except Exception as exc:
        log.error("ingestion.exception", file_id=file_id, error=str(exc))
        return None


async def soft_archive_drive_file(file_id: str, team_scope: str) -> None:
    """Call memory-api to soft-archive all facts from a deleted Drive file.

    Facts at WORKING+ are archived (validation_status='archived').
    Facts at EPHEMERAL are hard-deleted.
    Per RESEARCH.md Q4 deletion strategy.
    """
    token = _make_bridge_jwt()
    source = f"drive:{file_id}"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Search for all facts from this source
            resp = await client.get(
                f"{settings.MEMORY_API_URL}/v1/memory/search",
                params={"q": source, "limit": 100},
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Team-Scope": team_scope,
                },
            )
            if resp.status_code != 200:
                log.error("archive.search_failed", file_id=file_id, status=resp.status_code)
                return
            facts = resp.json()
            for fact in facts:
                if fact.get("source") != source:
                    continue
                truth_level = fact.get("truth_level", "EPHEMERAL")
                if truth_level == "EPHEMERAL":
                    # Hard delete — never promoted, no audit trail needed
                    await client.delete(
                        f"{settings.MEMORY_API_URL}/v1/memory/{fact['id']}",
                        headers={
                            "Authorization": f"Bearer {token}",
                            "X-Team-Scope": team_scope,
                        },
                    )
                else:
                    # Soft archive — WORKING+ must never disappear silently
                    now_str = datetime.datetime.utcnow().isoformat() + "Z"
                    await client.patch(
                        f"{settings.MEMORY_API_URL}/v1/memory/{fact['id']}",
                        json={
                            "patch": {
                                "validation_status": "archived",
                                "metadata": {
                                    "archived_reason": "source_drive_file_deleted",
                                    "archived_at": now_str,
                                },
                            }
                        },
                        headers={
                            "Authorization": f"Bearer {token}",
                            "X-Team-Scope": team_scope,
                        },
                    )
            log.info("archive.done", file_id=file_id, count=len(facts))
    except Exception as exc:
        log.error("archive.exception", file_id=file_id, error=str(exc))
