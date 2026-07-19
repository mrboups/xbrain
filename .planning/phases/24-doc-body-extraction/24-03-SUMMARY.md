---
phase: 24-doc-body-extraction
plan: 03
subsystem: testing
tags: [pytest, testcontainers, postgres, qdrant, fastembed, pypdf, python-docx, reportlab, integration-gate]

# Dependency graph
requires:
  - phase: 24-01
    provides: pure doc_extract core (extract_document / chunk_text) + DOCBODY_* config knobs + pypdf/python-docx/reportlab deps
  - phase: 24-02
    provides: extract_and_ingest_body service (linked, inherited-tag chunk memory_items) + media.upload_media wiring
  - phase: 19
    provides: keyless local fastembed embedder (384-dim) + the real-PG+Qdrant testcontainers gate pattern (test_local_embeddings.py)
provides:
  - "The binding DOCBODY-01 acceptance gate: real PG + real Qdrant, no OpenAI key, non-faked local embedder proof that a document BODY (PDF/DOCX/text-md) is extracted, embedded, and semantically retrieved keyless"
  - "Four live guard proofs: no_text_layer flag + zero chunks, oversized-body truncation with chunk_count bounded by DOCBODY_MAX_CHUNKS, unknown-mime skip, corrupt-doc fail-soft"
  - "Live cross-team isolation proof (T-24-12): a decoy team's identical body never leaks into the querying team's results"
affects: [doc-body-extraction, verify-phase24, retrieval, media-upload]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Gate-lesson discipline (Phase-19 sibling): real infra + real embedder on the actual write/read path; SKIP=FAIL — only the docker/model-materialization guard may skip, never a ranking/linkage/guard assertion"
    - "Distinctive BODY phrase (absent from caption/filename) as the retrieval query — proves the BODY, not the caption, is embedded"
    - "Cap tightening via mutate+restore (local_env convention) instead of an external patching lib — keeps the truncation bound deterministic and the file mock-free"

key-files:
  created:
    - apps/memory-api/tests/test_doc_body_extraction.py
  modified: []

key-decisions:
  - "Injected NativeProvider built directly from the testcontainers settings (no MinIO needed) — media_key is just a string; the provider does the real PG+Qdrant writes"
  - "DECOY_TEAM ingest of the SAME body proves team isolation live, not just team_scope==TEAM on the hit"
  - "Truncation caps tightened to 6000 chars / 1000-char chunks / MAX_CHUNKS=3 so a modest body deterministically hits the bound (chunk_count == cap) without embedding hundreds of chunks"

patterns-established:
  - "Real-infra body→embed→retrieve gate: extract_and_ingest_body → provider.search(distinctive_phrase) with the real keyless fastembed model"
  - "The word 'mock' never appears in the file (acceptance gate): the retrieval path is provably non-faked"

requirements-completed: [DOCBODY-01]

# Metrics
duration: ~20min
completed: 2026-07-19
---

# Phase 24 Plan 03: Document Body Extraction Gate Summary

**Real Postgres + real Qdrant, no-OpenAI-key, non-faked local-fastembed proof that a PDF/DOCX/text-markdown document BODY (a distinctive phrase absent from caption/filename) is extracted, embedded keyless, and retrieved by memory_search — plus four live guard proofs and a cross-team isolation proof. 7/7 integration tests pass on real infra.**

## Performance

- **Duration:** ~20 min
- **Completed:** 2026-07-19
- **Tasks:** 2
- **Files modified:** 1 (created)

## Accomplishments
- **The gate (SC1/SC2):** three `@pytest.mark.integration` tests prove, against a REAL Postgres:17 + REAL Qdrant:v1.17.1 (testcontainers) with `OPENAI_API_KEY=""` and the REAL 384-dim keyless fastembed model, that a PDF, a DOCX, and a text/markdown document whose BODY carries a distinctive phrase (never in the filename/caption) is extracted, chunked, embedded as linked `upload:body` memory_items, and retrieved first by `provider.search(<BODY phrase>)`. Each asserts chunk linkage (`metadata.parent_item_id` + `metadata.media_key`), inherited `team_scope` + `truth_level=WORKING`, and `source == "upload:body"`.
- **Four live guard proofs:** no-text-layer PDF → `no_text_layer` flag + zero chunks (parent unretrievable); oversized body → `truncated` + `chunk_count == DOCBODY_MAX_CHUNKS`; unknown/binary mime → skipped, zero chunks, parent unretrievable; corrupt PDF bytes → fail-soft (never raises), zero chunks.
- **Cross-team isolation (T-24-12):** the PDF gate ingests the SAME distinctive body under a `DECOY_TEAM`; the team-scoped search proves the decoy never surfaces in the querying team's results and every hit's `team_scope == TEAM`.
- **Non-faked by construction:** the file contains no `respx`/`mock`/`MagicMock`; the retrieval query is the BODY phrase, so a fabricated vector or caption-only embed could not pass. SKIP=FAIL.

## Test Run Record (SKIP=FAIL — the gate actually ran)

