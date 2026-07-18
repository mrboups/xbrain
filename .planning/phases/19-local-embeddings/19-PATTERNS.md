# Phase 19: Local Embeddings (OSS default) - Pattern Map

**Mapped:** 2026-07-18
**Files analyzed:** 9 (7 modify, 2 new-or-modify)
**Analogs found:** 9 / 9 (all files have an existing in-repo analog — this phase is pure wiring into an existing seam, per RESEARCH.md's own conclusion)

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `apps/memory-api/app/embedders.py` (MODIFY: add `local_embedder`, `get_embedder()`, `get_embedding_dimension()`) | service (provider factory) | transform (text -> vector) | itself (`openai_embedder` in the same file) | exact |
| `apps/memory-api/app/config.py` (MODIFY: add `EMBEDDINGS_PROVIDER`, `LOCAL_EMBEDDING_MODEL`) | config | request-response (env -> settings) | itself — the `LOCAL_AUTH_*` block (lines 245-252) and the `EDITION` field+validator (lines 203-226) | exact |
| `apps/memory-api/app/qdrant_setup.py` (MODIFY: `VECTOR_SIZE` -> derived) | service (startup provisioning) | CRUD (idempotent collection create) | itself — this is the file being changed; `packages/memory-models/.../native_provider.py:_ensure_collection` is the closest sibling analog for the "derive size, create collection, index payload fields" shape | exact |
| `apps/memory-api/app/routes/admin_wipe.py` (MODIFY: `_wipe_qdrant_full` line 242 `size=1536` -> derived) | route (admin action) | CRUD (destructive recreate) | `apps/memory-api/app/qdrant_setup.py` (same collection-create shape, same payload indexes) | exact |
| `apps/memory-api/app/deps.py` (MODIFY: `_build_provider()` "native" branch, 1 import swap) | service (DI / singleton factory) | request-response (build-once singleton) | itself — the `mem0` branch of the same function is the sibling pattern for "construct provider with an embedder-shaped dependency" | exact |
| `apps/memory-api/app/main.py` (MODIFY, if model warm-up is added to `lifespan`) | provider (app lifespan) | event-driven (startup/shutdown hooks) | `apps/memory-api/app/neo4j_client.py` (`init_driver`/`reconnect_loop`, called from `lifespan`) | role-match (graceful-degrade singleton loader, closest existing "load a heavy external resource at startup, never crash boot" pattern) |
| `apps/memory-api/pyproject.toml` (MODIFY: add `fastembed`, dev `testcontainers[postgres,qdrant]`) | config (deps manifest) | batch (dependency resolution) | itself — existing lazy-imported optional deps (`neo4j`, `mem0ai`, `boto3`, `minio`) | exact |
| `apps/memory-api/tests/conftest.py` (MODIFY: add `qdrant_url` session fixture) | test (fixture) | CRUD (testcontainers lifecycle) | itself — `pg_url` fixture (lines 67-123) | exact |
| `apps/memory-api/tests/test_local_embeddings.py` (NEW) | test | request-response / CRUD (integration) | `apps/memory-api/tests/conftest.py` fixtures + existing `test_phase12_org_membership.py` for the `respx`-mocked OpenAI-path unit test | role-match |
| `apps/embeddings-local/` (NEW, ONLY if planner picks the sidecar option) | service (standalone HTTP microservice) | request-response | `apps/brain-janitor/` (plain FastAPI-less Python service, repo-root build context, `-e` editable installs) for structure; `apps/mcp-scraper/Dockerfile` + its `docker-compose.yml` block for the "small sidecar, `mem_limit`, healthcheck" container pattern | role-match |

## Pattern Assignments

### `apps/memory-api/app/embedders.py` (service, transform)

**Analog:** itself — `openai_embedder` (lines 1-27), extend in place.

**Current full file** (lines 1-27):
```python
"""Embedding wrappers for backends that need explicit embedding."""

from openai import AsyncOpenAI

from app.config import settings

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        if not settings.OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY not configured for embeddings")
        _client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    return _client


async def openai_embedder(text: str) -> list[float]:
    """Embed text via OpenAI's text-embedding-3-small (1536 dims)."""
    client = _get_client()
    r = await client.embeddings.create(
        model=settings.OPENAI_EMBEDDING_MODEL,
        input=text,
    )
    return r.data[0].embedding
```

**Copy this shape exactly** — a lazy module-level singleton getter (`_get_client()` / a new `_get_local_model()`), guarded by `if X is None:`, mirrors the existing `_client` pattern. The new `local_embedder` must be `async def`, same signature `(text: str) -> list[float]`, because `NativeProvider.__init__`'s `Embedder` type alias (`packages/memory-models/xbrain_memory/providers/native_provider.py:25`) is `Callable[[str], Awaitable[list[float]]]` — both embedders must satisfy this exact shape to be interchangeable at the `deps.py` injection point.

**Selector pattern to add** (from RESEARCH.md, already grounded in this file's own style):
```python
def get_embedder():
    """Selects the configured embedder. Unknown/unset values fall back to
    `local` — never crash-loop a keyless install (mirrors the Phase 18
    LOCAL_AUTH_* pattern: a zero-key OSS install must still boot)."""
    if settings.EMBEDDINGS_PROVIDER.lower() == "openai":
        return openai_embedder
    return local_embedder
```
This fail-safe-default-on-typo behavior is the SAME contract as `config.py`'s `LOCAL_AUTH_*` block (deliberately no `field_validator`) — reuse that reasoning verbatim in the docstring/comment, don't invent new wording.

**Blocking-call pitfall (must copy this exact wrapping):** `fastembed.TextEmbedding.embed()` is a synchronous generator — it must be wrapped in `asyncio.to_thread(...)`, or it blocks the event loop for the duration of ONNX inference. There is no existing in-repo analog for this specific pitfall (openai_embedder is naturally async via `AsyncOpenAI`), so follow RESEARCH.md's proven snippet:
```python
async def local_embedder(text: str) -> list[float]:
    model = _get_local_model()
    vecs = await asyncio.to_thread(lambda: list(model.embed([text])))
    return vecs[0].tolist()
```

**Dimension helper — single source of truth (add to this file, RESEARCH.md §Code Examples):**
```python
_EMBEDDING_DIMENSIONS = {"local": 384, "openai": 1536}

def get_embedding_dimension() -> int:
    return _EMBEDDING_DIMENSIONS.get(settings.EMBEDDINGS_PROVIDER.lower(), 384)
```

---

### `apps/memory-api/app/config.py` (config)

**Analog:** itself — the `LOCAL_AUTH_*` block (Phase 18) is the exact precedent for "a new provider-selection knob that a zero-key OSS install must boot with."

**Pattern to copy — fail-safe default, NO `field_validator`** (lines 245-252):
```python
    # === Phase 18 (LAUTH-01/02) — native email/password auth ===
    # Safe defaults, deliberately NO field_validator: a zero-OAuth install must
    # still boot cleanly (SC#1). Per D-18-06 research OQ2 the 5/15 defaults are
    # settled and env-overridable — not an open question at runtime.
    LOCAL_AUTH_MAX_FAILED_ATTEMPTS: int = 5
    LOCAL_AUTH_LOCKOUT_MINUTES: int = 15
    LOCAL_AUTH_RATE_LIMIT: str = "10/minute"      # per-IP, in-process (NOT durable across uvicorn --workers 2 — Plan 02 rate_limit.py documents this)
    LOCAL_AUTH_MIN_PASSWORD_LENGTH: int = 10
```
Add `EMBEDDINGS_PROVIDER: str = "local"` and `LOCAL_EMBEDDING_MODEL: str = "BAAI/bge-small-en-v1.5"` in the same unvalidated style — CONTEXT.md D-19-02's default is `local`, and `get_embedder()`'s fallback-to-local-on-garbage-value behavior means this field must NOT get a `field_validator` (contrast with `EDITION`, which DOES fail loud on a bad value — see below for why that's the wrong pattern here).

**Contrast pattern — do NOT copy this one** (lines 216-226, `_validate_edition`): `EDITION` fails loud (`raise ValueError`) on an unknown value, because a typo'd `EDITION=Saas` would 404 the whole control plane. `EMBEDDINGS_PROVIDER` is the opposite case — CONTEXT.md/RESEARCH.md explicitly want silent fallback to `local` on garbage input (Validation check #7 in RESEARCH.md tests exactly this). Do not add a `@field_validator` for `EMBEDDINGS_PROVIDER`.

---

### `apps/memory-api/app/qdrant_setup.py` (service, CRUD/provisioning)

**Analog:** itself (the file being modified) + `packages/memory-models/xbrain_memory/providers/native_provider.py:_ensure_collection` (lines 58-87) as the sibling "derive size, create collection, index two payload fields" shape.

**Current full file** (already read in full, lines 1-49) — the one-line change:
```python
# BEFORE (line 16):
VECTOR_SIZE = 1536  # OpenAI text-embedding-3-small / placeholder Phase 2

# AFTER:
from app.embedders import get_embedding_dimension
VECTOR_SIZE = get_embedding_dimension()   # provider-derived (Phase 19, D-19-03)
```

**The payload-index pattern to preserve exactly** (lines 35-45) — do not touch, both new collection-create call sites must keep creating BOTH indexes:
```python
            await client.create_payload_index(
                collection_name=COLLECTION_NAME,
                field_name="team_scope",
                field_schema=PayloadSchemaType.KEYWORD,
            )
            await client.create_payload_index(
                collection_name=COLLECTION_NAME,
                field_name="truth_level",
                field_schema=PayloadSchemaType.KEYWORD,
            )
```

**Critical addition per Pitfall 1 (RESEARCH.md):** `main.py`'s `lifespan` wraps `ensure_collections()` in a blanket `except Exception` that only WARNs (see main.py excerpt below) — a dimension mismatch on an existing collection must NOT be swallowed there. Add an explicit dimension-check-on-existing-collection inside `ensure_collections()` itself (fetch `client.get_collection(name)`, compare `config.params.vectors.size` against `VECTOR_SIZE`), and raise/flag loudly rather than relying on `main.py`'s catch-all.

---

### `apps/memory-api/app/routes/admin_wipe.py` (route, CRUD/destructive)

**Analog:** `apps/memory-api/app/qdrant_setup.py` — same collection-create-with-indexes shape, just triggered by an admin action instead of startup.

**Current code to change** (lines 225-254, `_wipe_qdrant_full`):
```python
async def _wipe_qdrant_full() -> dict[str, Any]:
    """Delete + recreate the messages collection (empty, schema preserved)."""
    try:
        from qdrant_client import AsyncQdrantClient
        from qdrant_client.http.models import Distance, PayloadSchemaType, VectorParams

        client = AsyncQdrantClient(
            url=settings.QDRANT_URL,
            api_key=settings.QDRANT_API_KEY or None,
        )
        try:
            try:
                await client.delete_collection(collection_name=settings.QDRANT_COLLECTION)
            except Exception as exc:
                log.info("admin_wipe.qdrant_delete_collection_missing", error=str(exc))
            await client.create_collection(
                collection_name=settings.QDRANT_COLLECTION,
                vectors_config=VectorParams(size=1536, distance=Distance.COSINE),
            )
            await client.create_payload_index(
                collection_name=settings.QDRANT_COLLECTION,
                field_name="team_scope",
                field_schema=PayloadSchemaType.KEYWORD,
            )
            await client.create_payload_index(
                collection_name=settings.QDRANT_COLLECTION,
                field_name="truth_level",
                field_schema=PayloadSchemaType.KEYWORD,
            )
            return {"status": "ok", "collection": "messages"}
```
**Change:** `size=1536` -> `size=get_embedding_dimension()` (import from `app.embedders`, same helper `qdrant_setup.py` uses). This is Pitfall 2 in RESEARCH.md — flagged explicitly as the call site CONTEXT.md's canonical-refs list does NOT name, and easy to miss.

---

### `apps/memory-api/app/deps.py` (service, DI singleton factory)

**Analog:** itself — `_build_provider()`'s existing `mem0` branch (lines 428-433) is the sibling pattern for "construct a provider given an embedder-shaped kwarg."

**Current code to change** (lines 426-449):
```python
def _build_provider() -> MemoryProvider:
    backend = settings.MEMORY_BACKEND.lower()
    if backend == "mem0":
        from xbrain_memory.providers.mem0_provider import Mem0Provider
        return Mem0Provider(
            qdrant_url=settings.QDRANT_URL,
            openai_api_key=settings.OPENAI_API_KEY,
        )
    if backend == "native":
        from xbrain_memory.providers.native_provider import NativeProvider
        from app.embedders import openai_embedder
        # asyncpg DSN format (no SQLAlchemy driver prefix)
        pg_dsn = settings.DATABASE_URL.replace(
            "postgresql+asyncpg://", "postgresql://"
        )
        return NativeProvider(
            pg_dsn=pg_dsn,
            qdrant_url=settings.QDRANT_URL,
            embedder=openai_embedder,
            qdrant_api_key=settings.QDRANT_API_KEY,
        )
    # Default: stub (no external deps, in-process)
    from xbrain_memory.providers.native_stub import NativeStubProvider
    return NativeStubProvider()
```
**Change:** ONE line — `from app.embedders import openai_embedder` -> `from app.embedders import get_embedder`, and `embedder=openai_embedder` -> `embedder=get_embedder()`. Note `NativeProvider`, `MemoryProvider` ABC (`packages/memory-models/xbrain_memory/provider.py`), and `Mem0Provider` are OUT OF SCOPE — Pitfall 5 (RESEARCH.md) explicitly warns `mem0` stays on its own OpenAI-only embedding path (`mem0_provider.py`'s own `openai_api_key=settings.OPENAI_API_KEY` construction), do not touch it.

**Singleton pattern already in this file (lines 452-456) — no change needed, just confirms the seam:**
```python
def get_memory_provider() -> MemoryProvider:
    global _memory_provider_singleton
    if _memory_provider_singleton is None:
        _memory_provider_singleton = _build_provider()
    return _memory_provider_singleton
```

---

### `apps/memory-api/app/main.py` (lifespan, IF model warm-up is added here)

**Analog:** `apps/memory-api/app/neo4j_client.py` — `init_driver()` / `reconnect_loop()`, called from `lifespan` (lines 73-80 of main.py).

**Current lifespan excerpt** (main.py lines 64-96):
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    log = structlog.get_logger()
    log.info("memory_api_startup")
    try:
        await ensure_collections()
    except Exception as e:
        log.warning("qdrant_setup_skipped", err=str(e))

    # Init Neo4j driver (graceful degrade if NEO4J_URI/NEO4J_PASSWORD not set or unreachable)
    await init_driver()
    _reconnect_task = asyncio.create_task(reconnect_loop())
    _outbox_task = asyncio.create_task(drain_outbox(settings.DATABASE_URL))
    yield
    _reconnect_task.cancel()
    _outbox_task.cancel()
    for _t in (_reconnect_task, _outbox_task):
        try:
            await _t
        except asyncio.CancelledError:
            pass
    await close_driver()
    log.info("memory_api_shutdown")
```

**Neo4j's lazy-singleton, "verify before publish" pattern to copy for model warm-up** (`neo4j_client.py` lines 27-71 — the core idea, not the network specifics):
```python
async def init_driver(quiet: bool = False):
    global _driver
    if not (settings.NEO4J_URI and settings.NEO4J_PASSWORD):
        log.warning("neo4j.disabled", reason="...")
        return None
    from neo4j import AsyncGraphDatabase  # lazy import
    candidate = AsyncGraphDatabase.driver(...)
    try:
        await candidate.verify_connectivity()
    except Exception as exc:
        log.error("neo4j.connectivity_failed", error=str(exc))
        await candidate.close()
        return None
    _driver = candidate   # publish ONLY after verification succeeds
    return _driver
```
**Why this matters for the local embedder:** the same "publish only after verified working" discipline avoids a TOCTOU where a half-loaded/corrupt model gets published to the module-level singleton and a concurrent request hits it mid-load. If warm-up is added to `lifespan` (Claude's Discretion per CONTEXT.md), call a `warm_up_embedder()`-style function here, following the try/except-log-continue-never-crash-boot shape already established by BOTH `ensure_collections()`'s wrapper (lines 68-71) and `init_driver()`.

**IMPORTANT — do not naively load in-process under `--workers 2`:** RESEARCH.md's Anti-Pattern / Pitfall 3 measured this exact duplication (two independent ~272 MB processes, no copy-on-write, `apps/memory-api/Dockerfile:30` runs `--workers 2`). If the planner picks in-process (not sidecar), the warm-up call in `lifespan` must be reconciled with worker count — either drop OSS-light to `--workers 1` or accept ~540-550 MB. If the planner picks the sidecar option, `main.py`'s `lifespan` doesn't load anything — it just calls the sidecar over HTTP the same way it already does for other internal services (see `AGENT_RUNTIME_INTERNAL_URL` fire-and-forget pattern in `config.py` line 127 for the "internal Docker network URL to a sidecar" convention).

---

### `apps/memory-api/pyproject.toml` (config, deps manifest)

**Analog:** itself — existing lazy-imported optional dependencies already follow the "add to `dependencies`, comment with phase + purpose, lazy-import at the actual use site" convention.

**Pattern already established** (lines 19-29):
```python
  "openai>=1.55",  # Phase 2: embeddings for NativeProvider
  "mem0ai>=1.0.4",  # Phase 2: optional Mem0Provider backend (lazy-imported)
  "neo4j>=6.1.0",  # Phase 3: async graph driver (lazy-imported; graceful degrade if NEO4J_URI unset)
  "cryptography>=42.0.0",  # Phase 3 plan 03-10: Fernet encryption for OAuth credentials at rest
  "PyJWT[crypto]>=2.10,<3",  # Phase 12 plan 12-02: GitHub App JWT signing (RS256). cryptography>=42 already present.
  "aiosmtplib>=3.0.0",   # Phase 7 plan 07-06: async SMTP for task notifications
  "anthropic>=0.50.0",   # Phase 7 plan 07-06: Claude (Haiku) for CRM contact + task extraction
  "boto3>=1.35",  # Phase 11 plan 11-10: MinIO/S3 client for superadmin storage metrics (lazy-imported)
  "motor>=3.6",   # superadmin wipe-database: drop LibreChat Mongo DB (lazy-imported)
  "minio>=7.2",   # superadmin wipe-database: clear MinIO xbrain-decks bucket (lazy-imported)
  "argon2-cffi>=25.1.0",  # Phase 18 plan 18-02: argon2id local-auth password hashing (D-18-04)
  "limits>=5.8.0",  # Phase 18 plan 18-02: in-process rate limiting for local-auth routes (D-18-06)
```
**Add** (exact line from RESEARCH.md, verified both-arch):
```python
  "fastembed>=0.8.0",  # Phase 19: local keyless embeddings (ONNX via onnxruntime, no torch)
```
**Dev deps** (lines 34-42) — extend the existing `testcontainers[postgres]` line, do not add a second `testcontainers` entry:
```python
[project.optional-dependencies]
dev = [
  "pytest>=8.3",
  "pytest-asyncio>=0.25",
  "ruff>=0.8",
  "mypy>=1.13",
  "testcontainers[postgres]>=4.8",   # CHANGE -> testcontainers[postgres,qdrant]>=4.8
  "respx>=0.21",
]
```

**Arm64+amd64 verification note (D-19-04):** `onnxruntime` (fastembed's transitive dep) publishes `manylinux_2_27_aarch64`/`manylinux_2_28_aarch64` (arm64) AND `manylinux_2_27_x86_64`/`manylinux_2_28_x86_64` (amd64) wheels for `cp312` — matches `python:3.12-slim`, the base image both `builder` and `runtime` stages in `apps/memory-api/Dockerfile` already use (lines 4, 13). No Dockerfile base-image change needed.

---

### `apps/memory-api/tests/conftest.py` (test, fixture)

**Analog:** itself — the existing `pg_url` session fixture (lines 67-123) is the exact pattern to mirror for `qdrant_url`.

**Full pattern to copy** (lines 56-64, the Docker-availability guard, and 67-123, the fixture shape):
```python
def _docker_available() -> bool:
    try:
        import docker  # noqa: F401
        client = docker.from_env()
        client.ping()
        return True
    except Exception:
        return False


@pytest_asyncio.fixture(scope="session")
async def pg_url() -> AsyncGenerator[str, None]:
    """Spin a Postgres container, run Alembic upgrade head, yield the asyncpg URL."""
    if not _docker_available():
        pytest.skip("Docker not available — skipping integration fixture")
    from testcontainers.postgres import PostgresContainer
    pg = PostgresContainer("postgres:17", username="test", password="test", dbname="test").with_command(
        "postgres -c shared_preload_libraries=pgcrypto"
    )
    pg.start()
    raw = pg.get_connection_url()
    asyncpg_url = raw.replace("postgresql+psycopg2://", "postgresql+asyncpg://")
    os.environ["DATABASE_URL"] = asyncpg_url
    ...
    yield asyncpg_url
    pg.stop()
```
**New fixture to add**, same shape, using `testcontainers.qdrant.QdrantContainer` (the `qdrant` extra added to `pyproject.toml` above provides this class) — reuse `_docker_available()` unchanged, `scope="session"`, `pytest.skip(...)` if Docker unavailable, `.start()`/`.stop()` bracketing a `yield`.

---

### `apps/memory-api/tests/test_local_embeddings.py` (NEW test file)

**Analog:** `apps/memory-api/tests/conftest.py` fixtures (`pg_url`, `session`, the new `qdrant_url`) for the real-dependency integration shape; existing `respx`-based tests (e.g. `apps/memory-api/tests/test_phase12_org_membership.py`, already cited in RESEARCH.md as precedent) for the OpenAI-path unit test (check #6 in RESEARCH.md's Validation Approach table).

**No excerpt needed beyond the fixtures above** — RESEARCH.md's Validation Approach table (checks 1-7) is the authoritative test-case list; this file should implement checks 1-4 (real Postgres + real Qdrant, no OpenAI key, semantic-ranking assertion — the literal "gate lesson": a mocked embedder cannot fail the ranking assertion even if the integration is broken) and check 7 (garbage `EMBEDDINGS_PROVIDER` value falls back to `local`, doesn't raise). Check 6 (OpenAI path via `respx`) and check 5 (`docker run --network none` smoke test) can live in a separate unit test / CI script respectively.

---

### `apps/embeddings-local/` (NEW, sidecar option ONLY)

**Analog:** `apps/brain-janitor/` for the plain-Python-service repo-root-build-context structure; `apps/mcp-scraper/Dockerfile` + its `docker-compose.yml` block for the "small standalone container, healthcheck, `mem_limit`" convention.

**`apps/brain-janitor/Dockerfile` (full file, 12 lines) — repo-root build context pattern:**
```dockerfile
# Build context: REPO ROOT (not apps/brain-janitor) -- needed for packages/memory-models
# In docker-compose: context: ..   dockerfile: apps/brain-janitor/Dockerfile
FROM python:3.12-slim
WORKDIR /app
# Install shared package first (repo root context required)
COPY packages/memory-models/ ./packages/memory-models/
RUN pip install --no-cache-dir -e packages/memory-models/
# Install brain-janitor dependencies
COPY apps/brain-janitor/ ./apps/brain-janitor/
RUN pip install --no-cache-dir -e apps/brain-janitor/
WORKDIR /app/apps/brain-janitor
CMD ["python", "-m", "app.main"]
```
A local-embeddings sidecar does NOT need `packages/memory-models` (it's a pure HTTP embed endpoint, no provider abstraction) — use this as the multi-stage bake pattern reference only, combined with RESEARCH.md's proven `Pattern 1: Bake-then-offline` Dockerfile (already validated end-to-end with `docker run --network none` in the research session — see 19-RESEARCH.md §Pattern 1 for the exact multi-stage Dockerfile to use).

**`infrastructure/docker-compose.yml` mcp-scraper block (lines 794-814) — sidecar service registration pattern:**
```yaml
  mcp-scraper:
    build:
      context: ../apps/mcp-scraper
      dockerfile: Dockerfile
    image: xbrain/mcp-scraper:phase3
    container_name: xbrain-mcp-scraper
    restart: unless-stopped
    logging: *default-logging
    environment:
      LOG_LEVEL: ${LOG_LEVEL:-INFO}
      FASTMCP_HOST: "0.0.0.0"
      FASTMCP_PORT: "8100"
    networks: [xbrain_net]
    mem_limit: 128m
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- http://127.0.0.1:8100/healthz 2>/dev/null || exit 0"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 20s
```
Copy this shape exactly for a new `embeddings-local` service: `build.context`/`dockerfile`, `image: xbrain/embeddings-local:phase19`, `container_name: xbrain-embeddings-local`, `networks: [xbrain_net]`, a `healthcheck` on a plain `/healthz`, and — per RESEARCH.md §6/Open-Questions-2 — `mem_limit: 512m` as the starting budget (not `128m` like mcp-scraper; the model RSS alone measured 256-366 MB). memory-api would then call it over the internal Docker network the same way it already calls `AGENT_RUNTIME_INTERNAL_URL` (`config.py` line 127) — add an `EMBEDDINGS_LOCAL_URL` config knob following that exact naming/comment convention if the sidecar path is chosen.

## Shared Patterns

### Fail-safe config default (no crash-loop on a zero-key/typo'd install)
**Source:** `apps/memory-api/app/config.py` lines 245-252 (`LOCAL_AUTH_*` block, deliberately no `field_validator`)
**Apply to:** `EMBEDDINGS_PROVIDER`, `LOCAL_EMBEDDING_MODEL` in `config.py`; `get_embedder()`'s unknown-value fallback in `embedders.py`.
```python
    # Safe defaults, deliberately NO field_validator: a zero-OAuth install must
    # still boot cleanly (SC#1).
    LOCAL_AUTH_MAX_FAILED_ATTEMPTS: int = 5
```

### Lazy module-level singleton with guarded init
**Source:** `apps/memory-api/app/embedders.py` lines 7-16 (`_client` / `_get_client()`); `apps/memory-api/app/neo4j_client.py` lines 24, 27-71 (`_driver` / `init_driver()`, "publish only after verified working")
**Apply to:** the new `_local_model` / `_get_local_model()` singleton in `embedders.py` (and, if a sidecar warm-up hook lands in `main.py`'s `lifespan`, the same "verify then publish" discipline).

### Idempotent Qdrant collection provisioning with two payload indexes
**Source:** `apps/memory-api/app/qdrant_setup.py` lines 19-49 (`ensure_collections`); mirrored in `packages/memory-models/xbrain_memory/providers/native_provider.py` lines 58-87 (`_ensure_collection`, the self-heal-on-404 sibling) and `apps/memory-api/app/routes/admin_wipe.py` lines 225-254 (`_wipe_qdrant_full`)
**Apply to:** all three call sites must derive `VECTOR_SIZE`/`size=` from the SAME `get_embedding_dimension()` helper in `embedders.py` — this is D-19-03's core fix, and RESEARCH.md's Pitfall 2 exists specifically because these three sites currently disagree independently.

### Fail-soft-but-loud boundary at FastAPI lifespan
**Source:** `apps/memory-api/app/main.py` lines 68-71 (`ensure_collections()` wrapped in `try/except Exception: log.warning`) and `neo4j_client.py`'s `init_driver`/`reconnect_loop` (never raises, but DOES emit ERROR/WARNING with an actionable message)
**Apply to:** any new startup/warm-up step for the local embedder — never crash boot, but per RESEARCH.md Pitfall 1, a dimension-mismatch specifically must NOT be absorbed by `main.py`'s blanket except the same way a "Qdrant is down" error already is; it needs its own loud, actionable log line or a `/v1/healthz` degraded flag.

### Async wrapper for synchronous heavy work
**Source:** no direct existing analog in this codebase (a genuinely new pitfall introduced by this phase) — copy verbatim from RESEARCH.md's proven snippet:
```python
vecs = await asyncio.to_thread(lambda: list(model.embed([text])))
```
**Apply to:** `local_embedder()` in `embedders.py` only. `openai_embedder` doesn't need this (already async via `AsyncOpenAI`); a naive direct call to `model.embed()` inside `async def local_embedder` would block the event loop for every concurrent request on that worker.

## No Analog Found

None. Every file in this phase's scope has at least a role-match analog already in the repo (see table above) — consistent with RESEARCH.md's own framing: "every piece of this phase that looks like it needs new infrastructure... already has a designed seam or an off-the-shelf mechanism in this codebase."

## Metadata

**Analog search scope:** `apps/memory-api/app/` (embedders.py, config.py, qdrant_setup.py, deps.py, main.py, neo4j_client.py, routes/admin_wipe.py), `apps/memory-api/tests/conftest.py`, `apps/memory-api/pyproject.toml`, `apps/memory-api/Dockerfile`, `packages/memory-models/xbrain_memory/providers/` (native_provider.py, mem0_provider.py), `apps/brain-janitor/`, `apps/mcp-scraper/`, `infrastructure/docker-compose.yml`.
**Files scanned:** 15 read directly (full or targeted ranges), 3 globbed for structure.
**Pattern extraction date:** 2026-07-18
