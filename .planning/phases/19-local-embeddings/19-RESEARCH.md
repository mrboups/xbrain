# Phase 19: Local Embeddings (OSS default) - Research

**Researched:** 2026-07-18
**Domain:** Local ONNX text embeddings (fastembed) wired into an existing pluggable memory-provider abstraction; Qdrant collection dimension provisioning.
**Confidence:** HIGH (stack pick, offline mechanics, dimension landmine, provider seam, RAM duplication risk all VERIFIED empirically or via live registries in this session) / MEDIUM (exact amd64 production RAM number - measured only under QEMU emulation) / LOW (none of the load-bearing claims — see Assumptions Log for the two items that are genuinely unverified)

## Summary

Local embeddings are a well-scoped integration, not a research-heavy stack decision: **`fastembed` (ONNX runtime) with its own default model `BAAI/bge-small-en-v1.5` (384-dim)** is the correct pick, confirmed against the live PyPI registry (both arm64 and amd64 `onnxruntime` wheels exist for cp312) and empirically proven end-to-end in this session — I built the exact Dockerfile pattern the phase needs (bake the model at `docker build` time, run the container with `--network none` at runtime) and it produced a real 384-dim vector with **zero network access**, on both a native arm64 build and a QEMU-emulated amd64 build. The alternative, `sentence-transformers` + `torch`, was directly compared: `torch` alone is a 427-527 MB wheel (vs `onnxruntime`'s 16-19 MB), which would blow the OSS-light image-size and RAM budget for no quality benefit at this model tier.

The real risk in this phase is not the library choice — it's two integration landmines this research found in the live code, both confirmed by direct file reads: (1) **three separate call sites hardcode Qdrant's vector size to 1536** (`qdrant_setup.py`, `admin_wipe.py`, and implicitly via `settings.QDRANT_COLLECTION` read by a fourth service, `brain-janitor`, which already had a real production incident from exactly this kind of drift), and (2) **`apps/memory-api/Dockerfile` runs uvicorn with `--workers 2`**, which I proved empirically duplicates an in-process-loaded model into two independent ~272 MB processes (spawn-based, no copy-on-write) — enough to consume essentially all of the ~550 MB of headroom left on the OSS-light e2-medium (4 GB) budget once you account for what the other 10 untagged-core services already reserve.

**Primary recommendation:** Add `fastembed>=0.8.0` to `apps/memory-api/pyproject.toml`, bake `BAAI/bge-small-en-v1.5` into the image at `docker build` time with `HF_HUB_OFFLINE=1` set at runtime, load it **once** — via a small sidecar service, not in-process under the current 2-worker memory-api — and make `qdrant_setup.py` / `admin_wipe.py` derive `VECTOR_SIZE` from `EMBEDDINGS_PROVIDER` through one shared helper instead of three independent hardcodes.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**D-19-01 — Local, in-container, keyless, no external call.**
The embedder must run inside the memory-api container (or an untagged-core sidecar) with the model weights baked into the image or downloaded at build time — NOT fetched at runtime from HuggingFace (a zero-network OSS install must work). No API key, no telemetry, no external call at inference.

**D-19-02 — Provider-pluggable; OpenAI stays selectable.**
A config knob (e.g. `EMBEDDINGS_PROVIDER=local|openai`, default `local`) selects the provider. Setting `OPENAI_API_KEY` + provider=openai restores today's behavior with no code change. Respect the existing provider abstraction (`embedders.py` / `packages/memory-models`), don't fork it.

**D-19-03 — The dimension-mismatch story MUST be handled, not silently broken (SC#5).**
The local model's vector dimension will differ from 1536. Options the research must decide between: (a) create the collection at the local model's dimension when provider=local (fresh installs — clean); (b) a documented re-embed/migration path for an install that already has 1536-dim OpenAI vectors and switches to local (mixed-dimension in one collection is impossible in Qdrant — likely a per-provider collection name, or a re-embed). Fresh OSS-light installs (the phase's target) take path (a). The existing-vectors case must at least be documented and not crash. `qdrant_setup.py`'s hardcoded `VECTOR_SIZE = 1536` becomes provider-derived.

**D-19-04 — arm64 AND amd64, and it must fit the OSS-light RAM budget (SC#4).**
Dev host is arm64, prod is amd64. The model + its runtime (likely ONNX via `fastembed`, or `sentence-transformers`) must have artifacts/wheels for BOTH. And it must not OOM an e2-medium (the Phase-1 OSS-light box) — a small model (e.g. bge-small / all-MiniLM, ~100-400 MB RAM) is the likely fit, NOT a large one. Research must report the measured footprint, both arches.

### Claude's Discretion
- The exact model + runtime (`fastembed` ONNX vs `sentence-transformers` torch — fastembed is lighter and ONNX avoids a torch dependency, likely the better OSS-light fit; research decides with evidence).
- Whether the embedder is in-process in memory-api or a small sidecar (in-process is simpler if the RAM fits; a sidecar isolates the model's memory — research/plan decides).
- Batch vs single embed, warm-up, and where the model loads in the lifespan.

### Deferred Ideas (OUT OF SCOPE)
- The web chat frontend (Phase 20), install packaging (Phase 16) — not here.
- Re-embedding an existing large corpus at scale (a migration-performance concern) — document the path; a fresh OSS-light install has nothing to re-embed.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| EMBED-01 | A fresh install with NO embeddings API key ingests and semantically retrieves memory — embeddings run in-container, keyless, no external call. OpenAI (or another provider) remains selectable via config without code changes; the local model ships for both arm64 and amd64 and fits the OSS-light RAM budget. | Stack pick + both-arch wheel proof (§1), offline-bake Dockerfile pattern proven with `--network none` (§2), dimension-provisioning fix across `qdrant_setup.py`/`admin_wipe.py` (§3), `EMBEDDINGS_PROVIDER` seam at `deps.py:_build_provider()` (§4), single-embedder-instance proof for write+read parity (§5), measured RAM + workers=2 duplication finding + sidecar recommendation (§6), testcontainers-based real-Qdrant semantic-ranking test plan (§7/Validation). |
</phase_requirements>

## Project Constraints (from CLAUDE.md)

- Open-source + self-hostable only; no managed-cloud-only embeddings API may become a hard dependency — `fastembed` (Apache-2.0/MIT-family, runs fully local) satisfies this; OpenAI remains opt-in only.
- Deployment targets GCP VM Ubuntu 24.04 or Railway via Docker Compose — no new orchestration primitive; a sidecar is just another `docker-compose.yml` service, consistent with existing MCP sidecars (`mcp-scraper`, `mcp-brain`).
- Dev machine is ARM64, prod is amd64 — **never build memory-api's image locally and deploy it to the VM** (exec format error); this research itself only built *throwaway verification images*, never the production image, and used `docker buildx --platform linux/amd64 --load` (cross-build, not cross-deploy) purely to prove wheel/offline compatibility.
- Product/code in English only — new config keys, log messages, and doc strings introduced by this phase must be English (matches existing `embedders.py`/`config.py` style).
- GSD workflow — this file is research input to `/gsd-plan-phase 19`; no code changes were made in this session.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Text -> vector embedding (inference) | API/Backend (memory-api process, or a new embeddings sidecar) | — | Pure CPU inference, no persistence; belongs next to the code that already injects `Embedder` callables (`app/embedders.py`) |
| Vector storage + ANN search | Database/Storage (Qdrant) | API/Backend (query/filter construction in `NativeProvider`) | Qdrant already owns persistence + similarity search; memory-api only builds filters |
| Collection schema / dimension provisioning | API/Backend (`qdrant_setup.py` lifespan hook) | Database/Storage (Qdrant enforces the dimension at write time) | Provisioning logic must run before any request touches the collection; Qdrant is just the enforcer of whatever dimension it's told at creation |
| Provider selection (local vs OpenAI) | API/Backend (`app/config.py` + `app/embedders.py`) | — | Config-driven, no UI, no other tier is involved |
| Retrieval consumer (`memory_search` MCP tool) | API/Backend (mcp-brain) | — | mcp-brain is a thin HTTP proxy to memory-api's `/v1/memory/search` — it does **not** embed anything itself (verified, see §5); it stays in the API tier conceptually even though it's a separate container |

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `fastembed` | `0.8.0` (latest on PyPI, released 2026-03-23) [VERIFIED: PyPI registry `pypi.org/pypi/fastembed/json`] | Local ONNX text embedding, pure-python wrapper around `onnxruntime` | Maintained by Qdrant (the vector DB already in this stack); default model ships quantized ONNX, no torch; PyPI wheel is `py3-none-any` (platform-independent — the arch sensitivity lives entirely in its `onnxruntime` dependency, which was checked separately) |
| `onnxruntime` | `1.27.0` (latest, released 2026-06-15) [VERIFIED: PyPI registry] | ONNX inference engine `fastembed` depends on | Has published wheels for **both** `manylinux_2_27_aarch64.manylinux_2_28_aarch64` (arm64) and `manylinux_2_27_x86_64.manylinux_2_28_x86_64` (amd64) for `cp312` (matches `memory-api`'s `python:3.12-slim` base image) [VERIFIED: PyPI registry filenames] |

**Model:** `BAAI/bge-small-en-v1.5` (fastembed's own default — no `model_name` override needed)

| Property | Value | Source |
|----------|-------|--------|
| Dimension | 384 | [VERIFIED: empirically measured `len(vecs[0])` in this session, and HF model card] |
| Parameters | 33.4M | [CITED: huggingface.co/BAAI/bge-small-en-v1.5 model card] |
| On-disk size | 0.067 GB (~67 MB), ONNX + quantized by default | [CITED: fastembed `docs/examples/Supported_Models.ipynb`, fetched via Context7] |
| Max sequence length | 512 tokens | [CITED: HF model card] |
| MTEB average | 62.17 | [CITED: huggingface.co/BAAI/bge-small-en-v1.5 model card] |
| MTEB retrieval average | 51.68 | [CITED: huggingface.co/BAAI/bge-small-en-v1.5 model card] |
| License | MIT | [CITED: fastembed Supported Models table] |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `huggingface-hub` | `1.24.0` (latest on PyPI) [VERIFIED] | Transitive dep of `fastembed`; handles the model download + local cache, and honors `HF_HUB_OFFLINE=1` | Already pulled in by `fastembed`, no direct pin needed unless a specific offline behavior needs pinning |
| `testcontainers[postgres,qdrant]` | `>=4.8` (current dev pin is `testcontainers[postgres]>=4.8`; `qdrant` extra confirmed present in `testcontainers` `4.14.2`) [VERIFIED: PyPI `provides_extra` list] | Real-Qdrant integration tests for the "gate lesson" (§Validation) | Add the `qdrant` extra to the existing dev dependency, mirroring the `pg_url` fixture already in `apps/memory-api/tests/conftest.py:67-123` |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `fastembed` (ONNX) | `sentence-transformers` (torch) | `sentence-transformers` 5.6.0 requires `torch>=1.11.0` [VERIFIED: PyPI `requires_dist`]; `torch` 2.13.0's own `cp312` wheels are 427 MB (`manylinux_2_28_aarch64`) / 527 MB (`manylinux_2_28_x86_64`) [VERIFIED: PyPI registry] — 20-30x heavier than `onnxruntime`'s 16-19 MB wheel, for a model tier where fastembed's own default already beats `all-MiniLM-L6-v2` on MTEB. No reason to pay the torch tax at this model size. |
| `BAAI/bge-small-en-v1.5` | `sentence-transformers/all-MiniLM-L6-v2` (also in fastembed's own catalog, no torch needed either way) | Same 384 dims and similar on-disk size (0.090 GB vs 0.067 GB), but **128-token max sequence length vs 512** [CITED: HF model card] — meaningfully more truncation risk on chat messages/documents — and no comparable published MTEB average was found in-session (existing public benchmarks generally rank it below bge-small-en-v1.5 on retrieval, but that specific side-by-side number was not independently verified here — see Assumptions Log A1). |
| One shared Qdrant collection across providers | Per-provider collection name (e.g. `messages_384d` vs `messages_1536d`) | Rejected as the *default* approach. `settings.QDRANT_COLLECTION` is already read from 4+ independent call sites across 2 services (see §3); a real Phase-11 production incident already happened from exactly this kind of split-brain collection-name config drift (documented in `apps/brain-janitor/app/config.py:19-25`). Adding a second derived value (dimension-suffix) that all those call sites must independently compute correctly increases, not decreases, that risk. Recommended instead: one collection, fail loud at boot on mismatch (§3). |

**Installation:**
```bash
# apps/memory-api/pyproject.toml — dependencies array
"fastembed>=0.8.0",  # Phase 19: local keyless embeddings (ONNX via onnxruntime, no torch)
```
```bash
# apps/memory-api/pyproject.toml — [project.optional-dependencies].dev
"testcontainers[postgres,qdrant]>=4.8",  # add the qdrant extra alongside the existing postgres one
```

**Version verification:** confirmed live against `https://pypi.org/pypi/<pkg>/json` in this session (`fastembed` 0.8.0, `onnxruntime` 1.27.0, `sentence-transformers` 5.6.0, `torch` 2.13.0, `huggingface-hub` 1.24.0, `qdrant-client` 1.18.0 — current pin `qdrant-client>=1.17` in `apps/memory-api/pyproject.toml:14` remains compatible, no bump required).

## Architecture Patterns

### System Architecture Diagram

```
                     ┌─────────────────────────────────────────────────────┐
                     │                  memory-api (FastAPI)                │
                     │                                                       │
  ingest write ─────▶│  brain_ingest.py / rag_enrichment.py                 │
  (chat, doc,        │        │                                             │
   LibreChat, etc.)  │        ▼                                             │
                     │  get_memory_provider()  ── singleton, built once ──┐  │
                     │        │                                          │  │
  retrieval query ──▶│  routes/memory.py: /v1/memory/search               │  │
  (mcp-brain HTTP     │        │                                          │  │
   proxy, no local    │        ▼                                          │  │
   embedding logic)   │  NativeProvider.upsert() / .search()              │  │
                     │        │                          │                │  │
                     │        └──────── self._embedder ──┘◀───────────────┘  │
                     │                     │  (ONE injected callable        │
                     │                     │   for BOTH write and read)     │
                     │                     ▼                                 │
                     │        get_embedder()  [app/embedders.py]             │
                     │        reads EMBEDDINGS_PROVIDER                      │
                     │           │                    │                      │
                     │      "local" (default)     "openai"                  │
                     │           │                    │                      │
                     │           ▼                    ▼                      │
                     │   local_embedder()      openai_embedder()             │
                     │   (fastembed, in-        (AsyncOpenAI HTTP call,      │
                     │    process OR sidecar     requires OPENAI_API_KEY)    │
                     │    call — see §6)                                     │
                     └───────────┬───────────────────────────────────────────┘
                                 │ vector (384-dim local / 1536-dim openai)
                                 ▼
                     ┌─────────────────────────────┐
                     │           Qdrant             │
                     │  collection "messages"       │
                     │  dimension fixed at CREATE   │
                     │  time by qdrant_setup.py     │◀── must derive VECTOR_SIZE
                     │  (today: hardcoded 1536)     │    from EMBEDDINGS_PROVIDER
                     └─────────────────────────────┘
```

A reader can trace: chat/document text enters via `brain_ingest.py`, hits the single provider singleton, which calls whichever embedder `EMBEDDINGS_PROVIDER` selected, and the resulting vector is written to the one Qdrant collection whose dimension was fixed at container-startup time. Retrieval (`memory_search`) is the exact same path in reverse — same singleton, same embedder, same collection. mcp-brain never touches embeddings; it only proxies HTTP to `/v1/memory/search`.

### Recommended Project Structure

No new top-level directories. Changes are additive within the existing layout:

```
apps/memory-api/app/
├── embedders.py          # ADD: local_embedder(), get_embedder() selector
├── config.py              # ADD: EMBEDDINGS_PROVIDER, LOCAL_EMBEDDING_MODEL settings
├── qdrant_setup.py         # CHANGE: VECTOR_SIZE -> derived from get_embedding_dimension()
├── deps.py                 # CHANGE: _build_provider()'s "native" branch calls get_embedder()
└── routes/admin_wipe.py    # CHANGE: _wipe_qdrant_full()'s hardcoded size=1536 -> derived

apps/embeddings-local/      # NEW (if sidecar is chosen — see §6 recommendation)
├── Dockerfile              # bakes fastembed + BAAI/bge-small-en-v1.5 at build time
├── app/main.py              # tiny FastAPI/HTTP endpoint: POST /embed {text} -> {vector}
└── pyproject.toml
```

### Pattern 1: Bake-then-offline (D-19-01)

**What:** Download the model at `docker build` time (network available), embed the cache into an image layer, then force `HF_HUB_OFFLINE=1` at runtime so the container never calls out.
**When to use:** Any zero-network OSS install requirement where the model source (HuggingFace Hub) can't be reached at runtime.
**Example (proven working in this session — built the image, ran it with `docker run --network none`, got a real 384-dim vector back):**
```dockerfile
# Source: verified in this research session — see file citations below
FROM python:3.12-slim AS builder
RUN pip install --no-cache-dir fastembed
# Bake the model weights into a KNOWN path during build (network available here).
RUN python -c "from fastembed import TextEmbedding; \
    TextEmbedding(model_name='BAAI/bge-small-en-v1.5', cache_dir='/build/model_cache')"

FROM python:3.12-slim AS runtime
# ... existing multistage COPY of deps ...
COPY --from=builder --chown=xbrain:xbrain /build/model_cache /app/model_cache
ENV HF_HUB_OFFLINE=1
USER xbrain
# app code passes cache_dir="/app/model_cache" explicitly to TextEmbedding(...)
```
Two details that matter and are easy to get wrong against the REAL `apps/memory-api/Dockerfile` (verified by reading it, lines 1-30):
1. The existing Dockerfile's `runtime` stage does `USER xbrain` (uid 10001) at line 26. If the model is baked as root in a stage and copied without `--chown`, the non-root runtime user cannot read the cache files and the app will (a) fail to load the model, or (b) fall back to attempting a network download — defeating D-19-01 silently. Use `COPY --chown=xbrain:xbrain` (or an equivalent `chmod`/`chown` step).
2. Pass `cache_dir` **explicitly** as a constructor argument sourced from `settings` rather than relying on fastembed's implicit default location — this makes the build-time bake path and the runtime load path provably the same directory.

**Source (fastembed's own offline mechanics, fetched via Context7 `/qdrant/fastembed`):**
```python
# Source: https://github.com/qdrant/fastembed/blob/main/fastembed/fastembed/common/model_management.py
@classmethod
def download_model(cls, model, cache_dir, retries=3, **kwargs):
    local_files_only = kwargs.get("local_files_only", False)
    hf_offline = os.environ.get("HF_HUB_OFFLINE", "").strip().upper()
    if not local_files_only and hf_offline in {"1", "TRUE", "YES", "ON"}:
        local_files_only = True   # HF_HUB_OFFLINE env var forces cache-only, no network
```

### Pattern 2: Single-instance embedder injection (already exists — extend, don't fork)

**What:** `apps/memory-api/app/deps.py:426-449`'s `_build_provider()` constructs exactly one `NativeProvider` per process, injecting one `Embedder` callable. Both `upsert()` and `search()` on `NativeProvider` (`packages/memory-models/xbrain_memory/providers/native_provider.py:94` and `:224`) call `self._embedder(...)` — the same bound closure.
**When to use:** This is the ONLY seam Phase 19 needs to touch for provider selection. No changes needed to the `MemoryProvider` ABC (`packages/memory-models/xbrain_memory/provider.py`) or to `NativeProvider` itself.
**Example:**
```python
# apps/memory-api/app/embedders.py — ADD
import asyncio
from app.config import settings

async def local_embedder(text: str) -> list[float]:
    """Embed text via the in-container fastembed model (keyless, offline)."""
    model = _get_local_model()  # module-level singleton, lazy-init once
    # fastembed's .embed() is a SYNCHRONOUS generator — must offload or it
    # blocks the event loop for the duration of ONNX inference.
    vecs = await asyncio.to_thread(lambda: list(model.embed([text])))
    return vecs[0].tolist()

def get_embedder():
    """Selects the configured embedder. Unknown/unset values fall back to
    `local` — never crash-loop a keyless install (mirrors the Phase 18
    LOCAL_AUTH_* pattern: a zero-key OSS install must still boot)."""
    if settings.EMBEDDINGS_PROVIDER.lower() == "openai":
        return openai_embedder
    return local_embedder
```
```python
# apps/memory-api/app/deps.py:_build_provider() — the "native" branch, ONE line changes
if backend == "native":
    from xbrain_memory.providers.native_provider import NativeProvider
    from app.embedders import get_embedder          # was: openai_embedder
    ...
    return NativeProvider(..., embedder=get_embedder(), ...)
```

### Anti-Patterns to Avoid

- **Duplicating the model per uvicorn worker:** `apps/memory-api/Dockerfile:30` runs `--workers 2`. Loading the model in FastAPI's `lifespan` naively means **both** worker processes independently execute it — empirically confirmed in this session (two `multiprocessing.spawn` processes, ~272 MB RSS each, no copy-on-write). Do not load the model directly in memory-api's lifespan without addressing this (see §6).
- **A fourth hardcoded `1536`:** `apps/memory-api/app/routes/admin_wipe.py:242` recreates the Qdrant collection with `VectorParams(size=1536, ...)` on a superadmin "wipe database" action. If this site is missed, a provider=local install that ever gets wiped silently regresses back to the exact D-19-03 landmine.
- **Silent swallow of a real startup failure:** `apps/memory-api/app/main.py:69-71` wraps `ensure_collections()` in a blanket `try/except Exception` that only logs a WARNING (`qdrant_setup_skipped`) and continues. A dimension-mismatch error from `ensure_collections()` would be swallowed exactly like a "Qdrant is down" error is today — the container would report itself healthy while every subsequent embed silently fails.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|--------------|-----|
| ONNX text embedding inference | A custom tokenizer + onnxruntime session wrapper | `fastembed.TextEmbedding` | fastembed already handles tokenization, batching, quantized-model loading, and the HF-cache/offline mechanics; a hand-rolled wrapper would have to reimplement all of that for zero benefit |
| Blocking HF downloads at runtime | A custom "check network, skip if offline" guard around a raw `huggingface_hub.snapshot_download()` call | fastembed's own `local_files_only=True` / `HF_HUB_OFFLINE=1` support | Already built in, already tested by fastembed's own test suite; verified working end-to-end with `--network none` in this session |
| Re-embedding an existing corpus when switching provider | A new "embedding migration framework" | A plain script iterating `memory_items` (Postgres is already the content source of truth — Qdrant only stores the vector + a thin payload, verified via `native_provider.py:162-169`) and calling `provider.upsert()` again per row | The existing `upsert()` already does the right thing (writes both PG + Qdrant); no new abstraction needed, and CONTEXT.md explicitly defers large-corpus re-embed performance out of this phase |

**Key insight:** every piece of this phase that looks like it needs new infrastructure (embedding library, offline-mode handling, provider selection, dimension provisioning) already has a designed seam or an off-the-shelf mechanism in this codebase or in `fastembed` itself. The actual work is wiring, not building.

## Common Pitfalls

### Pitfall 1: Dimension mismatch is swallowed, not surfaced
**What goes wrong:** A fresh install boots fine, healthcheck passes, but every ingest silently fails to write a vector (logged only as a WARNING).
**Why it happens:** `apps/memory-api/app/main.py:69-71` catches all `ensure_collections()` exceptions and only logs; `NativeProvider.upsert()`'s self-heal (`native_provider.py:170-184`) only retries on a "collection doesn't exist" (404) error — a dimension mismatch produces a *different* Qdrant error ("Wrong input: vector dimension error") that does not match those substring checks, so it re-raises into `brain_ingest.py`'s own fail-soft `except Exception` wrapper, which again only logs a warning.
**How to avoid:** Compute `VECTOR_SIZE` from the *same* `get_embedding_dimension()` helper in `qdrant_setup.py` and `admin_wipe.py`; additionally, at `ensure_collections()` startup, if the collection already exists, fetch its real vector size (`client.get_collection(name)`) and compare against the configured provider's expected dimension — on mismatch, raise a clearly-worded, actionable error and make sure it is **not** absorbed by `main.py`'s blanket except (special-case it, or flip a module-level "degraded" flag that `/v1/healthz` reports).
**Warning signs:** Ingest returns success (fire-and-forget) but Qdrant point count never grows; `memory_search` always returns empty.

### Pitfall 2: A fourth hardcoded dimension in `admin_wipe.py`
**What goes wrong:** Superadmin "wipe database" recreates the collection at 1536 even on a provider=local install.
**Why it happens:** `apps/memory-api/app/routes/admin_wipe.py:242` has its own independent `VectorParams(size=1536, ...)` — not derived from anything, and easy to miss because CONTEXT.md's canonical-refs list only names `qdrant_setup.py`.
**How to avoid:** Route this call site through the same `get_embedding_dimension()` helper.
**Warning signs:** Embeddings work fine until someone runs the wipe-database admin action, then silently break.

### Pitfall 3: `--workers 2` duplicates the model in RAM
**What goes wrong:** Loading the model in memory-api's FastAPI lifespan costs ~540-550 MB, not the ~260-280 MB a single load would cost.
**Why it happens:** `apps/memory-api/Dockerfile:30` runs `uvicorn ... --workers 2`. Uvicorn's multi-worker mode spawns independent OS processes (`multiprocessing.spawn`, confirmed via `/proc/<pid>/status` in this session — not fork-based, so no copy-on-write memory sharing); each process runs its own copy of the FastAPI `lifespan` startup, so each independently loads the model.
**How to avoid:** See §6 — either a sidecar (single process regardless of memory-api's worker count) or reduce OSS-light's worker count to 1 (configurable, not a blanket change to the Dockerfile default, since the SaaS profile's Phase 18 comment indicates `--workers 2` was needed for concurrent-request headroom).
**Warning signs:** `docker stats` on memory-api shows RSS roughly double what a single-process local test predicted.

### Pitfall 4: `.embed()` is synchronous — blocks the event loop if called directly
**What goes wrong:** Calling `model.embed([text])` directly inside an `async def` embedder blocks that worker's entire event loop for the duration of ONNX inference (measured ~0.02-0.07s for a short sentence on arm64 — small per-call, but it adds up under concurrent chat traffic and grows with document length).
**Why it happens:** `fastembed.TextEmbedding.embed()` is a plain Python generator, not a coroutine.
**How to avoid:** Wrap it in `asyncio.to_thread(...)` (see Pattern 2's code example).
**Warning signs:** Other concurrent requests on the same worker (auth, chat, MCP calls) show latency spikes correlated with ingest/search volume.

### Pitfall 5: `MEMORY_BACKEND=mem0` is a separate, untouched path
**What goes wrong:** Assuming Phase 19's provider selector also fixes the `mem0` backend.
**Why it happens:** `packages/memory-models/xbrain_memory/providers/mem0_provider.py` constructs mem0 with its own `openai_api_key=settings.OPENAI_API_KEY` and relies on mem0's internal (OpenAI-based) embedding — a completely separate code path from `NativeProvider`.
**How to avoid:** Confirm scope explicitly with the planner: OSS-light's compose default is `MEMORY_BACKEND: ${MEMORY_BACKEND:-native}` (`infrastructure/docker-compose.yml:121`), so `mem0` is out of scope for EMBED-01. Don't spend plan budget wiring `local_embedder` into `Mem0Provider`.
**Warning signs:** N/A — this is a scoping trap, not a runtime symptom.

## Code Examples

### Provider-derived vector size (single source of truth)
```python
# NEW: apps/memory-api/app/embedders.py (or a small dedicated module)
# Source: derived from this session's read of qdrant_setup.py:16 and admin_wipe.py:242,
# both of which hardcode 1536 independently today.
_EMBEDDING_DIMENSIONS = {"local": 384, "openai": 1536}

def get_embedding_dimension() -> int:
    return _EMBEDDING_DIMENSIONS.get(settings.EMBEDDINGS_PROVIDER.lower(), 384)
```
```python
# apps/memory-api/app/qdrant_setup.py — CHANGE
from app.embedders import get_embedding_dimension
VECTOR_SIZE = get_embedding_dimension()   # was: VECTOR_SIZE = 1536 (hardcoded)
```

### Real offline proof (the exact command that validated D-19-01 in this session)
```bash
# Source: this session — built xbrain-embed-test from a Dockerfile that bakes
# BAAI/bge-small-en-v1.5 at build time, then ran it fully network-isolated:
docker run --rm --network none xbrain-embed-test
# -> dim: 384 / load_s: 0.533 / peak_rss_kb_self: 256092   (succeeded, zero network)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|-------------------|---------------|--------|
| `app/embedders.py:13-14` hard-raises `RuntimeError("OPENAI_API_KEY not configured for embeddings")` if no key set | `get_embedder()` selects `local_embedder` (fastembed, keyless) by default; `openai_embedder` remains available via `EMBEDDINGS_PROVIDER=openai` | Phase 19 (this phase) | Unblocks Phase 16 SC#3 (a zero-key OSS install can ingest AND retrieve); restores the "one key: Anthropic OR OpenAI OR Grok" promise, since embeddings no longer force OpenAI specifically |
| `qdrant_setup.py`'s `VECTOR_SIZE` is a hardcoded literal (1536) | Derived from the configured provider via one shared helper | Phase 19 | Fresh installs with `EMBEDDINGS_PROVIDER=local` create the collection at the correct 384 dims from first boot — no post-hoc migration needed for the phase's actual target (fresh OSS-light installs) |

**Deprecated/outdated:** none — this is additive, not a replacement of a deprecated pattern.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|----------------|
| A1 | `all-MiniLM-L6-v2` generally scores lower than `bge-small-en-v1.5` on MTEB retrieval — stated in the Alternatives Considered table as the reason to prefer bge-small. The exact side-by-side MTEB retrieval numbers for both models were not independently re-verified from a single authoritative source in this session (bge-small-en-v1.5's own numbers ARE verified via its HF model card: MTEB avg 62.17 / retrieval 51.68; all-MiniLM-L6-v2's page did not surface a single aggregate MTEB average in the fetch). | Standard Stack / Alternatives Considered | LOW — even without the exact comparative number, bge-small-en-v1.5's own verified 512-token context (vs MiniLM's verified 128) and its status as fastembed's own default model are independently sufficient reasons to prefer it; the MTEB delta is a secondary, reinforcing argument, not the sole basis. |
| A2 | The amd64 RAM/timing numbers (peak RSS ~366 MB, load 3.0s, import 11.3s) were measured under QEMU emulation on an arm64 host (`docker buildx --platform linux/amd64`), not on real amd64 hardware. | §6 RAM measurement | MEDIUM — if a planner or executor cites the emulated 366 MB figure as the real amd64 production number, the actual mem_limit sizing for the prod VM could be set incorrectly (too generous or too tight). The arm64-native figure (256 MB) is the more trustworthy baseline; treat amd64 as "confirmed installable + confirmed offline-capable" only, and re-measure once on a real amd64 box before finalizing `mem_limit`. |

**Planning implication:** neither assumption blocks the phase from being planned — A1 doesn't change the model recommendation, and A2 only affects the precision of one mem_limit number, which should be a Wave-0-style "measure once on the real target" verification step regardless.

## Open Questions

1. **Sidecar vs single-worker in-process — final call belongs to the planner, but the arithmetic favors sidecar.**
   - What we know: OSS-light's 10 untagged-core services already reserve ~2944 MB of `mem_limit` ceiling on a 4 GB e2-medium target (VERIFIED by summing `mem_limit:` lines in `infrastructure/docker-compose.yml` for `nginx`, `postgres`, `qdrant`, `memory-api`, `minio`, `mcp-gateway`, `mcp-scraper`, `mcp-brain`, `centrifugo`, `brain-janitor`), leaving roughly ~550 MB of headroom after OS/Docker overhead (per the CLAUDE.md Phase-1 sizing table's own ~600 MB overhead estimate). A single model load costs ~256-280 MB (measured); duplicated under `--workers 2` it costs ~540-550 MB (measured) — consuming essentially all remaining headroom.
   - What's unclear: whether the OSS-light profile can safely drop to `--workers 1` for memory-api without reintroducing the concurrent-request RSS/latency issue that justified raising `mem_limit` to 768m in Phase 18 (`infrastructure/docker-compose.yml:223-226` — that issue was about RSS hitting an *old, lower* 384m cap under concurrent auth requests, not explicitly about worker count, but the two are related).
   - Recommendation: default to a **sidecar** (new ~350-400m service, `apps/embeddings-local/`, matching the weight class of existing sidecars like `mcp-scraper`/`mcp-brain` at 128m each) as the primary plan — it fully decouples the model's memory from memory-api's worker count and is the lower-regression-risk choice. Document single-worker in-process as the cheaper fallback (+256-280m only) if the planner prefers to avoid adding an 11th untagged-core service (note: this would nudge Phase 16 SC#2's "~10 services" language to ~11 — flag for the roadmap wording, not a blocker).

2. **Exact mem_limit for whichever option is chosen.**
   - What we know: measured single-process warm/offline RSS on arm64 native = 256 MB; the same on amd64-under-QEMU = 366 MB (see Assumption A2 — likely inflated by emulation).
   - What's unclear: the real amd64 number, and how much margin to add above the bare model RSS for request concurrency inside the chosen component (sidecar or single memory-api worker).
   - Recommendation: size the sidecar (or the reduced-worker memory-api) at ~384-512m as a starting `mem_limit`, and treat "measure once against the actual e2-medium prod VM (amd64)" as a Wave-0 verification task before the phase is declared done — this is a "measure, don't guess" gap, not a design ambiguity.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|--------------|------------|---------|-----------|
| Docker daemon (for build + testcontainers) | Building the baked-model image; Validation Approach's real-Qdrant tests | Yes — was down at research session start, started successfully, confirmed `linux/aarch64` | Docker Desktop w/ buildx v0.35.0-desktop.2, BuildKit v0.31.1 | N/A — required for both dev and CI |
| `docker buildx` cross-platform emulation (amd64 on arm64 host) | Verifying both-arch installability during research/planning, NOT for building the production image (per the hard constraint: never build-and-deploy locally) | Yes — confirmed via `docker buildx ls` (`linux/amd64 (+2), linux/arm64, ...` builders present) | buildx v0.35.0-desktop.2 | N/A |
| PyPI registry access (network) | Verifying package versions/wheels for this research | Yes | — | N/A |
| HuggingFace Hub access (network, build-time only) | Baking `BAAI/bge-small-en-v1.5` into the image during `docker build` | Yes (confirmed — model downloaded successfully in ~8-11s during this session's test builds) | — | If HF is unreachable from the CI/build environment at image-build time, the model must be vendored into the repo or a build-time mirror used — not evaluated further here since D-19-01 only requires offline at *runtime*, not at *build* time |

**Missing dependencies with no fallback:** none identified.

**Missing dependencies with fallback:** none identified — the environment fully supports this phase's build and validation needs.

## Validation Approach

> `.planning/config.json` has `workflow.nyquist_validation: false`, so the full Nyquist test-framework template (Req->Test map, sampling cadence) is intentionally omitted. The phase's own CONTEXT.md explicitly invokes "the gate lesson" (real dependencies, not mocks) as a hard requirement, so the substantive test plan below is included regardless — this is what the planner should turn into Wave-0 test tasks.

### What "prove it" means for EMBED-01, concretely

| # | Check | Against | Asserts |
|---|-------|---------|---------|
| 1 | `ensure_collections()` with `EMBEDDINGS_PROVIDER=local` and no `OPENAI_API_KEY` set | Real Qdrant (testcontainers `QdrantContainer`, mirroring the existing `pg_url` fixture pattern in `apps/memory-api/tests/conftest.py:67-123`) | The created collection's `config.params.vectors.size == 384`, not 1536 — proves the dimension-derivation fix actually took effect, not just that boot didn't crash |
| 2 | Ingest 3-4 short documents with clearly distinct topics (e.g. "team offsite in Lisbon", "Q3 budget numbers", "deploying memory-api") via `NativeProvider.upsert()` | Real Postgres + real Qdrant (both testcontainers), no OpenAI key | Each write succeeds; Qdrant point count == number of documents |
| 3 | `provider.search("where are we going for the team trip?")` | Same real Qdrant instance from check 2 | The Lisbon-offsite item ranks **first** by score — a genuine semantic-relevance assertion, not "a row exists." This is the literal "gate lesson" check CONTEXT.md calls out: a mocked embedder would return a fixed/fake vector and could not fail this assertion even if the real integration were broken. |
| 4 | Same query path through `GET /v1/memory/search` (the route mcp-brain's `memory_search` tool proxies to) | The `client` fixture already in `conftest.py` (httpx ASGI transport against the real FastAPI app) | The HTTP-facing endpoint returns the same top hit as check 3 — proves the full request path (not just the provider object in isolation) works end to end |
| 5 | Run the baked-model image with `docker run --network none` (exact pattern proven working in this research session) | A throwaway/CI build of the real (or a representative) Dockerfile | The container produces a 384-dim vector with **zero network access** — the authoritative proof of D-19-01, since a pytest-level mock or `pytest-socket` check only proves fastembed's *own* code path avoids the network, not that nothing else in the image does |
| 6 | `EMBEDDINGS_PROVIDER=openai` + `OPENAI_API_KEY` set, OpenAI HTTP mocked via `respx` (existing precedent: `apps/memory-api/tests/test_phase12_org_membership.py` and others already use `respx` for HTTP mocking) | Unit-level, no testcontainers needed | `get_embedder()` returns `openai_embedder`; the existing OpenAI path is unchanged — regression coverage for "OpenAI stays selectable" (D-19-02) |
| 7 | Fresh install boot with `EMBEDDINGS_PROVIDER` unset/garbage value | Unit-level, `app.config.settings` reload | Falls back to `local`, does NOT raise — proves the "zero-key install must boot" invariant holds even under operator typos, matching the Phase 18 `LOCAL_AUTH_*` precedent of deliberately no strict `field_validator` |

### Wave 0 Gaps
- [ ] `apps/memory-api/tests/conftest.py` — add a `qdrant_url` session fixture mirroring the existing `pg_url` fixture (testcontainers `QdrantContainer`, skip if Docker unavailable).
- [ ] `apps/memory-api/tests/test_local_embeddings.py` (new) — covers checks 1-4 above.
- [ ] A CI/Docker-level smoke step (not pytest) reproducing check 5 — the `docker run --network none <image>` pattern already validated manually in this session.
- [ ] `apps/memory-api/pyproject.toml` dev deps — add the `qdrant` extra to the existing `testcontainers[postgres]` pin.

## Security Domain

`security_enforcement` is not set in `.planning/config.json` (absent = enabled). This phase adds no new authentication, authorization, or user-facing input surface — `EMBED-01` is a backend engine swap behind the existing, already-authorized `memory_search`/ingest routes. The relevant ASVS-adjacent considerations are narrow:

| Concern | Applies | Standard Control |
|---------|---------|-------------------|
| V6 Cryptography / supply chain | Yes, narrowly | The model weights baked into the image come from a public HF repo (`BAAI/bge-small-en-v1.5`, MIT license) at `docker build` time — no runtime trust decision is made, and no user-supplied input ever selects which model/weights to load (`EMBEDDINGS_PROVIDER` is an operator-set env var, not user input). No hand-rolled crypto is introduced. |
| V5 Input Validation | Indirect | Text passed to `local_embedder()` is the same content already validated by the existing tagging-contract write path (`MEM-01`) before it ever reaches the embedder — no new validation surface. |
| SSRF / outbound network | Mitigated by design | D-19-01 itself is the mitigation: baking the model at build time + `HF_HUB_OFFLINE=1` at runtime means the embedder makes zero outbound calls in production, removing what would otherwise be a new outbound-network surface per ingest/search call (unlike the existing `openai_embedder`, which already makes one HTTP call per embed — unchanged, opt-in only). |

No new STRIDE-relevant threat pattern was identified beyond what already exists for the `openai_embedder` path (which this phase does not modify).

## Sources

### Primary (HIGH confidence)
- PyPI registry JSON (`pypi.org/pypi/<pkg>/json`) — fetched live in this session for `fastembed` (0.8.0), `onnxruntime` (1.27.0, wheel filenames for arm64+amd64/cp312), `sentence-transformers` (5.6.0, `requires_dist`), `torch` (2.13.0, cp312 wheel sizes), `huggingface-hub` (1.24.0), `qdrant-client` (1.18.0), `testcontainers` (4.14.2, `provides_extra` including `qdrant`).
- Direct file reads (this session): `apps/memory-api/app/embedders.py`, `apps/memory-api/app/qdrant_setup.py`, `apps/memory-api/app/deps.py:415-457`, `apps/memory-api/app/main.py:1-94`, `apps/memory-api/app/routes/admin_wipe.py:195-254`, `apps/memory-api/app/repos/brain_metrics.py:100-140`, `apps/memory-api/Dockerfile`, `apps/memory-api/pyproject.toml`, `apps/memory-api/tests/conftest.py`, `packages/memory-models/xbrain_memory/provider.py`, `packages/memory-models/xbrain_memory/providers/native_provider.py`, `packages/memory-models/xbrain_memory/providers/native_stub.py`, `packages/memory-models/xbrain_memory/providers/mem0_provider.py`, `apps/mcp-brain/app/main.py:180-224`, `apps/mcp-brain/app/memory_client.py:1-75`, `apps/brain-janitor/app/config.py`, `infrastructure/docker-compose.yml` (mem_limit + profiles across all services), `.planning/features/open-core-edition-design.md` (Q3/Q5/Q6 locked decisions).
- Empirical Docker measurements (this session, on the actual arm64 dev host + one QEMU amd64 cross-build): fastembed cold-load (with download), warm-load (cached), and fully-offline (`--network none`) runs; two-worker uvicorn duplication test via `/proc/<pid>/status`; image size measurement (169.2 MB delta for base+deps+baked model).
- huggingface.co model cards for `BAAI/bge-small-en-v1.5` and `sentence-transformers/all-MiniLM-L6-v2` (fetched via WebFetch).
- fastembed source + docs, fetched via Context7 CLI fallback (`ctx7 docs /qdrant/fastembed`) — `model_management.py` (offline mechanics), `README.md` / `Supported_Models.ipynb` (model catalog, sizes).

### Secondary (MEDIUM confidence)
- `qdrant.github.io/fastembed/examples/Supported_Models/` (WebFetch) — model size/dimension table cross-referencing the Context7 snippets.
- General web search on MTEB comparative positioning of `all-MiniLM-L6-v2` vs retrieval-focused models — did not yield a single authoritative side-by-side number against `bge-small-en-v1.5` (see Assumption A1).

### Tertiary (LOW confidence)
- None relied upon for load-bearing claims.

## Metadata

**Confidence breakdown:**
- Standard stack (library/model pick, both-arch wheel proof): HIGH — verified against live PyPI registry JSON and empirically run in Docker in this session.
- Offline/build-bake mechanics (D-19-01): HIGH — not just documented, actually built and proven with `docker run --network none`.
- Dimension-mismatch landmine and fix (D-19-03): HIGH — all three hardcoded call sites located by direct file read with line numbers; the "split brain" risk is grounded in a real, already-documented Phase-11 production incident in this same codebase.
- Provider-selection seam (D-19-02): HIGH — the exact injection point (`deps.py:_build_provider()`) and its current behavior were read directly, not inferred.
- Write/read embedder parity (§5): HIGH — traced the full call graph from mcp-brain's HTTP client through to `NativeProvider._embedder`, confirming a single shared instance.
- RAM footprint + workers=2 duplication (§6): HIGH for arm64-native and the duplication mechanism itself (both measured); MEDIUM for the amd64 absolute number (measured only under QEMU emulation — flagged in Assumptions Log).
- Sidecar-vs-in-process recommendation: MEDIUM — grounded in real measured numbers and a real budget constraint, but the final call trades off against an operational concern (worker-count regression risk) that wasn't itself measured, only cited from an existing code comment.

**Research date:** 2026-07-18
**Valid until:** ~30 days for the architectural/integration findings (dimension landmine, provider seam, RAM duplication mechanism — these are properties of this codebase, not fast-moving); ~14 days for the specific package versions cited (fastembed/onnxruntime move at a moderate pace) — re-verify version pins at planning/execution time if this research is consumed more than 2 weeks after 2026-07-18.
