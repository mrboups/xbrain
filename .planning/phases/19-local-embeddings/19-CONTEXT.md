# Phase 19: Local Embeddings (OSS default) — Context

**Gathered:** 2026-07-13
**Status:** Ready for planning
**Source:** Locked decision **Q3** of `.planning/features/open-core-edition-design.md`; the ROADMAP Phase 19 block (SC#1..SC#5); user decision (2026-07-13) to make this a dedicated phase before Phase 16.

<domain>
## Phase Boundary

A fresh OSS-light install performs semantic ingest + retrieval with **no embeddings API key** — the embedder runs in-container, keyless, no external call. OpenAI (or another provider) stays selectable via config. This unblocks Phase 16 SC#3 ("a zero-key install can ingest AND retrieve") and honors the "one key — Anthropic OR OpenAI OR Grok" promise (embeddings must not force OpenAI specifically).

**IN scope:** a local embedder wired into the ingest + `memory_search` retrieval path; provider-pluggable so OpenAI stays selectable; the Qdrant vector-dimension story (the local model's dim will differ from OpenAI's 1536); arm64+amd64 + RAM-budget proof.

**OUT of scope:** the web chat frontend (Phase 20), install packaging (Phase 16), any new UI (this is a backend engine).
</domain>

<the_hard_facts_from_the_live_code>
## What is true today (verified 2026-07-13)

1. **Embeddings hard-require an OpenAI key.** `apps/memory-api/app/embedders.py:13-14` —
   `if not settings.OPENAI_API_KEY: raise RuntimeError("OPENAI_API_KEY not configured for embeddings")`.
   The only embedder is `openai_embedder` → `text-embedding-3-small`, 1536 dims. There is NO local/keyless path. So on a zero-key OSS install, any code path that embeds RAISES.

2. **The Qdrant collection is created at 1536 dims.** `apps/memory-api/app/qdrant_setup.py:16` —
   `VECTOR_SIZE = 1536  # OpenAI text-embedding-3-small / placeholder Phase 2`. The collection `messages`
   (D-15-04) is created with this fixed size. **A local model with a different dimension (e.g. bge-small-en
   = 384, all-MiniLM-L6-v2 = 384) will NOT write into a 1536-dim collection — Qdrant rejects a dimension
   mismatch.** This is the single biggest landmine of the phase (SC#5).

3. **There is a provider abstraction to respect.** `apps/memory-api/app/embedders.py` +
   `packages/memory-models/xbrain_memory/providers` (per the design doc). The local embedder should be a
   NEW provider selected by config, not a hack that bypasses the abstraction.

4. **Team-chat CONTEXT is already keyless** (`team_context_cache.py`: `ORDER BY created_at DESC LIMIT`,
   no vectors) — so chat context works without embeddings TODAY. What breaks keyless is semantic
   `memory_search` (mcp-brain) + any vector write on ingest. Phase 19 fixes THAT path.
</the_hard_facts>

<decisions>
## Implementation Decisions (locked / strongly-constrained)

### D-19-01 — Local, in-container, keyless, no external call.
The embedder must run inside the memory-api container (or an untagged-core sidecar) with the model
weights baked into the image or downloaded at build time — NOT fetched at runtime from HuggingFace (a
zero-network OSS install must work). No API key, no telemetry, no external call at inference.

### D-19-02 — Provider-pluggable; OpenAI stays selectable.
A config knob (e.g. `EMBEDDINGS_PROVIDER=local|openai`, default `local`) selects the provider. Setting
`OPENAI_API_KEY` + provider=openai restores today's behavior with no code change. Respect the existing
provider abstraction (`embedders.py` / `packages/memory-models`), don't fork it.

### D-19-03 — The dimension-mismatch story MUST be handled, not silently broken (SC#5).
The local model's vector dimension will differ from 1536. Options the research must decide between:
  (a) create the collection at the local model's dimension when provider=local (fresh installs — clean);
  (b) a documented re-embed/migration path for an install that already has 1536-dim OpenAI vectors and
      switches to local (mixed-dimension in one collection is impossible in Qdrant — likely a per-provider
      collection name, or a re-embed).
Fresh OSS-light installs (the phase's target) take path (a). The existing-vectors case must at least be
documented and not crash. `qdrant_setup.py`'s hardcoded `VECTOR_SIZE = 1536` becomes provider-derived.

### D-19-04 — arm64 AND amd64, and it must fit the OSS-light RAM budget (SC#4).
Dev host is arm64, prod is amd64. The model + its runtime (likely ONNX via `fastembed`, or
`sentence-transformers`) must have artifacts/wheels for BOTH. And it must not OOM an e2-medium (the
Phase-1 OSS-light box) — a small model (e.g. bge-small / all-MiniLM, ~100-400 MB RAM) is the likely fit,
NOT a large one. Research must report the measured footprint, both arches.

### Claude's Discretion
- The exact model + runtime (`fastembed` ONNX vs `sentence-transformers` torch — fastembed is lighter and
  ONNX avoids a torch dependency, likely the better OSS-light fit; research decides with evidence).
- Whether the embedder is in-process in memory-api or a small sidecar (in-process is simpler if the RAM
  fits; a sidecar isolates the model's memory — research/plan decides).
- Batch vs single embed, warm-up, and where the model loads in the lifespan.
</decisions>

<canonical_refs>
## Canonical References — read before planning
- `apps/memory-api/app/embedders.py` — the current OpenAI-only embedder + the abstraction to extend.
- `apps/memory-api/app/qdrant_setup.py` — `VECTOR_SIZE = 1536` (must become provider-derived).
- `packages/memory-models/xbrain_memory/providers/` — the provider abstraction (per design doc).
- `apps/mcp-brain/app/main.py` — `memory_search` (the retrieval consumer of the vectors).
- `apps/memory-api/app/services/brain_ingest.py` — the ingest path that writes vectors.
- `apps/memory-api/app/config.py` — where the `EMBEDDINGS_PROVIDER` knob + local-model config go (follow the fail-safe default pattern; a zero-key install must boot).
- `apps/memory-api/pyproject.toml` — where the embedder dep is added (must have arm64+amd64 wheels).
- `.planning/features/open-core-edition-design.md` — Q3 + the "one key" single-key promise.
- ROADMAP Phase 19 block — SC#1..SC#5.
</canonical_refs>

<specifics>
## The gate lesson applies

Seven defects across P14/15/18 shared one cause: a check that never traversed the real deployment path.
For embeddings that means: **a test that mocks the embedder proves nothing.** Verification MUST, against a
REAL Postgres + REAL Qdrant (testcontainers, as prior phases did) with NO OpenAI key set:
ingest a document → the LOCAL embedder produces a real vector → it lands in Qdrant → `memory_search`
retrieves it by semantic similarity. And prove the OpenAI path still works when a key IS set.

Docker is available; host is arm64 (do NOT build-and-deploy images; every added dep needs BOTH arch
artifacts). Git Bash host mounts need `MSYS_NO_PATHCONV=1` + a Windows path or they silently mount nothing.
Model weights must be present offline (baked at build) — a test that silently downloads from HF at runtime
is not proving the zero-network path.
</specifics>

<deferred>
- The web chat frontend (Phase 20), install packaging (Phase 16) — not here.
- Re-embedding an existing large corpus at scale (a migration-performance concern) — document the path;
  a fresh OSS-light install has nothing to re-embed.
</deferred>

---
*Phase: 19-local-embeddings*
*Context gathered: 2026-07-13*
