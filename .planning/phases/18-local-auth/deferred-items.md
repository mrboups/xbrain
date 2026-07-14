# Deferred Items — Phase 18

Issues discovered during plan execution that are OUT OF SCOPE for the current task
(pre-existing, in an unrelated file) — logged, not fixed, per the executor's Scope
Boundary rule.

## From 18-02 (argon2id password hashing + rate limiter + xbt_ mint helper)

### `tests/test_github_sync.py::test_sync_repo_multi_chunk_ids` fails on `main`, unrelated to this plan

- **Discovered during:** full-suite regression run after Task 2 (`pytest tests/ -m "not integration"`).
- **Symptom:** `AssertionError` — the recomputed `uuid.uuid5(GITHUB_SYNC_NS, ...)` chunk id
  doesn't match the id the mocked `provider.upsert` received.
- **Scope:** `apps/memory-api/app/services/github_sync.py` /
  `apps/memory-api/tests/test_github_sync.py` — neither file is touched by 18-02
  (`git diff --name-only` confirms only `pyproject.toml` +
  `app/services/{password_hash,rate_limit,api_tokens}.py` +
  `tests/{test_password_hash,test_rate_limit}.py` changed).
- **Last touched:** commit `a34e7f7` ("fix(memory-api): github-sync idempotency key
  includes team_scope (team isolation)") — predates this plan entirely.
- **Action:** NOT fixed here (out of scope). Flagging for a dedicated fix outside
  Phase 18's local-auth scope.

## From 18-03 (register + login routes)

### `tests/test_phase10_auth.py` — 7/7 tests fail with `UndefinedTableError: relation "memory_promotions" does not exist`

- **Discovered during:** the plan's own regression check
  (`pytest tests/test_phase10_auth.py tests/test_edition_gating.py -x`).
- **Symptom:** every `test_phase10_auth.py` test that reaches `merge_user_rows`
  (`app/repos/merge.py`) fails with `sqlalchemy.exc.ProgrammingError:
  UndefinedTableError: relation "memory_promotions" does not exist` on
  `UPDATE memory_promotions SET proposed_by = $1 WHERE proposed_by = $2`.
- **Root cause:** `app/repos/merge.py` references a table named
  `memory_promotions`; the actual table created by migration `0002` (and every
  subsequent migration) is named `promotions` (see
  `alembic/versions/0002_memory_promotions.py:117`, `op.create_table("promotions", ...)`
  — the migration's own module/id is `0002_memory_promotions` but the table it
  creates is `promotions`). This is a naming drift bug in `merge.py`, unrelated to
  any Phase 18 file.
- **Scope:** `app/repos/merge.py` — not touched by this plan (`git diff --name-only`
  confirms only `app/routes/auth_local.py`, `app/main.py`, and
  `tests/test_local_auth.py` changed for 18-03).
- **Confirmed pre-existing:** `pytest tests/test_phase10_auth.py` fails identically
  when run ALONE, with none of this plan's files present in the test session.
- **Action:** NOT fixed here (out of scope — Rule 1/scope boundary: the bug is in a
  file this plan never touches). `tests/test_edition_gating.py` (the other half of
  the plan's regression check) passes cleanly (13/13). Flagging for a dedicated fix
  to `app/repos/merge.py`'s table name.
