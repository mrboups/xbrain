---
phase: 19-local-embeddings
reviewed: 2026-07-18T08:12:06Z
depth: deep
files_reviewed: 15
files_reviewed_list:
  - apps/memory-api/app/embedders.py
  - apps/memory-api/app/config.py
  - apps/memory-api/app/deps.py
  - apps/memory-api/app/main.py
  - apps/memory-api/app/qdrant_setup.py
  - apps/memory-api/app/routes/admin_wipe.py
  - apps/memory-api/Dockerfile
  - infrastructure/docker-compose.yml
  - apps/memory-api/tests/conftest.py
  - apps/memory-api/tests/test_embedders_provider.py
  - apps/memory-api/tests/test_local_embeddings.py
  - apps/memory-api/pyproject.toml
  - .env.example
  - apps/memory-api/.env.example
  - docs/embeddings.md
findings:
  critical: 1
  warning: 2
  info: 2
  total: 5
status: issues_found
---

# Phase 19: Code Review Report

**Reviewed:** 2026-07-18T08:12:06Z
**Depth:** deep
**Files Reviewed:** 15
**Status:** issues_found

## Summary

The provider-selection seam is sound: `get_embedder()` / `get_embedding_dimension()` in
`embedders.py` fall back to `local`/384 for any garbage or unset `EMBEDDINGS_PROVIDER`
(no `field_validator`, matching the documented LOCAL_AUTH fail-safe precedent), and both
`qdrant_setup.py` and `admin_wipe.py` now derive the Qdrant vector size from that single
function — no stray hardcoded `1536` remains anywhere in `apps/memory-api/app/`.
`main.py`'s `except EmbeddingDimensionMismatch: raise` is correctly ordered ahead of the
blanket `except Exception: log.warning(...)`, so a real dimension conflict is genuinely
boot-fatal rather than swallowed. The lazy module-level model singleton in `embedders.py`
has no TOCTOU race under asyncio (the check-then-set has no `await` point in between), the
Dockerfile bakes the model before `USER xbrain` with `--chown` applied and sets
`HF_HUB_OFFLINE=1`, and `tests/test_local_embeddings.py` honors the "gate lesson" — the
semantic-ranking assertion runs against a real fastembed model + real Qdrant with zero
mock on that path.

One serious gap survived all of this: the phase's headline RAM-safety mechanism —
`UVICORN_WORKERS`, introduced specifically so an OSS-light install can run a single
worker and avoid loading the local embedding model twice — has **zero effect** in the
actual deployment path, because `infrastructure/docker-compose.yml`'s pre-existing
`command:` override for the `memory-api` service hardcodes `--workers 2` and never
reads the env var. Every `.env.example`, Dockerfile comment, and `docs/embeddings.md`
paragraph promises that setting `UVICORN_WORKERS=1` makes the model load once; in the
one file that actually starts the container, it doesn't matter what the operator sets —
compose always launches 2 workers, which is exactly the RAM-doubling scenario the phase's
own `mem_limit: 896m` sizing assumes cannot happen on an OSS-light install.

## Critical Issues

### CR-01: `UVICORN_WORKERS` is fully inert in the real deployment path — `docker-compose.yml` hardcodes `--workers 2`, defeating the phase's OOM-prevention mechanism

**File:** `infrastructure/docker-compose.yml:259-261`

**Issue:** The Dockerfile's `CMD` is parameterized specifically so this phase's new
`UVICORN_WORKERS` knob controls worker count at runtime:

```dockerfile
# apps/memory-api/Dockerfile:46-47
ENV UVICORN_WORKERS=2
CMD exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS}
```

But `docker-compose.yml`'s `command:` field on the `memory-api` service — unchanged by
this diff, sitting below the newly-added `UVICORN_WORKERS: ${UVICORN_WORKERS:-2}`
environment passthrough — completely replaces the container's command and hardcodes the
literal `2`, never referencing the shell variable at all:

```yaml
    command: >
      sh -c "python -m alembic upgrade head &&
             python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2"
```

Docker Compose's `command:` overrides the image's `CMD` entirely — the Dockerfile's
`${UVICORN_WORKERS}`-driven `CMD` never executes when the stack is brought up via this
compose file, which is the only compose file in the repo and the documented deployment
mechanism (`CLAUDE.md`: "GCP VM Ubuntu 24.04 ... via Docker Compose"). This means:

- `.env.example:107` sets `UVICORN_WORKERS=1` as the shipped OSS-light default, with the
  comment "OSS-light: 1 worker (loads the local model once)".
