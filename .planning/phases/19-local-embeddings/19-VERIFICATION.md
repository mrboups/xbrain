---
phase: 19-local-embeddings
verified: 2026-07-18T04:21:08Z
status: passed
score: 6/6 must-haves verified
overrides_applied: 0
deferred:
  - truth: "amd64 RAM footprint for the baked local-embedding model is measured on real amd64 hardware (not QEMU emulation)"
    addressed_in: "Phase 16"
    evidence: "Phase 16 SC#1: 'Following only the published install docs ... an operator provisions a fresh VM and reaches a running OSS-light stack — a clean-install test passes end-to-end.' Phase 16 is the first point the stack runs on real amd64 (GCP e2-medium), so the ~366 MB QEMU-emulated estimate (RESEARCH §6 Assumption A2, documented in docs/embeddings.md and 19-02-SUMMARY.md as 'a follow-up, not a blocker') gets its real-hardware confirmation there."
---

# Phase 19: Local Embeddings (OSS default) Verification Report

**Phase Goal:** A fresh OSS-light install performs semantic ingest + retrieval with NO OpenAI key — embeddings run in-container, keyless. OpenAI embeddings remain selectable via config. (Locked decision Q3, requirement EMBED-01.)
**Verified:** 2026-07-18T04:21:08Z
**Status:** passed
**Re-verification:** No — initial verification

## Verification Method

This verification did NOT rely on SUMMARY.md claims. Every load-bearing claim was independently reproduced in this session:

- Ran `apps/memory-api/tests/test_local_embeddings.py` live (not read-only) — **6 passed in 27.53s**, confirming the SUMMARY's "RAN GREEN (not skipped)" claim with a fresh execution, real `postgres:17` + `qdrant/qdrant:v1.17.1` testcontainers, no `OPENAI_API_KEY` set.
- Ran `apps/memory-api/tests/test_embedders_provider.py` live — 6/6 passed.
- Built the real `apps/memory-api/Dockerfile` locally (`docker build`, arm64 native) — succeeded, then ran `docker run --rm --network none ...` against it and got `OFFLINE_384_OK` — reproducing the zero-network proof myself, not trusting the SUMMARY's transcript.
- Ran `docker buildx build --platform linux/amd64 --load` against the same Dockerfile — succeeded (exit 0), independently confirming the both-arch claim (onnxruntime x86_64 cp312 wheel resolves, model bakes under QEMU).
- Wrote and ran an ad-hoc integration test (not part of the shipped suite) that creates a real 1536-dim Qdrant collection, then boots `EMBEDDINGS_PROVIDER=local` and calls `ensure_collections()` — confirmed `EmbeddingDimensionMismatch` is genuinely raised (`MISMATCH_RAISED_OK`), because the shipped test suite only unit-tests the exception's *importability*, not the actual raise-on-conflict path. Deleted the scratch file afterward (not committed — it exists only as verification evidence, matching the phase's own "verification-only, do not commit build artifacts" discipline).
- Deleted both verification-only Docker images (`xbrain/memory-api:verify19`, `xbrain/memory-api:amd64check`) after use — nothing pushed or deployed, per the plan's own arm64-dev/amd64-prod constraint.
- Read every modified source file directly (`embedders.py`, `config.py`, `deps.py`, `qdrant_setup.py`, `admin_wipe.py`, `main.py`, `Dockerfile`, `docker-compose.yml`, both `.env.example` files, `docs/embeddings.md`) and grepped for stray `1536` literals — none remain outside the intended dimension map.
- Confirmed `packages/memory-models` has zero diff across the Phase 19 commit range (`5a03492a^..6cf09d0`) — the `NativeProvider`/`MemoryProvider` ABC and the `mem0` branch are genuinely untouched, not just claimed untouched.

## Goal Achievement

### Observable Truths (Roadmap Success Criteria)

