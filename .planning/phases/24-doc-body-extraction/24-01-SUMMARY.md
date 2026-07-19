---
phase: 24-doc-body-extraction
plan: 01
subsystem: api
tags: [pypdf, python-docx, reportlab, lxml, document-extraction, chunking, config, tdd]

# Dependency graph
requires:
  - phase: 19-local-embeddings
    provides: local keyless embedder that Plan 24-02 will feed each body chunk through
provides:
  - "pypdf + python-docx runtime deps (pure-Python readers) + reportlab dev dep declared"
  - "7 DOCBODY_ config knobs with safe defaults, no field_validator (zero-key install boots)"
  - "app/services/doc_extract.py — pure extract_document() mime dispatch + chunk_text() + no_text_layer detection"
  - "test_doc_extract.py — 15 pure unit tests (real PDFs/DOCX generated in-test, no mocks)"
  - "lxml runtime-arch proof: cp312 manylinux2014 wheels fetch for BOTH aarch64 and x86_64"
affects: [24-02-doc-body-ingest-wiring, 24-03-real-infra-gate]

# Tech tracking
tech-stack:
  added: ["pypdf>=5.1 (runtime)", "python-docx>=1.1 (runtime)", "reportlab>=4.2 (dev)"]
  patterns:
    - "Pure side-effect-free service module: caps passed as explicit args, no settings import"
    - "Lazy import of heavy readers (pypdf/docx) inside per-surface helpers (mirrors embedders.py)"
    - "OSS zero-key config knob block: plain fields, safe defaults, no field_validator"

key-files:
  created:
    - apps/memory-api/app/services/doc_extract.py
    - apps/memory-api/tests/test_doc_extract.py
  modified:
    - apps/memory-api/pyproject.toml
    - apps/memory-api/app/config.py

key-decisions:
  - "doc_extract.py stays PURE — imports no app.config; all caps are explicit kwargs (threat T-24-05, unit-testability)"
  - "text surface = 'markdown' when mime contains 'markdown', else 'text'; markdown kept raw (D-24 discretion)"
  - "no_text_layer applies to pdf/docx only; short text/markdown is just short, not flagged"
  - "size guard runs BEFORE any parse (DoS defence T-24-02); parse errors caught in-module (T-24-01)"

patterns-established:
  - "Pattern: extraction module returns a dataclass ExtractResult(text, no_text_layer, skipped, surface, truncated, reason) rather than raising"
  - "Pattern: chunk_text strips each window and drops empty/whitespace-only chunks so no empty vector is ever embedded"

requirements-completed: [DOCBODY-01]

# Metrics
duration: 18min
completed: 2026-07-19
---

# Phase 24 Plan 01: Doc Extraction Foundation Summary

**Pure, unit-tested document body extractor (pypdf/python-docx/text/markdown mime dispatch + bounded overlapping chunking + no_text_layer detection) plus 7 DOCBODY_ config knobs, with lxml wheels proven to resolve on both runtime arches.**

## Performance

- **Duration:** ~18 min
- **Started:** 2026-07-19
- **Completed:** 2026-07-19
- **Tasks:** 2 (Task 2 = TDD: RED → GREEN)
- **Files modified:** 4 (2 created, 2 modified)

## Accomplishments
- Declared the two pure-Python readers as runtime deps (`pypdf>=5.1`, `python-docx>=1.1`) and `reportlab>=4.2` as a dev-only PDF generator for the gate.
- Added a Phase 24 config block with the 7 `DOCBODY_*` cap knobs (enabled/kill-switch, file-byte cap, total-char cap, chunk size/overlap, chunk-count cap, min-chars) — safe defaults, **no** `field_validator`, so a zero-key OSS install still boots.
- Built `doc_extract.py` as a PURE module: `extract_document()` dispatches by mime (PDF/DOCX/text/markdown), skips unknown/binary mime cleanly, catches malformed docs fail-soft, truncates oversized bodies, and flags `no_text_layer` for scanned/empty PDFs+DOCX (never an empty chunk). `chunk_text()` produces a bounded number of overlapping windows and drops empty ones.
- Proved the runtime-arch claim with real `pip download` commands (see below) rather than an assertion.

## lxml runtime-arch wheel proof (D-24-04 / checker warning)

`pypdf` ships a pure-Python `py3-none-any` wheel — no arch concern. `python-docx` pulls
`lxml` (a compiled C extension), so what actually matters is that lxml publishes cp312
wheels for **both** runtime arches (the container is always built on Linux — never
`docker build` locally, per project memory). Proven by running, into temp dirs:

```
pip download --only-binary=:all: --no-deps --python-version 312 --platform manylinux2014_aarch64 lxml
  -> Saved lxml-6.1.1-cp312-cp312-manylinux2014_aarch64.manylinux_2_17_aarch64.whl   (4.9 MB)  Successfully downloaded lxml

pip download --only-binary=:all: --no-deps --python-version 312 --platform manylinux2014_x86_64 lxml
  -> Saved lxml-6.1.1-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl     (5.1 MB)  Successfully downloaded lxml

pip download --only-binary=:all: --no-deps --python-version 312 --platform any pypdf
  -> Saved pypdf-6.14.2-py3-none-any.whl                                             Successfully downloaded pypdf
```

**Both runtime arches (linux/arm64 + linux/amd64) fetched an lxml cp312 wheel** — the
`python-docx` runtime claim holds on the amd64 prod VM and any arm64 host alike. The local
dev host runs Python as **win_amd64** (x86 emulation on the ARM64 laptop), so
`python-docx` + `lxml` also installed and imported locally, which is what let the DOCX unit
test run here rather than needing the container.

## Task Commits

