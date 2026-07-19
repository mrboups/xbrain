# Phase 24: Document Body Extraction on Upload — Context

**Gathered:** 2026-07-19 (autonomous — backlog feature #4).
**Source:** BACKLOG "Document body extraction on upload — media.py embeds only the caption, not the file content" (surfaced by the Phase-16 clean-install gate).

<domain>
## Phase Boundary

When a document is uploaded, extract its TEXT BODY (PDF / DOCX / Markdown / plain text) and make it semantically retrievable from the brain — not just the caption. Today `media.py` embeds only `caption or filename`; the uploaded bytes go to MinIO and the body is never extracted or embedded, so "upload a document and retrieve it semantically" only works to the extent the user typed a caption. This closes the Phase-16 SC#3 gap honestly.

**IN scope:** a text-extraction step on the media-upload path (dispatch by mime: PDF via pypdf, DOCX via python-docx, text/markdown decoded natively; unknown/binary → skip cleanly); chunking the extracted body + embedding each chunk as its own memory_item via the EXISTING local keyless embedder (Phase 19), carrying the full 7-field tagging contract, linked back to the MinIO object (the parent media item + the object key); size/type guards (cap file size, cap total extracted chars / chunk count, TRUNCATE not crash); a clear "no text layer" outcome for scanned PDFs (a flag on the parent item, NOT a silent empty embed); fail-soft (extraction failure must never break the upload, which already stored the object + the card item).

**OUT of scope:** OCR of scanned images/PDFs (no text layer → flagged, not OCR'd); spreadsheets/slides beyond what the chosen libs give cheaply (xlsx/pptx can be a follow-up); re-extracting already-uploaded historical documents (a backfill script is a separate follow-up); changing the retrieval/search path (it already reads memory_items via the provider — new body chunks are picked up automatically).
</domain>

<the_hard_facts_from_live_code>
1. **The gap is one line.** `apps/memory-api/app/routes/media.py:111` — `content=caption or file.filename or "media"`. The upload handler ALREADY has `data` (the raw bytes, used for the MinIO `put_object`) and calls `provider.upsert(item)` which embeds `content` via the local embedder. So the machinery to embed exists; only the BODY text is missing from what gets embedded.
2. **The embedder is keyless + local (Phase 19).** `apps/memory-api/app/embedders.py` `get_embedder()` → `local_embedder` by default; the memory provider embeds each memory_item's `content` on upsert. Chunk memory_items will be embedded the same way, zero new keys.
3. **The tagging contract is on MemoryItem already.** The upload builds a MemoryItem with team_scope, project_scope, source, truth_level, confidence, visibility, validation_status — body chunks inherit these from the upload (same team_scope/project_scope/truth_level/visibility).
4. **No doc-extraction libs yet.** `apps/memory-api/pyproject.toml` has none. Add `pypdf` (PDF) + `python-docx` (DOCX) — BOTH pure-Python (no compiled extension), so arm64+amd64 are both fine (no wheel-arch landmine like a C lib). Markdown/plain text need no lib (decode + optional strip).
5. **The Phase-16 gate asserted against the caption** (truthfully, since the body wasn't embedded). Once this lands, the 16-04 gate check can be updated to assert on the extracted BODY — but that update is optional here (note it; the new capability has its own gate).
</the_hard_facts>

<decisions>
## Implementation Decisions (locked)

### D-24-01 — Extraction dispatched by mime, fail-soft, on the existing upload path.
After the MinIO store + the parent card item, run an extraction step: PDF (`pypdf` → page text), DOCX (`python-docx` → paragraph text), `text/*` + markdown (decode utf-8, best-effort). Unknown/other mime → skip (no extraction, no error). Extraction MUST be wrapped so a failure logs + skips and NEVER breaks the upload response (the object + card item already succeeded). Prefer running it inline in the request if cheap, or fire-and-forget like brain_ingest — Claude's discretion, but it must not add unbounded latency to the upload (cap + truncate).

### D-24-02 — Chunk the body + embed each chunk as its own linked memory_item.
Split the extracted body into chunks (a sane char/token size, e.g. ~1–2k chars with small overlap), create one memory_item per chunk embedded via the existing provider. Each chunk carries the FULL 7-field tagging contract INHERITED from the upload (same team_scope, project_scope, truth_level, visibility, validation_status; confidence per policy), a distinct `source` (e.g. `upload:body` or `upload:body:<surface>`), and metadata LINKING it to the MinIO object + the parent item: `{media_key, parent_item_id, filename, chunk_index, chunk_total}`. The parent card item stays as-is (caption/filename) so the existing render is unchanged; the body chunks are additive and retrievable.

### D-24-03 — Guards: size, count, truncation, and a "no text layer" flag.
- File-size cap: skip extraction above a configurable byte cap (huge files) — the object is still stored.
- Total-body cap: TRUNCATE the extracted text to a configurable max chars before chunking (token-budget guard) — never crash on a 500-page PDF.
- Chunk-count cap: bound the number of chunk items per document.
- No text layer (scanned PDF / image-only): extraction yields empty/near-empty text → do NOT create empty-body chunks; instead set a `no_text_layer: true` flag on the PARENT item's metadata so it's explicit (not a silent no-op). NO OCR.
- All caps are config knobs with safe defaults (a zero-key install works).

### D-24-04 — arm64 + amd64 safe, keyless.
`pypdf` + `python-docx` are pure-Python → both arches fine (no compiled-wheel arch mismatch). Embedding is the Phase-19 local keyless embedder → no new API key. Confirm the deps resolve on this arm64 host and state it.

### Claude's Discretion
- Inline-in-request vs fire-and-forget extraction (latency vs simplicity) — must stay bounded either way.
- Exact chunk size/overlap + the caps' default values.
- Whether markdown is stripped of syntax or embedded as-is (as-is is simpler and fine for retrieval).
- Whether to also update the 16-04 gate to assert on the body (nice-to-have; the phase has its own gate regardless).
</decisions>

<canonical_refs>
## Canonical References — read before planning/executing
- `apps/memory-api/app/routes/media.py` (the upload handler — `:111` content line; it already has `data`, `mime`, `file.filename`, `team_scope`, `project_scope`, `truth_level`, and calls `provider.upsert`). This is where extraction + chunk-upsert hook in.
- `apps/memory-api/app/embedders.py` (`get_embedder` / `local_embedder`) + the memory provider (`get_memory_provider`) — how a memory_item's content is embedded on upsert.
- `packages/memory-models/xbrain_memory/types.py` (MemoryItem — the 7-field contract the chunks inherit).
- `apps/memory-api/pyproject.toml` (add pypdf + python-docx to the deps; both pure-Python).
- `apps/memory-api/app/config.py` (add the extraction caps as config knobs with safe defaults).
- `apps/memory-api/tests/conftest.py` + a prior real-Postgres+Qdrant gate (test_local_embeddings.py from Phase 19 shows the real-embed→Qdrant→retrieve pattern) — the gate to mirror: upload a real small PDF/DOCX/text → body extracted → chunks embedded → semantic search retrieves the BODY keyless.
- `infrastructure/scripts/verify-phase16.sh` (16-04 — optionally update its doc-analysis check to assert the body).
- CLAUDE.md — English-only; dev arm64 / prod amd64.
</canonical_refs>

<specifics>
## The gate lesson applies (hard — this is Phase 19's sibling)
"The body is extracted" proves nothing until a real document's body is embedded and retrieved keyless. Verification MUST, against a REAL Postgres + REAL Qdrant (testcontainers, as Phase 19 did) with NO OpenAI key: upload a small real PDF (and a DOCX, and a text/md) whose body contains a distinctive phrase NOT in the caption/filename → assert the body chunks land as memory_items linked to the MinIO key → assert `memory_search` for the distinctive body phrase RETRIEVES the document (proving the BODY, not the caption, is embedded). Assert the guards: an empty/no-text PDF → `no_text_layer` flag + NO empty chunks; an oversized/huge body → truncated, bounded chunk count; an unknown mime → skipped cleanly, upload still succeeds. Assert extraction failure does NOT break the upload. A test that embeds only the caption, or mocks the embedder, does NOT satisfy this (mirror Phase 19's non-mocked discipline). SKIP=FAIL. pypdf/python-docx are pure-Python (arm64+amd64). Git Bash docker needs MSYS_NO_PATHCONV=1.
</specifics>

<deferred>
- OCR of scanned/image-only documents — out (flagged as no_text_layer, not OCR'd).
- xlsx/pptx and other formats beyond PDF/DOCX/text/markdown — follow-up.
- Backfilling already-uploaded historical documents — a separate script.
- Updating the search/retrieval path — none needed (it reads memory_items already).
</deferred>

---
*Phase: 24-doc-body-extraction*
*Context gathered: 2026-07-19 (autonomous)*