| # | Truth | Status | Evidence |
| --- | --- | --- | --- |
| SC#1 | No-`OPENAI_API_KEY` install ingests, embeds locally, writes to Qdrant, and semantically retrieves — proven live | ✓ VERIFIED | Independently re-ran `test_local_embeddings.py`: `test_local_semantic_search_ranks_expected_first` ranks the Lisbon-offsite doc `results[0]` using the REAL fastembed embedder against real testcontainers Postgres+Qdrant, no mock on the semantic path (only `@respx.mock` on the clearly-separate OpenAI-regression test). `test_http_memory_search_returns_same_top_hit` proves the full `GET /v1/memory/search` HTTP route returns the same top hit. 6/6 passed, 27.53s, not skipped. |
| SC#2 | Local embedder runs in-container, zero external call, zero API key | ✓ VERIFIED | Independently built `apps/memory-api/Dockerfile` (arm64) and ran `docker run --rm --network none --entrypoint python ... -c "...assert len(v)==384..."` → printed `OFFLINE_384_OK`. Model is baked at `/app/model_cache` via `COPY --from=builder --chown=xbrain:xbrain` before `USER xbrain`; `ENV HF_HUB_OFFLINE=1` forces cache-only. |
| SC#3 | Embedder is pluggable — `EMBEDDINGS_PROVIDER=openai` switches with no code change; provider abstraction respected | ✓ VERIFIED | `app/embedders.py::get_embedder()` is a two-branch selector reading `settings.EMBEDDINGS_PROVIDER` at call time; `deps.py::_build_provider()`'s native branch injects `embedder=get_embedder()`. `test_openai_path_unchanged_when_selected` (respx) confirms the 1536-dim OpenAI HTTP path still fires unchanged. `packages/memory-models` (the `NativeProvider`/`MemoryProvider` ABC) and the `mem0` branch in `deps.py` have zero diff across the entire Phase 19 commit range — confirmed via `git diff --stat 5a03492a^ 6cf09d0 -- packages/memory-models` (empty). |
| SC#4 | Local model has BOTH arm64 and amd64 wheels/artifacts and fits the OSS-light RAM budget, stated with measured footprint | ✓ VERIFIED (with one deferred follow-up) | Independently ran `docker build` (arm64 native, exit 0) AND `docker buildx build --platform linux/amd64 --load` (exit 0, onnxruntime x86_64 cp312 wheel resolves, model bakes under QEMU) — both-arch claim reproduced, not merely read from the SUMMARY. `mem_limit` raised 768m→896m in `infrastructure/docker-compose.yml` with an inline comment citing ~256 MB (arm64-native) / ~366 MB (amd64-QEMU) measured RSS. The amd64 number is QEMU-emulated, not bare-metal-measured — this is transparently documented in `docs/embeddings.md`, `19-02-SUMMARY.md`, and the compose comment itself as "Assumption A2 — re-measure once on the real amd64 VM," not silently hidden. Deferred to Phase 16 (first real amd64 install) — see Deferred Items below. |
| SC#5 | Qdrant dimension handled correctly across provider switch; mismatch fails loud; migration documented | ✓ VERIFIED | `get_embedding_dimension()` in `embedders.py` is the single source of truth; `qdrant_setup.py`, `routes/admin_wipe.py` both derive `size=` from it (`grep -rn "1536" apps/memory-api/app/` finds only the intended `_EMBEDDING_DIMENSIONS` map and a docstring — zero stray hardcodes). The shipped test suite only unit-tests that `EmbeddingDimensionMismatch` is importable/has a message — it does NOT exercise the actual raise. I wrote and ran an ad-hoc integration test that creates a real 1536-dim Qdrant collection then boots `EMBEDDINGS_PROVIDER=local` and calls `ensure_collections()`: **`EmbeddingDimensionMismatch` genuinely raises** (`MISMATCH_RAISED_OK`, 1 passed). `main.py`'s lifespan re-raises it (`except EmbeddingDimensionMismatch: raise`) ahead of the blanket `except Exception: log.warning`, so it is boot-fatal, not swallowed. `docs/embeddings.md` documents both the fresh-install path and the wipe/re-embed migration path for an existing 1536-dim install switching to local. |
| EMBED-01 (fail-safe default) | A garbage/unset `EMBEDDINGS_PROVIDER` falls back to local, never crash-boots | ✓ VERIFIED | No `@field_validator("EMBEDDINGS_PROVIDER")` exists in `config.py` (confirmed by grep, 0 matches) — deliberate, matching the `LOCAL_AUTH_*` precedent. `get_embedder()`/`get_embedding_dimension()` default to `local`/384 for any value that isn't exactly `"openai"` (case-insensitive). Reproduced via `test_garbage_provider_falls_back_to_local` in both `test_embedders_provider.py` and `test_local_embeddings.py` — both pass. |

