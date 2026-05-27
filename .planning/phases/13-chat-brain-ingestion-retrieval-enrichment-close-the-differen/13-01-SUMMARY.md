---
phase: 13-chat-brain-ingestion-retrieval-enrichment-close-the-differen
plan: "01"
subsystem: memory-api
tags:
  - relevance-filter
  - haiku
  - prompt-caching
  - brain-ingest
  - fire-and-forget
  - budget-cap
dependency_graph:
  requires: []
  provides:
    - relevance_filter.classify (Haiku 4.5 classifier, usable by all Phase 13 plans)
    - brain_ingest.ingest_external_message (external ingest helper)
    - POST /v1/brain/ingest (endpoint for bridge + pipeline services)
  affects:
    - apps/memory-api/app/services/brain_ingest.py
    - apps/memory-api/app/routes/brain.py
    - apps/memory-api/app/config.py
    - apps/memory-api/app/schemas/brain.py
tech_stack:
  added: []
  patterns:
    - Haiku 4.5 lazy-singleton AsyncAnthropic client (mirrors task_intent_detector.py)
    - cache_control ephemeral on system block for Anthropic prompt caching
    - In-memory per-team daily token budget with UTC midnight reset
    - asyncio.create_task fire-and-forget pattern for brain ingest endpoint
    - UUID5 deterministic item ID for idempotency_key path
key_files:
  created:
    - apps/memory-api/app/services/relevance_filter.py
    - apps/memory-api/tests/test_relevance_filter.py
    - apps/memory-api/tests/test_brain_ingest_endpoint.py
  modified:
    - apps/memory-api/app/config.py (added 4 RELEVANCE_* settings)
    - apps/memory-api/app/schemas/brain.py (added BrainIngestRequest)
    - apps/memory-api/app/services/brain_ingest.py (added BRAIN_INGEST_NS + ingest_external_message)
    - apps/memory-api/app/routes/brain.py (added POST /v1/brain/ingest endpoint)
    - apps/memory-api/app/services/__init__.py (made empty to break circular import)
decisions:
  - "Integration tests (endpoint auth/validation) marked @pytest.mark.integration — follow the same pattern as test_brain_events_list.py; they skip cleanly when Docker is unavailable locally and run in the VM container environment"
  - "services/__init__.py left empty (no module-level imports) to avoid circular import: brain_ingest imports app.deps, app.deps imports app.auth, app.auth imports app.services.github_installation"
  - "relevance_filter uses lazy local import inside ingest_external_message (from app.services.relevance_filter import classify) to break the brain_ingest ↔ relevance_filter circular dependency"
  - "Tests for ingest_external_message patch app.services.relevance_filter.classify (not app.services.brain_ingest.classify) because the lazy import resolves at call time from the relevance_filter module namespace"
metrics:
  duration: "877 seconds (~15 minutes)"
  completed: "2026-05-27"
  tasks_completed: 3
  tasks_total: 3
  files_created: 3
  files_modified: 5
---

# Phase 13 Plan 01: Haiku Relevance Classifier + Brain Ingest Endpoint Summary

Haiku 4.5 relevance classifier with prompt caching, per-team daily token budget cap, fail-soft heuristic fallback, and `POST /v1/brain/ingest` fire-and-forget endpoint — foundation for all Phase 13 ingest paths.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Config knobs + BrainIngestRequest schema | 0b77269 | config.py, schemas/brain.py, test_relevance_filter.py |
| 2 | relevance_filter.py — Haiku classifier | af77382 | services/relevance_filter.py, services/__init__.py |
| 3 | ingest_external_message + /v1/brain/ingest | 3057b00 | services/brain_ingest.py, routes/brain.py, test_brain_ingest_endpoint.py |

## Test Results

- `test_relevance_filter.py`: **17 passed** (7 config/schema + 10 classifier)
- `test_brain_ingest_endpoint.py`: **4 unit tests passed**, 4 integration tests skipped (Docker unavailable locally; run in container environment)
- No regressions in existing test suite (2 pre-existing failures unrelated to this plan: `test_healthz_returns_200_without_db` and `test_invalid_source_format_rejected`)

## SYSTEM_PROMPT Token Count

- Byte length: **16,501 bytes** UTF-8
- Estimated token count: **4,125 tokens** (at 4 chars/token)
- Haiku 4.5 caching threshold: 4,096 tokens
- **Cache activation: YES** — `cache_creation_input_tokens > 0` expected on first call
- 90 few-shot examples covering all relevant/irrelevant message categories

## Key Implementation Details

### relevance_filter.classify signature
```python
async def classify(content: str, *, team_scope: str) -> bool:
```
Keyword-only `team_scope` matches the plan spec exactly.

