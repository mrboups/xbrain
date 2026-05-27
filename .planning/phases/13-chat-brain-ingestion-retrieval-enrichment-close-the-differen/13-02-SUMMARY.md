---
phase: 13-chat-brain-ingestion-retrieval-enrichment-close-the-differen
plan: "02"
subsystem: memory-api
tags:
  - brain-ingest
  - haiku-classifier
  - relevance-filter
  - tdd
  - fire-and-forget
dependency_graph:
  requires:
    - relevance_filter.classify (Plan 13-01)
  provides:
    - ingest_team_message now calls Haiku classifier before upsert
  affects:
    - apps/memory-api/app/services/brain_ingest.py
    - apps/memory-api/tests/test_brain_ingest.py
tech_stack:
  added: []
  patterns:
    - Local function-scoped import to break circular dependency (brain_ingest ↔ relevance_filter)
    - Fail-soft exception handling — ingest errors never propagate to chat-send path
    - TDD RED/GREEN flow: failing tests committed before implementation
key_files:
  created:
    - apps/memory-api/tests/test_brain_ingest.py
  modified:
    - apps/memory-api/app/services/brain_ingest.py
decisions:
  - "Local import inside ingest_team_message (not module-top) mirrors the pattern from ingest_external_message — breaks the brain_ingest↔relevance_filter circular dep"
  - "Patch target for tests is app.services.relevance_filter.classify — the lazy import resolves at call time from the relevance_filter module namespace, consistent with 13-01 test pattern"
  - "Outer try/except in ingest_team_message already covers classify raises — no new exception wrapper needed; exception from classify is caught, logged as brain_ingest.team_message.failed"
metrics:
  duration: "~8 minutes"
  completed: "2026-05-27"
  tasks_completed: 1
  tasks_total: 1
  files_created: 1
  files_modified: 1
---

# Phase 13 Plan 02: Haiku Classifier Swap in ingest_team_message Summary

Five-line swap in `ingest_team_message` replacing the standalone `is_brain_relevant` heuristic gate with `await relevance_filter.classify(content, team_scope=team_scope)` — team chat ingest now uses the same Haiku 4.5 semantic classifier as LibreChat and Open WebUI.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 (RED) | Failing tests for classify swap | 0a730c9 | tests/test_brain_ingest.py |
| 1 (GREEN) | Swap heuristic gate for Haiku classifier | fbfe3c6 | services/brain_ingest.py |

## Diff of ingest_team_message Change

```diff
-        if not is_brain_relevant(content):
-            return
+        # Phase 13 D1/D4 — Haiku 4.5 classifier replaces standalone heuristic.
+        # classify() runs is_brain_relevant() first as a fast-path filter,
+        # then Haiku for semantic relevance, with heuristic fallback on error.
+        from app.services.relevance_filter import classify  # local import — avoids cycle at module load
+        if not await classify(content, team_scope=team_scope):
+            log.info("brain_ingest.team_message.skipped_by_filter", team_scope=team_scope)
+            return
         provider = get_memory_provider()
```

## Test Results

```
tests/test_brain_ingest.py: 5 passed
tests/test_relevance_filter.py: 10 passed (unchanged)
tests/test_brain_ingest_endpoint.py: 11 passed, 4 skipped (integration, Docker unavailable locally)

Total: 26 passed, 4 skipped
```

## Acceptance Criteria Verification

| Criterion | Status |
|-----------|--------|
| `await classify(content, team_scope=team_scope)` present in brain_ingest.py | PASS (2 occurrences — ingest_team_message + ingest_external_message) |
| `brain_ingest.team_message.skipped_by_filter` log key present | PASS (1 occurrence) |
| `def is_brain_relevant` still present | PASS (1 occurrence) |
| No `if not is_brain_relevant(content)` in ingest_team_message | PASS (removed) |
| No module-top `from app.services.relevance_filter` import | PASS (0 occurrences) |
| `team_chat.py` unchanged | PASS (git diff = empty) |
| `pytest tests/test_brain_ingest.py -q` → 5 passed | PASS |
| `pytest tests/test_brain_ingest.py tests/test_relevance_filter.py tests/test_brain_ingest_endpoint.py -q` → 26 passed | PASS |

## Deviations from Plan

None — plan executed exactly as written. The 6-line diff matches the plan spec verbatim. Test patch target `app.services.relevance_filter.classify` is consistent with the 13-01 established pattern.

## Known Stubs

None. Implementation is fully functional.

## Threat Flags

None. No new network endpoints, auth paths, or schema changes. The change is internal to `ingest_team_message` — the caller (`team_chat.py`) and the response path are unchanged.

## Self-Check: PASSED

- FOUND: apps/memory-api/app/services/brain_ingest.py (contains `await classify(content, team_scope=team_scope)`)
- FOUND: apps/memory-api/tests/test_brain_ingest.py
- FOUND: commit 0a730c9 (RED — test file)
- FOUND: commit fbfe3c6 (GREEN — implementation)
- Test run: 26 passed, 4 skipped
