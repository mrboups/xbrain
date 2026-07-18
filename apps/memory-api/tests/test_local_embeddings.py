"""Phase 19 (EMBED-01) — the gate-lesson proof for keyless local embeddings.

Against a REAL Postgres + REAL Qdrant (testcontainers) with NO OpenAI key set, a
document is ingested -> embedded by the real local fastembed model -> written to
Qdrant -> retrieved by semantic search that ranks the topically-matching document
FIRST. A mocked embedder returns a fake vector and cannot fail a semantic-ranking
assertion even if the integration is broken — so the local/semantic path uses the
REAL embedder with NO mock. respx is used ONLY in the clearly-named OpenAI-regression
test (`test_openai_path_unchanged_when_selected`).

Covers RESEARCH.md Validation Approach checks 1-4 (integration), 7 (garbage->local
fallback, unit) and 6 (OpenAI path unchanged, respx unit).

Config is driven by mutating the `settings` singleton directly (the established
convention in test_embedders_provider.py): get_embedder(), get_embedding_dimension()
and ensure_collections() all read `settings` at call time, so no importlib.reload is
needed for the selector to pick the local path.
"""

import os
import tempfile
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid5

import pytest
import pytest_asyncio
import respx

from xbrain_memory.types import (
    MemoryItem,
    TruthLevel,
    ValidationStatus,
    Visibility,
)

# --- Fixed test corpus: distinct topics; the query targets the Lisbon offsite. ---
TEAM = "phase19-embed"
PROJECT = "gate-lesson"

_DOCS = [
    ("lisbon", "The team offsite this year is in Lisbon; we will all fly there for a week of workshops."),
    ("budget", "The Q3 budget numbers show a twelve percent increase in cloud infrastructure spend."),
    ("deploy", "Deploying memory-api requires running the alembic migrations before rolling the container."),
    ("hiring", "We are hiring two backend engineers to join the platform team next quarter."),
]
QUERY = "where are we going for the team trip?"


def _doc_id(key: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"{TEAM}:{key}"))


LISBON_ID = _doc_id("lisbon")


def _make_item(key: str, content: str) -> MemoryItem:
    """Build a MemoryItem carrying the FULL 7-field tagging contract (CLAUDE.md)."""
    now = datetime.now(timezone.utc)
    return MemoryItem(
        id=_doc_id(key),
        team_scope=TEAM,          # 1
        project_scope=PROJECT,    # 2
        content=content,
        metadata={},
        visibility=Visibility.TEAM,                 # 3
        confidence=1.0,                             # 4
        truth_level=TruthLevel.WORKING,             # 5
        source="test-local-embeddings",            # 6
        validation_status=ValidationStatus.VALIDATED,  # 7
        created_at=now,
        updated_at=now,
    )


# === Fixtures ================================================================


@pytest.fixture
def local_env():
    """Force the keyless local provider + native backend; restore afterward.

    Mutates the `settings` singleton in place (the convention in
    test_embedders_provider.py). Also pins the fastembed cache to a writable temp dir
    (the prod default '/app/model_cache' is a container path) so the pytest run — which
    HAS network — can materialize the real model once and reuse it.
    """
    from app.config import settings
    import app.deps as deps

    orig = (
        settings.EMBEDDINGS_PROVIDER,
        settings.OPENAI_API_KEY,
        settings.MEMORY_BACKEND,
        settings.EMBEDDING_CACHE_DIR,
    )
    orig_singleton = deps._memory_provider_singleton

    cache_dir = os.path.join(tempfile.gettempdir(), "xbrain_fastembed_cache")
    os.makedirs(cache_dir, exist_ok=True)

    settings.EMBEDDINGS_PROVIDER = "local"
    settings.OPENAI_API_KEY = ""
    settings.MEMORY_BACKEND = "native"
    settings.EMBEDDING_CACHE_DIR = cache_dir
    deps._memory_provider_singleton = None
    try:
        yield settings
    finally:
        (
            settings.EMBEDDINGS_PROVIDER,
            settings.OPENAI_API_KEY,
            settings.MEMORY_BACKEND,
            settings.EMBEDDING_CACHE_DIR,
        ) = orig
        deps._memory_provider_singleton = orig_singleton


