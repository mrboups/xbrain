# brain-janitor asyncpg interval bug fix (2026-05-24)

## One-liner

Fixed asyncpg TypeError: replaced `"30 days"` str literal with `datetime.timedelta(days=N)` so the interval wire type is correctly encoded for all daily purge runs.

## Problem

Every daily brain-janitor purge run has aborted since 2026-05-18 with:

```
brain_janitor.run_failed: invalid input for query argument $1:
'30 days' ('str' object has no attribute 'days')
```

No data was incorrectly deleted — the purge simply never ran. Soft-deleted rows in
`memory_items`, `messages`, `conversations`, `team_messages`, `tasks`, and `contacts`
accumulated without being physically removed.

## Root cause

`pg_purger.purge_pg()` built a plain Python string:

```python
interval_text = f"{int(retention_days)} days"
```

and passed it as `$1` to asyncpg's `conn.fetch(... "... deleted_at < now() - $1::interval", interval_text)`.

asyncpg's interval codec calls `.days` on the bound argument to encode it for the
Postgres wire protocol. A `str` object has no `.days` attribute — hence the error.

## Fix

`apps/brain-janitor/app/pg_purger.py`:

- Added `from datetime import timedelta`
- Replaced `interval_text = f"{int(retention_days)} days"` with `interval = timedelta(days=int(retention_days))`
- Changed query parameter from `interval_text` to `interval`
- Removed the `::interval` cast from the SQL predicate — it is no longer needed because asyncpg infers the type from the timedelta argument automatically

## Files changed

| File | Change |
|------|--------|
| `apps/brain-janitor/app/pg_purger.py` | Bug fix: str → timedelta; updated docstring and inline comment |
| `apps/brain-janitor/tests/test_pg_purger.py` | Added `from datetime import timedelta`; 2 new regression tests; updated 2 existing tests that incorrectly asserted `("30 days",)` |

## Commits

| Hash | Type | Message |
|------|------|---------|
| `cc6d4fd` | test (RED) | add failing tests reproducing asyncpg timedelta bug |
| `e31f90e` | fix (GREEN) | pass timedelta to asyncpg instead of str for interval |

## Test results

```
apps/brain-janitor/tests/test_pg_purger.py  10/10 passed
apps/brain-janitor/tests/test_neo4j_purger.py  4/4 passed
apps/brain-janitor/tests/test_qdrant_purger.py  3/3 passed
Total (excluding pre-existing test_main.py env failures): 17/17 passed
```

### Pre-existing test_main.py failures (out of scope)

`tests/test_main.py` has 3 pre-existing failures unrelated to this fix: the test file
instantiates `Settings()` which loads the project root `.env` file, which contains
extra fields not declared in the brain-janitor `Settings` model. This causes pydantic
`extra_forbidden` validation errors. These failures existed before this change and are
tracked separately (requires adding `model_config = ConfigDict(extra="ignore")` to
`Settings` or isolating the test `.env`).

## Deployment

The fix takes effect on the next `docker compose up --build brain-janitor`. No migration
or schema change is required. The next run after deploy will execute the backlog of
soft-deleted rows that accumulated since 2026-05-18.

## Self-check

- [x] `grep -rn "'30 days'" apps/brain-janitor/app/` returns 0 matches
- [x] `grep -n "timedelta" apps/brain-janitor/app/pg_purger.py` returns the import and usage
- [x] `pytest apps/brain-janitor/tests/test_pg_purger.py -q` exits 0 (10/10 passed)
- [x] Commits `cc6d4fd` and `e31f90e` exist on `worktree-agent-a281f0b4abc382d34`