Ran GREEN on this Docker-up host (Windows ARM64 dev machine, Docker Desktop; testcontainers pulled the multi-arch `postgres:17` + `qdrant/qdrant:v1.17.1` images and ran `alembic upgrade head`):

```
$ cd apps/memory-api && python -m pytest tests/test_doc_body_extraction.py -v
tests/test_doc_body_extraction.py::test_pdf_body_extracted_embedded_retrieved_keyless PASSED
tests/test_doc_body_extraction.py::test_docx_body_extracted_embedded_retrieved_keyless PASSED
tests/test_doc_body_extraction.py::test_text_markdown_body_extracted_embedded_retrieved_keyless PASSED
tests/test_doc_body_extraction.py::test_no_text_layer_pdf_flags_and_writes_no_chunks PASSED
tests/test_doc_body_extraction.py::test_oversized_body_truncated_and_chunk_count_bounded PASSED
tests/test_doc_body_extraction.py::test_unknown_mime_skipped_no_chunks PASSED
tests/test_doc_body_extraction.py::test_extraction_failure_is_fail_soft PASSED
======================= 7 passed, 5 warnings in 29.21s ========================
```

**Result: 7 passed, 0 skipped, 0 failed.** No assertion skipped — Docker + the fastembed model were both available, so the gate ran and PASSED (not environment-skipped). The only warnings are upstream deprecations (authlib, testcontainers wait-strategy, alembic path_separator, qdrant client version probe) — none from the test itself.

## Task Commits

Each task was committed atomically:

1. **Task 1: The gate — real PDF + DOCX + text/md body → embedded → retrieved keyless (non-faked)** - `57af79d` (test)
2. **Task 2: The guard proofs — no_text_layer, truncation/chunk-cap, unknown mime, fail-soft (live)** - `1b93578` (test)

**Plan metadata:** committed with this SUMMARY (docs: complete plan)

## Files Created/Modified
- `apps/memory-api/tests/test_doc_body_extraction.py` - The DOCBODY-01 acceptance gate: 7 `@pytest.mark.integration` tests (3 core body→embed→retrieve + 4 guards) mirroring the Phase-19 `test_local_embeddings.py` discipline (`local_env` / `local_model_ready` / `_build_native_provider` / `_aclose`), with lazy reportlab/python-docx generators for real PDF/DOCX bytes.

## Decisions Made
- **Injected provider, no MinIO:** built `NativeProvider` directly from the testcontainers settings and passed a plain string `media_key` — the gate exercises the real PG+Qdrant write/read without needing an object store.
- **Decoy team over a bare scope check:** ingesting the same body under `DECOY_TEAM` and asserting it never surfaces is a stronger, live proof of isolation than only checking `hits[0].item.team_scope`.
- **Deterministic truncation bound:** tightened `DOCBODY_MAX_TOTAL_CHARS=6000`, `DOCBODY_CHUNK_SIZE=1000`, `DOCBODY_CHUNK_OVERLAP=0`, `DOCBODY_MAX_CHUNKS=3` (mutate+restore) so a ~15k-char body deterministically truncates and clamps to exactly the cap — `chunk_count == settings.DOCBODY_MAX_CHUNKS`.

## Deviations from Plan

None - plan executed exactly as written. Both tasks target the single test file; the three core tests, the four guards, the DECOY-team isolation assertion, and the SKIP=FAIL / no-`mock` discipline all match the plan and its acceptance criteria verbatim.

## Issues Encountered
- The Write tool initially rejected the shared-checkout path — the agent is isolated to the worktree, so the file was written to the worktree copy (`.claude/worktrees/agent-aae6d404cc071f8c3/apps/memory-api/tests/...`). No functional impact.
- The `@pytest.mark.integration` count via `grep -c` reads 7 as decorators once the docstring's incidental mention is excluded (`grep -c '^@pytest.mark.integration'` = 7); the acceptance floor of 3 is met with margin.

## Known Stubs
None — this plan adds only a test file that drives the real, already-wired SUT (`extract_and_ingest_body`) against real infra. No placeholder data, no empty-value stubs.

## Next Phase Readiness
- DOCBODY-01 is now proven the honest way (real infra, keyless, non-faked). The capability's binding gate is green.
- Optional follow-up noted in 24-CONTEXT: `infrastructure/scripts/verify-phase16.sh` (16-04) could be updated to assert on the extracted BODY rather than the caption — out of scope here (this phase carries its own gate).
- STATE.md / ROADMAP.md intentionally NOT modified (parallel-executor constraint); the orchestrator advances phase state.

## Self-Check: PASSED

- FOUND: `apps/memory-api/tests/test_doc_body_extraction.py`
- FOUND: `.planning/phases/24-doc-body-extraction/24-03-SUMMARY.md`
- FOUND commit: `57af79d` (Task 1 — core gate tests)
- FOUND commit: `1b93578` (Task 2 — guard tests)

---
*Phase: 24-doc-body-extraction*
*Completed: 2026-07-19*
