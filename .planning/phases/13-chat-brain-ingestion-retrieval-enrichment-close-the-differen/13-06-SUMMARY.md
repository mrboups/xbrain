---
phase: 13-chat-brain-ingestion-retrieval-enrichment-close-the-differen
plan: "06"
subsystem: openwebui-pipeline
tags:
  - brain-ingest
  - per-turn-enrichment
  - anthropic-system-param
  - openwebui
  - fire-and-forget
  - idempotency
  - kill-switch
dependency_graph:
  requires:
    - 13-01 (POST /v1/brain/ingest endpoint + BrainIngestRequest schema)
    - 13-04/05 (librechat-bridge reference implementation for brain_ingest + get_system_prompt)
  provides:
    - openwebui-pipeline brain_ingest fire-and-forget on every user message
    - openwebui-pipeline per-turn enrichment via GET /v1/system-prompt?min_level=VALIDATED
    - BRAIN_INGEST_ENABLED kill-switch for both paths
  affects:
    - apps/openwebui-pipeline/app/main.py
    - apps/openwebui-pipeline/app/memory_api_client.py
    - apps/openwebui-pipeline/app/config.py
tech_stack:
  added: []
  patterns:
    - asyncio.create_task fire-and-forget for brain ingest (never blocks chat path)
    - Anthropic system= parameter injection (Pitfall 5 — NOT in messages list)
    - OpenAI first system-role message injection
    - hashlib.sha256 idempotency key: openwebui:{conv_id}:{sha256(content)[:32]}
    - Fail-soft: both brain_ingest and enrichment failures are swallowed with structlog warnings
    - BRAIN_INGEST_ENABLED kill-switch disables both ingest and enrichment
    - TDD: RED commit first, GREEN commit after — per-task atomic commits
key_files:
  created:
    - apps/openwebui-pipeline/tests/test_memory_api_client_phase13.py
    - apps/openwebui-pipeline/tests/test_main_brain_ingest_and_enrich.py
  modified:
    - apps/openwebui-pipeline/app/config.py (added 3 Phase 13 knobs)
    - apps/openwebui-pipeline/app/memory_api_client.py (added brain_ingest + get_system_prompt)
    - apps/openwebui-pipeline/app/main.py (wired brain ingest + enrichment into chat())
decisions:
  - "Anthropic system param injection uses system_prefix kwarg on _handle_anthropic — enrichment prepended to existing system content; never injected into messages list (Pitfall 5)"
  - "asyncio.create_task for brain ingest — fire-and-forget; test uses asyncio.sleep(0) to allow task execution before assertion"
  - "kill-switch test patches live_settings.BRAIN_INGEST_ENABLED directly (not via mock.patch object replacement) because main.py imports settings as a module-level name from the same singleton"
  - "get_system_prompt in pipeline client adds min_level param absent from librechat-bridge version — needed for VALIDATED+ filter per CHAT-07"
metrics:
  duration: "~25 minutes"
  completed: "2026-05-27"
  tasks_completed: 2
  tasks_total: 2
  files_created: 2
  files_modified: 3
---

# Phase 13 Plan 06: Open WebUI Brain Ingest + Per-Turn Enrichment Summary

Per-turn brain ingest (fire-and-forget) + VALIDATED+ enrichment injection for Open WebUI pipeline — closes MEM-04, CHAT-03, CHAT-07 for the Open WebUI frontend via `openwebui-pipeline`.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 (RED) | Failing tests for config + client methods | 4902ca5 | test_memory_api_client_phase13.py |
| 1 (GREEN) | Config knobs + brain_ingest + get_system_prompt | e3fb05d | config.py, memory_api_client.py |
| 2 (RED) | Failing tests for chat() wiring | d9e949d | test_main_brain_ingest_and_enrich.py |
| 2 (GREEN) | Wire enrichment + brain ingest into chat() | d871276 | main.py, test_main_brain_ingest_and_enrich.py |

## Test Results

- `test_memory_api_client_phase13.py`: **8 passed** (3 config + 2 signature + 2 request shape + 1 fail-soft)
- `test_main_brain_ingest_and_enrich.py`: **9 passed** (all 9 chat handler tests)
- Full suite: **28 passed** (11 pre-existing + 17 new)
- No regressions

## Key Implementation Details

### Config knobs (apps/openwebui-pipeline/app/config.py)
```python
BRAIN_INGEST_ENABLED: bool = True
CHAT07_TOP_K: int = 5
CHAT07_TRUTH_FILTER_MIN_LEVEL: str = "VALIDATED"
```

### brain_ingest + get_system_prompt on MemoryApiClient
Both methods mirror the existing `post_message` tenacity retry pattern (2 attempts, exponential backoff 0.3–2s). `get_system_prompt` adds the `min_level` parameter (absent from librechat-bridge v1) to support VALIDATED+ truth_level filtering.

