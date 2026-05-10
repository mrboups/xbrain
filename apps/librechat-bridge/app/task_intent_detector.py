"""Task intent detection in LibreChat messages (D5 trigger 3).

Strategy: lightweight Claude call to detect whether a chat message expresses
a task assignment / commitment. If yes, set metadata.contains_action = true
on the memory_item being forwarded. The actual task creation is delegated
to memory-api's _maybe_create_task_from_action background hook (plan 07-06).

Why not call /v1/tasks directly?
The bridge JWT is rejected by POST /v1/tasks (plan 07-03 line 257):
that endpoint calls _user_id_from_principal which raises 401 for bridge
principals. The bridge has no user identity to present.
Delegating via metadata.contains_action lets memory-api create the task
server-side with created_by = NULL (system attribution, nullable per migration 0010).

Fail-soft: any error returns None (no task flag added) and is logged at WARN level.
The standard message forward to memory-api is NEVER blocked by this hook.
"""

import json
from typing import Any

import structlog

from app.config import settings

log = structlog.get_logger(__name__)

SYSTEM_PROMPT = """Detect whether the user message expresses a task assignment or commitment.

Return STRICT JSON (no markdown, no prose, no fences):
{
  "has_task": true_or_false,
  "title": "concise actionable title <80 chars or null",
  "description": "details or null",
  "assignee_email": "email or null",
  "assignee_name": "name or null"
}

Triggers (positive cases):
- Imperative addressed to a specific person: "Alice, send the recap by Friday"
- Mention with action: "@bob fait X", "@alice doit terminer Y"
- Explicit TODO/action: "TODO: ...", "action requise: ...", "à faire: ..."
- Commitment: "I'll prepare the deck", "je vais envoyer le résumé"

Negative cases (informational, has_task=false):
- Question: "What's the status?"
- Discussion: "We talked about X yesterday"
- Greeting/banter: "Hi everyone"

If has_task=false, set all other fields to null.

Output ONLY the JSON object."""


_anthropic_client: Any | None = None


def _get_client() -> Any | None:
    """Lazy-init the Anthropic async client. Returns None if disabled or package absent."""
    global _anthropic_client
    if _anthropic_client is not None:
        return _anthropic_client
    if not settings.ANTHROPIC_API_KEY:
        return None
    try:
        from anthropic import AsyncAnthropic  # noqa: PLC0415
    except ImportError:
        log.warning("task_intent.anthropic_not_installed")
        return None
    _anthropic_client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _anthropic_client


async def detect_task_intent(message_content: str) -> dict[str, Any] | None:
    """Detect task intent in a chat message. Fail-soft: returns None on any error.

    Returns:
        - dict {has_task, title, description, assignee_email, assignee_name} on success
        - None if disabled, no API key, content too short, or any error

    The caller decides what to do: typically, set metadata.contains_action=true
    when result["has_task"] is True, optionally include the extracted title in
    the metadata for downstream hooks (plan 07-06 _maybe_create_task_from_action).
    """
    if not settings.TASK_INTENT_DETECTION:
        return None
    if not message_content or len(message_content.strip()) < 8:
        return None

    client = _get_client()
    if client is None:
        return None

    try:
        msg = await client.messages.create(
            model=settings.ANTHROPIC_TASK_INTENT_MODEL,
            max_tokens=512,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": message_content[:4000]}],
        )
        text = (msg.content[0].text if msg.content else "{}").strip()
        # Strip optional markdown fence if Claude includes one despite instructions
        if text.startswith("```"):
            text = text.split("```", 2)[1].lstrip("json").strip()
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            return None
        # Sanity-check shape — return normalised 5-key dict
        return {
            "has_task": bool(parsed.get("has_task")),
            "title": parsed.get("title") or None,
            "description": parsed.get("description") or None,
            "assignee_email": parsed.get("assignee_email") or None,
            "assignee_name": parsed.get("assignee_name") or None,
        }
    except json.JSONDecodeError as exc:
        log.warning("task_intent.json_decode_failed", error=str(exc))
        return None
    except Exception as exc:
        log.warning("task_intent.detection_failed", error=str(exc))
        return None
