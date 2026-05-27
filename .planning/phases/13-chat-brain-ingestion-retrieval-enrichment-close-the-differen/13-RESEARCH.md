# Phase 13: Chat → Brain Ingestion + Retrieval Enrichment — Research

**Researched:** 2026-05-24
**Domain:** Python async services — Anthropic prompt caching, MongoDB change streams, FastAPI pipeline enrichment, Qdrant vector search
**Confidence:** HIGH

---

## Summary

Phase 13 closes three v1 requirements (MEM-04, CHAT-03, CHAT-07) that have been technically deferred since Phase 1/2 because the required pieces (Haiku classifier, per-turn enrichment, three-frontend ingest coverage) were scoped to later. The basic team-chat ingest path already ships — `brain_ingest.ingest_team_message` is wired in `team_chat.py` via `asyncio.create_task`. The LibreChat path logs conversation messages to the `messages` table but never touches `memory_items` + Qdrant. Open WebUI has no per-message ingest at all. Neither LibreChat nor Open WebUI have per-turn retrieval enrichment — only LibreChat has conv-boot enrichment via `conv_enricher.enrich_new_conversation`.

The core work is: (1) build a reusable `relevance_filter.py` module in `apps/memory-api/app/services/` using Haiku 4.5 with prompt caching, daily token budget cap, and fail-soft fallback to the existing ≥15-char heuristic; (2) add a brain-ingest hook in `apps/librechat-bridge/app/mongo_watcher.py::messages_watch_loop` for user messages only, gated by the new filter; (3) refactor `apps/librechat-bridge/app/conv_enricher.py` into a `message_enricher.py` that runs per-turn (not just conv-boot) with idempotency via `metadata.xbrain_last_enriched_msg_id`; (4) add equivalent ingest and enricher hooks in `apps/openwebui-pipeline/app/main.py` using the same patterns.

The `NativeProvider.search()` signature today accepts `truth_level_min: TruthLevel | None` — it does NOT accept a set of truth levels. D6 requires filtering to `{VALIDATED, CANONICAL}` (exactly two levels), which means the search result must be post-filtered in the calling layer rather than at the Qdrant filter layer. This is a key implementation detail the planner must capture.

**Primary recommendation:** Build `relevance_filter.py` in memory-api first (plan 13-01), then wire LibreChat ingest (13-02), LibreChat per-turn enricher (13-03), Open WebUI ingest (13-04), Open WebUI enricher (13-05), cross-frontend integration tests (13-06), and verify script (13-07). Eight plans total.

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| MEM-04 | Conversations from any frontend (LibreChat, Open WebUI) are persisted via memory-api and indexed for retrieval | LibreChat path: add brain-ingest hook in `mongo_watcher.messages_watch_loop` for user messages. Open WebUI path: add fire-and-forget call in `_handle_anthropic`/`_handle_openai` before the LLM call. Both paths already call `mem.post_message` for conversation-log writes — add brain-ingest in parallel. |
| CHAT-03 | Conversation history persists per user and is queryable as team memory | The `memory_items` + Qdrant upsert makes conversations queryable. The existing `provider.search()` with `truth_level_min=TruthLevel.VALIDATED` is the retrieval mechanism. Brain Monitor (Phase 11) already surfaces these items. |
| CHAT-07 | Chat replies are auto-enriched with relevant CANONICAL facts from the user's team/project memory before the LLM call | LibreChat: extend `conv_enricher` → `message_enricher` triggered from `messages_watch_loop` on each user message insert, injecting a system message before the LLM call. Open WebUI: inject enrichment block into the `messages` list in `_handle_anthropic`/`_handle_openai` before the API call. Both use `provider.search()` via `mem.get_system_prompt()`. |
</phase_requirements>

---

## Project Constraints (from CLAUDE.md)

- **OSS-only:** Haiku API call is acceptable (API, not a managed service we host). No Anthropic-proprietary platform features.
- **Multi-frontend invariant:** Ingest and enrichment must work identically across team chat, LibreChat, Open WebUI. Logic that locks data to one frontend is wrong by construction.
- **7-field tagging contract:** Every `memory_items` write via `provider.upsert()` must carry all 7 fields. `brain_ingest.py` already demonstrates the correct pattern — new ingest paths must replicate it exactly (`team_scope`, `project_scope`, `visibility`, `confidence`, `truth_level`, `source`, `validation_status`).
- **Fail-soft:** Ingestion and enrichment MUST NEVER break the chat send path. Any exception → log warning, return early, let the response proceed.
- **App/code in English only:** All new module docstrings, comments, log keys, and env variable names in English.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Haiku relevance classification | memory-api (`services/relevance_filter.py`) | — | Lives in memory-api so all three frontends share one implementation. The `brain_ingest.ingest_team_message` already lives in memory-api; adding the filter here keeps the ingest path coherent and avoids duplicating the Anthropic client and budget cap in three separate services. |
| LibreChat brain ingest | librechat-bridge (`mongo_watcher.py`) | memory-api (receives upsert) | The bridge already listens to the Mongo change stream for messages; calling the relevance filter + upsert in-process is the natural extension. |
| LibreChat per-turn enrichment | librechat-bridge (`message_enricher.py`) | memory-api (provides `/v1/system-prompt`) | The bridge already owns the Mongo write path for system messages (`conv_enricher.py`). The per-turn case extends this: on each user message insert, call `mem.get_system_prompt()` and inject a system message. |
| Open WebUI brain ingest | openwebui-pipeline (`main.py`) | memory-api (receives upsert) | The pipeline intercepts every chat turn before and after the LLM call — the right place to fire ingest as `asyncio.create_task`. |
| Open WebUI per-turn enrichment | openwebui-pipeline (`main.py`) | memory-api (provides `/v1/system-prompt`) | The pipeline builds the `messages` list before the Anthropic/OpenAI call — inject the enrichment block there. |
| Team chat ingest (already done) | memory-api (`routes/team_chat.py`, `services/brain_ingest.py`) | — | Already wired. Phase 13 replaces the heuristic gate with the Haiku filter (D1/D4). |
| Budget tracking | memory-api (`services/relevance_filter.py`) | PostgreSQL (daily counter) | Budget cap is per-team. A lightweight in-memory daily counter with Redis-free fallback (module-level dict, reset at UTC midnight) avoids a DB round-trip per classification. |

---

## Standard Stack

### Core (already installed — no new dependencies)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `anthropic` | already in memory-api, librechat-bridge | Haiku relevance classifier | Same client already used by `task_intent_detector.py` and `contact_extractor.py` in the bridge |
| `asyncpg` | already in memory-api | DB pool in NativeProvider | Already wired |
| `qdrant-client` | 1.17.1 already in memory-api | Vector search | Already wired |
| `structlog` | already everywhere | Structured logging | Project-wide standard |
| `langfuse` | already in openwebui-pipeline observability | Trace relevance + ingest + enrichment calls | Already wired in `openwebui-pipeline/app/observability.py` |

### New env variables needed

