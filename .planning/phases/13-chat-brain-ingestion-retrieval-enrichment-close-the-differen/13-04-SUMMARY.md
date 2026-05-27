---
phase: 13-chat-brain-ingestion-retrieval-enrichment-close-the-differen
plan: "04"
subsystem: librechat-bridge
tags:
  - brain-ingest
  - fire-and-forget
  - idempotency
  - librechat
  - mongo-change-stream
  - kill-switch
dependency_graph:
  requires:
    - "13-01 (POST /v1/brain/ingest endpoint + BrainIngestRequest schema)"
  provides:
    - "MemoryApiClient.brain_ingest (POST /v1/brain/ingest with bridge JWT)"
    - "_maybe_ingest_to_brain (fire-and-forget hook for user messages in messages_watch_loop)"
    - "BRAIN_INGEST_ENABLED env kill-switch"
  affects:
    - apps/librechat-bridge/app/mongo_watcher.py
    - apps/librechat-bridge/app/memory_api_client.py
    - apps/librechat-bridge/app/config.py
tech_stack:
  added: []
  patterns:
    - "asyncio.create_task fire-and-forget for brain ingest (mirrors Phase 8 contact extraction pattern)"
    - "tenacity retry (3 attempts, exponential backoff) mirroring existing post_message contract"
    - "try/except BLE001 fail-soft wrapper in _maybe_ingest_to_brain"
    - "Deterministic idempotency_key=f'librechat:{librechat_id}' for resume-token re-delivery safety"
key_files:
  created:
    - apps/librechat-bridge/tests/test_memory_api_client_brain.py
    - apps/librechat-bridge/tests/test_mongo_watcher_brain_ingest.py
  modified:
    - apps/librechat-bridge/app/config.py (added BRAIN_INGEST_ENABLED)
    - apps/librechat-bridge/app/memory_api_client.py (added brain_ingest method)
    - apps/librechat-bridge/app/mongo_watcher.py (added _maybe_ingest_to_brain + wired into loop)
decisions:
  - "brain_ingest uses same tenacity retry config as post_message (httpx.HTTPError, 3 attempts, 0.5-4s backoff) — consistent contract across all MemoryApiClient methods"
  - "_maybe_ingest_to_brain placed between forwarded_message log and contact extraction in messages_watch_loop — additive, non-interfering with Phase 7/8 hooks"
  - "Tests patch app.mongo_watcher.settings per-test (not via importlib.reload) to avoid module-state pollution across test suite; singleton reload in test_brain_ingest_kill_switch_disabled is isolated via monkeypatch"
metrics:
  duration: "~25 minutes"
  completed: "2026-05-24"
  tasks_completed: 2
  tasks_total: 2
  files_created: 2
  files_modified: 3
---

# Phase 13 Plan 04: LibreChat Brain Ingest Hook Summary

Fire-and-forget hook in `messages_watch_loop` that sends every LibreChat user message to `POST /v1/brain/ingest` with idempotency key `librechat:{mongo_id}`, gated by `BRAIN_INGEST_ENABLED` kill-switch and wrapped in try/except so ingest failures never break the change-stream loop.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 RED | Failing tests for brain_ingest + config | 2bf81eb | tests/test_memory_api_client_brain.py |
| 1 GREEN | BRAIN_INGEST_ENABLED config + brain_ingest method | 2a1e0ad | config.py, memory_api_client.py, tests/test_memory_api_client_brain.py |
| 2 RED | Failing tests for _maybe_ingest_to_brain hook | 1edd0d1 | tests/test_mongo_watcher_brain_ingest.py |
| 2 GREEN | _maybe_ingest_to_brain + messages_watch_loop wiring | 0070565 | mongo_watcher.py, tests/test_mongo_watcher_brain_ingest.py |

## Test Results

- `test_memory_api_client_brain.py`: **7 passed**
- `test_mongo_watcher_brain_ingest.py`: **7 passed**
- No regressions in existing suite (21 pre-existing tests still pass)
- **Total: 35 passed, 3 warnings (RuntimeWarning from asyncio.create_task mock — expected)**

## Key Implementation Details

### MemoryApiClient.brain_ingest

Located at `apps/librechat-bridge/app/memory_api_client.py` (line ~108), method mirrors `post_message` exactly:

```python
@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=0.5, max=4),
       retry=retry_if_exception_type(httpx.HTTPError), reraise=True)
async def brain_ingest(self, *, sub, team_scope, content, source,
                       metadata=None, project_scope=None) -> dict:
    # POSTs to /v1/brain/ingest with bridge JWT + X-Team-Scope
```