**Score:** 6/6 truths verified (5 Roadmap Success Criteria + EMBED-01 fail-safe default)

### Deferred Items

| # | Item | Addressed In | Evidence |
|---|------|-------------|----------|
| 1 | Real (non-QEMU) amd64 RAM measurement for the baked local-embedding model | Phase 16 | Phase 16 SC#1/SC#2 are the first point the OSS-light stack is provisioned and boot-tested on a real amd64 VM from install docs alone. The ~366 MB amd64 figure currently backing the `mem_limit: 896m` decision is QEMU-emulated (RESEARCH §6 Assumption A2), explicitly flagged in `docs/embeddings.md` and `19-02-SUMMARY.md` as a non-blocking follow-up to re-measure on the real VM. |

### Required Artifacts

| Artifact | Expected | Status | Details |
| -------- | ----------- | ------ | ------- |
| `apps/memory-api/app/embedders.py` | `local_embedder`, `get_embedder`, `get_embedding_dimension`, `EmbeddingDimensionMismatch`, `_get_local_model` | ✓ VERIFIED | All present, read in full; `asyncio.to_thread` offload confirmed (event-loop safe). |
| `apps/memory-api/app/config.py` | `EMBEDDINGS_PROVIDER`, `LOCAL_EMBEDDING_MODEL`, `EMBEDDING_CACHE_DIR`, no field_validator | ✓ VERIFIED | Lines 254-258, no validator (grep confirms 0). |
| `apps/memory-api/app/qdrant_setup.py` | provider-derived `VECTOR_SIZE` + mismatch guard | ✓ VERIFIED | `get_embedding_dimension()` read inside `ensure_collections()`; mismatch guard reproduced live (raises). |
| `apps/memory-api/app/routes/admin_wipe.py` | wipe recreates collection at provider-derived dimension | ✓ VERIFIED | `VectorParams(size=get_embedding_dimension()...)`, no `size=1536` literal remains. |
| `apps/memory-api/app/deps.py` | `NativeProvider(embedder=get_embedder())` | ✓ VERIFIED | Native branch confirmed; mem0 branch untouched. |
| `apps/memory-api/pyproject.toml` | `fastembed>=0.8.0`, `testcontainers[postgres,qdrant]>=4.8` | ✓ VERIFIED | Both present; installed and imported successfully in this session. |
| `apps/memory-api/Dockerfile` | bake + `HF_HUB_OFFLINE` + `--chown` before `USER` + configurable workers | ✓ VERIFIED | Full file read; `--chown=xbrain:xbrain` appears before `USER xbrain`; `ENV UVICORN_WORKERS=2` + shell-form `CMD exec ... --workers ${UVICORN_WORKERS}`. Independently built (arm64 + amd64). |
| `infrastructure/docker-compose.yml` | `EMBEDDINGS_PROVIDER`/`LOCAL_EMBEDDING_MODEL`/`UVICORN_WORKERS` passthrough + `mem_limit` | ✓ VERIFIED | All three env knobs present with safe defaults; `mem_limit: 896m` with cited RSS. |
| `.env.example` + `apps/memory-api/.env.example` | documented knobs | ✓ VERIFIED | Both templates document `EMBEDDINGS_PROVIDER`/`LOCAL_EMBEDDING_MODEL`/`UVICORN_WORKERS`. |
| `apps/memory-api/tests/test_local_embeddings.py` | gate-lesson semantic test + regressions | ✓ VERIFIED, WIRED, RAN GREEN | Independently executed — 6/6 pass, no mock on semantic path. |
| `apps/memory-api/tests/test_embedders_provider.py` | selector/dimension unit tests | ✓ VERIFIED, RAN GREEN | Independently executed — 6/6 pass. |
| `apps/memory-api/tests/conftest.py` | `qdrant_url` session fixture mirroring `pg_url` | ✓ VERIFIED | Session-scoped, Docker-gated, patches `settings.QDRANT_URL` directly; `pg_url`/`session`/`client` untouched. |
| `docs/embeddings.md` | provider config + offline guarantee + re-embed migration | ✓ VERIFIED | Full file read; covers providers table, offline mechanism, RAM/worker note, fresh-install vs re-embed dimension paths, OpenAI selection. |

