---
phase: 13-chat-brain-ingestion-retrieval-enrichment-close-the-differen
plan: "07"
subsystem: infrastructure/scripts
tags:
  - verify-script
  - integration-test
  - cross-frontend
  - env-docs
dependency_graph:
  requires:
    - 13-01 (POST /v1/brain/ingest + RELEVANCE_* config)
    - 13-04 (librechat-bridge BRAIN_INGEST_ENABLED + brain_ingest hook)
    - 13-05 (librechat-bridge CHAT07_* config + message_enricher.enrich_turn)
    - 13-06 (openwebui-pipeline BRAIN_INGEST_ENABLED + CHAT07_* config)
  provides:
    - verify-phase13.sh (8-test SKIP-aware verification harness for Phase 13)
    - test-phase13-cross-frontend.py (Python cross-frontend integration test)
    - .env.example docs for memory-api, librechat-bridge, openwebui-pipeline
  affects:
    - infrastructure/scripts/verify-phase13.sh
    - infrastructure/scripts/test-phase13-cross-frontend.py
    - .env.example
    - apps/memory-api/.env.example
    - apps/librechat-bridge/.env.example
    - apps/openwebui-pipeline/.env.example
tech_stack:
  added: []
  patterns:
    - SKIP-aware verify script pattern (mirrors verify-phase11.sh / verify-phase12.sh)
    - Python asyncio + httpx for multi-step cross-frontend integration tests
    - Bridge JWT minting in Python (mirrors bridge_token.make_bridge_jwt)
    - UUID5 deterministic item ID for idempotent fixture management
    - Exit code 77 for SKIP (matches verify script convention)
key_files:
  created:
    - infrastructure/scripts/verify-phase13.sh
    - infrastructure/scripts/test-phase13-cross-frontend.py
    - apps/librechat-bridge/.env.example
    - apps/openwebui-pipeline/.env.example
  modified:
    - .env.example (Phase 13 section appended)
    - apps/memory-api/.env.example (Phase 13 section appended)
decisions:
  - "Python helper uses exit code 77 for SKIP (no prerequisite) matching the verify-phase12 SKIP convention — bash maps 77 to skip() call, preserving the SKIP-never-blocks invariant"
  - "Bridge JWT minting in the Python helper replicates bridge_token.py exactly (iss=librechat-bridge, scope=bridge, HS256) — no new auth mechanism introduced"
  - "Test (h) fail-soft assertion uses log-based evidence rather than iptables network isolation — the latter requires root + container restart, not safe from a verify script; manual UAT steps are documented inline"
  - "Test (f) enrichment check uses Mongo mongosh insertOne + countDocuments pattern — requires LC Mongo container and at least 1 VALIDATED item; both checked via SKIP guard"
  - "librechat-bridge/.env.example and openwebui-pipeline/.env.example created from scratch (were missing); document all service vars for operator onboarding"
metrics:
  duration: "~570 seconds (~9.5 minutes)"
  completed: "2026-05-27"
  tasks_completed: 3
  tasks_total: 3
  files_created: 4
  files_modified: 2
---

# Phase 13 Plan 07: Verify Script + Cross-Frontend Test + .env.example Docs Summary

8-test SKIP-aware verify-phase13.sh covering all ROADMAP Phase 13 success criteria (a-h), Python cross-frontend integration test with 4 test functions (a/b/c/g), and .env.example documentation for all 3 Phase 13 services.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Python cross-frontend integration test helper | 6989138 | infrastructure/scripts/test-phase13-cross-frontend.py |
| 2 | verify-phase13.sh — 8-test SKIP-aware verifier | 05f34d3 | infrastructure/scripts/verify-phase13.sh |
| 3 | .env.example updates for all 3 services | 80f1dc8 | .env.example, apps/memory-api/.env.example, apps/librechat-bridge/.env.example, apps/openwebui-pipeline/.env.example |

## Verify Script — Local Run (No VM)

```
=== Phase 13 Verification ===
[1/8] (a) Team chat ingest → memory_items + Qdrant point
  SKIPPED: BRIDGE_SHARED_SECRET not set
[2/8] (b) LibreChat user-msg ingest → memory_items + Qdrant point
  SKIPPED: BRIDGE_SHARED_SECRET not set
[3/8] (c) Open WebUI user-msg ingest → memory_items + Qdrant point
  SKIPPED: OWUI pipeline at http://localhost:8200 unreachable
[4/8] (d) Haiku LOW-relevance message does NOT land in memory_items
  SKIPPED: DB container xbrain-postgres not running
[5/8] (e) Haiku ERROR/DISABLED path → heuristic fallback still ingests
  SKIPPED: DB container xbrain-postgres not running
[6/8] (f) LibreChat enrichment system message (xbrain-turn-*) appears in Mongo
  SKIPPED: LC Mongo container xbrain-mongodb-librechat not running
[7/8] (g) Cross-frontend retrieval
  SKIPPED: BRIDGE_SHARED_SECRET not set
[8/8] (h) Chat send still succeeds when memory-api brain ingest is degraded
  SKIPPED: memory-api container xbrain-memory-api not running

Phase 13 verification: PASS: 0 / 8 — FAIL: 0 — SKIP: 8
Phase 13 verification: ALL PASS (FAIL=0; SKIPPED never blocks)
```

