---
phase: 24-doc-body-extraction
reviewed: 2026-07-19T00:00:00Z
depth: deep
files_reviewed: 8
files_reviewed_list:
  - apps/memory-api/app/services/doc_extract.py
  - apps/memory-api/app/services/doc_body_ingest.py
  - apps/memory-api/app/routes/media.py
  - apps/memory-api/app/config.py
  - apps/memory-api/pyproject.toml
  - apps/memory-api/tests/test_doc_extract.py
  - apps/memory-api/tests/test_doc_body_ingest.py
  - apps/memory-api/tests/test_doc_body_extraction.py
findings:
  blocker: 1
  high: 1
  medium: 3
  low: 1
  total: 6
status: issues_found
---

# Phase 24: Code Review Report

**Reviewed:** 2026-07-19T00:00:00Z
**Depth:** deep
**Files Reviewed:** 8
**Status:** issues_found

## Summary

The fail-soft contract itself is solid and well-tested: `extract_document` never lets a
malformed PDF/DOCX propagate (every parser call is wrapped, size guard runs before any
parsing), `chunk_text` correctly drops empty/whitespace windows so no empty vector is ever
embedded, `extract_and_ingest_body` wraps its entire body in one `try/except Exception` so a
mid-loop `provider.upsert` failure returns a partial `IngestResult` instead of raising, and
`_run_body_ingest` in `media.py` wraps that again — a parse failure, huge file, or corrupt
document genuinely cannot break the upload's 201 response. `team_scope`/`project_scope`/
`truth_level` are threaded through explicitly end-to-end (no derivation, no defaulting), chunk
ids are deterministic per `(parent_item_id, index)` via `uuid5`, and the real-infra gate
(24-03) proves cross-team isolation and no-text-layer non-leakage against live Postgres+Qdrant.

Two issues rise above cosmetic. Most importantly: PDF/DOCX parsing runs synchronously on the
event loop inside the fire-and-forget task, with no size/complexity cap beyond raw byte count
and no thread offload — this repo already has the `asyncio.to_thread` pattern for exactly this
class of problem (`embedders.py:60`) and Phase 24 does not use it, which matters acutely
because the documented OSS-light deployment target runs `UVICORN_WORKERS=1`. Second: the
`no_text_layer` patch onto the parent item uses a metadata snapshot captured at upload time
rather than a fresh read, so a legitimate concurrent `PATCH /v1/memory/{item_id}` landing in
the extraction window is silently overwritten. Three lower-severity design gaps are also
recorded below (a config-triggerable infinite loop in `chunk_text`, incomplete outcome
auditability on the parent item, and implicit rather than explicit inheritance of 3 of the 7
tagging fields).

## Blocker Issues

### BL-01: Synchronous PDF/DOCX parsing blocks the single event loop — no thread offload, no complexity cap

**File:** `apps/memory-api/app/services/doc_extract.py:55-76` (`_extract_pdf`, `_extract_docx`), called synchronously from `apps/memory-api/app/services/doc_body_ingest.py:84-91`, scheduled via `apps/memory-api/app/routes/media.py:171-184` (`asyncio.create_task(_run_body_ingest(...))`)

**Issue:** `extract_document` bounds only raw upload byte count (`DOCBODY_MAX_FILE_BYTES`,
10 MB) before parsing. Nothing bounds page count, DOCX zip-entry decompressed size, or wall-clock
parse time, and `_extract_pdf`/`_extract_docx` are plain synchronous functions invoked directly
inside the `async def extract_and_ingest_body` coroutine — never via `asyncio.to_thread` or an
executor. Because `asyncio.create_task` only detaches the work from *this* request's response,
not from the event loop itself, any CPU-heavy parse (a legitimate large multi-hundred-page PDF,
or a deliberately crafted compact-but-page-heavy/zip-bomb document well under the 10 MB cap)
blocks that worker's event loop for the full duration of the parse — stalling every other
concurrent request (chat, search, other uploads) on that process.

This is not a theoretical edge case for this project: `apps/memory-api/Dockerfile:46` defaults
to 2 workers, but `.env.example:88` pins the documented OSS-light single-VM target to
`UVICORN_WORKERS=1` specifically "so the ~256-366 MB model loads ONCE" — meaning in the primary
deployment target there is exactly one event loop servicing the entire API, and this code path
can freeze it. The codebase already has the fix pattern in hand: `apps/memory-api/app/embedders.py:60`
wraps the local ONNX embed call in `asyncio.to_thread` for precisely this reason, and Phase 24
does not reuse it.