### _maybe_ingest_to_brain guard chain

```
BRAIN_INGEST_ENABLED=False  → return (kill-switch)
role != 'user'              → return (assistant messages excluded)
content.strip() == ''       → return (empty content excluded)
librechat_id missing        → return (no idempotency anchor)
→ try: await mem.brain_ingest(...)
  except Exception: log.warning(...)  # never propagates
```

### Insertion point in messages_watch_loop

```
await mem.post_message(...)       # existing (unchanged)
log.info("forwarded_message")     # existing (unchanged)
asyncio.create_task(              # NEW — Phase 13 plan 13-04
    _maybe_ingest_to_brain(...)
)
asyncio.create_task(              # Phase 8 (unchanged)
    extract_contacts_from_message(...)
)
save_resume_token(...)            # existing (unchanged)
```

### Idempotency key

`metadata["idempotency_key"] = f"librechat:{librechat_id}"` where `librechat_id = str(mongo_doc["_id"])`. Server-side `uuid5(BRAIN_INGEST_NS, key)` produces a deterministic `MemoryItem.id` so Mongo resume-token re-delivery is harmless.

## Deviations from Plan

### [Rule 1 - Bug] httpx.HTTPStatusError is a subclass of httpx.HTTPError

**Found during:** Task 1 GREEN (test_brain_ingest_single_attempt_on_422 failed)

**Issue:** Initial test assumed `httpx.HTTPStatusError` would NOT be matched by `retry_if_exception_type(httpx.HTTPError)` so 4xx would cause 1 attempt only. In fact `httpx.HTTPStatusError` IS a subclass of `httpx.HTTPError` — all errors get retried up to 3 times (same behavior as `post_message`).

**Fix:** Updated test assertion from `call_count == 1` to `call_count == 3` and renamed test to `test_brain_ingest_retries_on_422_matches_post_message_contract` with explanatory comment. No behavior change to production code.

**Commit:** 2a1e0ad

### [Rule 1 - Bug] Module-level settings singleton pollution across tests

**Found during:** Task 2 GREEN (test_user_message_triggers_brain_ingest failed when run after test_brain_ingest_kill_switch_disabled)

**Issue:** `test_brain_ingest_kill_switch_disabled` in `test_memory_api_client_brain.py` calls `importlib.reload(app.config)` with `BRAIN_INGEST_ENABLED=false` set in env. Even after `monkeypatch` restores the env var, the `app.mongo_watcher.settings` module-level reference still points to the reloaded Settings object (which has `BRAIN_INGEST_ENABLED=False`). Subsequent tests calling `_maybe_ingest_to_brain` found the kill-switch active.

**Fix:** Changed tests 1, 2, 4, 7 in `test_mongo_watcher_brain_ingest.py` to create an explicit `Settings(BRAIN_INGEST_ENABLED=True)` instance and patch it via `patch("app.mongo_watcher.settings", enabled_settings)` for test isolation. No production code change.

**Commit:** 0070565

## Known Stubs

None. All implementations are functional. `BRAIN_INGEST_ENABLED` defaults to `True` so the hook is on by default in production.

## Threat Flags

None. The new `_maybe_ingest_to_brain` path calls an existing authenticated endpoint (`/v1/brain/ingest` requiring bridge JWT, already audited in Plan 13-01 threat flags). No new network surface introduced.

## Self-Check: PASSED

- FOUND: apps/librechat-bridge/app/config.py contains `BRAIN_INGEST_ENABLED: bool = True`
- FOUND: apps/librechat-bridge/app/memory_api_client.py contains `async def brain_ingest(`
- FOUND: apps/librechat-bridge/app/memory_api_client.py contains `/v1/brain/ingest`
- FOUND: apps/librechat-bridge/app/mongo_watcher.py contains `async def _maybe_ingest_to_brain(`
- FOUND: apps/librechat-bridge/app/mongo_watcher.py contains `await mem.brain_ingest(`
- FOUND: apps/librechat-bridge/app/mongo_watcher.py contains `f"librechat:{librechat_id}"`
- FOUND: apps/librechat-bridge/tests/test_memory_api_client_brain.py (7 tests)
- FOUND: apps/librechat-bridge/tests/test_mongo_watcher_brain_ingest.py (7 tests)
- FOUND: commits 2bf81eb, 2a1e0ad, 1edd0d1, 0070565
- Test run: 35 passed, 0 failed
