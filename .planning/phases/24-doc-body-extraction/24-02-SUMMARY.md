---
phase: 24-doc-body-extraction
plan: 02
subsystem: api
tags: [document-extraction, chunking, memory-item, fire-and-forget, team-scope, fail-soft, uuid5]

# Dependency graph
requires:
  - phase: 24-01
    provides: pure doc_extract.extract_document + chunk_text + DOCBODY_ caps this service orchestrates
  - phase: 19-local-embeddings
    provides: keyless local embedder each body chunk is embedded through on provider.upsert
provides:
  - "app/services/doc_body_ingest.py — extract_and_ingest_body(): extract -> chunk -> build linked, tag-inherited chunk memory_items -> provider.upsert; never raises; returns IngestResult"
  - "DOCBODY_INGEST_NS fixed uuid5 namespace -> deterministic (idempotent) chunk ids"
  - "media.upload_media wiring: fire-and-forget body ingest AFTER the parent upsert, gated by DOCBODY_EXTRACTION_ENABLED, sets no_text_layer on the parent via provider.update"
  - "test_doc_body_ingest.py — 8 unit tests against a fake provider (linkage, inheritance, cross-team invariant, no_text_layer=>0 chunks, fail-soft)"
affects: [24-03-real-infra-gate]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Settings-aware orchestration layer over a pure module: caps read from settings here, passed as explicit args into doc_extract"
    - "Fire-and-forget detached ingest via asyncio.create_task AFTER the commit point, mirroring team_chat/brain_ingest"
    - "Deterministic chunk ids via a dedicated uuid5 namespace (idempotent on retry), mirroring BRAIN_INGEST_NS"

key-files:
  created:
    - apps/memory-api/app/services/doc_body_ingest.py
    - apps/memory-api/tests/test_doc_body_ingest.py
  modified:
    - apps/memory-api/app/routes/media.py

key-decisions:
  - "Body ingest runs fire-and-forget (asyncio.create_task) AFTER provider.upsert(item), not inline — the 201 + object + card are already committed, so a slow/failed extraction is invisible to the uploader (D-24-01/T-24-07)"
  - "Chunk team_scope is INHERITED verbatim from the upload's team_scope, never derived/defaulted — the T-24-06 cross-team invariant, asserted in the unit suite"
  - "no_text_layer is surfaced EXPLICITLY on the parent item via provider.update(patch=metadata+no_text_layer:true), not a silent no-op (D-24-03/T-24-09)"
  - "extract_and_ingest_body wraps its whole body in try/except -> on partial failure returns IngestResult(chunk_count=<landed so far>), never raises (fail-soft)"

patterns-established:
  - "Pattern: linked chunk metadata = {media_key, parent_item_id, filename, chunk_index, chunk_total} back-links every body chunk to its MinIO object + parent card"
  - "Pattern: source='upload:body' distinguishes body chunks from the parent's upload:<surface> card"

requirements-completed: [DOCBODY-01]

# Metrics
duration: 22min
completed: 2026-07-19
---

# Phase 24 Plan 02: Doc Body Ingest Wiring Summary

**extract_and_ingest_body() service that extracts an uploaded document's body, chunks it, and embeds each chunk as a team-scope-inherited, MinIO-linked memory_item (source=upload:body) — wired fire-and-forget into media.upload_media after the parent upsert, with an explicit no_text_layer parent flag and full fail-soft guarantees.**

## Performance

- **Duration:** ~22 min
- **Started:** 2026-07-19
- **Completed:** 2026-07-19
- **Tasks:** 2 (Task 1 = TDD: RED -> GREEN)
- **Files modified:** 3 (2 created, 1 modified)

