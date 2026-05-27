# Phase 13 Test Fix Notes

**Date:** 2026-05-24
**Scope:** Two failing tests in `apps/memory-api/tests/`

---

## Fix 1 — test_health.py::test_healthz_returns_200_without_db

**Failure:** `ModuleNotFoundError: No module named 'qdrant_client'`

**Root cause:** The test imports `app.main` which transitively imports `app.qdrant_setup`, which does `from qdrant_client import AsyncQdrantClient` at module level. The local dev/test environment did not have `qdrant-client` installed.

**Resolution:** `qdrant-client>=1.17` is already declared as a runtime dependency in `pyproject.toml`. The fix is to install it in the dev environment:

```bash
pip install "qdrant-client>=1.17"
```

Or, preferably, install the full project with dev extras:

```bash
pip install -e ".[dev]"
```

**No code changes required.** The pyproject.toml already declares the dep correctly. This was a missing `pip install` step in the dev setup.

**Installed version:** `qdrant-client==1.18.0`

---

## Fix 2 — test_tagging_contract.py::test_invalid_source_format_rejected

**Failure:** `Failed: DID NOT RAISE <class 'pydantic_core._pydantic_core.ValidationError'>`

**Root cause:** The `bad_sources` list includes `"a-b:gpt"`. The prior `SOURCE_PATTERN` was:

```
^[a-z][a-z0-9_-]*:[a-z0-9._-]+$
```

The character class `[a-z0-9_-]*` in the namespace/prefix part (before the colon) allows hyphens, so `"a-b:gpt"` was treated as valid — `a` matched `[a-z]`, `-b` matched `[a-z0-9_-]*`, `:gpt` matched `:[a-z0-9._-]+`. No ValidationError was raised.

**Intent of the test:** Source namespaces must be single-word lowercase identifiers (e.g., `librechat`, `openwebui`, `agent`, `manual`) — not hyphenated compound names. Hyphens are reserved for the value part after the colon.

**Fix:** Remove hyphen from the namespace prefix character class:

```
Before: ^[a-z][a-z0-9_-]*:[a-z0-9._-]+$
After:  ^[a-z][a-z0-9_]*:[a-z0-9._-]+$
```

The suffix part `[a-z0-9._-]+` retains hyphens, so `librechat:claude-3-5-sonnet` and `openwebui:gpt-4o` remain valid.

**Files changed:**
- `apps/memory-api/app/models/tagging.py` — `SOURCE_PATTERN` constant
- `apps/memory-api/tests/test_tagging_contract.py` — regression guard `test_source_pattern_constant_is_correct` updated to match new pattern

**Commit:** `061540b` — `fix(memory-api): tighten SOURCE_PATTERN — disallow hyphens in namespace prefix`

---

## Test Results After Fixes

```
pytest apps/memory-api/tests/ -q
109 passed, 192 skipped, 0 failed
```

- All non-integration tests pass
- Skipped tests are integration tests that require Docker (Postgres, Qdrant containers) — expected behavior
- The two previously failing tests now pass:
  - `test_health.py::test_healthz_returns_200_without_db` PASSED
  - `test_tagging_contract.py::test_invalid_source_format_rejected` PASSED
