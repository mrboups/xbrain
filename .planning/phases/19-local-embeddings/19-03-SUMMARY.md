---
phase: 19-local-embeddings
plan: 03
subsystem: testing
tags: [embeddings, fastembed, qdrant, testcontainers, semantic-search, gate-lesson, respx, docs, EMBED-01]

# Dependency graph
requires:
  - phase: 19-local-embeddings (plan 01)
    provides: local_embedder + get_embedder() selector, get_embedding_dimension() (384/1536), EmbeddingDimensionMismatch, provider-derived qdrant_setup dimension, settings.EMBEDDINGS_PROVIDER / EMBEDDING_CACHE_DIR
  - phase: 19-local-embeddings (plan 02)
    provides: baked model image (HF_HUB_OFFLINE=1), UVICORN_WORKERS knob, EMBEDDINGS_PROVIDER/LOCAL_EMBEDDING_MODEL in .env.example, memory-api mem_limit 896m
provides:
  - executable proof (real Postgres + real Qdrant, NO OpenAI key) that keyless local ingest -> Qdrant -> semantic search ranks the topically-matching doc results[0] with the REAL fastembed embedder (NO mock on the semantic path — the gate lesson)
  - GET /v1/memory/search end-to-end parity proof (same top hit through the HTTP route)
  - regression coverage — collection created at 384 (not 1536), garbage EMBEDDINGS_PROVIDER -> local fallback, OpenAI 1536-dim path unchanged (respx)
  - qdrant_url session testcontainers fixture (mirrors pg_url) reusable by future Qdrant integration tests
  - docs/embeddings.md — provider config, offline guarantee, worker/RAM note, dimension/re-embed migration runbook (D-19-03)
affects: [phase-16-install-packaging, deployment-vm-sizing]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Docker-gated session testcontainers fixture mirroring pg_url (QdrantContainer, skip if Docker unavailable), patching settings.QDRANT_URL directly because the settings singleton is frozen at import"
    - "Gate-lesson integration test: REAL embedder against REAL Qdrant, semantic ranking assertion a fake vector cannot satisfy; narrow (ConnectionError, OSError)->pytest.skip guard around ONLY the first model materialization, never the ranking assertion"
    - "Fresh provider per test (direct NativeProvider construction, not the deps singleton) so its asyncpg pool + Qdrant client live in the test's own event loop"

key-files:
  created:
    - apps/memory-api/tests/test_local_embeddings.py
    - docs/embeddings.md
    - .planning/phases/19-local-embeddings/19-03-SUMMARY.md
  modified:
    - apps/memory-api/tests/conftest.py

key-decisions:
  - "Config driven by mutating the settings singleton in place (test_embedders_provider.py convention) rather than importlib.reload — get_embedder()/get_embedding_dimension()/ensure_collections() all read settings at call time, so a reload is unnecessary."
  - "Pinned QdrantContainer to qdrant/qdrant:v1.17.1 (stack version) instead of testcontainers' older built-in default, per the plan."
  - "Integration tests build a fresh NativeProvider directly (not via get_memory_provider() singleton) except the HTTP-route test, which must use the singleton the route resolves — avoids cross-event-loop asyncpg pool reuse under pytest-asyncio's function-scoped loops."

# Metrics
metrics:
  duration_minutes: 18
  tasks_completed: 3
  files_changed: 3
  completed_date: 2026-07-18
requirements: [EMBED-01]
---

# Phase 19 Plan 03: Prove Keyless Local Embeddings (Gate Lesson) Summary

**One-liner:** A real-Postgres + real-Qdrant (testcontainers), zero-OpenAI-key test proves keyless local ingest → fastembed 384-dim vector → Qdrant → semantic search ranks the topically-matching doc `results[0]` with the REAL embedder (no mock), plus HTTP-route parity, 384-dim provisioning, garbage→local fallback, an OpenAI-path respx regression, and a `docs/embeddings.md` migration runbook.

## Gate-lesson result: RAN GREEN (not skipped)

The gate-lesson semantic-ranking test **ran and passed** — it did **not** skip.
Docker and network were both available in this environment, so the test executed
the real path end to end:

- `python -m pytest tests/test_local_embeddings.py -q` → **6 passed in 48.37s**.
- The real fastembed model `BAAI/bge-small-en-v1.5` downloaded on first embed
  (cache miss), a real `postgres:17` + real `qdrant/qdrant:v1.17.1` container
  spun up, and `provider.search("where are we going for the team trip?")`
  ranked the **Lisbon-offsite** document `results[0]` — an assertion a
  mocked/fake vector could not satisfy.
- The HTTP `GET /v1/memory/search` route returned the same top hit through the
  full request path.

No acceptance is BLOCKED. Nothing was faked or force-skipped.

## Environment setup taken (required before the real test could run)

`fastembed` and `qdrant-client` were declared in
`apps/memory-api/pyproject.toml` by Wave 1 (19-01) but were **not installed** in
the local Python env. Before running pytest I installed them:

```bash
python -m pip install "fastembed>=0.8.0" "qdrant-client>=1.17"
```

`testcontainers[qdrant]` (the `QdrantContainer` class) and `respx` were already
importable. Confirmed `python -c "import fastembed; from testcontainers.qdrant import QdrantContainer; from qdrant_client import AsyncQdrantClient"` succeeds before relying on the test.

## What was built

### Task 1 — `qdrant_url` testcontainers fixture (`apps/memory-api/tests/conftest.py`)
Session-scoped, Docker-gated fixture mirroring `pg_url`: starts a real
`qdrant/qdrant:v1.17.1` container, yields
`http://{host}:{get_exposed_port(6333)}`, sets `os.environ["QDRANT_URL"]` **and**
patches `app.config.settings.QDRANT_URL` directly (the singleton is frozen at
import), so `ensure_collections()` and `NativeProvider` both target the
container. Skips cleanly with `pytest.skip` when Docker is unavailable.
`pg_url` / `session` / `client` fixtures untouched. Commit `c6c4d57`.

### Task 2 — the gate-lesson test (`apps/memory-api/tests/test_local_embeddings.py`)
Six tests implementing RESEARCH Validation checks 1–4, 6, 7:
- `test_local_collection_created_at_384` — `ensure_collections()` then
  `client.get_collection(...)` asserts `size == 384`.
- `test_local_ingest_writes_vectors` — upserts 4 distinct-topic docs (each with
  the full 7-field tagging contract), asserts Qdrant point count (filtered by
  `team_scope`) equals the doc count.
- `test_local_semantic_search_ranks_expected_first` — **THE gate lesson**: real
  fastembed embedder, asserts `results[0].item.id == LISBON_ID`. No mock on this
  path.
- `test_http_memory_search_returns_same_top_hit` — same query through the httpx
  ASGI `client` fixture with a bridge JWT, asserts the same top hit.
- `test_garbage_provider_falls_back_to_local` — unit; garbage
  `EMBEDDINGS_PROVIDER` → `local`, dim 384, no raise.
- `test_openai_path_unchanged_when_selected` — respx unit; `openai` provider,
  mocked OpenAI HTTP endpoint returns a 1536-length vector, path unchanged.

A narrow `try/except (ConnectionError, OSError) → pytest.skip` guard wraps only
the **first model materialization** (the `local_model_ready` fixture warm-up),
never the ranking assertion. Commit `a090fb7`.

### Task 3 — `docs/embeddings.md`
Documents: provider table (local 384 keyless default vs openai 1536), the "one
key" promise, the offline guarantee (bake at build + `HF_HUB_OFFLINE=1`, cited
`docker run --network none` proof), the `UVICORN_WORKERS=1` OSS-light RAM note,
and the D-19-03 dimension paths — fresh install auto-384, and an existing
1536-dim install that switches to local **fails loud** at boot with
`EmbeddingDimensionMismatch`, with a wipe-and-recreate or re-embed migration
(large-corpus re-embed performance explicitly out of scope). Commit `5824889`.

## Verification

| Check | Result |
|-------|--------|
| `pytest tests/test_local_embeddings.py -q` | 6 passed in 48.37s (RAN, not skipped) |
| Task 1 greps (QdrantContainer, `async def qdrant_url`, `scope="session"`, `pg_url` count 1, ast parse) | all pass |
| Task 2 greps (semantic test name, `results[0]`, `size == 384`, garbage test, respx, narrow `pytest.skip`) | all pass |
| Task 3 greps (`EMBEDDINGS_PROVIDER`, `re-embed`, offline/`HF_HUB_OFFLINE`, `EmbeddingDimensionMismatch`/`fail`, `384`, `1536`) | all pass |