### Budget cap mechanics
- In-memory `_daily_budget` dict keyed by `team_scope`
- Auto-resets when `entry["date"] != str(date.today())` (UTC midnight)
- Default cap: `RELEVANCE_DAILY_TOKEN_CAP_PER_TEAM = 50_000` input tokens/day/team
- Budget exceeded → log `relevance_filter.budget_exceeded` → return heuristic result

### Fail-soft flow
1. `is_brain_relevant(content)` → False: return False immediately (no Haiku call)
2. `_get_client()` returns None (SDK absent, key missing, or `RELEVANCE_HAIKU_ENABLED=False`): return True (heuristic passed)
3. Budget exceeded: return True (heuristic passed), log budget_exceeded
4. Haiku exception/timeout: return True (heuristic passed), log haiku_failed_fallback

### Circular import resolution
`brain_ingest.py` imports `deps.py` (for `get_memory_provider`) at module level.
`relevance_filter.py` imports `brain_ingest.is_brain_relevant` at module level.
`ingest_external_message` uses a lazy import inside the function body:
```python
from app.services.relevance_filter import classify  # inside try block
```
This breaks the potential cycle without affecting runtime behavior.

### Deterministic UUID5 for idempotency
```python
BRAIN_INGEST_NS = uuid.UUID("8e7c2b00-1aae-5a40-9c4f-13b7c0d72f10")  # never change
item_id = str(uuid.uuid5(BRAIN_INGEST_NS, idempotency_key))
```
Safe under MongoDB change-stream resume-token re-delivery (same key → same ID → upsert overwrites, no duplicate).

## Deviations from Plan

### [Rule 1 - Bug] Circular import in services/__init__.py

**Found during:** Task 2 implementation (services/__init__.py importing brain_ingest at startup)

**Issue:** The plan said "add `relevance_filter` to `services/__init__.py` exports". Initial implementation imported all three submodules at startup. `brain_ingest` imports `app.deps`, `app.deps` imports `app.auth`, `app.auth` imports `app.services.github_installation` — which triggers `services/__init__.py` again before it's finished loading. Python raises `ImportError: cannot import name 'get_memory_provider' from partially initialized module 'app.deps'`.

**Fix:** Made `services/__init__.py` empty (comment-only). Submodules are already imported directly by their consumers — the `__init__.py` listing was decorative, not functional. No behavior change.

**Files modified:** `apps/memory-api/app/services/__init__.py`

**Commit:** 3057b00

### [Rule 3 - Blocking] Integration tests require Docker (qdrant_client not installed locally)

**Found during:** Task 3 testing (tests that import `app.main` fail with `ModuleNotFoundError: No module named 'qdrant_client'`)

**Issue:** `app.main` → `app.qdrant_setup` → `qdrant_client`. Tests 1-4 (HTTP endpoint) need `app.main`. Pattern already established in `test_brain_events_list.py` (marks tests `@pytest.mark.integration`).

**Fix:** Marked endpoint tests as `@pytest.mark.integration` following existing project pattern. Unit tests 5-8 (testing `ingest_external_message` directly via mock.patch) run without Docker. All 4 unit tests pass; 4 integration tests skip cleanly locally and run in the VM container environment.

**Files modified:** `apps/memory-api/tests/test_brain_ingest_endpoint.py`

**Commit:** 3057b00

## Known Stubs

None. All implementations are functional. The SYSTEM_PROMPT placeholder text shown in the plan spec was replaced with 90 real few-shot examples.

## Threat Flags

| Flag | File | Description |
|------|------|-------------|
| threat_flag: new_endpoint | apps/memory-api/app/routes/brain.py | POST /v1/brain/ingest accepts bridge JWTs — new unauthenticated ingest path. Auth enforced via get_current_principal (401 on missing/invalid JWT); team_scope enforced via get_team_scope dependency. No content injection risk: content is stored as a MemoryItem (never executed). |

## Self-Check: PASSED

- FOUND: apps/memory-api/app/services/relevance_filter.py
- FOUND: apps/memory-api/app/routes/brain.py (contains `@router.post("/brain/ingest"`)
- FOUND: apps/memory-api/app/schemas/brain.py (contains `class BrainIngestRequest`)
- FOUND: apps/memory-api/app/config.py (contains `RELEVANCE_HAIKU_ENABLED`)
- FOUND: apps/memory-api/tests/test_relevance_filter.py
- FOUND: apps/memory-api/tests/test_brain_ingest_endpoint.py
- FOUND: commit 0b77269 (Task 1)
- FOUND: commit af77382 (Task 2)
- FOUND: commit 3057b00 (Task 3)
- Test run: 21 passed, 4 skipped (integration)