### Key Link Verification

| From | To | Via | Status | Details |
| ---- | --- | --- | ------ | ------- |
| `qdrant_setup.py` | `app.embedders.get_embedding_dimension` | `VECTOR_SIZE = get_embedding_dimension()` (read inside `ensure_collections()`) | ✓ WIRED | Confirmed by read + live test (`test_local_collection_created_at_384` passes, size==384). |
| `routes/admin_wipe.py` | `app.embedders.get_embedding_dimension` | `VectorParams(size=get_embedding_dimension())` | ✓ WIRED | Confirmed by read; no `size=1536` literal remains. |
| `deps.py` | `app.embedders.get_embedder` | `NativeProvider(embedder=get_embedder())` | ✓ WIRED | Confirmed by read + live test (provider search uses the injected local embedder). |
| `Dockerfile` builder stage | `settings.EMBEDDING_CACHE_DIR` (`/app/model_cache`) | bake-then-`COPY --chown` | ✓ WIRED | Confirmed by independent `docker build` + `docker run --network none` producing a real 384-dim vector from that exact path. |
| `docker-compose.yml` `memory-api.environment` | `app.config.EMBEDDINGS_PROVIDER` | env passthrough with `${EMBEDDINGS_PROVIDER:-local}` | ✓ WIRED | Confirmed by read; matches `config.py` default. |
| `main.py` lifespan | `EmbeddingDimensionMismatch` | `except EmbeddingDimensionMismatch: raise` ahead of the blanket except | ✓ WIRED | Confirmed by read + independently-run ad-hoc test proving the exception genuinely propagates from `ensure_collections()`. |

### Data-Flow Trace (Level 4)

Not applicable in the traditional sense (no frontend/UI component renders this data — `UI hint: no` per ROADMAP). The equivalent trace for a backend engine phase is embedder → Qdrant → retrieval, which is exactly what the gate-lesson test proves end to end with real components (not a mock returning static/empty data at any hop):

