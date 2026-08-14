"""A re-sync must retire what it replaced, and never more than that.

The bug this pins: chunk ids are `uuid5(team_scope:repo:path:sha:idx)`, so when
a file's content changes its sha changes, the new chunks land under new ids —
and the old sha's chunks stayed in memory_items and Qdrant forever. Same for a
file that shrank to fewer chunks (idx 3..N orphaned) and for a file deleted from
the repo. Nothing ever removed them, so every re-sync grew the store and fed
dead versions of the code back into recall.

The other half of the fix is knowing when NOT to prune. A capped run stopped
early and a failed directory listing never saw its contents: in both cases "this
walk did not see the file" does not mean "the file is gone", and sweeping on
that belief deletes live data. Those two guards get tests of their own, because
they are the ones that turn a cleanup into data loss.
"""
from __future__ import annotations

import types
import uuid

import pytest

from app.services import github_sync

pytestmark = pytest.mark.asyncio


class _Provider:
    """Records upserts and soft-deletes; never touches a database."""

    def __init__(self) -> None:
        self.upserted: list[str] = []
        self.deleted: list[str] = []

    async def upsert(self, item):
        self.upserted.append(item.id)
        return item.id

    async def mark_deleted(self, item_id, deleted_at):
        self.deleted.append(item_id)


class _Session:
    """Answers the one SELECT the pruner makes, from a canned id list."""

    def __init__(self, existing: list[str]) -> None:
        self.existing = existing
        self.queries: list[tuple[str, dict]] = []

    async def execute(self, stmt, params=None):
        self.queries.append((str(stmt), params or {}))
        ids = list(self.existing)

        class _R:
            def scalars(self_inner):
                return types.SimpleNamespace(all=lambda: ids)

        return _R()


def _chunk_id(team, repo, path, sha, idx) -> str:
    return str(
        uuid.uuid5(github_sync.GITHUB_SYNC_NS, f"{team}:{repo}:{path}:{sha}:{idx}")
    )


# ── the query itself ─────────────────────────────────────────────────────────


async def test_it_never_retires_a_chunk_this_run_just_wrote():
    """The whole safety of pruning by file: keep_ids wins over everything."""
    keep = _chunk_id("t", "o/r", "a.py", "newsha", 0)
    stale = _chunk_id("t", "o/r", "a.py", "oldsha", 0)
    session = _Session([keep, stale])

    got = await github_sync._stale_chunk_ids(
        session, team_scope="t", repo="o/r", file_path="a.py", keep_ids={keep}
    )
    assert got == [stale]


async def test_it_only_looks_at_rows_this_sync_wrote():
    """A person's own note about the repo must never be in scope."""
    session = _Session([])
    await github_sync._stale_chunk_ids(
        session, team_scope="t", repo="o/r", file_path="a.py", keep_ids=set()
    )
    sql, params = session.queries[0]
    assert "ingestion_origin" in sql and "github-sync" in sql
    assert "team_scope = :ts" in sql and params["ts"] == "t"
    assert "deleted_at IS NULL" in sql, "an already-removed row must not be re-deleted"


async def test_an_empty_walk_sweeps_nothing():
    """Belt and braces behind the caller's guard — no paths, no deletions."""
    session = _Session(["anything"])
    got = await github_sync._stale_chunk_ids(
        session,
        team_scope="t",
        repo="o/r",
        file_path=None,
        keep_ids=set(),
        seen_paths=set(),
    )
    assert got == []


# ── the guards that keep a cleanup from becoming data loss ───────────────────


def _sync_source() -> str:
    import inspect

    return inspect.getsource(github_sync.sync_repo)


def test_the_sweep_is_refused_after_an_incomplete_walk():
    """A capped run or a failed listing cannot tell 'deleted' from 'unseen'."""
    src = _sync_source()
    assert "if not capped and not walk_failed and seen_paths:" in src, (
        "the end-of-sync sweep must be guarded on BOTH capped and walk_failed — "
        "without either, an interrupted walk deletes files that still exist"
    )


def test_a_failed_directory_listing_disqualifies_the_sweep():
    src = _sync_source()
    assert "walk_failed = True" in src, (
        "a directory that could not be listed must set walk_failed, or its files "
        "look deleted to the sweep"
    )


def test_a_file_whose_chunks_all_failed_is_not_pruned():
    """Pruning after a failed rewrite would delete the good copy and leave none."""
    src = _sync_source()
    idx_prune = src.index("_stale_chunk_ids(")
    guard = src.rindex("if file_upserted > 0:", 0, idx_prune)
    assert guard < idx_prune, "the per-file prune must sit under `if file_upserted > 0`"


def test_the_summary_reports_what_it_retired():
    """An invisible deletion is the kind nobody notices going wrong."""
    src = _sync_source()
    assert '"chunks_pruned": chunks_pruned' in src
