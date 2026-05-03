---
phase: 02-memoire-intelligente-agents
plan: 02
subsystem: api
tags: [interface, abc, pydantic, memory, abstraction, contract-tests]
requires: []
provides:
  - packages/memory-models/ — shared Python package installable
  - MemoryProvider ABC with 7 async methods (upsert, get, search, update, delete, history, health)
  - Pydantic types : MemoryItem (extra='forbid'), SearchHit, TruthLevel (>= comparable), Visibility, ValidationStatus
  - NativeStubProvider in-process impl (tests + bootstrap)
  - 11 contract tests parameterized — adding a new provider = 1 line in conftest
affects: [02-03 (impls), 02-04 (truth workflow uses provider), 02-05 (RAG calls provider), 02-06 (agents use provider via memory-api)]

tech-stack:
  added: [xbrain-memory shared package, pydantic-settings (transitive)]
  patterns: [ABC interface, parametrized contract tests, repository-style abstraction]

key-files:
  created:
    - packages/memory-models/pyproject.toml (xbrain-memory v0.1.0, dep pydantic>=2.10)
    - packages/memory-models/README.md
    - packages/memory-models/xbrain_memory/__init__.py (re-exports)
    - packages/memory-models/xbrain_memory/types.py (Pydantic models + enums + TruthLevel ordering)
    - packages/memory-models/xbrain_memory/provider.py (ABC MemoryProvider, 7 methods)
    - packages/memory-models/xbrain_memory/providers/__init__.py
    - packages/memory-models/xbrain_memory/providers/native_stub.py (in-process dict impl)
    - packages/memory-models/tests/__init__.py
    - packages/memory-models/tests/conftest.py (PROVIDERS_TO_TEST fixture)
    - packages/memory-models/tests/test_provider_contract.py (11 tests)

key-decisions:
  - "MemoryProvider methods use kwargs-only after item_id (via *,) — forces explicit team_scope at every call site"
  - "team_scope is REQUIRED (no default) on get/search/update/delete/history — signature-level safety net"
  - "TruthLevel implements __ge__/__gt__/__le__/__lt__ for ordinal comparison (search truth_level_min works)"
  - "delete is idempotent (no error if absent OR wrong team) — predictable for retries"
  - "NativeStubProvider naive substring search (production = vector embeddings) — clearly marked NOT FOR PRODUCTION"
  - "PROVIDERS_TO_TEST fixture pattern : adding a new provider in 02-03 = 1 line append + tests run automatically"

patterns-established:
  - "Pattern A — Backend abstraction via ABC : every consumer (memory-api, agent-runtime) calls MemoryProvider methods, never the concrete impl. Swap mem0/native/Zep = config change."
  - "Pattern B — Contract tests parameterized : same 11 tests run against any future provider. Garantees behavioral consistency."
  - "Pattern C — Stub-first scaffolding : NativeStubProvider lets memory-api compile + run tests BEFORE real impl exists (no chicken-and-egg)."

requirements-completed:
  - MEM-06   # backend abstraction enables versioning (impl in 02-03)

duration: ~10 min (inline)
completed: 2026-05-03
---

# Plan 02-02 — MemoryProvider interface

**Foundation for all Phase 2 memory work. Contract tests pass against the stub. Real impls (mem0/native) plug in via 1-line conftest update in Plan 02-03.**

## Performance

- Files created: 10
- Lines of code: ~400 (incl. tests)
- Tests: 11/11 PASSED against NativeStubProvider in 0.19s

## Verification

```
$ cd packages/memory-models && python -m pytest tests/ -v
============================= 11 passed in 0.19s ==============================
```

All 11 contract tests pass :
- test_health
- test_upsert_returns_id
- test_get_round_trip
- test_get_wrong_team_returns_none (team isolation)
- test_search_filters_team (team isolation)
- test_search_truth_level_min
- test_update_changes_content
- test_update_wrong_team_raises (team isolation)
- test_delete_idempotent
- test_delete_wrong_team_silent (team isolation)
- test_history_returns_versions

## Notes

- Plan 02-03 will register Mem0Provider (or NativeProvider full) in conftest PROVIDERS_TO_TEST
- These same 11 tests will then run against the real backend, garantizing behavioral consistency with the stub