**Fix:** Offload the parse to a thread and add a hard cap on parse cost independent of raw
byte size, e.g.:
```python
# doc_body_ingest.py
import asyncio
...
res = await asyncio.to_thread(
    extract_document,
    data, mime, filename,
    max_file_bytes=settings.DOCBODY_MAX_FILE_BYTES,
    max_total_chars=settings.DOCBODY_MAX_TOTAL_CHARS,
    min_chars=settings.DOCBODY_MIN_CHARS,
)
```
Additionally bound `_extract_pdf` by page count (e.g. cap `reader.pages` iteration at a
configurable `DOCBODY_MAX_PDF_PAGES` and stop early) so a compressed page-bomb PDF cannot force
unbounded work even inside the thread pool.

## High Issues

### HI-01: `no_text_layer` parent-metadata patch uses a stale snapshot — clobbers concurrent metadata edits

**File:** `apps/memory-api/app/routes/media.py:52-59, 182`

**Issue:** `_run_body_ingest` receives `parent_metadata=item.metadata` (line 182), a snapshot
of the parent item's metadata captured in the request handler's closure at upload time, and
later (line 58) does:
```python
await provider.update(
    kw["parent_item_id"], team_scope=kw["team_scope"],
    patch={"metadata": {**parent_metadata, "no_text_layer": True}},
)
```
Both `NativeProvider.update` (`packages/memory-models/xbrain_memory/providers/native_provider.py:301-313`)
and `Mem0Provider.update` (`packages/memory-models/xbrain_memory/providers/mem0_provider.py:167-186`)
re-fetch `existing` fresh, but then do `existing.model_copy(update=patch)` — the `metadata` key
is a **full replace**, not a merge against `existing.metadata`. Since the patch dict was built
from the pre-captured `parent_metadata`, any metadata change that happened on that same item
between the initial `upsert` and this task actually running is silently overwritten and lost.

This is exploitable through an ordinary, already-shipped code path: `PATCH /v1/memory/{item_id}`
(`apps/memory-api/app/routes/memory.py:429-458`) is a public authenticated endpoint that can
patch `metadata` on any item the caller owns, including a just-uploaded media item. A client
that uploads a scanned PDF and immediately edits the item's metadata (e.g. via a caption/tag
update) races the fire-and-forget extraction task; if extraction takes any meaningful time
(large file, page-heavy PDF — see BL-01), the user's edit is dropped without any error, warning,
or indication that it never persisted.

**Fix:** Re-fetch the item's *current* metadata immediately before merging, rather than reusing
the closure-captured snapshot:
```python
async def _run_body_ingest(*, provider, **kw):
    ...
    res = await extract_and_ingest_body(provider=provider, **kw)
    if res.no_text_layer:
        current = await provider.get(kw["parent_item_id"], team_scope=kw["team_scope"])
        merged_metadata = {**(current.metadata or {}), "no_text_layer": True} if current else {"no_text_layer": True}
        await provider.update(
            kw["parent_item_id"], team_scope=kw["team_scope"],
            patch={"metadata": merged_metadata},
        )
```
(Ideally, push this fetch-then-merge behavior into `provider.update()` itself for a dedicated
`metadata` patch, so every caller gets read-modify-write safety instead of full-replace
semantics.)

## Medium Issues

### MD-01: `chunk_text` has no floor on `chunk_size` — a misconfigured `DOCBODY_CHUNK_SIZE<=0` is an infinite loop

**File:** `apps/memory-api/app/services/doc_extract.py:164-194`

**Issue:** The overlap guard only protects against `overlap >= chunk_size`:
```python
step = chunk_size - overlap
if step <= 0:
    step = chunk_size   # falls back to chunk_size, which can itself be <= 0
```
If `chunk_size <= 0` (e.g. an operator sets `DOCBODY_CHUNK_SIZE=0` or a negative value in
`.env` — `config.py:283-284` explicitly documents "Deliberately NO field_validator" for this
whole knob group, mirroring EMBED-01/CATCHUP), `step` also ends up `<= 0`. With `step == 0`,
`window = text[start:start+0]` is always `""` (never appended) and `start += 0` never advances:
`while start < length and len(chunks) < max_chunks` is true forever — an unconditional infinite
loop with no `await` inside it, which (per BL-01) hangs the entire event loop permanently the
first time any document is uploaded after such a misconfiguration. This is qualitatively worse
than the other DOCBODY_* knobs, where a bad value just degrades output (e.g.
`DOCBODY_MAX_CHUNKS=0` cleanly yields zero chunks) rather than hanging the process.

