---
phase: 13-chat-brain-ingestion-retrieval-enrichment-close-the-differen
plan: "05"
subsystem: librechat-bridge
tags: [rag, enrichment, per-turn, chat, memory, tdd]
dependency_graph:
  requires: [13-01, 13-04]
  provides: [CHAT-07]
  affects: [librechat-bridge, memory-api]
tech_stack:
  added: []
  patterns:
    - "Fire-and-forget asyncio.create_task for per-turn RAG enrichment (fail-soft)"
    - "Idempotency via messageId pre-insert find_one guard"
    - "TruthLevel >= VALIDATED semantics via min_level query param"
key_files:
  created:
    - apps/librechat-bridge/app/message_enricher.py
    - apps/librechat-bridge/tests/test_message_enricher.py
  modified:
    - apps/librechat-bridge/app/memory_api_client.py
    - apps/librechat-bridge/app/config.py
    - apps/librechat-bridge/app/conv_enricher.py
    - apps/librechat-bridge/tests/test_conv_enricher.py
    - apps/librechat-bridge/tests/test_memory_api_client_brain.py
decisions:
  - "conv_enricher keeps source='memory-api:rag-canonical' for backward compat; per-turn uses source='memory-api:rag-validated' to distinguish the two injection paths in Mongo"
  - "Integration tests verify coroutine scheduling (name in create_task arg) rather than execution, avoiding asyncio timing coupling"
  - "FakeMemClient in test_conv_enricher accepts **kwargs for forward compat with signature extensions"
metrics:
  duration: "11 minutes"
  completed: "2026-05-27"
  tasks: 2
  files: 7
---

# Phase 13 Plan 05: Per-Turn RAG Enricher (CHAT-07) Summary

Per-turn LibreChat RAG enrichment shipped: every user message INSERT in the Mongo change stream now triggers `message_enricher.enrich_turn` (VALIDATED+ facts, top_k=5, idempotent, fail-soft), closing CHAT-07.

## What Was Built

### `apps/librechat-bridge/app/message_enricher.py`

New module. `async def enrich_turn(msg_doc, db, mem, *, sub, team_scope) -> bool`:

1. Validates `conversationId` and `_id` — returns False early if missing
2. Idempotency check: `db["messages"].find_one({"messageId": f"xbrain-turn-{conv_id}-{msg_id}"})` BEFORE calling memory-api
3. Calls `mem.get_system_prompt(min_level=CHAT07_TRUTH_FILTER_MIN_LEVEL, top_k=CHAT07_TOP_K)`
4. Empty addendum (no facts) → returns False (no empty system message injected)
5. Inserts system message with shape:
   ```
   messageId: xbrain-turn-{conv_id}-{msg_id}
   user: "system"
   isCreatedByUser: False
   metadata.xbrain_turn_enrichment: True
   metadata.source: "memory-api:rag-validated"
   metadata.trigger_msg_id: str(msg_id)
   ```
6. All error paths (memory-api, db) return False and log a warning — never raise

### `apps/librechat-bridge/app/mongo_watcher.py` (diff)

- Added `from app.message_enricher import enrich_turn`
- In `messages_watch_loop`, after the brain-ingest `create_task`:
  ```python
  if payload.get("role") == "user":
      asyncio.create_task(
          enrich_turn(doc, db, mem, sub=payload["sub"], team_scope=team_scope)
      )
  ```
- User messages only; `doc` (Mongo fullDocument with `_id`) passed rather than `payload` (mapped form)

### `apps/librechat-bridge/app/memory_api_client.py` (diff)

- `get_system_prompt` signature extended with `min_level: str | None = None`
- When `min_level` is not None, adds `params["min_level"] = min_level` to the GET request
- Default None preserves server-side CANONICAL default (backward compat)

### `apps/librechat-bridge/app/config.py` (diff)

```python
# Phase 13 plan 13-05 — Chat enrichment (CHAT-07)
CHAT07_TOP_K: int = 5
CHAT07_TRUTH_FILTER_MIN_LEVEL: str = "VALIDATED"  # VALIDATED + CANONICAL + PUBLIC per >= semantics
```

### `apps/librechat-bridge/app/conv_enricher.py` (diff)

- Added `from app.config import settings`
- `get_system_prompt` call now passes `top_k=settings.CHAT07_TOP_K` and `min_level=settings.CHAT07_TRUTH_FILTER_MIN_LEVEL` (D6 alignment: VALIDATED instead of server-default CANONICAL)
- Source label kept as `"memory-api:rag-canonical"` for backward compat with existing Mongo queries that filter by this label

## Idempotency Design

Two distinct messageId namespaces prevent collision and double-enrichment:

| Enricher | messageId shape | Fires when |
|----------|----------------|-----------|
| conv_enricher | `xbrain-system-{conv_id}` | Conversation INSERT/UPDATE (title set) |
| message_enricher | `xbrain-turn-{conv_id}-{msg_id}` | User message INSERT |

