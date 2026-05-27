"""Phase 13 plan 13-03 — NativeProvider.upsert() race-condition regression tests.

Tests the fix for the SELECT+INSERT race condition (13-RESEARCH §Pitfall 6):

  Before the fix, two concurrent upsert() calls with the same deterministic UUID
  both pass the `existing = None` SELECT check before either completes INSERT,
  causing asyncpg.UniqueViolationError on the second caller.

  The fix replaces SELECT+INSERT/UPDATE with a single-statement
  INSERT ... ON CONFLICT (id) DO UPDATE inside an explicit transaction:

    1. Pre-snapshot the existing row to memory_items_history (SELECT→INSERT) —
       INSERT 0 if no prior row, INSERT 1 if updating.
    2. INSERT ... ON CONFLICT (id) DO UPDATE — atomic, row-level write lock
       serialises concurrent calls on the same id.

Five tests:

  Test 1 (single new upsert):   1 row in memory_items, 0 rows in history.
  Test 2 (upsert updates):      1 row in memory_items (new content), 1 history row.
  Test 3 (5 concurrent upserts): zero exceptions, 1 final row, 4 history rows.
  Test 4 (return value):        all calls return the same item_id string.
  Test 5 (Qdrant payload):      team_scope, truth_level, source, deleted_at_ts=0.0
                                 all present in Qdrant payload (no regression).

Tests are marked @pytest.mark.integration and skip automatically when:
  - PG_TEST_DSN env var is absent AND localhost:5432 is not reachable, OR
  - asyncpg is not importable.

Identical SKIP pattern to test_native_provider_soft_delete.py's Qdrant tests.
The unit layer (NativeStubProvider) has no coverage for the race because the
stub is single-threaded and uses an in-memory dict — the concurrent code path
is only exercised against a real PostgreSQL instance.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest

from xbrain_memory.types import MemoryItem, TruthLevel, ValidationStatus, Visibility

# --------------------------------------------------------------------------
# Availability probe — checked once at module load.
# --------------------------------------------------------------------------

_PG_SKIP_REASON = (
    "No PostgreSQL available for upsert-race integration tests. "
    "Set PG_TEST_DSN to a writable postgres connection string, "
    "or run against a local postgres on localhost:5432. "
    "Tests skip automatically in CI without postgres."
)


def _pg_dsn() -> str | None:
    """Return the first PG connection string that is reachable, or None."""
    explicit = os.environ.get("PG_TEST_DSN", "").strip()
    if explicit:
        return explicit
    # Probe default local dev postgres.
    return "postgresql://postgres:postgres@localhost:5432/postgres"


def _pg_reachable() -> bool:
    """True iff asyncpg is importable AND a connection to the target DSN succeeds."""
    try:
        import asyncpg  # noqa: F401 — import check only
    except ImportError:
        return False
    dsn = _pg_dsn()
    if not dsn:
        return False
    try:
        async def _probe():
            conn = await asyncpg.connect(dsn, timeout=3.0)
            await conn.close()

        asyncio.get_event_loop().run_until_complete(_probe())
        return True
    except Exception:
        return False


_PG_REACHABLE = _pg_reachable()
_pg_skip = pytest.mark.skipif(not _PG_REACHABLE, reason=_PG_SKIP_REASON)


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def pg_test_dsn() -> str:
    dsn = _pg_dsn()
    if not dsn:
        pytest.skip(_PG_SKIP_REASON)
    return dsn


@pytest.fixture
async def pg_schema(pg_test_dsn):
    """Create a temporary, isolated schema in the test DB.

    Creates memory_items + memory_items_history tables in a fresh schema
    named xbrain_test_race_<uuid12> and drops the schema on teardown.
    Isolation ensures concurrent test runs and crashed-mid-test runs do not
    bleed into each other or into production tables.
    """
    import asyncpg

    suffix = uuid.uuid4().hex[:12]
    schema = f"xbrain_test_race_{suffix}"
    dsn_with_schema = pg_test_dsn  # schema override applied via SET search_path

    pool = await asyncpg.create_pool(dsn_with_schema, min_size=2, max_size=8)
    async with pool.acquire() as conn:
        await conn.execute(f'CREATE SCHEMA "{schema}"')
        await conn.execute(f'SET search_path TO "{schema}"')
        await conn.execute(f"""
            CREATE TABLE "{schema}".memory_items (
                id                uuid PRIMARY KEY,
                team_scope        text NOT NULL,
                project_scope     text,
                content           text NOT NULL,
                metadata          jsonb DEFAULT '{{}}'::jsonb,
                truth_level       text NOT NULL DEFAULT 'EPHEMERAL',
                validation_status text NOT NULL DEFAULT 'pending',
                visibility        text NOT NULL DEFAULT 'team',
                confidence        float8 NOT NULL DEFAULT 1.0,
                source            text NOT NULL,
                created_at        timestamptz NOT NULL DEFAULT now(),
                updated_at        timestamptz NOT NULL DEFAULT now(),
                deleted_at        timestamptz
            )
        """)
        await conn.execute(f"""
            CREATE TABLE "{schema}".memory_items_history (
                id                bigserial PRIMARY KEY,
                item_id           uuid NOT NULL,
                team_scope        text NOT NULL,
                content           text NOT NULL,
                metadata          jsonb DEFAULT '{{}}'::jsonb,
                truth_level       text NOT NULL,
                validation_status text NOT NULL,
                visibility        text NOT NULL,
                confidence        float8 NOT NULL DEFAULT 1.0,
                source            text NOT NULL,
                snapshot_at       timestamptz NOT NULL DEFAULT now()
            )
        """)

    # Build a per-schema DSN (asyncpg supports options= param)
    yield pool, schema

    async with pool.acquire() as conn:
        await conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
    await pool.close()


def _make_item(
    item_id: str,
    team: str = "t1",
    content: str = "test fact",
    truth_level: TruthLevel = TruthLevel.WORKING,
) -> MemoryItem:
    now = datetime.now(timezone.utc)
    return MemoryItem(
        id=item_id,
        team_scope=team,
        content=content,
        source="test:13-03-race",
        truth_level=truth_level,
        validation_status=ValidationStatus.PENDING,
        visibility=Visibility.TEAM,
        confidence=1.0,
        created_at=now,
        updated_at=now,
    )


async def _build_provider(pool, schema: str):
    """Build a NativeProvider that uses the isolated test schema.

    We monkey-patch the pool after construction to reuse our fixture pool
    and SET search_path on every acquired connection so all SQL lands in
    the isolated schema.
    """
    import asyncpg
    from xbrain_memory.providers.native_provider import NativeProvider

    # Fake Qdrant — records upserts without a real server.
    class _FakeQdrant:
        def __init__(self):
            self.upserted: list[dict[str, Any]] = []

        async def upsert(self, *, collection_name: str, points: list) -> None:
            for p in points:
                self.upserted.append({"id": p.id, "payload": dict(p.payload)})

    class _SearchPathPool:
        """Wrapper that sets search_path on each acquired connection."""

        def __init__(self, real_pool, schema_name: str):
            self._pool = real_pool
            self._schema = schema_name

        def acquire(self):
            return _SchemaConn(self._pool, self._schema)

    class _SchemaConn:
        def __init__(self, pool, schema: str):
            self._pool = pool
            self._schema = schema
            self._conn = None

        async def __aenter__(self):
            self._conn = await self._pool.acquire()
            await self._conn.execute(f'SET search_path TO "{self._schema}"')
            return self._conn

        async def __aexit__(self, *args):
            await self._pool.release(self._conn)

    fake_qdrant = _FakeQdrant()

    async def _fake_embedder(text: str) -> list[float]:
        return [0.1, 0.2, 0.3, 0.4]

    provider = NativeProvider(
        pg_dsn="postgresql://unused",
        qdrant_url="http://unused",
        embedder=_fake_embedder,
    )
    # Inject isolated pool + fake qdrant
    provider._pool = _SearchPathPool(pool, schema)
    provider._qdrant = fake_qdrant

    return provider, fake_qdrant


# --------------------------------------------------------------------------
# Integration tests
# --------------------------------------------------------------------------


@_pg_skip
@pytest.mark.asyncio
async def test_single_new_upsert_creates_one_row(pg_schema):
    """Test 1: single new upsert → 1 row in memory_items, 0 in history."""
    pool, schema = pg_schema
    provider, _ = await _build_provider(pool, schema)

    fixed_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    returned_id = await provider.upsert(_make_item(fixed_id, content="first"))

    assert returned_id == fixed_id

    async with pool.acquire() as conn:
        await conn.execute(f'SET search_path TO "{schema}"')
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM memory_items WHERE id = $1",
            uuid.UUID(fixed_id),
        )
        hist_count = await conn.fetchval(
            "SELECT COUNT(*) FROM memory_items_history WHERE item_id = $1",
            uuid.UUID(fixed_id),
        )

    assert count == 1, f"Expected 1 row in memory_items, got {count}"
    assert hist_count == 0, f"Expected 0 history rows on first insert, got {hist_count}"


@_pg_skip
@pytest.mark.asyncio
async def test_upsert_updates_existing_and_snapshots_history(pg_schema):
    """Test 2: two sequential upserts with same id → 1 row (new content), 1 history row."""
    pool, schema = pg_schema
    provider, _ = await _build_provider(pool, schema)

    fixed_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    await provider.upsert(_make_item(fixed_id, content="version one"))
    await provider.upsert(_make_item(fixed_id, content="version two"))

    async with pool.acquire() as conn:
        await conn.execute(f'SET search_path TO "{schema}"')
        row = await conn.fetchrow(
            "SELECT content FROM memory_items WHERE id = $1",
            uuid.UUID(fixed_id),
        )
        hist_count = await conn.fetchval(
            "SELECT COUNT(*) FROM memory_items_history WHERE item_id = $1",
            uuid.UUID(fixed_id),
        )

    assert row is not None, "Row must exist after two upserts"
    assert row["content"] == "version two", (
        f"Expected 'version two' after second upsert, got '{row['content']}'"
    )
    assert hist_count == 1, (
        f"Expected 1 history snapshot (of version one) after second upsert, got {hist_count}"
    )


@_pg_skip
@pytest.mark.asyncio
async def test_five_concurrent_upserts_no_exception(pg_schema):
    """Test 3: 5 concurrent upserts with same id → zero exceptions, 1 final row, 4 history rows.

    This is the core race-condition regression test.
    Five concurrent calls for the same UUID must not raise UniqueViolationError.
    All five writers race for the same row — the INSERT...ON CONFLICT...DO UPDATE
    serialises writes at the row level. One caller wins the INSERT; the other
    four each produce an UPDATE → history snapshot.
    """
    pool, schema = pg_schema
    provider, _ = await _build_provider(pool, schema)

    fixed_id = "cccccccc-cccc-cccc-cccc-cccccccccccc"

    results = await asyncio.gather(
        *[
            provider.upsert(_make_item(fixed_id, content=f"version {i}"))
            for i in range(5)
        ],
        return_exceptions=True,
    )

    # Assert zero exceptions
    exceptions = [r for r in results if isinstance(r, Exception)]
    assert not exceptions, (
        f"Expected zero exceptions from 5 concurrent upserts, got: {exceptions}"
    )

    async with pool.acquire() as conn:
        await conn.execute(f'SET search_path TO "{schema}"')
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM memory_items WHERE id = $1",
            uuid.UUID(fixed_id),
        )
        hist_count = await conn.fetchval(
            "SELECT COUNT(*) FROM memory_items_history WHERE item_id = $1",
            uuid.UUID(fixed_id),
        )

    assert count == 1, (
        f"Expected exactly 1 row in memory_items after 5 concurrent upserts, got {count}"
    )
    assert hist_count == 4, (
        f"Expected 4 history snapshots (4 UPDATEs out of 5 calls), got {hist_count}"
    )


@_pg_skip
@pytest.mark.asyncio
async def test_concurrent_upserts_return_same_item_id(pg_schema):
    """Test 4: all concurrent calls return the same item_id string (the input id)."""
    pool, schema = pg_schema
    provider, _ = await _build_provider(pool, schema)

    fixed_id = "dddddddd-dddd-dddd-dddd-dddddddddddd"
    results = await asyncio.gather(
        *[provider.upsert(_make_item(fixed_id, content=f"v{i}")) for i in range(5)],
        return_exceptions=True,
    )

    for r in results:
        assert not isinstance(r, Exception), f"Unexpected exception: {r}"
        assert r == fixed_id, (
            f"All callers must return the input id '{fixed_id}', got '{r}'"
        )


@_pg_skip
@pytest.mark.asyncio
async def test_qdrant_payload_unchanged_after_upsert(pg_schema):
    """Test 5: Qdrant payload still includes team_scope, truth_level, source, deleted_at_ts=0.0."""
    pool, schema = pg_schema
    provider, fake_qdrant = await _build_provider(pool, schema)

    fixed_id = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
    await provider.upsert(
        _make_item(
            fixed_id,
            team="payload-team",
            content="payload check",
            truth_level=TruthLevel.VALIDATED,
        )
    )

    assert fake_qdrant.upserted, "Qdrant upsert must have been called"
    last_point = fake_qdrant.upserted[-1]
    payload = last_point["payload"]

    assert payload.get("team_scope") == "payload-team", (
        f"team_scope missing or wrong in Qdrant payload: {payload}"
    )
    assert payload.get("truth_level") == TruthLevel.VALIDATED.value, (
        f"truth_level missing or wrong in Qdrant payload: {payload}"
    )
    assert payload.get("source") == "test:13-03-race", (
        f"source missing in Qdrant payload: {payload}"
    )
    assert payload.get("deleted_at_ts") == 0.0, (
        f"deleted_at_ts must be 0.0 in fresh upsert Qdrant payload: {payload}"
    )
    assert last_point["id"] == fixed_id, (
        f"Qdrant point id must match item_id: {last_point['id']}"
    )