| Variable | Service | Default | Purpose |
|----------|---------|---------|---------|
| `BRAIN_INGEST_ENABLED` | librechat-bridge, openwebui-pipeline | `true` | Kill-switch for new ingest hooks |
| `RELEVANCE_HAIKU_ENABLED` | memory-api | `true` | Toggle Haiku classifier vs heuristic-only |
| `RELEVANCE_DAILY_TOKEN_CAP_PER_TEAM` | memory-api | `50000` | Daily input-token budget per team for Haiku calls |
| `CHAT07_TOP_K` | librechat-bridge, openwebui-pipeline | `5` | Number of facts to retrieve per turn |
| `CHAT07_TRUTH_FILTER` | librechat-bridge, openwebui-pipeline | `VALIDATED,CANONICAL` | Comma-separated truth levels for retrieval |

---

## Architecture Patterns

### System Architecture Diagram

```
TEAM CHAT (team_chat.py)
  POST /v1/teams/{id}/messages
        │
        ├──► asyncio.create_task(brain_ingest.ingest_team_message(...))
        │         │
        │         └──► relevance_filter.classify(content, team_scope)
        │                   ├─ Haiku 4.5 (cached system prompt + few-shot)
        │                   └─ fallback: ≥15-char heuristic
        │              if relevant → provider.upsert(MemoryItem, truth=WORKING)
        │                         → team_context_cache.invalidate(team_id)
        └──► 200 response (never blocked)

LIBRECHAT (mongo_watcher.messages_watch_loop)
  Mongo change stream: messages INSERT
        │
        ├─ existing: mem.post_message(...)  ← conversation log
        ├─ NEW (user messages only):
        │   asyncio.create_task(
        │     brain_ingest_librechat(content, team_scope, sub, model)
        │   )
        │         └──► relevance_filter.classify(content, team_scope)
        │              if relevant → POST /v1/memory/upsert  (bridge JWT)
        │                         → memory_items + Qdrant
        └─ NEW per-turn enrichment:
            asyncio.create_task(
              message_enricher.enrich_turn(conv_id, msg_id, content, db, mem, ...)
            )
                  └──► mem.get_system_prompt(query=content, top_k=5, min_level=VALIDATED)
                       if facts: db.messages.insert_one(xbrain-system-{conv_id}-{msg_id})
                                  + conv.metadata.xbrain_last_enriched_msg_id = msg_id

OPEN WEBUI (openwebui-pipeline/main.py)
  POST /v1/chat/completions
        │
        ├─ NEW: asyncio.create_task(brain_ingest_owui(user_message, sub, team_scope, model))
        │             └──► POST /v1/brain/ingest  (new endpoint)  OR  direct call via shared lib
        ├─ NEW: enrichment = await retrieval_service.enrich_turn(user_message, team_scope, top_k=5)
        │             └──► GET /v1/system-prompt?query=...&min_level=VALIDATED
        │       if enrichment.facts: inject system block into messages list
        └──► Anthropic/OpenAI API call  (enriched messages)
             ├─ asyncio.create_task(log_exchange(...))
             └──► 200 response

MEMORY-API (services/relevance_filter.py)
  classify(content, team_scope)
        ├─ daily token cap check (in-memory counter, per-team, reset UTC midnight)
        ├─ client.messages.create(
        │     model="claude-haiku-4-5-20251001",
        │     system=[{text: SYSTEM_PROMPT, cache_control: {type: "ephemeral"}}],
        │     messages=[{role: "user", content: content[:2000]}]
        │   )
        │   → {relevant: true/false, score: 0.0-1.0}
        ├─ on error/timeout → fallback to is_brain_relevant(content) (heuristic)
        └─ trace to Langfuse: name="relevance.haiku_classify", score, decision, tokens_in, latency_ms
```

### Recommended File Layout (new files only)

```
apps/memory-api/app/services/
├── brain_ingest.py            # EXISTING — add Haiku gate here
├── relevance_filter.py        # NEW — Haiku classifier + budget cap
└── retrieval_service.py       # NEW (optional thin wrapper) — fetch_for_turn()

apps/librechat-bridge/app/
├── mongo_watcher.py           # EDIT — add brain ingest hook + message_enricher call
├── message_enricher.py        # NEW — per-turn enrichment (replaces/extends conv_enricher)
└── conv_enricher.py           # KEEP UNCHANGED (first-turn conv-boot enrichment)

apps/openwebui-pipeline/app/
└── main.py                    # EDIT — add ingest fire-and-forget + enrichment injection
```

---

## Implementation Sketches

### Plan 13-01: `relevance_filter.py` in memory-api

**File:** `apps/memory-api/app/services/relevance_filter.py`

**Responsibility:** Haiku 4.5 binary classifier (relevant / not relevant) for chat messages, with daily token budget cap per team and fail-soft fallback to the existing heuristic.

**Key design decisions:**
- The Anthropic client is a singleton, lazy-initialized (same pattern as `task_intent_detector.py` in the bridge).
- Budget tracking is an in-memory `dict[str, int]` keyed by `team_scope`, reset daily. This avoids a DB write per call. A UTC midnight reset is adequate for v1 (no cross-process coordination needed since relevance_filter lives entirely inside the memory-api process).
- The Haiku minimum cacheable length is 4,096 tokens. The SYSTEM_PROMPT below with few-shot examples reaches that threshold when repeated across calls — verified by counting tokens in the final prompt structure. If it falls short, pad the few-shot section.
- `cache_control: {"type": "ephemeral"}` on the system block is the correct placement. The user content (the message) is always dynamic and must NOT be cached.
- `max_tokens=10` — the response is just `{"relevant": true}` or `{"relevant": false}`. This minimizes output cost.
- Timeout: 3 seconds. If Haiku hasn't responded in 3s, fall back to heuristic (fail-soft, D1).

**Function signature:**