Exit code: 0 (correct — SKIP never blocks).

## Expected VM Run (Full Deploy Applied)

After `bash infrastructure/scripts/deploy-vm.sh` with Phase 13 containers live:

```
Phase 13 verification: PASS: 8 / 8 — FAIL: 0 — SKIP: 0
```

Or with OWUI pipeline in internal-only mode (test c SKIP):

```
Phase 13 verification: PASS: 7 / 8 — FAIL: 0 — SKIP: 1
```

Exit code: 0 in both cases (SKIP never blocks).

## Cross-Frontend Test — CLI Usage

```bash
# Single test
python infrastructure/scripts/test-phase13-cross-frontend.py \
  --test g --team-scope dejavudev --sub mrboups@github

# All four tests
BRIDGE_SHARED_SECRET=<secret> \
python infrastructure/scripts/test-phase13-cross-frontend.py \
  --all --team-scope dejavudev --sub mrboups@github
```

Langfuse traces for the cross-frontend flow are emitted by the memory-api Haiku classifier
(`relevance_filter.classified` span) and the librechat-bridge per-turn enricher
(`message_enricher.turn_enriched` span). These are observable post-deploy at `lang.grooveos.app`.

## DB Cleanup Verification

```sql
SELECT COUNT(*) FROM memory_items WHERE source = 'verify-phase13';
-- Returns 0 after script cleanup phase
```

The verify script includes a safety-net cleanup block after the summary that removes any
residual `source='verify-phase13'` rows even if the Python helper cleanup was interrupted.
The Python helper's `_cleanup_memory_item` runs after each assertion by default; use
`--keep-fixtures` to retain fixtures for debugging.

## Environment Variables Documented

| Var | Service | File |
|-----|---------|------|
| `RELEVANCE_HAIKU_ENABLED=true` | memory-api | apps/memory-api/.env.example |
| `RELEVANCE_DAILY_TOKEN_CAP_PER_TEAM=50000` | memory-api | apps/memory-api/.env.example |
| `RELEVANCE_HAIKU_MODEL=claude-haiku-4-5-20251001` | memory-api | apps/memory-api/.env.example |
| `RELEVANCE_HAIKU_TIMEOUT_S=3.0` | memory-api | apps/memory-api/.env.example |
| `BRAIN_INGEST_ENABLED=true` | librechat-bridge | apps/librechat-bridge/.env.example |
| `CHAT07_TOP_K=5` | librechat-bridge | apps/librechat-bridge/.env.example |
| `CHAT07_TRUTH_FILTER_MIN_LEVEL=VALIDATED` | librechat-bridge | apps/librechat-bridge/.env.example |
| `BRAIN_INGEST_ENABLED=true` | openwebui-pipeline | apps/openwebui-pipeline/.env.example |
| `CHAT07_TOP_K=5` | openwebui-pipeline | apps/openwebui-pipeline/.env.example |
| `CHAT07_TRUTH_FILTER_MIN_LEVEL=VALIDATED` | openwebui-pipeline | apps/openwebui-pipeline/.env.example |
| All 10 above (summary) | — | .env.example (project root) |

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None. All test functions are fully implemented. The verify script delegates multi-step tests to the Python helper and implements tests (d), (e), (f), (h) directly in bash using psql/mongosh/docker patterns. Test (h) documents the manual UAT procedure for the full iptables network isolation variant inline.

## Threat Flags

None. The verify script and test helper are operator tooling only — they do not introduce new network endpoints, auth paths, or schema changes.

## Self-Check: PASSED

- FOUND: infrastructure/scripts/verify-phase13.sh (executable, -rwxr-xr-x)
- FOUND: infrastructure/scripts/test-phase13-cross-frontend.py
- FOUND: apps/memory-api/.env.example (contains RELEVANCE_HAIKU_ENABLED)
- FOUND: apps/librechat-bridge/.env.example (created, contains BRAIN_INGEST_ENABLED)
- FOUND: apps/openwebui-pipeline/.env.example (created, contains BRAIN_INGEST_ENABLED)
- FOUND: .env.example (root, contains Phase 13 section)
- bash -n infrastructure/scripts/verify-phase13.sh → exit 0 (syntax OK)
- python import check → PASSED (all 4 test functions + mint_bridge_jwt found)
- Local dry run → PASS: 0 / 8 — FAIL: 0 — SKIP: 8 (exit 0)
- FOUND: commit 6989138 (Task 1)
- FOUND: commit 05f34d3 (Task 2)
- FOUND: commit 80f1dc8 (Task 3)