| Data Path | Source | Produces Real Data | Status |
| --------- | ------ | ------------------- | ------ |
| `local_embedder(text)` → `NativeProvider.upsert()` → real Qdrant point | `fastembed.TextEmbedding` (ONNX inference, not a static vector) | Yes — verified length-384 real vectors, verified semantic ranking differentiates 4 distinct-topic docs correctly | ✓ FLOWING |
| Qdrant point → `NativeProvider.search()` → `GET /v1/memory/search` | Real Qdrant COSINE similarity search | Yes — HTTP route returns the same top hit as the direct provider call | ✓ FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
| -------- | ------- | ------ | ------ |
| Keyless semantic ranking (gate lesson) | `python -m pytest tests/test_local_embeddings.py -q` | `6 passed in 27.53s` | ✓ PASS |
| Provider selector unit coverage | `python -m pytest tests/test_embedders_provider.py -q` | `6 passed in 1.58s` | ✓ PASS |
| Zero-network offline embed | `docker build` (arm64) + `docker run --rm --network none ... -c "...assert len(v)==384..."` | `OFFLINE_384_OK` | ✓ PASS |
| Both-arch image build | `docker buildx build --platform linux/amd64 --load` | exit 0 (onnxruntime x86_64 wheel + model bake succeed under QEMU) | ✓ PASS |
| Dimension-mismatch fails loud (NOT in shipped suite — verified ad hoc) | testcontainers Qdrant: create 1536-dim collection, boot `EMBEDDINGS_PROVIDER=local`, call `ensure_collections()` | `EmbeddingDimensionMismatch` raised (`MISMATCH_RAISED_OK`) | ✓ PASS |
| Pre-existing regression check | `python -m pytest tests/test_admin_wipe.py -q` | `4 failed, 7 passed` — all 4 failures are `neo4j_outbox.team_scope does not exist` (stale Phase-12 schema expectation) and unrelated to embeddings; file untouched by any Phase 19 commit (`git log -- tests/test_admin_wipe.py` shows last touch predates Phase 19) | ✓ PASS (confirmed pre-existing, not a Phase 19 regression) |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
| ----------- | ---------- | ----------- | ------ | -------- |
| EMBED-01 | 19-01, 19-02, 19-03 | Fresh install with no embeddings API key ingests and semantically retrieves; embeddings run in-container, keyless, no external call; OpenAI selectable without code changes; both-arch + RAM budget | ✓ SATISFIED (code) | All 5 Roadmap SCs independently reproduced above. **Note:** `.planning/REQUIREMENTS.md` line 38 checkbox is still `[ ]` and the coverage table (line 67) still says "Pending" — this is a documentation-sync gap, not a code gap (see Anti-Patterns / Notes below). |

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
| ---- | ---- | ------- | -------- | ------ |
| `.planning/REQUIREMENTS.md` | 38, 67 | `EMBED-01` checkbox unticked, status table says "Pending" | ℹ️ Info | Stale planning artifact — the code fully delivers EMBED-01 (see table above), and `ROADMAP.md` line 32 already correctly shows Phase 19 `[x]` completed 2026-07-18. Needs a follow-up "tick" commit (same pattern as `79a259f docs(phase-18): ... tick LAUTH-01/02`). Not a blocker. |
| `.planning/STATE.md` | 8, 24-31 | Still reads "Phase 19 execution started" / "Plan 1 of 3" / status: executing | ℹ️ Info | Stale — all 3 plans are complete and merged (confirmed via git log: `6cf09d0`, `9349824`, etc.). Same doc-sync follow-up as above. Not a blocker. |
| — | — | No TODO/FIXME/placeholder/stub code found in any of the 12 modified/created files | — | Clean |

No blocker-severity anti-patterns found in the actual code delivered.

### Human Verification Required

None required to determine phase-goal achievement — every claim in this phase was independently reproducible with automated commands (pytest, docker build, docker run) and was reproduced in this verification session rather than taken on faith from SUMMARY.md.

One optional follow-up for ops confidence (not blocking, tracked as a deferred item above): once Phase 16 provisions a real amd64 e2-medium VM, confirm the local-embedding-model warm RSS against the `mem_limit: 896m` budget (currently backed by a QEMU-emulated ~366 MB estimate, not bare-metal amd64).

### Gaps Summary

No blocking gaps. All 5 Roadmap Success Criteria and the EMBED-01 requirement are delivered in the codebase and were independently verified in this session — not merely inferred from SUMMARY.md text:

- The "gate lesson" test (`test_local_semantic_search_ranks_expected_first`) genuinely runs the real fastembed embedder against real testcontainers Postgres+Qdrant with no `OPENAI_API_KEY`, with zero mock on the semantic path, and it ranks correctly. Re-executed live in this session, not just read.
- The offline/keyless guarantee (`docker run --network none` → `OFFLINE_384_OK`) was rebuilt and re-run from scratch in this session, not copy-pasted from the SUMMARY transcript.
- The both-arch claim (arm64 native + amd64 buildx cross-build) was rebuilt from scratch in this session for both architectures; both exited 0.
- The dimension-mismatch "fails loud" behavior (SC#5's most safety-critical claim) was NOT covered by any shipped automated test — I wrote and ran one to close that gap during verification and confirmed the exception genuinely raises against a real conflicting collection.
- The `mem0` provider branch and the `NativeProvider`/`MemoryProvider` ABC in `packages/memory-models` were confirmed untouched via `git diff --stat`, not just asserted in the SUMMARY.

Two informational (non-blocking) findings: `.planning/REQUIREMENTS.md` and `.planning/STATE.md` have not been synced to reflect Phase 19 completion (stale checkboxes/status text) — a documentation follow-up, not a code gap. One item (real-hardware amd64 RAM measurement) is deferred to Phase 16, which is the first phase that provisions a real amd64 VM.

---

_Verified: 2026-07-18T04:21:08Z_
_Verifier: Claude (gsd-verifier)_
