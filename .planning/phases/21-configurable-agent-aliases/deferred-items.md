# Phase 21 — Deferred / Out-of-Scope Items

Discovered during execution but NOT caused by the current task's changes. Logged
per the executor SCOPE BOUNDARY rule (do not fix pre-existing failures in
unrelated files).

## Pre-existing test breakage: `seeded_two_teams` returns ORM objects, old tests use dict access

**Discovered during:** Plan 21-02, Task 2 (running related integration tests after editing `team_chat.py` / `brain_ingest.py`).

**Symptom:** 6 tests fail with `TypeError: 'Team' object is not subscriptable`.

**Failing tests (all pre-existing, files untouched by 21-02):**
- `tests/test_team_context_cache.py::test_empty_team_returns_placeholder`
- `tests/test_team_context_cache.py::test_only_working_or_above_included`
- `tests/test_team_context_cache.py::test_cache_hit_on_second_call`
- `tests/test_team_context_cache.py::test_cache_isolates_per_team`
- `tests/test_team_context_cache.py::test_invalidate_drops_cache_entry`
- `tests/test_soft_delete_regression.py::test_team_context_cache_excludes_soft_deleted_memory_items`

**Root cause:** The `seeded_two_teams` conftest fixture returns ORM `Team`/`User`
objects (`{"team_a": <Team>, ...}`), but these older tests access them as dicts
(`team_a["slug"]`, `team_a["id"]`). The mismatch predates Phase 21 — the fixture
already returns objects at the plan-21-02 base commit `313ce0f`, and none of
`conftest.py`, `test_team_context_cache.py`, or `test_soft_delete_regression.py`
are modified by this plan (verified via `git diff --name-only 313ce0f..HEAD`).

**Fix (deferred):** Update the 6 tests to use attribute access (`team_a.slug`,
`team_a.id`) instead of subscripting. Trivial, but out of scope for 21-02 — it
touches unrelated test files and is not a regression from this plan's changes.
