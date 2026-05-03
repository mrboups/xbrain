"""LLM-based fact extraction via Claude.

Returns a list of structured fact dicts ready for /v1/memory/upsert. Each fact
suggests a `truth_level` (default WORKING) — extraction NEVER produces CANONICAL
or PUBLIC, those require the 4-eyes promotion workflow (02-04).

Validation_status is always 'pending' on insertion — it flips to 'validated'
only when the fact crosses the VALIDATED truth-level via promotion.
"""

from __future__ import annotations

import json
import re
from typing import Any

from anthropic import AsyncAnthropic

from app.config import settings

MAX_FACTS = 20
MAX_FACT_CHARS = 500

_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        if not settings.ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY required for ingestion-agent")
        _client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _client


SYSTEM_PROMPT = """You are a fact extractor for the xbrain memory system.

Extract atomic, verifiable facts from the provided document. Each fact must:
1. Be self-contained (understandable without the source doc)
2. Be verifiable (states something testable / has a referent)
3. Avoid opinions and speculation
4. Have suggested_truth_level: WORKING (default), EPHEMERAL (uncertain),
   or VALIDATED (only if the source is itself a canonical org doc).
   NEVER suggest CANONICAL or PUBLIC — those require human promotion.
5. Have confidence: 0.0-1.0 — how certain is THIS extraction (NOT the fact's truthiness)

Return strict JSON: {"facts": [
  {"content": "...", "suggested_truth_level": "WORKING|EPHEMERAL|VALIDATED",
   "confidence": 0.85}
]}

Max 20 facts. Quality over quantity. Empty list ({"facts": []}) is acceptable
when the document contains no atomic verifiable facts."""


_SUGGESTED_LEVELS = {"WORKING", "EPHEMERAL", "VALIDATED"}


def _coerce_level(raw: Any) -> str:
    if isinstance(raw, str) and raw.upper() in _SUGGESTED_LEVELS:
        return raw.upper()
    return "WORKING"


def _coerce_confidence(raw: Any) -> float:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 0.7
    return max(0.0, min(1.0, v))


def _parse_json_loose(text: str) -> dict:
    """Try strict json.loads first, then look for a JSON code block fence."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    return {}


async def extract_facts(document_text: str, source_url: str) -> list[dict[str, Any]]:
    """Run Claude on the document and return up to MAX_FACTS structured facts.

    Returns empty list on parse failure (no crash). Each item:
        {content, suggested_truth_level, confidence, source, metadata}
    """
    client = _get_client()
    msg = await client.messages.create(
        model="claude-3-5-sonnet-latest",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"Source: {source_url}\n\n---\n\n{document_text}",
            }
        ],
    )
    text = "".join(b.text for b in msg.content if b.type == "text")
    data = _parse_json_loose(text)
    raw_facts = data.get("facts") or []
    if not isinstance(raw_facts, list):
        return []

    cleaned: list[dict[str, Any]] = []
    for f in raw_facts:
        if not isinstance(f, dict):
            continue
        content = f.get("content")
        if not content or not isinstance(content, str):
            continue
        cleaned.append(
            {
                "content": content[:MAX_FACT_CHARS],
                "suggested_truth_level": _coerce_level(f.get("suggested_truth_level")),
                "confidence": _coerce_confidence(f.get("confidence")),
                "source": "agent:ingestion-v1",
                "metadata": {"source_url": source_url},
            }
        )
        if len(cleaned) >= MAX_FACTS:
            break
    return cleaned
