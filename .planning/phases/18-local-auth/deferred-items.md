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

## From 18-06 (verify-phase18.sh acceptance gate)

### `tests/test_phase10_auth.py` — TWO MORE pre-existing bugs found while building the gate (7/7 fail total, not just the 1 already logged above)

- **Discovered during:** building `verify-phase18.sh`'s check (b2)/(b3) — running
  `pytest tests/test_phase10_auth.py -q` in isolation (Docker present, zero Phase 18
  files touched) reproduces `7 failed` — the 1 `memory_promotions` failure already
  logged above (`test_orphan_token_lands_on_survivor`, which calls
  `merge_user_rows()` directly) PLUS 6 more failures with a completely different
  root cause:
  1. **Stale env-var names in the test fixture.** `_github_oauth_env` (an autouse
     fixture in `test_phase10_auth.py`) sets `GITHUB_CLIENT_ID`/`GITHUB_CLIENT_SECRET`
     — but `app/routes/auth_github.py:109`'s actual gate on the GitHub App OAuth path
     is `GITHUB_APP_CLIENT_ID`/`GITHUB_APP_CLIENT_SECRET` (renamed by the Phase 12
     GitHub App migration; `GITHUB_CLIENT_ID`/`GITHUB_CLIENT_SECRET` now identify a
     *different*, unrelated OAuth App — see `app/config.py:41-48`'s own comment,
     "Do NOT confuse with GITHUB_APP_CLIENT_ID below"). Every test that POSTs to
     `/v1/auth/github/signin` gets a 503 ("GitHub App OAuth not configured on
     memory-api") instead of the expected 200.
  2. **Stale mock response shape.** Once (1) is worked around (e.g. by setting
     `GITHUB_APP_CLIENT_ID`/`SECRET` directly), the same fixture's
     `_configure_gh_router()` mocks GitHub's token-exchange response as
     `{"access_token": "gho_test_abc"}` only — but
     `_exchange_code_for_token()` (`auth_github.py:131`) now requires BOTH
     `access_token` AND `refresh_token` in the body (added by the Phase 12 rewrite,
     which persists a refresh token for the 6-month re-auth window). The mock
     predates that requirement, so every signin call 400s with "GitHub token
     exchange missing required fields".
- **Confirmed pre-existing and unrelated to Phase 18:** both bugs reproduce
  identically with zero Phase 18 files in the working tree (verified against the
  pre-Phase-18 base commit `100e6d9`); `app/config.py`'s own comment shows the
  `GITHUB_CLIENT_ID` vs `GITHUB_APP_CLIENT_ID` split predates Phase 18 entirely
  (Phase 5 vs Phase 12 vintage). Neither bug is in a file Phase 18 touches.
- **Scope:** `apps/memory-api/tests/test_phase10_auth.py` (test fixture only —
  `_github_oauth_env` needs `GITHUB_APP_CLIENT_ID`/`GITHUB_APP_CLIENT_SECRET`
  instead of the legacy names, and `_configure_gh_router()`'s mocked
  `/login/oauth/access_token` response needs a `refresh_token` field). Not touched
  by this plan (Scope Boundary rule — it is a Phase 10 test file).
- **Action:** NOT fixed here. `verify-phase18.sh`'s check (b2) instead compares the
  *set of failing test names* against this documented pre-existing-broken set (all
  7 names, from both this bug and the `memory_promotions` one above) — a genuinely
  NEW failure name would fail the gate; these 7 known ones do not. Check (b3) adds a
  gate-owned, correctly-shaped respx-mocked live exercise of the real
  `/v1/auth/github/signin` route as the independent, non-stale proof that GitHub
  sign-in still works today. Flagging `test_phase10_auth.py`'s fixture for a
  dedicated fix (rename the two settings attrs, add `refresh_token` to the mock)
  outside Phase 18.