@pytest_asyncio.fixture
async def local_model_ready(local_env):
    """Materialize the real fastembed model ONCE — the narrow gate-lesson skip guard.

    In a restricted CI/sandbox where Docker is up but the model is neither cached NOR
    downloadable, this skips cleanly instead of hard-failing. The guard wraps ONLY the
    model materialization here — never the ranking assertion in the test body (a wrong
    ranking MUST fail, not skip). Mirrors the `_docker_available()` -> pytest.skip pattern.
    """
    from app.embedders import local_embedder

    try:
        # First call that loads/uses the real fastembed model (downloads on cache miss).
        vec = await local_embedder("warmup: materialize the real fastembed model")
    except (ConnectionError, OSError) as e:  # model not cached AND no network to fetch it
        pytest.skip(f"local model not cached and no network to download it: {e}")
    assert len(vec) == 384  # sanity: the REAL model is loaded and returns 384 dims
    return True


# === Helpers =================================================================


def _build_native_provider():
    """Construct a fresh NativeProvider bound to the testcontainers PG + Qdrant.

    Built directly (not via the deps singleton) so each test gets a provider whose
    asyncpg pool + Qdrant client live in THIS test's event loop — the same reason the
    `session` fixture disposes its engine per test.
    """
    from app.config import settings
    from app.embedders import get_embedder
    from xbrain_memory.providers.native_provider import NativeProvider

    pg_dsn = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    return NativeProvider(
        pg_dsn=pg_dsn,
        qdrant_url=settings.QDRANT_URL,
        embedder=get_embedder(),
        qdrant_api_key=settings.QDRANT_API_KEY,
    )


async def _aclose(provider) -> None:
    """Close the provider's asyncpg pool + Qdrant client to avoid stale-loop warnings."""
    try:
        if getattr(provider, "_pool", None) is not None:
            await provider._pool.close()
    finally:
        try:
            await provider._qdrant.close()
        except Exception:
            pass


async def _ingest_all(provider) -> None:
    for key, content in _DOCS:
        item_id = await provider.upsert(_make_item(key, content))
        assert item_id == _doc_id(key)


# === check 1: collection created at 384, not 1536 ===========================


@pytest.mark.integration
async def test_local_collection_created_at_384(pg_url, qdrant_url, local_env):
    from app.config import settings
    from app.qdrant_setup import ensure_collections
    from qdrant_client import AsyncQdrantClient

    await ensure_collections()

    client = AsyncQdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY or None)
    try:
        info = await client.get_collection(settings.QDRANT_COLLECTION)
        vectors = info.config.params.vectors
        size = (
            next(iter(vectors.values())).size
            if isinstance(vectors, dict)
            else vectors.size
        )
        assert size == 384  # local provider dimension, NOT the OpenAI 1536
    finally:
        await client.close()


# === check 2: keyless ingest writes real vectors ============================


@pytest.mark.integration
async def test_local_ingest_writes_vectors(pg_url, qdrant_url, local_model_ready):
    from app.config import settings
    from app.qdrant_setup import ensure_collections
    from qdrant_client import AsyncQdrantClient
    from qdrant_client.http.models import FieldCondition, Filter, MatchValue

    await ensure_collections()
    provider = _build_native_provider()
    try:
        await _ingest_all(provider)
    finally:
        await _aclose(provider)

    client = AsyncQdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY or None)
    try:
        res = await client.count(
            collection_name=settings.QDRANT_COLLECTION,
            count_filter=Filter(
                must=[FieldCondition(key="team_scope", match=MatchValue(value=TEAM))]
            ),
            exact=True,
        )
        assert res.count == len(_DOCS)  # every doc landed as a real point
    finally:
        await client.close()


# === check 3 (THE gate lesson): real semantic ranking, top hit first ========