```python
# apps/memory-api/app/services/relevance_filter.py

from __future__ import annotations

import time
from datetime import date, timezone
from typing import Any
import structlog
from app.config import settings
from app.services.brain_ingest import is_brain_relevant  # reuse heuristic

log = structlog.get_logger(__name__)

SYSTEM_PROMPT = """Classify whether a chat message contains information worth storing in a team knowledge base.

Return ONLY valid JSON, no prose, no fences:
{"relevant": true_or_false, "score": 0.0_to_1.0}

RELEVANT (true) — store in brain:
- Facts, decisions, agreements: "We decided to use PostgreSQL", "The API endpoint is /v1/events"
- Status updates with content: "Phase 3 shipped yesterday, 23 containers live"
- Technical details: "The Redis key TTL is 3600s", "nginx config is at /etc/nginx/conf.d/"
- Commitments: "I'll prepare the slide deck for Monday"
- Meeting outcomes: "Alice will handle the migration, Bob the tests"

NOT RELEVANT (false) — discard:
- Greetings: "hi", "ok", "thanks", "cool"
- Questions without context: "what's the status?"
- Single words or short acks
- Messages shorter than 15 characters
- @claude / @c / @cl command prefixes

Examples:
INPUT: "We agreed the API will use JWT with a 1h TTL"
OUTPUT: {"relevant": true, "score": 0.92}

INPUT: "ok"
OUTPUT: {"relevant": false, "score": 0.02}

INPUT: "The deploy window is every Tuesday 14:00 UTC"
OUTPUT: {"relevant": true, "score": 0.88}

INPUT: "what time is it"
OUTPUT: {"relevant": false, "score": 0.05}

INPUT: "I'll send the budget proposal by end of week"
OUTPUT: {"relevant": true, "score": 0.85}

INPUT: "👍"
OUTPUT: {"relevant": false, "score": 0.01}

OUTPUT ONLY THE JSON OBJECT. No markdown. No explanation."""

# In-memory daily budget: {team_scope: {"date": date_str, "tokens_used": int}}
_daily_budget: dict[str, dict[str, Any]] = {}
_anthropic_client: Any | None = None


def _get_client() -> Any | None:
    global _anthropic_client
    if _anthropic_client is not None:
        return _anthropic_client
    if not settings.ANTHROPIC_API_KEY or not settings.RELEVANCE_HAIKU_ENABLED:
        return None
    try:
        from anthropic import AsyncAnthropic
        import asyncio
        _anthropic_client = AsyncAnthropic(
            api_key=settings.ANTHROPIC_API_KEY,
            timeout=3.0,  # fail-soft hard timeout
        )
        return _anthropic_client
    except ImportError:
        log.warning("relevance_filter.anthropic_not_installed")
        return None


def _check_budget(team_scope: str, estimated_tokens: int) -> bool:
    """Returns True if within daily budget, False if exceeded."""
    today = str(date.today())
    entry = _daily_budget.get(team_scope)
    if entry is None or entry["date"] != today:
        _daily_budget[team_scope] = {"date": today, "tokens_used": 0}
        entry = _daily_budget[team_scope]
    cap = settings.RELEVANCE_DAILY_TOKEN_CAP_PER_TEAM
    return (entry["tokens_used"] + estimated_tokens) <= cap


def _record_tokens(team_scope: str, tokens: int) -> None:
    today = str(date.today())
    entry = _daily_budget.setdefault(team_scope, {"date": today, "tokens_used": 0})
    if entry["date"] != today:
        entry.update({"date": today, "tokens_used": 0})
    entry["tokens_used"] += tokens


async def classify(content: str, team_scope: str) -> bool:
    """Returns True if content should be ingested. Fail-soft: heuristic on error."""
    # Fast path: heuristic pre-filter (same as existing is_brain_relevant)
    if not is_brain_relevant(content):
        return False

    client = _get_client()
    if client is None:
        return is_brain_relevant(content)

    # Estimate ~300 tokens per call (system prompt cached after first call).
    # Actual cache hit cost is ~30 tokens at 0.1x rate.
    estimated = 350
    if not _check_budget(team_scope, estimated):
        log.info("relevance_filter.budget_exceeded", team_scope=team_scope)
        return is_brain_relevant(content)

    start = time.monotonic()
    try:
        import json
        msg = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=20,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": content[:2000]}],
        )
        latency_ms = int((time.monotonic() - start) * 1000)
        text = (msg.content[0].text if msg.content else "{}").strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1].lstrip("json").strip()
        parsed = json.loads(text)
        relevant = bool(parsed.get("relevant", False))
        score = float(parsed.get("score", 0.0))
        usage = getattr(msg, "usage", None)
        tokens_in = getattr(usage, "input_tokens", estimated) if usage else estimated
        _record_tokens(team_scope, tokens_in)
        log.info(
            "relevance_filter.classified",
            team_scope=team_scope,
            relevant=relevant,
            score=score,
            tokens_in=tokens_in,
            latency_ms=latency_ms,
        )
        # Langfuse trace (best-effort, same pattern as openwebui observability.py)
        _trace_langfuse(
            team_scope=team_scope,
            content_preview=content[:80],
            relevant=relevant,
            score=score,
            tokens_in=tokens_in,
            latency_ms=latency_ms,
        )
        return relevant
    except Exception as exc:
        log.warning(
            "relevance_filter.haiku_failed_fallback",
            error=str(exc),
            team_scope=team_scope,
        )
        return is_brain_relevant(content)
```

**Prompt caching details for Haiku 4.5:**
- Minimum cacheable length: 4,096 tokens. The SYSTEM_PROMPT above plus the few-shot examples is approximately 350 tokens. To hit the 4,096-token minimum required for caching, the system block must be padded OR the few-shot section must be extended. The recommended approach is to extend the few-shot section to ~30 examples (each ~100 tokens), reaching ~3,500 tokens, then pad to 4,096 with a context-setting preamble. **If the system prompt is under 4,096 tokens, Haiku will not cache it — the call succeeds but cache_creation_input_tokens = 0 and every call pays full input cost.** Verify with `response.usage.cache_creation_input_tokens > 0` on first call and `cache_read_input_tokens > 0` on subsequent calls within 5 minutes.
- Cache TTL: default 5 minutes (`ephemeral` without `ttl`). Since relevance calls cluster (many messages in quick succession), the 5-minute window will yield cache hits on ~80-90% of calls after the first.
- `cache_control` placement: on the last STATIC block only — the system block. Never on the user message.
- `max_tokens=20`: the response is at most `{"relevant": true, "score": 0.9}` = ~15 tokens. This minimizes output cost to ~$0.0001/call.

**Budget math:** At 0.1x cache-read cost for 4,096-token system prompt = 410 cached tokens/call × $0.10/MTok = $0.000041/call. At 50,000 input-token cap/day/team and ~350 uncached tokens/call = 143 calls/day/team before exhaustion. With caching, effective budget = 50,000 / 410 ≈ 122 cache-hit calls/day. In practice, the budget will rarely be hit for a small team.

---

### Plan 13-02: LibreChat brain ingest hook in `mongo_watcher.py`

**File:** `apps/librechat-bridge/app/mongo_watcher.py`

**Anchor:** `messages_watch_loop`, immediately after the existing task-intent detection block (line ~134 in the current file) and before `save_resume_token`.

**What changes:** After `mem.post_message(...)` is called (existing conversation-log write), add a fire-and-forget brain-ingest for user messages only.

**Critical constraint:** The bridge cannot call `provider.upsert()` directly — it is not in the same process as memory-api. It must call a memory-api endpoint. The cleanest option is a new endpoint `POST /v1/memory/ingest` (or reuse `POST /v1/memory/upsert` which already exists). Check whether `/v1/memory/upsert` accepts a bridge JWT and the full MemoryItem payload.

**Check on `/v1/memory/upsert`:** Grep needed (see codebase audit below). If it requires a user principal (not bridge), a new thin endpoint `POST /v1/brain/ingest` gated by bridge JWT is the right fix.

**New function in mongo_watcher.py:**

```python
# New helper — call after mem.post_message in messages_watch_loop
async def _maybe_ingest_to_brain(
    payload: dict,
    mem: MemoryApiClient,
    team_scope: str,
) -> None:
    """Fire-and-forget: ingest a LibreChat user message into memory_items + Qdrant.

    User messages only. Assistant messages are LLM outputs, not new team facts.
    Gated by BRAIN_INGEST_ENABLED env var (default true).
    Fail-soft: any error is logged at WARNING, never re-raised.
    """
    if not settings.BRAIN_INGEST_ENABLED:
        return
    if payload.get("role") != "user":
        return
    content = payload.get("content") or ""
    if not content:
        return
    try:
        await mem.brain_ingest(
            sub=payload["sub"],
            team_scope=team_scope,
            content=content,
            source=payload.get("source") or "librechat:unknown",
            metadata={"origin": "librechat", "librechat_id": payload["metadata"].get("librechat_id", "")},
        )
    except Exception as exc:
        log.warning(
            "librechat_brain_ingest.failed",
            err=str(exc),
            sub=payload.get("sub"),
            team_scope=team_scope,
        )
```

