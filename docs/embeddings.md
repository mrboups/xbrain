# Embeddings

xbrain embeds every ingested memory item into a vector so that `memory_search`
(and the `GET /v1/memory/search` route it proxies) can retrieve it by semantic
similarity. This document covers how the embedding provider is selected, the
zero-network guarantee, the RAM/worker tradeoff, and the vector-dimension
migration story.

## Providers

The provider is chosen by a single env var, `EMBEDDINGS_PROVIDER`
(`apps/memory-api/app/config.py`). It is read by `get_embedder()` /
`get_embedding_dimension()` in `apps/memory-api/app/embedders.py`.

| `EMBEDDINGS_PROVIDER` | Model | Dimension | Key required | Network at inference |
|-----------------------|-------|-----------|--------------|----------------------|
| `local` (default)     | `BAAI/bge-small-en-v1.5` (fastembed / ONNX) | 384 | No | None (in-container) |
| `openai`              | `text-embedding-3-small` | 1536 | `OPENAI_API_KEY` | One HTTPS call per embed |

The default is `local`: a fresh install ingests and semantically retrieves memory
with **no embeddings API key at all**. This is what honors the "one key —
Anthropic OR OpenAI OR Grok" promise: embeddings no longer force an OpenAI key.
A garbage or unset `EMBEDDINGS_PROVIDER` value falls back to `local` (there is
deliberately no strict validator — a zero-key install must always boot).

## Offline guarantee (local provider)

The local provider makes **zero outbound network calls at inference time**. This
is enforced at two layers in `apps/memory-api/Dockerfile`:

1. **Weights are baked at build time.** While the network is available during
   `docker build`, the builder stage downloads `BAAI/bge-small-en-v1.5` into
   `/build/model_cache` and copies it into the runtime image at
   `/app/model_cache` (which equals `settings.EMBEDDING_CACHE_DIR`).
2. **`HF_HUB_OFFLINE=1` at runtime.** The runtime stage sets `HF_HUB_OFFLINE=1`,
   which forces fastembed / huggingface-hub into cache-only mode — it never
   reaches out to HuggingFace when the model is loaded or used.

This was proven end to end in Plan 19-02 by running the built image with
`docker run --network none` and getting a real 384-dim vector back with the
network fully cut off. If you rebuild the image in an environment where
HuggingFace is unreachable at **build** time, vendor the weights or use a build
mirror — offline is only required at runtime, not at build.

## RAM and worker count

The local model loads lazily, only the first time `EMBEDDINGS_PROVIDER=local`
actually triggers an embed, and stays resident as a module-level singleton
(measured ≈256 MB RSS on arm64, ≈366 MB under amd64 QEMU emulation).

Because uvicorn's `--workers N` spawns independent processes (no copy-on-write),
each worker would load its **own** copy of the model. To fit the OSS-light
e2-medium (4 GB) budget:

- **OSS-light:** set `UVICORN_WORKERS=1` so the model loads **once**. The
  `memory-api` container's `mem_limit` is `896m` to give that single lazy load
  headroom on top of the auth base.
- **SaaS / hosted:** keep `UVICORN_WORKERS=2` **and** `EMBEDDINGS_PROVIDER=openai`
  — with the OpenAI path selected the in-process model never loads, so the extra
  worker costs no model RAM.

`UVICORN_WORKERS` is a plain env knob (see `.env.example`); the Dockerfile `CMD`
reads it at runtime, so changing it is a `.env` edit, never a rebuild.

## Dimension handling and migration (D-19-03)

Qdrant fixes a collection's vector size at creation time and cannot mix
dimensions inside one collection. The local model is 384-dim; OpenAI is 1536-dim.
`apps/memory-api/app/embedders.py` exposes `get_embedding_dimension()` as the
single source of truth, and `qdrant_setup.py` + `routes/admin_wipe.py` both
derive the Qdrant vector size from it (no more hardcoded `1536`).

### (a) Fresh install — nothing to do

On a fresh install the `messages` collection is created automatically at the
active provider's dimension (384 for `local`, 1536 for `openai`). This is the
OSS-light target path and needs no manual step.

### (b) Switching an existing install to a different dimension

If an install already has 1536-dim OpenAI vectors and you switch
`EMBEDDINGS_PROVIDER` to `local` (384-dim), the app does **not** silently write
mismatched vectors. At boot `ensure_collections()` compares the existing
collection's real dimension against the configured provider's expected dimension
and **fails loud** with `EmbeddingDimensionMismatch` rather than corrupting data.

To migrate, pick one of:

1. **Wipe + recreate (simplest).** Use the superadmin wipe-database action (which
   recreates the `messages` collection at the new provider's dimension), then
   re-ingest. Appropriate when the vectors are disposable.
2. **Re-embed.** Run a plain script that iterates the `memory_items` rows in
   Postgres (the content source of truth — Qdrant only stores the vector plus a
   thin payload) and calls `provider.upsert(...)` again for each row. Each item is
   re-embedded at the new dimension and rewritten to a collection created at that
   dimension. No new framework is needed; `upsert()` already writes both Postgres
   and Qdrant correctly.

> Large-corpus re-embed **performance** (batching, throughput, downtime) is
> explicitly out of scope for this phase. A fresh OSS-light install has nothing
> to re-embed, and the fail-loud boot check guarantees the mismatch is surfaced
> rather than silently broken.

## Selecting OpenAI

To use OpenAI embeddings instead of the local model — no code change:

```env
EMBEDDINGS_PROVIDER=openai
OPENAI_API_KEY=sk-...
```

Restart `memory-api`. `get_embedder()` returns the OpenAI path unchanged, the
collection dimension becomes 1536, and (on a fresh install) the collection is
created at 1536. On an existing 384-dim install, follow the migration path in
section (b) above.