### Anthropic system-param injection (Pitfall 5 compliance)
```python
# In _handle_anthropic — enrichment prepended to system= parameter:
parts = []
if system_prefix:
    parts.append(system_prefix)
if system_msgs:
    parts.append("\n".join(system_msgs))
system_param = "\n\n".join(parts) if parts else None
```
This ensures `role=system` never appears in the `messages` list sent to Anthropic.

### OpenAI enrichment injection
```python
# In _handle_openai — injected as first system-role message:
msgs: list[dict[str, str]] = []
if system_prefix:
    msgs.append({"role": "system", "content": system_prefix})
msgs.extend({"role": m.role, "content": m.content} for m in body.messages)
```

### chat() wiring (after slash-command intercept)
```python
enrichment_addendum = ""
if settings.BRAIN_INGEST_ENABLED and user_message:
    asyncio.create_task(
        _brain_ingest_owui(
            mem=mem, sub=sub, team_scope=team_scope,
            content=user_message, source=f"openwebui:{body.model}",
            conversation_id=conversation_id,
        )
    )
    enrichment_addendum = await _fetch_enrichment_owui(
        mem=mem, sub=sub, team_scope=team_scope, user_message=user_message,
    )
```

### Idempotency key construction
```python
idem_key = f"openwebui:{conversation_id}:{hashlib.sha256(content.encode('utf-8')).hexdigest()[:32]}"
```
Matches the idempotency contract from Plan 13-01 (UUID5 dedup in `ingest_external_message`).

## Deviations from Plan

### [Rule 1 - Bug] Test 6 asyncio.create_task timing

**Found during:** Task 2 GREEN phase testing

**Issue:** `mock_mem.brain_ingest.assert_called_once()` failed because `asyncio.create_task()` schedules the coroutine but does not execute it before the test's assertion runs. The test was immediately checking the mock after the HTTP response returned, before the event loop had a chance to run the task.

**Fix:** Added `await asyncio.sleep(0)` inside the `with patch(...)` block after the HTTP call and before the assertion. This yields control to the event loop, allowing the scheduled task to execute.

**Files modified:** `apps/openwebui-pipeline/tests/test_main_brain_ingest_and_enrich.py`

**Commit:** d871276

### [Rule 1 - Bug] Kill-switch test (Test 7) mock strategy

**Found during:** Task 2 RED phase — test passed unexpectedly pre-implementation

**Issue:** Original test patched `app.main.settings` as a new mock object. This prevented `check_auth` from matching the API key (mock object had no `PIPELINE_API_KEY` attribute matching the real key), causing a 401 instead of 200.

**Fix:** Changed to directly mutate the attribute on the live settings singleton (`live_settings.BRAIN_INGEST_ENABLED = False`) with a `try/finally` restore. This is correct because `main.py` and `config.py` share the same singleton object.

**Files modified:** `apps/openwebui-pipeline/tests/test_main_brain_ingest_and_enrich.py`

## Known Stubs

None. All implementations are functional. Both brain ingest and enrichment are fully wired to production endpoints (`/v1/brain/ingest` and `/v1/system-prompt`).

## Threat Flags

None. No new network endpoints introduced. The pipeline already has authenticated access to memory-api via bridge JWT. The new methods reuse the same auth pattern as `post_message` and `post_conversation`.

## Self-Check: PASSED

- FOUND: apps/openwebui-pipeline/app/config.py (contains `BRAIN_INGEST_ENABLED: bool = True`)
- FOUND: apps/openwebui-pipeline/app/memory_api_client.py (contains `async def brain_ingest(`)
- FOUND: apps/openwebui-pipeline/app/memory_api_client.py (contains `async def get_system_prompt(`)
- FOUND: apps/openwebui-pipeline/app/memory_api_client.py (contains `/v1/brain/ingest`)
- FOUND: apps/openwebui-pipeline/app/memory_api_client.py (contains `/v1/system-prompt`)
- FOUND: apps/openwebui-pipeline/app/main.py (contains `async def _brain_ingest_owui(`)
- FOUND: apps/openwebui-pipeline/app/main.py (contains `async def _fetch_enrichment_owui(`)
- FOUND: apps/openwebui-pipeline/app/main.py (contains `enrichment_addendum = await _fetch_enrichment_owui(`)
- FOUND: apps/openwebui-pipeline/app/main.py (contains `system_prefix=enrichment_addendum` — 2 occurrences)
- FOUND: apps/openwebui-pipeline/app/main.py (contains `hashlib.sha256`)
- FOUND: commit 4902ca5 (Task 1 RED)
- FOUND: commit e3fb05d (Task 1 GREEN)
- FOUND: commit d9e949d (Task 2 RED)
- FOUND: commit d871276 (Task 2 GREEN)
- Test run: 28 passed, 0 failed