## Accomplishments
- Built `doc_body_ingest.extract_and_ingest_body()` — the settings-aware layer between the pure `doc_extract` core (Plan 01) and the provider: reads the `DOCBODY_*` caps, calls `extract_document` + `chunk_text`, and upserts one linked memory_item per chunk. Every chunk INHERITS the upload's full 7-field tagging (team_scope, project_scope, truth_level, visibility, validation_status, confidence), carries `source="upload:body"`, and links back to the object via `{media_key, parent_item_id, filename, chunk_index, chunk_total}`.
- Made chunk ids deterministic — `uuid5(DOCBODY_INGEST_NS, "<parent>:<index>")` — so a retry re-upserts the SAME ids (idempotent), and unknown-mime / no_text_layer inputs create ZERO chunks (never an empty vector).
- Guaranteed fail-soft: the whole body is wrapped in `try/except Exception`; a provider.upsert that raises mid-way returns a partial `IngestResult` rather than propagating.
- Wired it into `media.upload_media` fire-and-forget via `asyncio.create_task` AFTER `provider.upsert(item)` (the parent card), gated by `settings.DOCBODY_EXTRACTION_ENABLED`. The detached `_run_body_ingest` sets `no_text_layer=True` on the PARENT item's metadata via `provider.update` when the document has no text layer, and swallows all exceptions (the request already returned).
- 8 unit tests against a fake in-memory provider prove linkage metadata, 7-field inheritance, the cross-team invariant, deterministic ids, no_text_layer -> 0 chunks, oversized truncation bound, and fail-soft — all green.

## Task Commits

Each task was committed atomically:

1. **Task 1 (TDD RED): failing unit tests for extract_and_ingest_body** - `6e104fb` (test)
2. **Task 1 (TDD GREEN): doc_body_ingest service** - `9c1b10c` (feat)
3. **Task 2: wire body ingest into media.upload_media** - `a5ea9e7` (feat)

_No REFACTOR commit — the GREEN implementation was ruff-clean after import sorting; no behavioral cleanup needed._

## Files Created/Modified
- `apps/memory-api/app/services/doc_body_ingest.py` (created, ~162 lines) — `DOCBODY_INGEST_NS` fixed uuid5 namespace, `IngestResult` dataclass, `async extract_and_ingest_body()` (caps-from-settings -> extract_document -> skip/no_text_layer short-circuits -> chunk_text -> per-chunk linked MemoryItem upsert; never raises).
- `apps/memory-api/tests/test_doc_body_ingest.py` (created, ~192 lines) — fake in-memory provider + 8 unit tests (no real DB/Qdrant/embedder); empty PDF generated in-test via reportlab for the no_text_layer path.
- `apps/memory-api/app/routes/media.py` (modified) — added `import asyncio` + `extract_and_ingest_body` import; new module-level `_run_body_ingest` detached task (sets parent no_text_layer via provider.update, fully guarded); scheduled via `asyncio.create_task` after the parent upsert, gated by `DOCBODY_EXTRACTION_ENABLED`. Upload response, parent item fields, MinIO put, and 413/503 guards left unchanged. Also fixed a pre-existing I001 import-sort lint on the import block.

