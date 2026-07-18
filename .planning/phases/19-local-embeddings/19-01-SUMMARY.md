---
phase: 19-local-embeddings
plan: 01
subsystem: api
tags: [fastembed, onnx, qdrant, embeddings, memory-api, keyless]

# Dependency graph
requires:
  - phase: 15-open-core-edition
    provides: EDITION-gated router registry, settings.py conventions (LOCAL_AUTH_* fail-safe pattern)
provides:
  - "app.embedders.get_embedder() — provider-pluggable embedder selector (local default, openai opt-in)"
  - "app.embedders.get_embedding_dimension() — single source of truth for Qdrant vector size"
  - "app.embedders.EmbeddingDimensionMismatch — boot-fatal exception on existing-collection dimension disagreement"
  - "fastembed>=0.8.0 dependency wired for zero-key local embedding inference"
affects: [19-02-dockerfile-model-baking, 19-03-e2e-semantic-retrieval-test]

# Tech tracking
tech-stack:
  added: ["fastembed>=0.8.0 (ONNX BAAI/bge-small-en-v1.5, 384-dim, no torch)"]
  patterns:
    - "Lazy module-level singleton for heavyweight model loading (_get_local_model), matching neo4j/boto3 lazy-import convention"
    - "Config fail-safe fallback (no field_validator) for zero-key-install settings — mirrors LOCAL_AUTH_* block, contrasts with EDITION's fail-loud validator"
    - "Single-source-of-truth dimension helper consumed by all 3 Qdrant provisioning sites instead of independent hardcoded literals"

key-files:
  created:
    - apps/memory-api/tests/test_embedders_provider.py
  modified:
    - apps/memory-api/app/config.py
    - apps/memory-api/pyproject.toml
    - apps/memory-api/app/embedders.py
    - apps/memory-api/app/deps.py
    - apps/memory-api/app/qdrant_setup.py
    - apps/memory-api/app/routes/admin_wipe.py
    - apps/memory-api/app/main.py

key-decisions:
  - "EMBEDDINGS_PROVIDER has NO field_validator (deliberate) — garbage/unset value falls back to local rather than crash-booting, per D-19-02/D-19-03"
  - "local_embedder offloads fastembed's synchronous .embed() generator via asyncio.to_thread — avoids blocking the event loop (RESEARCH Pitfall 4)"
  - "get_embedding_dimension() read INSIDE ensure_collections() (not at qdrant_setup.py module-import time) so tests that reload config observe the new provider"
  - "EmbeddingDimensionMismatch is re-raised (not swallowed) by main.py's blanket except Exception around ensure_collections() — boot-fatal by design"

patterns-established:
  - "Provider selector functions (get_embedder/get_embedding_dimension) read settings at CALL time, not import time — enables config reload in tests and avoids stale singletons"

requirements-completed: [EMBED-01]

# Metrics
duration: 8min
completed: 2026-07-18
---

# Phase 19 Plan 01: Local Embeddings Provider Wiring Summary

**Provider-pluggable embedding layer (fastembed/ONNX `BAAI/bge-small-en-v1.5`, 384-dim, keyless-by-default) wired into memory-api's NativeProvider, with all three Qdrant vector-dimension provisioning sites now derived from one helper instead of three independent hardcoded `1536` literals.**

## Performance

- **Duration:** ~8 min (commit-to-commit)
- **Started:** 2026-07-18T03:31:57+02:00 (Task 1 commit)
- **Completed:** 2026-07-18T03:39:45+02:00 (Task 3 commit)
- **Tasks:** 3/3 completed
- **Files modified:** 7 (+ 1 test file created)

## Accomplishments

