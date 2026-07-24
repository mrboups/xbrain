# Phase 26 — Deferred / Out-of-Scope Items

Discovered during execution but NOT caused by the current plan's changes. Logged per the
executor scope boundary (pre-existing failures in unrelated files are not fixed here).

## Pre-existing test failure — unrelated to 26-02

- **Test:** `apps/memory-api/tests/test_github_sync.py::test_sync_repo_multi_chunk_ids`
- **Discovered during:** plan 26-02 regression smoke (`pytest -m "not integration"`).
- **Symptom:** uuid5-determinism assertion mismatch — the item id the sync path produces
  (`e8b04edb-605f-5497-89ae-9fc177372eb8`) differs from the id the test recomputes from
  `uuid5(GITHUB_SYNC_NS, "o/r:multi.py:msha:{chunk_idx}")`
  (`4001b7dd-b8fd-5387-a0d9-14f519203d59`). The chunk-id key format in
  `app/services/github_sync.py` and the test's expected key have diverged.
- **Why out of scope:** neither `app/services/github_sync.py` nor
  `tests/test_github_sync.py` is touched by plan 26-02 (this plan touches only
  `alembic/versions/0028_boards.py`, `app/models/board.py`, `app/config.py`,
  `app/repos/boards.py`, `app/routes/board_helpers.py`, `app/routes/boards.py`,
  `app/main.py`, `tests/test_board_token.py`). The failure predates this work.
- **Action:** none taken. Belongs to the GitHub-sync surface (quick-260607-267 lineage),
  not the collaborative board.