- `apps/memory-api/.env.example:35` repeats the same default and rationale.
- `docker-compose.yml:131-134`'s own inline comment says "OSS-light sets 1 so the local
  model is loaded ONCE ... under --workers 2 the model is duplicated (~2x RSS,
  spawn-based, no copy-on-write) and can OOM e2-medium."
- `docs/embeddings.md:62-63` states "`UVICORN_WORKERS` is a plain env knob ...; the
  Dockerfile `CMD` reads it at runtime, so changing it is a `.env` edit, never a rebuild."
- `docker-compose.yml:238-243`'s `mem_limit: 896m` is explicitly sized for a **single**
  lazy model load ("Bumped 768m → 896m to give that single-worker model load headroom").

None of this is true once the stack is actually started via `docker compose up`: the
container always runs 2 uvicorn worker **processes** (separate processes, not
copy-on-write threads), and with the default `EMBEDDINGS_PROVIDER=local`, each worker
independently lazy-loads its own ~256–366 MB fastembed model on its first embed request.
That is ~2x the RSS the `896m` cap was sized for, on top of the pre-existing signin-path
base that already justified `768m` before this phase. An operator who follows the
shipped `.env.example` exactly (`EMBEDDINGS_PROVIDER=local`, `UVICORN_WORKERS=1`) will,
in practice, still get 2 workers and is a strong OOM-kill candidate on the e2-medium
budget this phase exists to fit (Locked Decision Q3 / requirement EMBED-01).

This is a genuine implementation gap, not a documentation gap: 19-VERIFICATION.md's
"WIRED" check for this link only confirmed the three env vars are *present* in the
compose file with safe defaults — it never exercised the actual container command, so
the check passed without ever noticing the pre-existing `command:` override downstream
silently nullifies all three.

**Fix:** Either drop the `command:` override so the Dockerfile's parameterized `CMD`
runs, or make the override itself read the env var:

```yaml
    command: >
      sh -c "python -m alembic upgrade head &&
             exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS}"
```

(Keep `exec` on the final process for the same PID-1/signal-forwarding reason the
Dockerfile comment already documents.) After the fix, add a real assertion —
`docker compose config` / a boot-time smoke test that greps the running process list for
`--workers 1` when `UVICORN_WORKERS=1` is set — so this class of "knob exists but isn't
wired to the thing that starts the process" gap cannot silently regress again.

## Warnings

### WR-01: First-ever local embed call blocks the event loop for the full model-load duration — no startup warm-up, and construction isn't offloaded like `.embed()` is

**File:** `apps/memory-api/app/embedders.py:36-60`, `apps/memory-api/app/main.py:65-99`

**Issue:** `local_embedder()` explicitly offloads `.embed()` to a thread with a comment
citing "RESEARCH Pitfall 4" for exactly this reason — fastembed's inference call is
synchronous and would otherwise block the event loop:

```python
async def local_embedder(text: str) -> list[float]:
    model = _get_local_model()
    # fastembed's .embed() is a synchronous generator — offload to a thread so it
    # doesn't block the event loop (RESEARCH Pitfall 4).
    vecs = await asyncio.to_thread(lambda: list(model.embed([text])))
    return vecs[0].tolist()
```

But `_get_local_model()` — called synchronously, one line above, inside the same
coroutine — constructs `TextEmbedding(...)` directly on the event loop thread, with no
`asyncio.to_thread` offload:

```python
def _get_local_model():
    global _local_model
    if _local_model is None:
        from fastembed import TextEmbedding
        _local_model = TextEmbedding(
            model_name=settings.LOCAL_EMBEDDING_MODEL,
            cache_dir=settings.EMBEDDING_CACHE_DIR,
        )
    return _local_model
```

`TextEmbedding(...)` reads the baked ONNX weights off disk and initializes an
onnxruntime session — the same category of blocking, CPU/IO-bound work the `.embed()`
comment is explicitly guarding against, just one call earlier. `main.py`'s `lifespan()`
never calls `local_embedder`/`get_embedder()`/`_get_local_model()` before `yield`, so
this cost is not paid at startup — it lands on whichever live HTTP request happens to be
the first to trigger an embed after boot, stalling every other coroutine on that worker
(other API calls, health probes handled by the same event loop) for the load duration.

**Fix:** Either offload construction the same way `.embed()` already is —

```python
async def _get_local_model_async():
    global _local_model
    if _local_model is None:
        def _load():
            from fastembed import TextEmbedding
            return TextEmbedding(model_name=settings.LOCAL_EMBEDDING_MODEL, cache_dir=settings.EMBEDDING_CACHE_DIR)
        _local_model = await asyncio.to_thread(_load)
    return _local_model
```