1. **Task 1: Declare deps + DOCBODY_ config knobs** — `35bec19` (feat)
2. **Task 2 (TDD RED): failing unit tests** — `4d26c7b` (test)
3. **Task 2 (TDD GREEN): pure doc_extract module** — `9198eea` (feat)

_No REFACTOR commit — the GREEN implementation was already ruff-clean and needed no cleanup._

## Files Created/Modified
- `apps/memory-api/app/services/doc_extract.py` (created, 194 lines) — `ExtractResult` dataclass, `PDF_MIME`/`DOCX_MIME` constants, `extract_document()` (size guard → mime dispatch → fail-soft parse → truncate → no_text_layer), `chunk_text()` (bounded overlapping windows), lazy pypdf/docx imports.
- `apps/memory-api/tests/test_doc_extract.py` (created) — 15 pure unit tests; real PDFs/DOCX generated in-test via reportlab/python-docx (lazy-imported helpers), no mocks.
- `apps/memory-api/pyproject.toml` (modified) — added pypdf + python-docx (runtime), reportlab (dev).
- `apps/memory-api/app/config.py` (modified) — Phase 24 block with 7 `DOCBODY_*` knobs.

## Verification (real output)

- `python -m pytest tests/test_doc_extract.py -q` → **15 passed** (covers pdf text-layer extract, empty-PDF `no_text_layer`, docx paragraphs, markdown-as-is, plain text, short-text-not-flagged, unknown-mime skip, oversized truncation, file-too-large skip-before-parse, corrupt-PDF no-raise, chunk count/size, exact 150-char overlap, chunk-count cap, empty input, whitespace-only drop).
- `python -m ruff check` on all three touched Python files → **All checks passed!**
- `grep -v '^#' app/config.py | grep -c 'DOCBODY_'` → **7**.
- `grep -c 'from app.config\|import settings' app/services/doc_extract.py` → **0** (module stays pure).
- pypdf/docx import lines confirmed inside function bodies only (lines 57, 65) — lazy import honoured.
- `git diff --diff-filter=D` base→HEAD → no file deletions.

## Decisions Made
- Kept `doc_extract.py` free of any `app.config` import; caps arrive as explicit keyword args (`max_file_bytes`, `max_total_chars`, `min_chars`, `chunk_size`, `overlap`, `max_chunks`). This is the T-24-05 mitigation and what makes the module trivially unit-testable.
- `chunk_text` guards against a pathological `overlap >= chunk_size` by falling back `step = chunk_size` (prevents a non-advancing infinite loop) — a Rule 2 correctness addition beyond the literal spec.
- DOCX extraction also pulls table-cell text (not just paragraphs) so a table-only document is not falsely flagged `no_text_layer`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] chunk_text overlap>=chunk_size infinite-loop guard**
- **Found during:** Task 2 (chunk_text implementation)
- **Issue:** The plan's sliding-window spec did not address `overlap >= chunk_size`, which would make `step <= 0` and never advance the window (hang / unbounded loop on any input) — a DoS-adjacent correctness gap given caps are config-driven.
- **Fix:** `step = chunk_size - overlap; if step <= 0: step = chunk_size`.
- **Files modified:** apps/memory-api/app/services/doc_extract.py
- **Verification:** Existing chunk tests still green (15 passed); `max_chunks` also independently bounds the loop.
- **Committed in:** 9198eea (Task 2 GREEN commit)

**2. [Rule 1 - Bug] ruff lint fixes on new files (pre-commit hook clean)**
- **Found during:** Task 2 (after GREEN)
- **Issue:** ruff flagged RUF100 (stale `noqa: BLE001` — BLE not in project select), and B905/RUF007 (`zip()` without `strict=` / prefer `itertools.pairwise`) in the test.
- **Fix:** Removed the unused noqa (kept a plain comment on the intentional broad except); switched the overlap test to `itertools.pairwise`.
- **Files modified:** app/services/doc_extract.py, tests/test_doc_extract.py
- **Verification:** `ruff check` → all checks passed; 15 tests still green.
- **Committed in:** 9198eea (Task 2 GREEN commit)

---

**Total deviations:** 2 auto-fixed (1 missing-critical, 1 lint/bug).
**Impact on plan:** Both necessary for correctness and to pass the repo's commit hooks. No scope creep — module contract unchanged.

## Issues Encountered
None blocking. The dev host runs Python as win_amd64 (x86 emulation on the ARM64 laptop) rather than native arm64, so the local install proves amd64; the arm64 runtime claim is proven separately via the `pip download --platform manylinux2014_aarch64` command above (a wheel was fetched), not by a local import.

## Known Stubs
None — the module is fully implemented against its contract. (Wiring into the upload path is Plan 24-02; the real Postgres+Qdrant embed→retrieve gate is Plan 24-03, by design.)

## User Setup Required
None — no new API keys or external service configuration. `pypdf`/`python-docx` are keyless pure-Python readers; the DOCBODY_ knobs have safe defaults requiring no `.env` entry.

## Next Phase Readiness
- `doc_extract.extract_document()` + `chunk_text()` present a defined, pure contract for Plan 24-02 to wire into `media.py` (pass `settings.DOCBODY_*` as the explicit cap args) and for Plan 24-03's real-infra gate to prove keyless body embed→retrieve.
- No STATE.md / ROADMAP.md edits made in this plan (parallel-executor constraint) — the orchestrator advances phase state.

## Self-Check: PASSED

- Created files exist: doc_extract.py, test_doc_extract.py, 24-01-SUMMARY.md — all FOUND.
- Task commits present in history: `35bec19` (deps+config), `4d26c7b` (RED test), `9198eea` (GREEN module) — all FOUND.

---
*Phase: 24-doc-body-extraction*
*Completed: 2026-07-19*
