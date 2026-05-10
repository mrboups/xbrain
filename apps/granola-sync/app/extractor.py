"""Extract structured (participants, action_items, decisions, project_scope) from a Granola note summary using Claude."""

import json
from typing import Any

import structlog
from anthropic import AsyncAnthropic

from app.config import settings

log = structlog.get_logger(__name__)

SYSTEM_PROMPT = """You extract structured information from meeting note summaries.

Given a summary, return STRICT JSON (no markdown, no prose) with this shape:
{
  "participants": [{"name": "string", "email": "string-or-null"}],
  "action_items": [{"title": "string", "description": "string-or-null", "assignee_email": "string-or-null"}],
  "decisions": ["string", ...],
  "project_scope": "string-or-null"
}

Rules:
- "participants": every distinct person mentioned (presenters, attendees, mentioned-by-name).
- "action_items": every sentence expressing a task, commitment, or follow-up. Title is concise (<80 chars).
- "decisions": explicit choices made by the team (e.g. "We will use option B").
- "project_scope": if a project/initiative name is consistently referenced, return its slug. Otherwise null.
- If no items in a category, return an empty list (or null for project_scope).
- Output ONLY the JSON object, no surrounding text or markdown fences."""


_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        if not settings.ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY not set in granola-sync env")
        _client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _client


async def extract_from_summary(
    summary_text: str,
    fallback_attendees: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Call Claude to extract participants/action_items/decisions/project_scope.

    Returns a dict in the shape expected by memory-api ingest:
    {participants, action_items, decisions, project_scope}.

    Fail-soft: returns minimal structure (with fallback_attendees as participants) on any error.
    """
    fallback = {
        "participants": fallback_attendees or [],
        "action_items": [],
        "decisions": [],
        "project_scope": None,
    }
    if not summary_text or not summary_text.strip():
        return fallback

    try:
        client = _get_client()
        msg = await client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=2048,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": summary_text[:20000]}],  # cap input (T-07-05-10)
        )
        text = msg.content[0].text if msg.content else ""
        # Strip optional markdown fence if Claude includes one despite instructions
        text = text.strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1].lstrip("json").strip()
        parsed = json.loads(text)
        # Sanity-check shape
        return {
            "participants": parsed.get("participants", []) or [],
            "action_items": parsed.get("action_items", []) or [],
            "decisions": parsed.get("decisions", []) or [],
            "project_scope": parsed.get("project_scope") or None,
        }
    except json.JSONDecodeError as exc:
        log.warning("extract.json_decode_failed", error=str(exc))
        return fallback
    except Exception as exc:
        log.warning("extract.failed", error=str(exc))
        return fallback