— or (better for latency-sensitive first requests) pay the cost once during
`lifespan()`, after `ensure_collections()`, guarded by the active provider:

```python
if get_embedder() is local_embedder:
    await local_embedder("warmup")  # materialize the model before serving traffic
```

### WR-02: `get_embedding_dimension()` is keyed on `EMBEDDINGS_PROVIDER`, not on the actual `LOCAL_EMBEDDING_MODEL` — changing the model silently desyncs the dimension the boot check trusts

**File:** `apps/memory-api/app/embedders.py:74-79`

**Issue:**

```python
_EMBEDDING_DIMENSIONS = {"local": 384, "openai": 1536}

def get_embedding_dimension() -> int:
    return _EMBEDDING_DIMENSIONS.get(settings.EMBEDDINGS_PROVIDER.lower(), 384)
```

This hardcodes "local always means 384," which is only true for the one model actually
baked into the shipped image (`BAAI/bge-small-en-v1.5`). `LOCAL_EMBEDDING_MODEL` is a
first-class, documented env var (`.env.example:106`, `apps/memory-api/.env.example:33`)
— an operator who points it at any other fastembed model with a different output
dimension (e.g. a 768-dim model) gets no boot-time signal at all: `ensure_collections()`
will happily create (or accept an existing) collection at 384, because
`get_embedding_dimension()` never looks at `LOCAL_EMBEDDING_MODEL`. The actual
`local_embedder()` output will then be 768-dim, and every subsequent `upsert()` will fail
at Qdrant's vector-size validation on the request path — not at boot, and not with the
loud, actionable `EmbeddingDimensionMismatch` this phase built specifically to make
provider-dimension conflicts fail loud (D-19-03). This is the same class of failure the
phase closes for the local↔openai switch, reopened one level down for the
local-model↔local-model switch.

**Fix:** Either derive the dimension from a real embed instead of a static provider-name
table (e.g. `len(await local_embedder("dim-probe"))` during the model's first load, cached
alongside the singleton), or key `_EMBEDDING_DIMENSIONS` off `LOCAL_EMBEDDING_MODEL`
itself with an explicit `KeyError`/actionable-error path for unrecognized model names,
so an operator who changes the model gets the same fail-loud boot check the
provider-switch path already gets, rather than a silent per-request Qdrant rejection.

## Info

### IN-01: Baked model has no pinned revision — a rebuild months apart can silently fetch different weights

**File:** `apps/memory-api/Dockerfile:16-17`

**Issue:**

```dockerfile
RUN PYTHONPATH=/build/deps python -c "from fastembed import TextEmbedding; \
    TextEmbedding(model_name='BAAI/bge-small-en-v1.5', cache_dir='/build/model_cache')"
```

No revision/commit hash is pinned for the HuggingFace model resolution. The review focus
explicitly calls out "weights integrity" for this bake step: today's build reproducibly
gets today's snapshot of `BAAI/bge-small-en-v1.5`, but re-running the identical
Dockerfile in six months (a routine base-image security rebuild, for instance) could
silently resolve a different snapshot if the upstream repo is ever updated, changing
embedding output for a corpus that was ingested under the old weights with no signal
that anything changed. This is a supply-chain hardening gap, not a functional bug today.

**Fix:** Pin an explicit revision where the fastembed/huggingface_hub API supports it
(or vendor a hash-verified copy of the weights into the build context), so the bake step
is byte-reproducible across rebuilds, not just "whatever's current on HF Hub today."

### IN-02: `local_model_ready` test fixture's skip-guard exception tuple may not cover every huggingface_hub network-failure type

**File:** `apps/memory-api/tests/test_local_embeddings.py:118-135`

**Issue:** The fixture that materializes the real model for the integration tests skips
cleanly only on `(ConnectionError, OSError)`:

```python
try:
    vec = await local_embedder("warmup: materialize the real fastembed model")
except (ConnectionError, OSError) as e:
    pytest.skip(f"local model not cached and no network to download it: {e}")
```

`requests`/`huggingface_hub` can raise other exception shapes on a restricted network
(e.g. HTTP-level errors that aren't plain connection/OS errors) that would not be caught
here, turning what should be a clean skip in a no-network CI sandbox into a hard test
failure. Low likelihood in this repo's own CI (which appears to have network access, per
19-VERIFICATION.md's live run), but worth broadening for robustness in more locked-down
environments.

**Fix:** Widen the guard to the base `huggingface_hub`/`requests` exception classes (or
catch bare `Exception` here specifically, since this fixture's only job is "skip if the
model can't be materialized" and the real assertions live in the test bodies, not this
fixture).

---

_Reviewed: 2026-07-18T08:12:06Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_
