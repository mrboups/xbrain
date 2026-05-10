"""Contact extraction in LibreChat messages (Phase 8 D3 RESEARCH.md).

Strategy: lightweight Claude call to detect person mentions in a chat message
(both user AND assistant messages). Each mention is upserted into the CRM via
POST /v1/crm/contacts using a bridge JWT scoped to the message's team.

Pattern mirrored from apps/librechat-bridge/app/task_intent_detector.py:
  - Lazy AsyncAnthropic singleton
  - Fail-soft: any error logs WARN and returns silently
  - Opt-in via CONTACT_EXTRACTION env var (default false)
  - Bridge JWT for service-to-service auth with memory-api
"""

import json
from typing import Any

import httpx
import structlog

from app.bridge_token import make_bridge_jwt
from app.config import settings

log = structlog.get_logger(__name__)

SYSTEM_PROMPT = """Extract distinct person mentions from the message content.

Return STRICT JSON array (no markdown fences, no prose):
[
  {"name": "Full name or null", "email": "email@example.com or null", "company": "company name or null", "role": "their role or null"}
]

Rules:
- Only include people who are clearly mentioned by name, email, or @mention
- Skip generic references ("the user", "they", pronouns)
- Skip the chat participants themselves unless they're discussing OTHERS
- If both name and email are absent, skip the entry
- Maximum 10 entries per message
- If no people mentioned, return []

Output ONLY the JSON array."""


_anthropic_client: Any | None = None


def _get_client() -> Any | None:
    """Lazy-init Anthropic async client. Returns None if disabled or package absent."""
    global _anthropic_client
    if _anthropic_client is not None:
        return _anthropic_client
    if not settings.ANTHROPIC_API_KEY:
        return None
    try:
        from anthropic import AsyncAnthropic  # noqa: PLC0415
    except ImportError:
        log.warning("contact_extractor.anthropic_not_installed")
        return None
    _anthropic_client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _anthropic_client


async def _upsert_contact(
    *,
    sub: str,
    team_scope: str,
    name: str | None,
    email: str | None,
    company: str | None,
    role: str | None,
    source: str,
    source_ref: str | None,
) -> None:
    """POST /v1/crm/contacts with bridge JWT. Fail-soft."""
    token = make_bridge_jwt(sub=sub, team_scope=team_scope)
    body = {
        "team_scope": team_scope,
        "contact_type": "direct",
        "full_name": name,
        "email": email,
        "company": company,
        "role": role,
        "source": source,
        "truth_level": "EPHEMERAL",
        "confidence": 0.6,
    }
    if source_ref:
        body["source_ref"] = source_ref
    url = f"{settings.MEMORY_API_URL}/v1/crm/contacts"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                url,
                json=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Team-Scope": team_scope,
                    "Content-Type": "application/json",
                },
            )
        if resp.status_code >= 400:
            log.warning(
                "contact_extractor.upsert_non_2xx",
                status=resp.status_code,
                body=resp.text[:200],
                team_scope=team_scope,
            )
    except Exception as exc:
        log.warning(
            "contact_extractor.upsert_failed",
            error=str(exc),
            team_scope=team_scope,
        )


async def extract_contacts_from_message(
    *,
    content: str,
    sub: str,
    team_scope: str,
    source: str,
    source_ref: str | None = None,
) -> None:
    """Extract person mentions from a chat message and upsert into CRM. Fail-soft.

    Triggered fire-and-forget from mongo_watcher.messages_watch_loop.
    Never blocks the forward.
    """
    if not settings.CONTACT_EXTRACTION:
        return
    if not content or len(content.strip()) < 30:
        return

    client = _get_client()
    if client is None:
        return

    try:
        msg = await client.messages.create(
            model=settings.ANTHROPIC_CONTACT_MODEL,
            max_tokens=1024,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": content[:6000]}],
        )
        text = (msg.content[0].text if msg.content else "[]").strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1].lstrip("json").strip()
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            return
    except json.JSONDecodeError as exc:
        log.warning("contact_extractor.json_decode_failed", error=str(exc))
        return
    except Exception as exc:
        log.warning("contact_extractor.detection_failed", error=str(exc))
        return

    contacts_upserted = 0
    for person in parsed[:10]:
        if not isinstance(person, dict):
            continue
        name = person.get("name") or None
        email = person.get("email") or None
        if not (name or email):
            continue
        await _upsert_contact(
            sub=sub,
            team_scope=team_scope,
            name=name,
            email=email,
            company=person.get("company") or None,
            role=person.get("role") or None,
            source=source,
            source_ref=source_ref,
        )
        contacts_upserted += 1

    if contacts_upserted > 0:
        log.info(
            "contact_extractor.upserted",
            count=contacts_upserted,
            team_scope=team_scope,
            source=source,
        )
