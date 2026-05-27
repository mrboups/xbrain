"""Unit tests for the SQL query + dispatch logic in pg_purger.

We don't spin up a real Postgres — we fake the asyncpg.Pool + Connection
surface that ``purge_pg`` actually uses (acquire, transaction, fetch, execute).

What we lock down here:
  1. Every table in PURGE_TABLES gets a DELETE issued in order, with the
     ``deleted_at IS NOT NULL AND deleted_at < now() - $1`` predicate and the
     correct ``$1`` interval argument is a ``datetime.timedelta`` (NOT a plain
     string like ``"30 days"``). asyncpg serialises timedelta natively to the
     Postgres interval wire type; passing a bare str causes
     ``'str' object has no attribute 'days'`` at query-execution time.
  2. The audit_log INSERT fires exactly ONCE per run and only when at least
     one row was purged (sum of all RETURNING lists > 0).
  3. When zero rows are purged, NO audit_log INSERT runs (avoids noise).
  4. The returned dict maps table_name → list of UUIDs, in the same order
     queries were issued — so downstream Qdrant/Neo4j purgers get the
     memory_items list specifically.
"""
from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.pg_purger import PURGE_TABLES, _pg_url, purge_pg


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeRow(dict):
    """Mimic asyncpg.Record (dict-like, supports r['id'])."""

    def __getitem__(self, key: str) -> Any:  # type: ignore[override]
        return super().__getitem__(key)


class _FakeConn:
    """Records every ``fetch`` and ``execute`` call.

    The caller hands us a ``script`` (per-table list of UUIDs to RETURN); we
    pop one item per fetch in order so the test asserts deterministically.
    """

    def __init__(self, script: dict[str, list[UUID]]) -> None:
        self._script = {k: list(v) for k, v in script.items()}
        self.fetched: list[tuple[str, tuple[Any, ...]]] = []
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        # Map table name from the SQL string to know what to return.
        self._fetch_call_idx = 0

    @asynccontextmanager
    async def transaction(self):  # noqa: D401 — context manager
        yield

    async def fetch(self, sql: str, *args: Any) -> list[_FakeRow]:
        self.fetched.append((sql, args))
        # Match "DELETE FROM <table>" in the SQL to know which list to return
        m = re.search(r"DELETE FROM (\w+)", sql)
        assert m is not None, f"unexpected fetch SQL: {sql!r}"
        table = m.group(1)
        ids = self._script.get(table, [])
        self._script[table] = []  # consume — second call would return empty
        return [_FakeRow(id=u) for u in ids]

    async def execute(self, sql: str, *args: Any) -> None:
        self.executed.append((sql, args))


class _FakePool:
    """Yields a single FakeConn from ``acquire`` (async context manager)."""

    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self):  # returns an async-context-manager
        outer = self

        class _Acquired:
            async def __aenter__(self):
                return outer._conn

            async def __aexit__(self, exc_type, exc, tb):
                return False

        return _Acquired()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purge_pg_issues_one_delete_per_table_in_order():
    """Every PURGE_TABLES entry → one DELETE, in declared order, correct shape."""
    conn = _FakeConn({t: [] for t, _ in PURGE_TABLES})
    pool = _FakePool(conn)

    await purge_pg(pool, retention_days=30)  # type: ignore[arg-type]

    assert len(conn.fetched) == len(PURGE_TABLES)
    for (table, _), (sql, args) in zip(PURGE_TABLES, conn.fetched, strict=True):
        # Right table targeted
        assert f"DELETE FROM {table}" in sql, sql
        # Predicate references deleted_at
        assert "deleted_at IS NOT NULL" in sql
        assert "deleted_at < now() - $1" in sql
        assert "RETURNING id" in sql
        # Argument is a timedelta (asyncpg interval codec requirement — NOT a str)
        assert args == (timedelta(days=30),)


@pytest.mark.asyncio
async def test_purge_pg_returns_uuids_grouped_by_table():
    """Returned dict has one entry per table, with the UUIDs we faked RETURNING."""
    mem_ids = [uuid4(), uuid4()]
    task_ids = [uuid4()]
    script: dict[str, list[UUID]] = {t: [] for t, _ in PURGE_TABLES}
    script["memory_items"] = mem_ids
    script["tasks"] = task_ids

    conn = _FakeConn(script)
    pool = _FakePool(conn)

    purged = await purge_pg(pool, retention_days=30)  # type: ignore[arg-type]

    assert purged["memory_items"] == mem_ids
    assert purged["tasks"] == task_ids
    # Tables that returned nothing still show up with an empty list
    assert purged["conversations"] == []
    assert purged["messages"] == []