**Fix:** Add a defensive floor independent of the "no validator" policy for the other knobs,
since the failure mode here is a hang rather than degraded output:
```python
chunk_size = max(1, chunk_size)
step = max(1, chunk_size - overlap)
```

### MD-02: Only `no_text_layer` is surfaced on the parent item — `skipped` / mid-ingest failures are logged only

**File:** `apps/memory-api/app/routes/media.py:52-66`; `apps/memory-api/app/services/doc_body_ingest.py:93-105, 151-162`

**Issue:** `_run_body_ingest` only calls `provider.update(...)` when `res.no_text_layer` is
`True`. When `res.skipped` is `True` (unsupported mime, over-size, or a caught parse error) or
when the outer `except Exception` branch in `extract_and_ingest_body` fires mid-loop
(`reason=f"ingest_error:{type(exc).__name__}"`, possibly after some chunks already landed), the
outcome is only written to structured logs (`media.body_ingest_done` / `doc_body_ingest.failed`)
— nothing is recorded on the item itself. The module's own stated design principle
(D-24-03: "auditable, not a silent no-op") is applied to exactly one of the several possible
zero/partial-chunk outcomes; a client or admin inspecting the item via `GET /v1/memory/{item_id}`
cannot distinguish "unsupported file type," "extraction crashed after 3 of 10 chunks," and
"still processing" from each other, and has to go to logs to find out why a document isn't
searchable.

**Fix:** Extend the same patch to cover `skipped` and the `ingest_error` reason, e.g. always
set `metadata["docbody_status"] = {"skipped": res.skipped, "no_text_layer": res.no_text_layer, "reason": res.reason}` when the outcome is not a clean full success, using the read-modify-write
fix from HI-01.

### MD-03: `visibility` / `confidence` / `validation_status` are implicitly, not explicitly, inherited

**File:** `apps/memory-api/app/routes/media.py:170-184` (call site); `apps/memory-api/app/services/doc_body_ingest.py:72-74` (defaults)

**Issue:** The module docstring and tests describe "full 7-field tagging inheritance," and
`team_scope` / `project_scope` / `truth_level` are indeed passed explicitly end-to-end. But
`visibility`, `validation_status`, and `confidence` are never passed by the `media.py` call
site (lines 170-184 omit all three) — they rely on `extract_and_ingest_body`'s default
parameter values (`visibility="team"`, `validation_status="pending"`, `confidence=1.0`,
`doc_body_ingest.py:72-74`) coincidentally matching the hardcoded values used when constructing
the parent item (`media.py:157-159`). Today both are hardcoded identically in two separate
files, so there is no live divergence — but the moment either place changes independently (e.g.
a future per-item visibility option, or a different default for uploads via a specific
`source_surface`), the chunks will silently carry stale/wrong tags for these three fields
despite the code's stated invariant that inheritance is never defaulted.

**Fix:** Pass all three explicitly from the actual parent item's values rather than relying on
matching defaults:
```python
_run_body_ingest(
    ...,
    truth_level=truth_level,
    visibility=item.visibility,
    validation_status=item.validation_status,
    confidence=item.confidence,
)
```

## Low Issues

### LO-01: Exception logging drops the stack trace

**File:** `apps/memory-api/app/services/doc_body_ingest.py:151-158`; `apps/memory-api/app/routes/media.py:67-72`

**Issue:** Both catch-all handlers log `error=str(exc)` only, discarding the traceback. Since
these are the only two catch-alls standing between a bug in the extraction/ingest path and
total silence (by design — they must never raise), losing the traceback makes diagnosing a real
regression here harder than it needs to be. This mirrors the existing convention elsewhere in
the codebase, so it's low priority, but worth tightening given how much this particular code
path depends on "logs are the only signal."

**Fix:** Use structlog's exception capture, e.g. `log.warning("doc_body_ingest.failed", ..., exc_info=exc)` (or `log.exception(...)`), so the traceback is preserved in structured output.

---

_Reviewed: 2026-07-19T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_