## Decisions Made
- **Fire-and-forget over inline.** Plan 01's context (D-24-01) left inline-vs-detached to discretion; chose `asyncio.create_task` after the parent upsert (mirroring `team_chat.py` / `brain_ingest.py`) so extraction latency/failure is fully decoupled from the 201 response.
- **Partial-ingest IngestResult on failure.** When `provider.upsert` raises on chunk N, the service records `chunk_count = <chunks landed before the failure>` and a machine-readable `reason` (`ingest_error:<Type>`) rather than pretending 0 or raising — accurate + fail-soft.
- **Kept the `create_task` call bare** (no stored reference) to match the established, committed fire-and-forget convention in `team_chat.py` (RUF006 is tolerated repo-wide for these detached tasks).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed pre-existing I001 import-sort lint in media.py**
- **Found during:** Task 2 (media.py wiring)
- **Issue:** Adding the two new imports surfaced ruff `I001` on the import block; the block already failed `I001` on the base commit (pre-existing), so leaving it would have kept the file lint-dirty.
- **Fix:** Ran a TARGETED `ruff check --select I --fix` (import ordering only) so the pre-existing `UP017`/`B008` lines were NOT touched.
- **Files modified:** apps/memory-api/app/routes/media.py
- **Verification:** `ruff check --select I app/routes/media.py` -> All checks passed; net ruff for the file dropped the I001 and added only one RUF006 (identical to team_chat.py's committed bare `create_task`).
- **Committed in:** a5ea9e7 (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 lint hygiene).
**Impact on plan:** Minor, in-scope (import block). No behavioral change; the service contract and wiring match the plan exactly.

## Issues Encountered
- **3 `@pytest.mark.integration` tests in test_media.py fail in this environment (503, not caused by this plan).** `test_upload_returns_201_and_item_id`, `test_raw_serve_streams_back_uploaded_file`, and `test_upload_413_on_oversized_file` require a real MinIO (they short-circuit at `get_minio_client() is None -> 503` before reaching any of this plan's code). Verified they fail IDENTICALLY on the base media.py (pre-change), so they are pre-existing and environmental — the real Postgres+Qdrant+MinIO gate is Plan 24-03 by design. All non-integration media unit tests (10) and all doc_body_ingest tests (8) pass.

## Verification (real output)

- `python -m pytest tests/test_doc_body_ingest.py -q` -> **8 passed** (linkage metadata, 7-field inheritance, `all(item.team_scope == TEAM)` cross-team invariant, deterministic ids, unknown-mime skip=0 upserts, no_text_layer=0 chunks, oversized truncated+bounded, fail-soft when upsert raises).
- `python -m pytest tests/test_doc_body_ingest.py tests/test_media.py -q -m "not integration"` -> **18 passed, 5 deselected**.
- `python -c "import ast; ast.parse(open('app/routes/media.py').read())"` -> media.py parses; `import app.routes.media` (with conftest env) -> imports OK, `_run_body_ingest` + `extract_and_ingest_body` present.
- Acceptance greps: `extract_and_ingest_body`, `DOCBODY_EXTRACTION_ENABLED`, `asyncio.create_task`, `no_text_layer`, `except Exception` all present in media.py; `DOCBODY_INGEST_NS`, `class IngestResult`, `source = "upload:body"`, and all four link fields present in doc_body_ingest.py.
- Line order: `asyncio.create_task` (line 172) is AFTER the parent `await provider.upsert(item)` (line 164).
- `ruff check app/services/doc_body_ingest.py tests/test_doc_body_ingest.py` -> All checks passed.
- `git diff --diff-filter=D` base->HEAD -> no file deletions; no untracked files left.

## Known Stubs
None — the service and wiring are fully implemented against their contract. The real Postgres+Qdrant+MinIO keyless embed->retrieve gate (upload a real PDF/DOCX/text whose body phrase is not in the caption, then `memory_search` retrieves it) is Plan 24-03 by design, not a stub.

## Threat Flags
None — no new security surface beyond the plan's `<threat_model>`. Body chunks embed via the SAME keyless local provider the upload already uses (no new key/endpoint); team_scope is inherited (T-24-06 closed at construction); chunk count/size are capped upstream (T-24-08); ingest is fully fail-soft (T-24-07).

## User Setup Required
None — no new API keys or external service configuration. Body embedding reuses the Phase-19 keyless local embedder; the `DOCBODY_*` knobs (including the `DOCBODY_EXTRACTION_ENABLED` kill-switch) have safe defaults requiring no `.env` entry.

## Next Phase Readiness
- `extract_and_ingest_body` + the media.py wiring present the live upload->body-embed path for Plan 24-03's real-infra gate to prove keyless body embed->retrieve against real Postgres+Qdrant+MinIO (the "gate lesson": a distinctive body phrase NOT in the caption must be retrievable).
- No STATE.md / ROADMAP.md edits made (parallel-executor constraint) — the orchestrator advances phase state.

## Self-Check: PASSED

- Created files exist: doc_body_ingest.py, test_doc_body_ingest.py, 24-02-SUMMARY.md — all FOUND.
- Task commits present in history: `6e104fb` (RED test), `9c1b10c` (GREEN service), `a5ea9e7` (media wiring) — all FOUND.

---
*Phase: 24-doc-body-extraction*
*Completed: 2026-07-19*