## Deviations from Plan

### Environment / blocking accommodations (Rule 3)

**1. [Rule 3 - Blocking] Installed fastembed + qdrant-client into the local env**
- **Found during:** Task 2 (env setup, mandated by the plan's key_constraints).
- **Issue:** Both deps were declared in `pyproject.toml` by Wave 1 but not
  installed locally, so imports failed.
- **Fix:** `python -m pip install "fastembed>=0.8.0" "qdrant-client>=1.17"`. No
  source change; `pyproject.toml` already declared them.

**2. [Rule 3 - Blocking] Overrode `EMBEDDING_CACHE_DIR` to an OS temp dir in the test fixture**
- **Found during:** Task 2.
- **Issue:** The production default `settings.EMBEDDING_CACHE_DIR=/app/model_cache`
  is a container path; on the local (Windows) pytest host that is not a suitable
  writable cache location.
- **Fix:** The `local_env` test fixture points `EMBEDDING_CACHE_DIR` at
  `<tempdir>/xbrain_fastembed_cache` (writable, persists across the session so the
  model downloads once) and restores the original afterward. **Test-only** — no
  production/Dockerfile change; the baked-image path is unaffected.
- **Files modified:** `apps/memory-api/tests/test_local_embeddings.py`.

### Acceptance-criterion interpretation (documented, not a code deviation)

**`grep -c 'mock' test_local_embeddings.py` returns 7, not 0.** The criterion's
stated intent is "0 **on the local/semantic path** (the only mocking allowed is
respx in the OpenAI-path test)". That intent is fully met:
- **Zero mock constructs execute on the local/semantic path.** The only
  executable mocking construct in the file is `@respx.mock` (one line) on the
  clearly-named `test_openai_path_unchanged_when_selected`.
- The other six `mock` substrings are **prose/comments** explaining the no-mock
  discipline — including the gate-lesson comment the plan's own `<action>`
  **mandates verbatim** ("the real fastembed embedder MUST run here; a
  mocked/fake vector could not satisfy this ranking assertion — this is the gate
  lesson"). Because the plan mandates a comment containing "mocked" **and**
  requires `respx` in the same file, a literal whole-file `grep -c 'mock' == 0`
  is impossible by construction; the semantic-path integrity the criterion
  protects is intact.

No architectural (Rule 4) changes were needed; no authentication gates occurred.

## TDD Gate Compliance

Task 2 is marked `tdd="true"`, but this is a **proving/verification** plan
(Wave 2): the production seam it exercises — `local_embedder`, `get_embedder()`,
`get_embedding_dimension()`, provider-derived `qdrant_setup` dimension,
`EmbeddingDimensionMismatch` — already shipped in Plans 19-01/19-02. The plan's
Task 2 `<action>` creates **only** the test file; it specifies **no** new
production code. Consequently the RED→GREEN cycle collapses: the new tests pass
green against the already-landed implementation (there is nothing to make them
go from red to green in this plan). This is expected and correct for a gate-proof
plan — there is a `test(...)` commit (`a090fb7`) but no `feat(...)` commit
because no production behavior was added here. The genuine gate is that the test
runs against REAL dependencies with NO embedder mock, which it does (6 passed).

## Known Stubs

None. The two code artifacts are a test fixture and a test file (no data flows to
UI), and `docs/embeddings.md` is documentation. No placeholder/TODO/empty-value
stubs were introduced.

## Commits

- `c6c4d57` — test(19-03): add Docker-gated qdrant_url testcontainers fixture
- `a090fb7` — test(19-03): gate-lesson proof for keyless local embeddings
- `5824889` — docs(19-03): document provider config, offline guarantee, re-embed path

## Self-Check: PASSED

- Files: all 4 present (conftest.py, test_local_embeddings.py, docs/embeddings.md, 19-03-SUMMARY.md).
- Commits: `c6c4d57`, `a090fb7`, `5824889` all present in git history.