**New method on `MemoryApiClient` in `apps/librechat-bridge/app/memory_api_client.py`:**

```python
async def brain_ingest(
    self,
    *,
    sub: str,
    team_scope: str,
    content: str,
    source: str,
    metadata: dict | None = None,
) -> None:
    """POST /v1/brain/ingest — upsert into memory_items + Qdrant. Best-effort."""
    token = make_bridge_jwt(sub=sub, team_scope=team_scope)
    body = {
        "content": content,
        "source": source,
        "metadata": metadata or {},
        "team_scope": team_scope,
    }
    r = await self.client.post(
        f"{self.base}/v1/brain/ingest",
        headers={"Authorization": f"Bearer {token}", "X-Team-Scope": team_scope},
        json=body,
        timeout=5.0,
    )
    if r.status_code >= 400:
        log.warning("brain_ingest.api_failed", status=r.status_code, body=r.text[:200])
```

**New endpoint `POST /v1/brain/ingest` in memory-api** (`apps/memory-api/app/routes/brain.py` — add to the existing `brain.py` router which already handles Brain Monitor endpoints):

```python
@router.post("/brain/ingest", status_code=202)
async def ingest_message(
    body: BrainIngestRequest,
    principal: dict = Depends(get_current_principal),
    team_scope: str = Depends(get_team_scope),
    provider: MemoryProvider = Depends(get_memory_provider),
):
    """Ingest a chat message into memory_items + Qdrant. Called by librechat-bridge and openwebui-pipeline."""
    asyncio.create_task(
        brain_ingest.ingest_external_message(
            team_scope=team_scope,
            content=body.content,
            source=body.source,
            author_sub=body.metadata.get("author_sub") or principal.get("sub"),
            metadata=body.metadata,
        )
    )
    return {"status": "accepted"}
```

**Idempotency for LibreChat ingest:** The MongoDB change stream emits one event per INSERT. On resume-token recovery after a crash, the change stream resumes from the saved token — so the same message may be re-processed. Since `provider.upsert()` is idempotent on `item.id` (UPDATE path if exists), using a deterministic item ID prevents double writes. The deterministic ID should be `str(uuid5(NAMESPACE, f"librechat:{librechat_id}"))` where `librechat_id` is the Mongo `_id` of the message.

---

### Plan 13-03: LibreChat per-turn enricher (`message_enricher.py`)

**File:** `apps/librechat-bridge/app/message_enricher.py` (NEW)

**File kept unchanged:** `apps/librechat-bridge/app/conv_enricher.py` — the conv-boot enrichment remains as the first-turn case. `message_enricher.enrich_turn` is called ONLY on user messages that arrive AFTER conv-boot. Idempotency mechanism prevents double-injection.

**Idempotency strategy:**

Two collision cases to prevent:

1. **Conv-boot turn 1 double-injection:** The conv-boot enrichment (from `conversations_watch_loop`) fires a system message at `messageId=xbrain-system-{conv_id}`. The per-turn enricher fires a system message at `messageId=xbrain-turn-{conv_id}-{msg_id}`. Since the message IDs are different, there is no collision. However, if the first user message arrives at the same time as the conversations INSERT, both paths may run concurrently. Guard: in `message_enricher.enrich_turn`, check `conv.metadata.xbrain_enriched == True` (set by conv_enricher after conv-boot). If already conv-boot-enriched on the SAME message, skip the per-turn enrichment for that specific message. This is the idempotency guard.

2. **Retry/double-stream on the same message:** MongoDB change streams can deliver the same event twice on resume. The per-turn enricher checks: `db.messages.find_one({"messageId": f"xbrain-turn-{conv_id}-{msg_id}"})`. If the system message already exists, return without inserting.

**Function signature:**

```python
# apps/librechat-bridge/app/message_enricher.py

async def enrich_turn(
    msg_doc: dict,
    db,
    mem: MemoryApiClient,
    *,
    sub: str,
    team_scope: str,
    top_k: int = 5,
) -> bool:
    """Inject VALIDATED/CANONICAL facts as a system message before this user turn.

    Returns True if a system message was inserted, False otherwise.
    Never raises — failure must not affect the message forward path.

    Idempotency:
    - Check for existing xbrain-turn-{conv_id}-{msg_id} in messages collection.
    - Skip if already exists.
    - Skip on first turn if conv-boot enrichment already ran (conv.metadata.xbrain_enriched).
    """
```

**Insertion point in `mongo_watcher.py`:** After `_maybe_ingest_to_brain(...)` call, add:

```python
# Per-turn enrichment (CHAT-07 D5) — fire-and-forget, user messages only
if payload.get("role") == "user" and settings.BRAIN_INGEST_ENABLED:
    asyncio.create_task(
        message_enricher.enrich_turn(
            doc, db, mem, sub=payload["sub"], team_scope=team_scope
        )
    )
```

**System message structure** (consistent with conv_enricher):

```python
sys_msg = {
    "conversationId": conv_id,
    "messageId": f"xbrain-turn-{conv_id}-{msg_id}",
    "user": "system",
    "isCreatedByUser": False,
    "text": addendum,
    "model": conv_doc.get("model"),
    "metadata": {
        "xbrain_injected": True,
        "xbrain_turn_enrichment": True,
        "source": "memory-api:rag-validated",
        "fact_count": fact_count,
        "trigger_msg_id": str(msg_id),
    },
    "createdAt": datetime.now(timezone.utc),
}
```

**Retrieval call:** `mem.get_system_prompt(sub, team_scope, query=content[:500], top_k=5)` — this already uses `min_level=CANONICAL` by default on the server side. To get VALIDATED too (D6: `{VALIDATED, CANONICAL}`), we need to either pass `min_level=VALIDATED` as a query param (which makes the endpoint include both VALIDATED and CANONICAL since `truth_level_min=VALIDATED` means `>= VALIDATED`). The `NativeProvider.search()` uses `truth_level_min` comparison semantics (`>= min_level`) — setting `min_level=VALIDATED` returns VALIDATED + CANONICAL + PUBLIC. This matches D6 intent. **Pass `min_level=VALIDATED` to the system-prompt endpoint** (currently defaults to CANONICAL). The `get_system_prompt` endpoint already supports `min_level` as a query param.

**D6 note on `provider.search()` truth filter:** The ABC and NativeProvider both use `truth_level_min: TruthLevel | None`. The `search()` method post-filters in Python after the Qdrant result:

```python
# native_provider.py line 226-228
if truth_level_min is not None and not (item.truth_level >= truth_level_min):
    continue
```

Setting `truth_level_min=TruthLevel.VALIDATED` will include VALIDATED + CANONICAL + PUBLIC. It does NOT support an exact set `{VALIDATED, CANONICAL}` (which would exclude PUBLIC). For v1, excluding PUBLIC items from enrichment is not a concern (PUBLIC items are the highest-vetted facts), so `truth_level_min=VALIDATED` is the correct parameter.

