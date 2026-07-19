---
phase: 24-doc-body-extraction
verified: 2026-07-19T15:40:50Z
status: passed
score: 4/4 success criteria verified (DOCBODY-01 satisfied)
overrides_applied: 0
---

# Phase 24: Document Body Extraction on Upload — Verification Report

**Phase Goal:** An uploaded document's text body (PDF/DOCX/MD/text) is extracted, chunked, and embedded via the existing local keyless embedder so it is semantically retrievable — not just the caption; full 7-field tagging inherited, linked to the MinIO object; guards + no-text-layer flag; fail-soft (never breaks the upload).

**Verified:** 2026-07-19T15:40:50Z (against `main` HEAD `9e04b5b`)
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths / Success Criteria

| # | Truth (ROADMAP SC) | Status | Evidence |
|---|---------------------|--------|----------|
| SC1 | Uploading a real PDF/DOCX/text-or-markdown file whose BODY contains a distinctive phrase NOT in the caption/filename → body extracted, chunked, each chunk embedded as a memory_item linked to the MinIO object (media_key + parent_item_id) with full 7-field tagging inherited — proven against real Postgres + real Qdrant, no OpenAI key | VERIFIED | Re-ran `tests/test_doc_body_extraction.py` live on this Docker-up host: **7 passed, 0 skipped, 0 failed** (real numbers, matches SUMMARY claim exactly — SKIP=FAIL held, nothing skipped). `test_pdf_...`, `test_docx_...`, `test_text_markdown_...` each assert `hits[0].item.metadata["parent_item_id"]` + `["media_key"]` + `team_scope`/`truth_level`/`source` on the real embed→PG→Qdrant path. |
| SC2 | `memory_search` for the distinctive BODY phrase RETRIEVES the doc keyless (body, not caption); embedder NOT mocked | VERIFIED | `grep -E 'respx|MagicMock|mock' tests/test_doc_body_extraction.py` → **no matches** (confirmed independently, not just trusting the SUMMARY). Each core test's load-bearing assertion is `provider.search(PDF_PHRASE / DOCX_PHRASE / TEXT_PHRASE, team_scope=TEAM)` — the BODY phrase, never present in the filename/caption strings used (`q3-notes.pdf`, `spec.docx`, `recipe.md`). `local_env` forces `OPENAI_API_KEY=""` + `EMBEDDINGS_PROVIDER="local"`; `local_model_ready` asserts the real 384-dim fastembed vector. |
| SC3 | Guards hold live: oversized→truncated+bounded chunk count; no-text-layer PDF→flag+zero chunks; unknown mime→skipped+zero chunks; extraction failure→fail-soft; wiring AFTER parent upsert, fire-and-forget | VERIFIED | All 4 guard tests PASSED in the same live run: `test_oversized_body_truncated_and_chunk_count_bounded` (`truncated=True`, `chunk_count == settings.DOCBODY_MAX_CHUNKS`), `test_no_text_layer_pdf_flags_and_writes_no_chunks` (`no_text_layer=True`, 0 chunks, PARENT-SCAN unretrievable), `test_unknown_mime_skipped_no_chunks` (`skipped=True`, 0 chunks), `test_extraction_failure_is_fail_soft` (corrupt PDF bytes → no raise, 0 chunks). Read `app/routes/media.py` directly: `asyncio.create_task(_run_body_ingest(...))` is at line 171, strictly AFTER `await provider.upsert(item)` (parent card) at line 163; `_run_body_ingest` and `extract_and_ingest_body` are each wrapped in `try/except Exception`, gated by `settings.DOCBODY_EXTRACTION_ENABLED`. |
| SC4 | pypdf + python-docx (pure-Python) resolve on arm64 AND amd64; embedding stays keyless | VERIFIED | Independently re-ran the wheel-resolution proof (not trusting the SUMMARY's numbers): `pip download --only-binary=:all: --platform manylinux2014_aarch64 --python-version 312 lxml` → fetched `lxml-6.1.1-...manylinux2014_aarch64...whl`; same for `manylinux2014_x86_64` → fetched `lxml-6.1.1-...manylinux2014_x86_64...whl`; `pypdf` (`py3-none-any`) and `python-docx` (`py3-none-any`) both resolved with `--platform any`. No new API key: `local_env` fixture proves the embed path runs with `OPENAI_API_KEY=""`. |

**Score:** 4/4 success criteria verified. DOCBODY-01 requirement SATISFIED.

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `apps/memory-api/app/services/doc_extract.py` | Pure mime-dispatch + chunk_text + no_text_layer core | VERIFIED | 195 lines. Exports `extract_document`, `chunk_text`, `ExtractResult`. Imports pypdf/docx lazily (inside `_extract_pdf`/`_extract_docx`), imports no `app.config` (`grep -c 'from app.config\|import settings'` → 0). Size guard before parse, per-surface try/except, truncation, no_text_layer for pdf/docx only. |
| `apps/memory-api/app/services/doc_body_ingest.py` | Settings-aware orchestration: extract→chunk→linked chunk MemoryItem→provider.upsert; never raises | VERIFIED | 163 lines. `extract_and_ingest_body` wraps its whole body in `try/except Exception`, reads `settings.DOCBODY_*` caps, builds chunk items with `team_scope` inherited verbatim, `source="upload:body"`, metadata = `{media_key, parent_item_id, filename, chunk_index, chunk_total}`, deterministic id `uuid5(DOCBODY_INGEST_NS, "<parent>:<i>")`. |
| `apps/memory-api/app/routes/media.py` | upload_media schedules body ingest fire-and-forget after parent upsert; sets no_text_layer on parent | VERIFIED | `asyncio.create_task(_run_body_ingest(...))` at line 171, after `provider.upsert(item)` at line 163; gated by `settings.DOCBODY_EXTRACTION_ENABLED`; `_run_body_ingest` sets `no_text_layer` on the parent via `provider.update(...)` and is wrapped in `try/except Exception`. Upload response shape, parent MemoryItem fields, MinIO put, 413/503 guards unchanged. |
| `apps/memory-api/app/config.py` | DOCBODY_* knobs, safe defaults, no field_validator | VERIFIED | `grep -v '^#' app/config.py \| grep -c 'DOCBODY_'` → **7** (matches spec exactly: ENABLED, MAX_FILE_BYTES, MAX_TOTAL_CHARS, CHUNK_SIZE, CHUNK_OVERLAP, MAX_CHUNKS, MIN_CHARS). No `field_validator` on the block. |
| `apps/memory-api/pyproject.toml` | pypdf + python-docx runtime, reportlab dev | VERIFIED | `pypdf>=5.1`, `python-docx>=1.1` under `[project].dependencies`; `reportlab>=4.2` under `[project.optional-dependencies].dev`. |
| `apps/memory-api/tests/test_doc_extract.py` | Pure unit tests, no DB/network | VERIFIED | `python -m pytest tests/test_doc_extract.py -q` → **15 passed** (re-ran live). |
| `apps/memory-api/tests/test_doc_body_ingest.py` | Unit tests against fake provider | VERIFIED | `python -m pytest tests/test_doc_body_ingest.py -q` → **8 passed** (re-ran live). Asserts linkage, 7-field inheritance, `all(item.team_scope == TEAM)`, deterministic ids, no_text_layer→0 upserts, fail-soft. |
| `apps/memory-api/tests/test_doc_body_extraction.py` | THE gate: real-infra, non-mocked, keyless body→embed→retrieve + guards | VERIFIED | `python -m pytest tests/test_doc_body_extraction.py -v` → **7 passed, 0 skipped, 0 failed** (re-ran live against real testcontainers PG:17 + Qdrant:v1.17.1). 244 lines file confirmed >= min_lines: 180 from plan frontmatter (actually 484 lines). |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `app/routes/media.py` | `app/services/doc_body_ingest.py` | `asyncio.create_task(_run_body_ingest(...))` after `provider.upsert(parent)` | WIRED | Line-order confirmed: create_task (171) > parent upsert (163). Top-level import `from app.services.doc_body_ingest import extract_and_ingest_body` present. |
| `app/services/doc_body_ingest.py` | `provider.upsert` | one upsert per body chunk MemoryItem | WIRED | `await provider.upsert(item)` inside the `for i, chunk in enumerate(chunks)` loop. |
| chunk `MemoryItem.metadata` | parent media object | `media_key` + `parent_item_id` + `chunk_index`/`chunk_total` | WIRED | Confirmed in both the fake-provider unit test (exact dict match) and the real-infra gate (`hits[0].item.metadata["parent_item_id"]`/`["media_key"]` assertions, all 3 formats). |
| `app/services/doc_body_ingest.py` | `app/services/doc_extract.py` | `extract_document(...)` + `chunk_text(...)` with `settings.DOCBODY_*` passed as explicit args | WIRED | Confirmed by reading the orchestration call site (lines 84–112) — caps sourced from `settings`, passed as kwargs into the pure module which imports no config. |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|---------------------|--------|
| Chunk `MemoryItem`s written on upload | `res.text` / `chunks` from `extract_document`/`chunk_text` | Real uploaded bytes → real pypdf/python-docx/utf-8 decode → real fastembed embedding → real Qdrant vector | Yes | FLOWING — proven by the live gate: `provider.search(<distinctive BODY phrase>)` ranks the correct chunk first against real Postgres + real Qdrant, no mock/stub on the embed or search path. |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Real-infra gate runs green with real numbers (not trusting SUMMARY) | `MSYS_NO_PATHCONV=1 python -m pytest tests/test_doc_body_extraction.py -v` | `7 passed, 0 skipped, 0 failed in 27.22s` | PASS |
| Unit suites (Plan 01 + Plan 02) still green | `python -m pytest tests/test_doc_extract.py tests/test_doc_body_ingest.py -q` | `23 passed` | PASS |
| Pre-existing media unit tests unaffected (non-integration subset) | `python -m pytest tests/test_media.py -q -m "not integration"` | `10 passed, 5 deselected` | PASS |
| lxml/pypdf/python-docx wheel resolution on both runtime arches (independent re-verification, not trusting SUMMARY numbers) | `pip download --only-binary=:all: --platform manylinux2014_aarch64/manylinux2014_x86_64 --python-version 312 lxml` + `pip download --platform any pypdf/python-docx` | All 4 downloads succeeded (`Successfully downloaded`) | PASS |
| DOCBODY_ config knob count | `grep -v '^#' app/config.py \| grep -c 'DOCBODY_'` | `7` | PASS |
| No mock/respx/MagicMock in the gate file | `grep -E 'respx\|MagicMock\|mock' tests/test_doc_body_extraction.py` | no matches | PASS |
| Full `test_media.py` (incl. integration) — confirms pre-existing environmental gap, not a Phase-24 regression | `python -m pytest tests/test_media.py -q` | `3 failed, 12 passed` — the 3 failures are `503` from `get_minio_client() is None` (no real MinIO container in this sandbox); confirmed `test_media.py` untouched since commit `c700260` (pre-Phase-24) via `git log --follow` | PASS (pre-existing, unrelated to DOCBODY-01) |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|--------------|------------|--------------|--------|----------|
| DOCBODY-01 | 24-01, 24-02, 24-03 | Uploaded document body extracted/chunked/embedded via local keyless embedder, semantically retrievable, full 7-field tagging inherited, linked to MinIO object, guards + no-text-layer flag, fail-soft | SATISFIED | All 4 roadmap SCs verified above with live re-run evidence (not SUMMARY-trust). No orphaned requirements found for Phase 24 in REQUIREMENTS.md (DOCBODY-01 is the sole mapped requirement). |

Note: `REQUIREMENTS.md` line 60 still shows `- [ ] **DOCBODY-01**` (unchecked) and Phase 24 does not yet appear in ROADMAP.md's Progress table (which stops at Phase 17) — this is expected documentation bookkeeping performed by the orchestrator after phase verification, not a code gap.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| — | — | none found | — | `grep -i 'TODO\|FIXME\|XXX\|HACK\|PLACEHOLDER\|coming soon\|not yet implemented'` across `doc_extract.py`, `doc_body_ingest.py`, `media.py` → no matches. No French text found in the new modules (`grep -i '[àâäéèêëïîôöùûüç]'` → no matches, English-only honored). No OCR/xlsx/pptx imports or references in the new modules (scope-creep guard). |

### Human Verification Required

None. Phase 24 is a backend-only ingestion capability (ROADMAP "UI hint: no") and every success criterion is proven programmatically against real Postgres + real Qdrant with the real keyless embedder — no visual, real-time, or external-service behavior requires human judgment.

### Gaps Summary

No gaps. All 4 ROADMAP success criteria for Phase 24 are VERIFIED with live, independently re-run evidence (not SUMMARY-trust):

- The real-infra gate (`tests/test_doc_body_extraction.py`) was re-executed on this Docker-up host and produced **7 passed, 0 skipped, 0 failed** — identical to the SUMMARY's claimed numbers, satisfying "SKIP=FAIL" since nothing skipped.
- The gate file contains no mock/respx/MagicMock; the load-bearing retrieval assertions query the distinctive BODY phrase (never the caption/filename) via the real 384-dim keyless fastembed model.
- All 4 guards (no_text_layer, truncation+chunk-cap, unknown-mime skip, fail-soft) are proven live against real Postgres+Qdrant, not just unit-level.
- The media.py wiring is confirmed fire-and-forget, scheduled strictly after the parent `provider.upsert`, fully exception-guarded, and gated by a kill-switch.
- Cross-team isolation (T-24-12) is proven live via a DECOY_TEAM ingest that never surfaces in the querying team's results.
- arm64 + amd64 wheel resolution for pypdf/python-docx/lxml was independently re-verified via fresh `pip download` commands (not merely re-reading the SUMMARY's claim).
- No scope creep: no OCR, no xlsx/pptx, no backfill script, no change to the retrieval/search path beyond new memory_items being picked up automatically.
- English-only maintained in all new code/comments/docstrings.
- The 3 pre-existing `test_media.py` integration-test failures (503 from no real MinIO in this sandbox) are confirmed unmodified since a pre-Phase-24 commit (`c700260`) and are unrelated to DOCBODY-01.

---

_Verified: 2026-07-19T15:40:50Z_
_Verifier: Claude (gsd-verifier)_