Under Mongo change-stream resume-token re-delivery (both enrichers can fire again), the pre-insert `find_one` guard on the target `messageId` makes both idempotent.

## First-Turn Case

On message #1 in a conversation, both enrichers may fire:
- `conv_enricher` fires on the conversation INSERT (uses conv title as query)
- `enrich_turn` fires on the message INSERT (uses message text as query)

The two system messages have different `messageId` values — no collision. Both contribute context from different angles (title vs. actual first message content), which is desirable.

## Truth Level Semantics

`min_level=VALIDATED` instructs the server-side `NativeProvider.search` to apply `TruthLevel.__ge__` filtering, which returns items where `truth_level IN (VALIDATED, CANONICAL, PUBLIC)`. This is Phase 13 decision D6: include recently-validated facts that the old CANONICAL-only filter would miss.

## Test Results

```
tests/test_message_enricher.py      11 passed
tests/test_mongo_watcher_brain_ingest.py  7 passed (no regression)
tests/test_memory_api_client_brain.py    13 passed (6 original + 7 new)
tests/ (full suite)                  52 passed
```

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] FakeMemClient in test_conv_enricher.py rejected new kwargs**
- **Found during:** Task 1 GREEN phase
- **Issue:** `FakeMemClient.get_system_prompt` only accepted `sub`, `team_scope`, `query` — adding `top_k` and `min_level` to the real call caused `TypeError: got an unexpected keyword argument 'top_k'` in 3 existing tests
- **Fix:** Added `**kwargs` to `FakeMemClient.get_system_prompt` signature — collects extra params into `self.calls` entries for future assertions
- **Files modified:** `apps/librechat-bridge/tests/test_conv_enricher.py`
- **Commit:** e0e791f

**2. [Rule 1 - Bug] Integration test path used project-root-relative path inside pytest CWD**
- **Found during:** Task 1 GREEN phase
- **Issue:** `test_conv_enricher_uses_validated_min_level` used `Path("apps/librechat-bridge/app/conv_enricher.py")` — fails when pytest runs from `apps/librechat-bridge/`
- **Fix:** Changed to `Path(__file__).parent.parent / "app" / "conv_enricher.py"` for pytest-CWD-independent resolution
- **Files modified:** `apps/librechat-bridge/tests/test_memory_api_client_brain.py`
- **Commit:** e0e791f

**3. [Rule 1 - Bug] Integration test relied on task execution rather than scheduling**
- **Found during:** Task 2 GREEN phase
- **Issue:** `test_messages_watch_loop_schedules_enrich_turn_for_user_messages_only` checked `enrich_turn_calls` list which was populated only when the coroutine executed — but `asyncio.ensure_future` schedules execution for the next loop tick, after the test's `messages_watch_loop` had already completed
- **Fix:** Changed to check coroutine name in `scheduled_coro_names` list (populated when `create_task` is called, not when the coroutine runs). Added `await asyncio.sleep(0)` to flush the task queue
- **Files modified:** `apps/librechat-bridge/tests/test_message_enricher.py`
- **Commit:** 9c6a4fd

**4. [Rule 1 - Bug] Accidental commit to main branch (immediately reverted)**
- **Found during:** Task 1 RED phase commit
- **Issue:** First RED-phase commit was mistakenly run from `D:/VSC/xbrain` (main branch) instead of the worktree `D:/VSC/xbrain/.claude/worktrees/agent-ad4b8fc3cf519ce9b`
- **Fix:** `git reset --hard HEAD~1` on main to remove the commit; re-applied changes from the correct worktree directory
- **Main branch state:** Restored to `2bd0fa2` (no lingering diff)

## Sample Mongo Output (Production Smoke Test Shape)

After a conversation with at least one VALIDATED memory item, both system messages should appear:

```js
db.messages.find(
  {conversationId: "<conv-id>"},
  {messageId: 1, "metadata.xbrain_turn_enrichment": 1, "metadata.fact_count": 1, _id: 0}
).toArray()

// Expected:
[
  { messageId: "xbrain-system-<conv-id>", metadata: { fact_count: 2 } },
  { messageId: "xbrain-turn-<conv-id>-<msg-id>", metadata: { xbrain_turn_enrichment: true, fact_count: 2 } }
]
```

## Self-Check

### Check created files exist:
- [x] `apps/librechat-bridge/app/message_enricher.py` — FOUND
- [x] `apps/librechat-bridge/tests/test_message_enricher.py` — FOUND

### Check commits exist:
- [x] `a0b6b83` — test(13-05): failing tests for get_system_prompt min_level + CHAT07 config
- [x] `e0e791f` — feat(13-05): extend get_system_prompt + add CHAT07 config + align conv_enricher
- [x] `2c60835` — test(13-05): failing tests for message_enricher.enrich_turn + mongo_watcher
- [x] `9c6a4fd` — feat(13-05): add message_enricher.enrich_turn + wire per-turn enrichment

## Self-Check: PASSED