- `EMBEDDINGS_PROVIDER` (default `local`), `LOCAL_EMBEDDING_MODEL`, `EMBEDDING_CACHE_DIR` settings added to `config.py` with no `field_validator` — a zero-key OSS install boots even with a garbage/unset provider value (falls back to local, never crashes).
- `local_embedder()` added to `embedders.py`: lazy-loaded `fastembed.TextEmbedding` singleton, event-loop-safe via `asyncio.to_thread`. `openai_embedder` untouched.
- `get_embedder()` selector and `get_embedding_dimension()` single-source-of-truth dimension helper added; `EmbeddingDimensionMismatch` exception defined for boot-fatal handling.
- `deps.py`'s `_build_provider()` native branch now injects `embedder=get_embedder()` instead of the hardcoded `openai_embedder`; the `mem0` branch is untouched (Pitfall 5 — out of scope).
- All three Qdrant vector-size provisioning sites (`qdrant_setup.py:ensure_collections`, `admin_wipe.py:_wipe_qdrant_full`, and by extension `main.py`'s lifespan error handling) now derive `size` from `get_embedding_dimension()`. A dimension mismatch on an already-existing collection raises `EmbeddingDimensionMismatch`, which `main.py`'s lifespan re-raises (boot-fatal) instead of downgrading to a warning like other `ensure_collections()` failures.
- Both `create_payload_index` calls (`team_scope`, `truth_level`) preserved at both create sites — team-isolation invariant intact.

## Task Commits

Each task was committed atomically:

1. **Task 1: Add config knobs + fastembed dependency** - `2998a5b` (feat)
2. **Task 2: local_embedder + provider selector + dimension helper + mismatch exception; wire deps.py** - `5c593a3` (feat) + `f25c878` (test, added after implementation — see TDD Gate Compliance below)
3. **Task 3: Derive Qdrant VECTOR_SIZE from the provider at all three sites; fail loud on dimension mismatch** - `083d6a5` (feat)

## Files Created/Modified

- `apps/memory-api/app/config.py` - `EMBEDDINGS_PROVIDER`/`LOCAL_EMBEDDING_MODEL`/`EMBEDDING_CACHE_DIR` settings, no validator
- `apps/memory-api/pyproject.toml` - `fastembed>=0.8.0` dependency, `testcontainers[postgres,qdrant]>=4.8` dev extra
- `apps/memory-api/app/embedders.py` - `local_embedder`, `_get_local_model` lazy singleton, `get_embedder`, `get_embedding_dimension`, `EmbeddingDimensionMismatch`
- `apps/memory-api/app/deps.py` - native branch now injects `embedder=get_embedder()`
- `apps/memory-api/app/qdrant_setup.py` - `VECTOR_SIZE` literal removed; dimension read via `get_embedding_dimension()` inside `ensure_collections()`; existing-collection dimension guard added
- `apps/memory-api/app/routes/admin_wipe.py` - `_wipe_qdrant_full()` uses `size=get_embedding_dimension()`
- `apps/memory-api/app/main.py` - lifespan re-raises `EmbeddingDimensionMismatch` instead of swallowing it
- `apps/memory-api/tests/test_embedders_provider.py` (new) - unit coverage for the selector/dimension/fallback logic

## Decisions Made

- Kept `get_embedding_dimension()` reads INSIDE `ensure_collections()` rather than at `qdrant_setup.py` module-import time, per the plan's explicit instruction — this lets a test that reloads `app.config` observe a changed `EMBEDDINGS_PROVIDER` without needing to reload `qdrant_setup` itself.
- Handled the Qdrant `CollectionConfig.params.vectors` union type (`VectorParams | Dict[str, VectorParams]`) defensively in the mismatch guard, even though this codebase only ever creates the unnamed-vector shape — confirmed via `qdrant_client.http.models.VectorsConfig.__args__` inspection during implementation.

## Deviations from Plan

### Auto-fixed Issues

**1. [Process deviation, self-corrected] Task 2's `tdd="true"` RED/GREEN ordering not followed strictly**
- **Found during:** Task 2 (local_embedder + selector)
- **Issue:** Task 2 was flagged `tdd="true"` in the plan, which per executor convention calls for a RED (failing test) commit before a GREEN (implementation) commit. The plan's own `<verify>` block for this task was an inline `python -c` smoke script rather than a persisted pytest file, and the implementation was written directly against that inline script, then committed as a single `feat` commit.
- **Fix:** Added `apps/memory-api/tests/test_embedders_provider.py` covering the task's `<behavior>` requirements (garbage/unset → local/384, `openai` → openai_embedder/1536, case-insensitivity, `EmbeddingDimensionMismatch` importability, async `Embedder` contract shape) and committed it separately as `test(19-01): ...` (`f25c878`) — after the `feat` commit rather than before.
- **Files modified:** `apps/memory-api/tests/test_embedders_provider.py`
- **Verification:** `pytest tests/test_embedders_provider.py` — 6/6 PASS
- **Committed in:** `f25c878`

---

**Total deviations:** 1 (process-only — RED/GREEN commit ordering, not a functional gap)
**Impact on plan:** No functional impact. All acceptance criteria and inline `<verify>` scripts from the plan itself passed exactly as specified before any additional test file was added.

## TDD Gate Compliance

Task 2 was marked `tdd="true"`. Git log shows:
- `feat(19-01): add local_embedder + provider selector + dimension helper` (`5c593a3`) — GREEN
- `test(19-01): add embedder provider selector + dimension unit coverage` (`f25c878`) — test coverage, committed AFTER the implementation, not before

**Warning:** RED-before-GREEN ordering was not followed for Task 2 — the test commit followed the feat commit rather than preceding it. The plan's own `<verify>` block (an inline `python -c` selector/dimension smoke test, not a persisted test file) was used to drive and validate the implementation instead, and passed. Test coverage was subsequently added to the permanent test suite for future regression protection. No functional gap: `pytest tests/test_embedders_provider.py` passes 6/6 against the shipped implementation.

## Issues Encountered

- `apps/memory-api/tests/test_admin_wipe.py` has 4 pre-existing failures (`test_wipe_team_requires_auth`, `test_wipe_team_actually_deletes`, `test_wipe_team_preserves_other_teams`, `test_wipe_database_requires_auth`) unrelated to this plan's changes — confirmed by re-running the same suite with Task 3's `qdrant_setup.py`/`admin_wipe.py`/`main.py` changes stashed out; the same 4 tests fail identically on the pre-Task-3 tree. Root causes appear to be a stale `neo4j_outbox.team_scope` column expectation and an auth-dependency-override gap, both out of scope for Phase 19 (SCOPE BOUNDARY). Not fixed here; logged for a future phase/quick-task.
- `fastembed>=0.8.0` was verified installable via `pip install --dry-run` on this ARM64 Windows dev host (resolves prebuilt wheels for `fastembed`, `onnxruntime`, `tokenizers`, `mmh3`, `py_rust_stemmers` — no source build required), satisfying the "confirm it installs on this arm64 host" constraint for Task 1. The actual container build (linux/amd64 prod target) is Plan 02's job.

## User Setup Required

None - no external service configuration required. `EMBEDDINGS_PROVIDER` defaults to `local`; no `.env` change is needed for the zero-key path. `OPENAI_API_KEY` remains opt-in for `EMBEDDINGS_PROVIDER=openai`.

## Next Phase Readiness

- Plan 02 (Dockerfile model baking) can now rely on `settings.EMBEDDING_CACHE_DIR` (`/app/model_cache`) and `settings.LOCAL_EMBEDDING_MODEL` (`BAAI/bge-small-en-v1.5`) as the exact values `_get_local_model()` reads — bake the model to that same path.
- Plan 03 (end-to-end semantic retrieval test, real Postgres + real Qdrant, no OpenAI key) can now exercise `get_embedder()` returning `local_embedder` by default and assert a 384-dim collection is created; the `EmbeddingDimensionMismatch` boot-fatal path is also ready for a testcontainers-backed mismatch test.
- No blockers. `NativeProvider` / `MemoryProvider` ABC / the `mem0` branch remain untouched as required.

---
*Phase: 19-local-embeddings*
*Completed: 2026-07-18*

## Self-Check: PASSED

All 8 modified/created files confirmed present on disk; all 4 task commit hashes (`2998a5b`, `5c593a3`, `f25c878`, `083d6a5`) confirmed present in git log.