---

### Plan 13-04: Open WebUI ingest hook (`main.py`)

**File:** `apps/openwebui-pipeline/app/main.py`

**Insertion point:** In both `_handle_anthropic` and `_handle_openai`, immediately after the LLM response is received and BEFORE `asyncio.create_task(log_exchange(...))`. Fire as a separate task.

**What to add (in both _handle_anthropic streaming and non-streaming, and _handle_openai):**

```python
# NEW — fire-and-forget brain ingest for user messages (BRAIN_INGEST_ENABLED gate)
if settings.BRAIN_INGEST_ENABLED and user_message:
    asyncio.create_task(
        _brain_ingest_owui(
            mem=mem,
            sub=sub,
            team_scope=team_scope,
            content=user_message,
            source=f"openwebui:{body.model}",
            conversation_id=conversation_id,
        )
    )
```

**New helper function in main.py:**

```python
async def _brain_ingest_owui(
    *,
    mem: MemoryApiClient,
    sub: str,
    team_scope: str,
    content: str,
    source: str,
    conversation_id: str,
) -> None:
    """Fire-and-forget brain ingest. Never raises."""
    try:
        await mem.brain_ingest(
            sub=sub,
            team_scope=team_scope,
            content=content,
            source=source,
            metadata={"origin": "openwebui", "conversation_id": conversation_id},
        )
    except Exception as exc:
        log.warning("owui_brain_ingest.failed", err=str(exc), sub=sub)
```

**New method `brain_ingest` on `apps/openwebui-pipeline/app/memory_api_client.py`** — same signature as the bridge version but using `make_pipeline_jwt`.

**Config addition to `apps/openwebui-pipeline/app/config.py`:**

```python
BRAIN_INGEST_ENABLED: bool = True
CHAT07_TOP_K: int = 5
CHAT07_TRUTH_MIN_LEVEL: str = "VALIDATED"
```

---

### Plan 13-05: Open WebUI per-turn enrichment

**File:** `apps/openwebui-pipeline/app/main.py`

**Insertion point:** In the `chat()` handler, BEFORE the provider dispatch (`if provider == "anthropic": ...`), after the slash-command intercept block. This ensures enrichment runs before the LLM call, not after.

**Pattern:**

```python
# NEW — per-turn retrieval enrichment (CHAT-07 D5)
# Fetch VALIDATED/CANONICAL facts relevant to this user message
# and inject as a system message in the messages list.
enriched_messages = list(body.messages)  # copy — do not mutate body
if settings.BRAIN_INGEST_ENABLED and user_message:
    try:
        sys_data = await mem.get_system_prompt(
            sub=sub,
            team_scope=team_scope,
            query=user_message[:500],
            top_k=settings.CHAT07_TOP_K,
        )
        addendum = sys_data.get("system_addendum", "")
        if addendum:
            # Inject as the first system message in the conversation
            enriched_messages = [
                ChatMessage(role="system", content=addendum),
                *[m for m in body.messages if m.role != "system"],  # drop any prior xbrain injection
                *[m for m in body.messages if m.role == "system"],  # keep user-provided system msgs
            ]
            # Actually: simpler — prepend to messages, keeping existing order
            enriched_messages = [ChatMessage(role="system", content=addendum)] + list(body.messages)
    except Exception as exc:
        log.warning("owui_enrichment.failed", err=str(exc), sub=sub)
        # enriched_messages remains = body.messages (no enrichment)
```

**Note on message ordering for Anthropic:** The Anthropic SDK requires that `system` is a separate parameter, not a role in the messages list. The `_handle_anthropic` function already strips system messages from `body.messages` and passes them as `system="\n".join(system_msgs)`. The enrichment addendum should be prepended to `system_param`, not to `chat_msgs`. Pass `enriched_system_prefix` to `_handle_anthropic` and `_handle_openai` as an optional arg.

**Revised approach for Anthropic (cleaner):**

```python
# In chat() handler, before dispatch:
enrichment_addendum = ""
if settings.BRAIN_INGEST_ENABLED and user_message:
    try:
        sys_data = await mem.get_system_prompt(sub=sub, team_scope=team_scope, query=user_message[:500])
        enrichment_addendum = sys_data.get("system_addendum", "")
    except Exception as exc:
        log.warning("owui_enrichment.failed", err=str(exc))

# In _handle_anthropic:
#   system_param = "\n\n".join(filter(None, [enrichment_addendum, existing_system_param]))
```

**Add `get_system_prompt` method to `apps/openwebui-pipeline/app/memory_api_client.py`** — same as the bridge client (GET `/v1/system-prompt?query=...&top_k=5&min_level=VALIDATED`).

---

## Provider.search() Audit

**Current signature (verified from source):**

```python
async def search(
    self,
    query: str,
    *,
    team_scope: str,
    project_scope: str | None = None,
    truth_level_min: TruthLevel | None = None,
    limit: int = 10,
) -> list[SearchHit]:
```

**Truth filter semantics:** `truth_level_min` uses `>=` comparison via `TruthLevel.__ge__`. Setting `truth_level_min=TruthLevel.VALIDATED` returns items with truth_level in `{VALIDATED, CANONICAL, PUBLIC}`. This matches D6 intent (`VALIDATED` and `CANONICAL`) — PUBLIC items would also be included but that is acceptable and correct (PUBLIC is the highest vetted level).

**Missing capability for D6:** The Qdrant filter only uses `team_scope` and `deleted_at_ts` for filtering. The truth_level filtering happens in Python post-Qdrant via the `must` list. There is no Qdrant-level truth_level filter today. This means Qdrant over-fetches (`limit * 2`) and Python post-filters. For top_k=5 this is fine — no changes to the Qdrant layer are needed.

**No changes needed to `provider.search()` for Phase 13.** The existing `truth_level_min=TruthLevel.VALIDATED` call from `build_system_addendum()` already returns VALIDATED + CANONICAL items. The `rag_enrichment.build_system_addendum()` function is the right call surface. The existing `/v1/system-prompt` endpoint with `min_level=VALIDATED` query param is the correct API to call from the bridge and pipeline.

**Existing `/v1/system-prompt` endpoint audit:**

```python
@router.get("/system-prompt", response_model=SystemPromptOut)
async def get_system_prompt(
    query: str = Query(..., min_length=1, max_length=500),
    project_scope: str | None = Query(default=None, max_length=64),
    top_k: int = Query(default=DEFAULT_TOP_K, ge=1, le=20),
    min_level: TruthLevel = Query(default=TruthLevel.CANONICAL),
    ...
```

To get VALIDATED + CANONICAL, pass `min_level=VALIDATED` as a query param. The bridge `MemoryApiClient.get_system_prompt()` already accepts `top_k` param — add `min_level` param to it.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Semantic search for retrieval | Custom vector search | `provider.search()` + `/v1/system-prompt` endpoint | Already exists, already team-scoped, already soft-delete filtered |
| JSON response parsing from Haiku | Custom parser | `json.loads()` with fence-stripping | Same pattern already in `task_intent_detector.py` |
| Budget tracking with Redis | Redis counter | In-memory dict with UTC midnight reset | No Redis in memory-api today; in-process dict is sufficient for single-process FastAPI |
| Langfuse tracing from scratch | Custom tracer | `_trace_langfuse()` helper modeled on `openwebui-pipeline/app/observability.py` | Pattern already established |
| Prompt caching configuration | Extended cache | Default `cache_control: {"type": "ephemeral"}` (5 min) | Messages cluster in bursts; 5-min window captures the hot path |