@pytest.mark.integration
async def test_local_semantic_search_ranks_expected_first(pg_url, qdrant_url, local_model_ready):
    from app.qdrant_setup import ensure_collections

    await ensure_collections()
    provider = _build_native_provider()
    try:
        await _ingest_all(provider)

        # the real fastembed embedder MUST run here; a mocked/fake vector could not
        # satisfy this ranking assertion — this is the gate lesson. NO mock on this path.
        results = await provider.search(QUERY, team_scope=TEAM, limit=5)
    finally:
        await _aclose(provider)

    assert results, "semantic search returned no hits"
    assert results[0].item.id == LISBON_ID, (
        "expected the Lisbon-offsite doc ranked FIRST for a 'team trip' query, got "
        f"{results[0].item.content!r} (score={results[0].score})"
    )


# === check 4: same top hit through the real HTTP route ======================


@pytest.mark.integration
async def test_http_memory_search_returns_same_top_hit(
    pg_url, qdrant_url, client, local_model_ready, bridge_jwt
):
    import app.deps as deps
    from app.deps import get_memory_provider
    from app.qdrant_setup import ensure_collections

    await ensure_collections()
    # Ingest through the SAME singleton the route resolves via Depends(get_memory_provider),
    # built in THIS test's event loop so its asyncpg pool is valid for the HTTP call below.
    deps._memory_provider_singleton = None
    provider = get_memory_provider()
    try:
        await _ingest_all(provider)

        token = bridge_jwt("svc-phase19-test", TEAM)
        resp = await client.get(
            "/v1/memory/search",
            params={"q": QUERY, "limit": 5},
            headers={"Authorization": f"Bearer {token}", "X-Team-Scope": TEAM},
        )
        assert resp.status_code == 200, resp.text
        hits = resp.json()
        assert hits, "HTTP /v1/memory/search returned no hits"
        assert hits[0]["item"]["id"] == LISBON_ID  # same top hit as the provider call
    finally:
        await _aclose(provider)
        deps._memory_provider_singleton = None


# === check 7: garbage EMBEDDINGS_PROVIDER falls back to local (unit) =========


def test_garbage_provider_falls_back_to_local():
    from app.config import settings
    from app.embedders import get_embedder, get_embedding_dimension, local_embedder

    orig = settings.EMBEDDINGS_PROVIDER
    try:
        settings.EMBEDDINGS_PROVIDER = "not-a-provider"
        # No exception; unknown value falls back to the keyless local path.
        assert get_embedder() is local_embedder
        assert get_embedding_dimension() == 384
    finally:
        settings.EMBEDDINGS_PROVIDER = orig


# === check 6: OpenAI path unchanged when selected (respx unit) ===============


@respx.mock
async def test_openai_path_unchanged_when_selected():
    """Regression for D-19-02: setting EMBEDDINGS_PROVIDER=openai + a key restores the
    unchanged OpenAI 1536-dim path with no code change. respx (not a real embedder mock)
    intercepts the OpenAI HTTP call — this is the ONLY place mocking is allowed, and it
    is deliberately isolated from the local/semantic path above."""
    from app.config import settings
    import app.embedders as embedders
    from app.embedders import get_embedder, get_embedding_dimension, openai_embedder

    orig = (settings.EMBEDDINGS_PROVIDER, settings.OPENAI_API_KEY)
    orig_client = embedders._client
    try:
        settings.EMBEDDINGS_PROVIDER = "openai"
        settings.OPENAI_API_KEY = "sk-dummy-test-key-not-real"
        embedders._client = None  # force a fresh AsyncOpenAI bound to the dummy key

        assert get_embedder() is openai_embedder
        assert get_embedding_dimension() == 1536

        route = respx.post("https://api.openai.com/v1/embeddings").respond(
            json={
                "object": "list",
                "data": [{"object": "embedding", "index": 0, "embedding": [0.01] * 1536}],
                "model": "text-embedding-3-small",
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            },
        )

        vec = await openai_embedder("regression check: the OpenAI path still works")
        assert route.called  # the OpenAI endpoint was actually hit
        assert len(vec) == 1536  # the 1536-dim OpenAI path is unchanged
    finally:
        settings.EMBEDDINGS_PROVIDER, settings.OPENAI_API_KEY = orig
        embedders._client = orig_client