@pytest.mark.asyncio
async def test_purge_pg_writes_one_audit_row_when_purges_happened():
    """audit_log INSERT fires once, with team_scope='system' + JSON payload."""
    mem_ids = [uuid4()]
    script: dict[str, list[UUID]] = {t: [] for t, _ in PURGE_TABLES}
    script["memory_items"] = mem_ids

    conn = _FakeConn(script)
    pool = _FakePool(conn)

    await purge_pg(pool, retention_days=30)  # type: ignore[arg-type]

    # Exactly one execute call (the audit INSERT)
    assert len(conn.executed) == 1
    sql, args = conn.executed[0]
    assert "INSERT INTO audit_log" in sql
    assert "'system'" in sql
    assert "'brain_janitor.purge'" in sql
    # Payload is JSON-encoded with counts + total + retention_days
    assert len(args) == 1
    payload = json.loads(args[0])
    assert payload["retention_days"] == 30
    assert payload["total"] == 1
    assert payload["counts"]["memory_items"] == 1
    # Every PURGE_TABLES key shows up in counts (even zero ones)
    for table, _ in PURGE_TABLES:
        assert table in payload["counts"]


@pytest.mark.asyncio
async def test_purge_pg_skips_audit_when_nothing_purged():
    """No rows deleted → no audit_log noise."""
    conn = _FakeConn({t: [] for t, _ in PURGE_TABLES})
    pool = _FakePool(conn)

    purged = await purge_pg(pool, retention_days=30)  # type: ignore[arg-type]

    assert all(len(v) == 0 for v in purged.values())
    assert conn.executed == []  # no audit INSERT


@pytest.mark.asyncio
async def test_purge_pg_accepts_custom_retention():
    """retention_days flows into the $1 timedelta arg correctly."""
    conn = _FakeConn({t: [] for t, _ in PURGE_TABLES})
    pool = _FakePool(conn)

    await purge_pg(pool, retention_days=90)  # type: ignore[arg-type]

    for _, args in conn.fetched:
        assert args == (timedelta(days=90),)


def test_pg_url_strips_sqlalchemy_async_prefix():
    """`postgresql+asyncpg://` and `postgres+asyncpg://` are both normalised."""
    assert (
        _pg_url("postgresql+asyncpg://u:p@h:5432/db")
        == "postgresql://u:p@h:5432/db"
    )
    assert (
        _pg_url("postgres+asyncpg://u:p@h:5432/db")
        == "postgresql://u:p@h:5432/db"
    )
    # Already-plain URL is untouched
    assert _pg_url("postgresql://u:p@h:5432/db") == "postgresql://u:p@h:5432/db"


def test_purge_tables_covers_six_brain_entities():
    """The 6 tables that gained deleted_at in migration 0017 are all listed."""
    expected = {
        "memory_items",
        "conversations",
        "messages",
        "team_messages",
        "tasks",
        "contacts",
    }
    assert {t for t, _ in PURGE_TABLES} == expected


def test_memory_items_comes_first_in_purge_tables():
    """memory_items must be first so its UUIDs reach Qdrant/Neo4j purgers."""
    assert PURGE_TABLES[0][0] == "memory_items"


# ---------------------------------------------------------------------------
# Regression tests: asyncpg timedelta requirement (bug fix 2026-05-24)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purge_pg_passes_timedelta_not_string_to_asyncpg():
    """$1 bound to the DELETE query MUST be a datetime.timedelta, not a str.

    asyncpg serialises timedelta to the Postgres interval wire type.
    Passing a bare Python str (e.g. "30 days") causes::

        'str' object has no attribute 'days'

    at query-execution time because asyncpg tries to call ``.days`` on the
    bound argument when encoding the interval codec.

    This test reproduces the production regression that caused every
    brain-janitor daily run to abort with::

        brain_janitor.run_failed: invalid input for query argument $1:
        '30 days' ('str' object has no attribute 'days')

    since 2026-05-18.
    """
    conn = _FakeConn({t: [] for t, _ in PURGE_TABLES})
    pool = _FakePool(conn)

    await purge_pg(pool, retention_days=30)  # type: ignore[arg-type]

    assert conn.fetched, "expected at least one DELETE query"
    for sql, args in conn.fetched:
        assert len(args) == 1, f"expected exactly one $1 arg, got {args!r}"
        arg = args[0]
        assert isinstance(arg, timedelta), (
            f"$1 must be datetime.timedelta, got {type(arg).__name__!r}: {arg!r}. "
            "asyncpg requires a timedelta for interval-typed parameters; "
            "passing a plain str causes 'str' object has no attribute .days'."
        )
        assert arg.days == 30, f"expected 30 days, got {arg.days}"


@pytest.mark.asyncio
async def test_purge_pg_timedelta_uses_correct_retention_days():
    """retention_days value flows through to the timedelta correctly."""
    conn = _FakeConn({t: [] for t, _ in PURGE_TABLES})
    pool = _FakePool(conn)

    await purge_pg(pool, retention_days=90)  # type: ignore[arg-type]

    for _sql, args in conn.fetched:
        assert len(args) == 1
        assert isinstance(args[0], timedelta)
        assert args[0].days == 90