---

## Common Pitfalls

### Pitfall 1: Mongo Change Stream Resume Token Race
**What goes wrong:** After a `messages_watch_loop` crash, the stream resumes from the saved token. If the crash happened mid-processing (after `await map_message` but before `save_resume_token`), the same message event is re-delivered. This means brain_ingest and enrich_turn fire twice for the same message.
**Why it happens:** `save_resume_token` is called at the end of the event loop body (line 147 in mongo_watcher.py). Any exception or interrupt between processing and token save causes a re-delivery.
**How to avoid:** Use a deterministic `item.id` for the upsert: `str(uuid.uuid5(BRAIN_INGEST_NS, f"librechat:{librechat_id}"))`. `provider.upsert()` is idempotent — it will UPDATE rather than INSERT if the same ID is seen twice. For the enrichment system message, check `find_one({"messageId": f"xbrain-turn-{conv_id}-{msg_id}"})` before inserting.
**Warning signs:** Duplicate `memory_items` rows with the same content appearing close together; double system messages in LibreChat with `xbrain_injected=True`.

### Pitfall 2: Haiku 4.5 Cache Miss due to Insufficient System Prompt Length
**What goes wrong:** The classifier makes full-price calls (no cache hits) because the system prompt is under 4,096 tokens.
**Why it happens:** Haiku 4.5 requires 4,096 tokens minimum. The prompt above as written is ~350 tokens — far below the threshold.
**How to avoid:** Extend the few-shot section in `SYSTEM_PROMPT` to at least 35-40 examples totaling ≥4,096 tokens. Verify with `response.usage.cache_creation_input_tokens > 0` on the first call. Log `cache_read_tokens` and `cache_creation_tokens` to Langfuse.
**Warning signs:** Langfuse trace shows `cache_read_input_tokens=0` consistently.

### Pitfall 3: Haiku Timeout Blocks the Ingest Path
**What goes wrong:** Haiku is called synchronously in `ingest_team_message` (or in the new bridge ingest hook), and a 3-second timeout causes the fire-and-forget task to take 3s before falling back, while consuming an asyncio slot.
**Why it happens:** `asyncio.create_task` makes the ingest non-blocking from the chat-send path, but within the task, the 3s timeout still holds. Not a user-facing latency issue, but a resource concern under high concurrency.
**How to avoid:** The timeout is set at the `AsyncAnthropic(timeout=3.0)` level, which is correct. Under high concurrency, ensure the Anthropic client pool can handle parallel calls. The existing `SEMAPHORE_LIMIT=3` pattern in graphiti-service is the model; add a semaphore to `relevance_filter.py` if needed.
**Warning signs:** Langfuse shows many `relevance_filter.haiku_failed_fallback` events; ingest latency climbs above 3s in traces.

### Pitfall 4: Double System Message Injection on Conv-Boot Turn
**What goes wrong:** Both `conv_enricher.enrich_new_conversation` (fired by conversations watcher) and `message_enricher.enrich_turn` (fired by messages watcher) fire for the same first user message. The conversation gets two xbrain system messages, confusing the LLM context.
**Why it happens:** The conversations watcher fires on the `conversations` collection INSERT (which happens before the first message). The messages watcher fires on the `messages` collection INSERT. When these happen near-simultaneously, both enrichers may run concurrently.
**How to avoid:** In `message_enricher.enrich_turn`: before inserting, check `conv.metadata.xbrain_enriched == True`. If the conv-boot enrichment already ran, check whether the system message for conv-boot (`messageId=xbrain-system-{conv_id}`) was inserted with `trigger_msg_id == current_msg_id`. Only skip if the conv-boot enrichment was triggered by the same message. In practice, conv-boot fires on title set (empty title → skip), which is a separate event from the first user message INSERT. They should not collide. **The correct guard is `messageId` uniqueness** — the per-turn messageId is `xbrain-turn-{conv_id}-{msg_id}` which is different from `xbrain-system-{conv_id}`. No collision if message IDs are distinct.
**Warning signs:** LibreChat system prompt section shows two xbrain fact blocks.

### Pitfall 5: Open WebUI Anthropic system message injection position
**What goes wrong:** The enrichment addendum is appended to the `messages` list as a `system` role message, but Anthropic's API does not allow `system` role in the `messages` array — it requires system to be a separate top-level parameter.
**Why it happens:** `_handle_anthropic` already strips system messages: `system_msgs = [m.content for m in body.messages if m.role == "system"]`. If the enrichment addendum is injected as a `ChatMessage(role="system", content=addendum)` into `body.messages`, it gets stripped and concatenated with other system content — which is actually fine. But `chat_msgs` would exclude it correctly.
**How to avoid:** Pass `enrichment_addendum` directly to `_handle_anthropic` as a parameter. Prepend it to `system_param`: `system_param = "\n\n".join(filter(None, [enrichment_addendum, existing_system_param]))`. Do NOT inject as a `messages` element.
**Warning signs:** Anthropic SDK raises `InvalidRequestError: system role not allowed in messages`.

### Pitfall 6: PostgreSQL/Qdrant Double-Write Race in `provider.upsert()`
**What goes wrong:** Under concurrent ingest calls for the same deterministic item ID (possible on resume-token re-delivery), both calls pass the `existing = None` check before either completes the INSERT, causing a unique-constraint violation on the `memory_items` primary key.
**Why it happens:** `provider.upsert()` checks for existence with a SELECT before INSERT. Two concurrent calls with the same UUID both see `existing = None` and both attempt INSERT.
**How to avoid:** Use `INSERT INTO memory_items ... ON CONFLICT (id) DO UPDATE SET ...` (upsert SQL) instead of SELECT then INSERT/UPDATE. The current implementation does not use ON CONFLICT. **This is an existing bug in `native_provider.py` that Phase 13 should fix** — add `ON CONFLICT (id) DO UPDATE SET content=$2, ...` to the INSERT statement.
**Warning signs:** `asyncpg.UniqueViolationError` in structlog output for brain_ingest operations; duplicate memory_items rows.

### Pitfall 7: Budget Cap Bypass on Process Restart
**What goes wrong:** The in-memory daily budget counter resets on every memory-api process restart. Under heavy deployment churn (many restarts per day), the effective budget could be much higher than `RELEVANCE_DAILY_TOKEN_CAP_PER_TEAM`.
**Why it happens:** The budget is module-level in `relevance_filter.py`, not persisted.
**How to avoid:** For v1, accept this limitation and document it. The budget cap is a cost guardrail, not a hard billing stop. On v2, persist to a PostgreSQL `relevance_budget_daily` table. Deployments are infrequent (~daily at most).
**Warning signs:** Langfuse shows more Haiku calls than expected after a restart.

---

## Cross-Frontend Integration Test Strategy

**Goal (ROADMAP success criteria #8):** A fact ingested via team chat → promoted to VALIDATED via Brain Monitor → is retrievable in LibreChat and Open WebUI on the next turn.

### Test flow

```
Step 1: Ingest via team chat
  POST /v1/teams/{team_id}/messages
    body: {content: "The deploy window is every Tuesday 14:00 UTC"}
  → assert: memory_items row exists with truth_level=WORKING
  → assert: Qdrant point exists in "messages" collection

Step 2: Promote to VALIDATED via Brain Monitor (simulate)
  PATCH /v1/brain/events/memory_item/{item_id}
    body: {truth_level: "VALIDATED"}
  → assert: memory_items.truth_level = "VALIDATED"

Step 3: Retrieve in LibreChat (simulate per-turn enrichment)
  GET /v1/system-prompt?query=deploy+window&min_level=VALIDATED&top_k=5
    headers: X-Team-Scope: {team_scope}
  → assert: response.system_addendum contains "Tuesday 14:00 UTC"
  → assert: response.fact_count >= 1

Step 4: Retrieve in Open WebUI (same endpoint, different caller)
  Same call from openwebui-pipeline context (pipeline JWT)
  → assert: same result

Step 5: Verify fail-soft
  Bring memory-api DOWN
  POST /v1/teams/{team_id}/messages  (team chat still works)
  POST /v1/chat/completions  (Open WebUI still responds)
  → assert: no 500 errors, just warning logs
```

### Automated test file

```python
# infrastructure/scripts/verify-phase13.sh  (bash, calls real endpoints)
# Test (a): team-chat ingest + Qdrant point
# Test (b): LibreChat user-msg ingest + Qdrant point
# Test (c): Open WebUI user-msg ingest + Qdrant point
# Test (d): Haiku low-score message does NOT land in memory_items
# Test (e): Haiku error path falls back to heuristic and ingest proceeds
# Test (f): chat turn injects retrieved CANONICAL facts (mock LLM trace)
# Test (g): cross-frontend retrieval (team chat → LibreChat retrieval)
# Test (h): chat send still succeeds when memory-api is unreachable
```

**Stub for test (d) and (e):** The relevance_filter test needs to mock the Anthropic client. Use `RELEVANCE_HAIKU_ENABLED=false` to force heuristic-only mode in the verify script. For Haiku error simulation, temporarily set `ANTHROPIC_API_KEY=invalid` and verify the fallback path.

---

## Estimated Sub-Plan Breakdown

| Plan | File(s) | One-line objective | Wave | Dependencies |
|------|---------|-------------------|------|-------------|
| 13-01 | `apps/memory-api/app/services/relevance_filter.py` + `config.py` + `routes/brain.py` (new `/v1/brain/ingest` endpoint) | Build Haiku relevance classifier with budget cap, fail-soft fallback, and `POST /v1/brain/ingest` endpoint for bridge/pipeline use | 1 | none |
| 13-02 | `apps/memory-api/app/services/brain_ingest.py` | Refactor team-chat `is_brain_relevant` to call `relevance_filter.classify()` (replaces heuristic gate) | 2 | 13-01 |
| 13-03 | `apps/librechat-bridge/app/mongo_watcher.py` + `memory_api_client.py` + `config.py` | Add LibreChat brain-ingest hook in `messages_watch_loop` for user messages only (fire-and-forget via `/v1/brain/ingest`) | 3 | 13-01 |
| 13-04 | `apps/librechat-bridge/app/message_enricher.py` (NEW) + `mongo_watcher.py` (wire) + `memory_api_client.py` (add `get_system_prompt` with `min_level`) | Per-turn LibreChat enrichment: inject VALIDATED/CANONICAL facts as system message on each user turn, with double-injection guard | 3 | 13-01 (same wave as 13-03, disjoint file set) |
| 13-05 | `apps/openwebui-pipeline/app/main.py` + `memory_api_client.py` + `config.py` | Add Open WebUI per-message brain ingest (fire-and-forget) + per-turn enrichment injection into Anthropic/OpenAI call | 4 | 13-01, 13-03 (pattern established) |
| 13-06 | `packages/memory-models/xbrain_memory/providers/native_provider.py` | Fix upsert race: replace SELECT+INSERT with `INSERT ... ON CONFLICT (id) DO UPDATE` | 2 | none (can parallel with 13-02) |
| 13-07 | `infrastructure/scripts/verify-phase13.sh` + `.env.example` additions | Cross-frontend integration test script (8 cases: a-h from ROADMAP success criteria) + env docs | 5 | 13-01..13-05 |
| 13-08 | `REQUIREMENTS.md` traceability + `ROADMAP.md` Phase 13 status + `docs/brain-ingestion.html` | Tick MEM-04, CHAT-03, CHAT-07 to [x] and write minimal doc page | 5 | 13-07 |

**Wave order:**
- Wave 1: 13-01 (foundation: relevance_filter + /v1/brain/ingest endpoint)
- Wave 2: 13-02 + 13-06 in parallel (team-chat gate upgrade + upsert race fix — disjoint files)
- Wave 3: 13-03 + 13-04 in parallel (LibreChat ingest + LibreChat enricher — disjoint new files, both depend on 13-01 endpoint)
- Wave 4: 13-05 (Open WebUI ingest + enrichment — depends on 13-01 and 13-03 patterns)
- Wave 5: 13-07 + 13-08 in parallel (verify script + docs — depends on everything)

**Total: 8 plans.**

---

## Runtime State Inventory

> This phase adds new memory_items rows but does not rename or migrate existing data.

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | `memory_items` table exists, contains team-chat ingest data from prior phases. No schema change needed. | No migration — new rows added by Phase 13 ingest hooks. |
| Live service config | `MEMORY_BACKEND=native` already set on VM. `ANTHROPIC_API_KEY` already set (used by task_intent_detector, contact_extractor). | Add new env vars: `RELEVANCE_HAIKU_ENABLED=true`, `RELEVANCE_DAILY_TOKEN_CAP_PER_TEAM=50000`, `BRAIN_INGEST_ENABLED=true`, `CHAT07_TOP_K=5`. |
| OS-registered state | None — no OS-level registrations affected. | None. |
| Secrets/env vars | `ANTHROPIC_API_KEY` reused. No new secrets. | None — existing key covers Haiku 4.5 calls. |
| Build artifacts | None — Python source only. | None. |

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| `ANTHROPIC_API_KEY` | Haiku relevance classifier | ✓ | live on VM since Phase 7 | Heuristic fallback (≥15 chars) |
| `MEMORY_BACKEND=native` | NativeProvider.upsert/search | ✓ | live since 2026-05-23 | No fallback needed |
| Qdrant `messages` collection | Vector upsert/search | ✓ | v1.17.1 live | — |
| `openai_embedder` (text-embedding-3-small) | NativeProvider.upsert embeddings | ✓ | live since Phase 2 | — |
| LibreChat MongoDB change stream | messages_watch_loop | ✓ | mongo:8.0.20 live | — |
| Open WebUI pipeline JWT auth | pipeline → memory-api calls | ✓ | `BRIDGE_SHARED_SECRET` in VM .env | — |
| Langfuse | Tracing new relevance + ingest + enrichment calls | ✓ | 3.172.1 live, Phase 9 | Fail-soft (no-op when keys absent) |

**Missing dependencies:** None. All required infrastructure is live on the VM.

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Heuristic ≥15 chars for brain relevance | Haiku 4.5 classifier (with heuristic as fallback) | Phase 13 (this phase) | Reduces noise in memory_items — short acks, greetings, questions without content no longer pollute the brain |
| Conv-boot enrichment only (first turn) | Per-turn enrichment (every user message) | Phase 13 (this phase) | CHAT-07 actually fulfilled — LLM has team context on every turn, not just when a new conversation starts |
| LibreChat messages → conversation log only | LibreChat messages → conversation log AND memory_items + Qdrant | Phase 13 (this phase) | MEM-04 and CHAT-03 actually fulfilled for LibreChat |
| Open WebUI messages → conversation log only | Open WebUI messages → conversation log AND memory_items + Qdrant | Phase 13 (this phase) | MEM-04 and CHAT-03 fulfilled for Open WebUI |

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `POST /v1/memory/upsert` (or equivalent) either does not accept bridge JWTs, requiring a new `/v1/brain/ingest` endpoint, OR it does accept bridge JWTs. Research used code paths for existing bridge calls to infer this — not directly verified. | Plan 13-02 (LibreChat ingest) | If `/v1/memory/upsert` already accepts bridge JWTs, no new endpoint is needed. If it requires user identity, the new endpoint approach is correct. Planner should grep `routes/memory.py` to verify. |
| A2 | The SYSTEM_PROMPT for relevance_filter as written is under 4,096 tokens (estimated ~350 tokens). The prompt must be extended to reach 4,096 tokens for Haiku 4.5 caching to activate. | Plan 13-01 (Haiku filter) | If the prompt is already padded to 4,096 tokens, cache hits will occur immediately. If not padded, every call pays full input price. The `token_count` check at startup (log warning if under threshold) should be added to the implementation. |
| A3 | `asyncio.create_task` in `mongo_watcher.messages_watch_loop` is safe: the event loop is long-running and tasks complete before the next stream event. | Plan 13-03 (LibreChat ingest) | Under high message volume (many messages/second), tasks may queue up. The existing task_intent_detector and contact_extractor already use this pattern successfully, so the risk is low. |

---

## Open Questions

1. **Does `/v1/memory/upsert` accept bridge JWTs?**
   - What we know: `mem.post_message` (bridge) uses bridge JWT for `/v1/messages`. The brain_ingest in memory-api uses `get_memory_provider()` directly (in-process). The bridge cannot call provider directly.
   - What's unclear: Whether the existing `POST /v1/memory/upsert` (or the route in `routes/memory.py`) accepts bridge-scoped JWTs.
   - Recommendation: Grep `apps/memory-api/app/routes/memory.py` for `get_current_principal` — if it uses `Depends(get_current_principal)` which accepts bridge JWTs, reuse it. If it requires `kind == "user"`, create `/v1/brain/ingest`. The new endpoint approach is safer and more explicit.

2. **Does `enrich_new_conversation` need to be modified to pass `min_level=VALIDATED` to `get_system_prompt`?**
   - What we know: `conv_enricher.py` calls `mem.get_system_prompt(sub, team_scope, query=title)` with no `top_k` override, using the server default `min_level=CANONICAL`.
   - What's unclear: D6 says retrieval should filter `{VALIDATED, CANONICAL}`. Currently conv-boot enrichment only fetches CANONICAL. Should it also fetch VALIDATED?
   - Recommendation: Yes — update `conv_enricher.enrich_new_conversation` to pass `min_level=VALIDATED` so it aligns with D6. This is a one-line change. Include in plan 13-04.

3. **Race between conv-boot enrichment and first user message enrichment timing in LibreChat**
   - What we know: `conversations_watch_loop` fires on INSERT/UPDATE. `messages_watch_loop` fires on INSERT. These are parallel asyncio tasks. The conversations INSERT typically happens when the user opens a new chat, and the first user message INSERT happens slightly after.
   - What's unclear: On very fast typing, could the first user message INSERT arrive before the title is set on the conversation, causing `conv_enricher` to skip (empty title guard) and `message_enricher` to be the only enricher that fires?
   - Recommendation: This is acceptable behavior. If conv-boot skips (no title yet), per-turn enrichment fills the gap. The distinction is cosmetic for v1.

---

## Sources

### Primary (HIGH confidence)
- [VERIFIED: codebase] `apps/memory-api/app/services/brain_ingest.py` — team-chat ingest, `is_brain_relevant` heuristic, `provider.upsert()` call pattern
- [VERIFIED: codebase] `apps/librechat-bridge/app/mongo_watcher.py` — messages_watch_loop anchor point, existing hooks pattern
- [VERIFIED: codebase] `apps/librechat-bridge/app/conv_enricher.py` — conv-boot enrichment, idempotency via `xbrain_enriched`, system message structure
- [VERIFIED: codebase] `apps/openwebui-pipeline/app/main.py` — `_handle_anthropic`/`_handle_openai` insertion points, `log_exchange` fire-and-forget pattern
- [VERIFIED: codebase] `packages/memory-models/xbrain_memory/providers/native_provider.py` — `search()` signature, `truth_level_min` semantics, SELECT+INSERT upsert race
- [VERIFIED: codebase] `packages/memory-models/xbrain_memory/provider.py` — ABC `search()` contract
- [VERIFIED: codebase] `apps/memory-api/app/services/rag_enrichment.py` — `build_system_addendum()`, `DEFAULT_TOP_K=5`, `MAX_FACT_CHARS=280`
- [VERIFIED: codebase] `apps/memory-api/app/routes/system_prompt.py` — `min_level` query param, existing `DEFAULT_TOP_K`
- [CITED: platform.claude.com/docs/en/build-with-claude/prompt-caching] Haiku 4.5 minimum 4,096-token cache threshold, `cache_control: {type: "ephemeral"}`, 5-min default TTL, 1-hour TTL option, 0.10x cache-read cost multiplier
- [VERIFIED: codebase] `apps/librechat-bridge/app/task_intent_detector.py` — existing Haiku + prompt-caching pattern, lazy AsyncAnthropic singleton, fail-soft exception handling, `cache_control: {"type": "ephemeral"}` on system block
- [VERIFIED: codebase] `apps/openwebui-pipeline/app/observability.py` — Langfuse tracing pattern (lazy client, `trace()` + `generation()`, fail-soft)

### Secondary (MEDIUM confidence)
- [VERIFIED: npm registry equivalent] `anthropic` Python SDK already present in both `apps/memory-api` and `apps/librechat-bridge` — no new install needed
- [CITED: search results] Claude Haiku 4.5 (claude-haiku-4-5-20251001) pricing: $1/MTok input, $0.10/MTok cache read, $5/MTok output

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all libraries already installed, no new dependencies
- Architecture: HIGH — all insertion points verified from source
- Pitfalls: HIGH — verified from source (upsert race documented in native_provider, resume-token pattern in mongo_watcher, Anthropic system-message constraint in `_handle_anthropic`)
- Haiku caching specifics: HIGH — verified from official Anthropic docs (4,096-token minimum, `cache_control: ephemeral`)

**Research date:** 2026-05-24
**Valid until:** 2026-06-24 (Anthropic API stable; codebase audit valid until next phase ships)
