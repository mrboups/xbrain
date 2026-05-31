---
quick_id: 260531-x8i
status: complete
commits:
  part_a: 4d7d3ad
  part_b: ca3e620
---

# Quick Task 260531-x8i — SUMMARY

## What changed

| File | Change |
|------|--------|
| `apps/memory-api/app/routes/internal.py` | NEW — `GET /internal/resolve-team-scope` endpoint; resolves sub via source_user_id, email:-prefix, or bare email; follows merge pointer; returns `{team_scope, user_id}` nulls on unknown; never 500. |
| `apps/memory-api/app/main.py` | Modified — import `internal` router + `app.include_router(internal.router, prefix="/v1", tags=["internal"])` wired after health router. |
| `apps/memory-api/tests/test_internal_resolve_team_scope.py` | NEW — 3 integration tests (require Docker): email-sub resolves to slug, unknown sub returns nulls, bridge-kind JWT accepted. Marked `@pytest.mark.integration`. |
| `apps/librechat-bridge/app/memory_api_client.py` | Modified — added `resolve_team_scope(self, *, sub: str) -> str | None` method with `@retry` decorator (same pattern as siblings) + internal try/except to convert final failure to None. |
| `apps/librechat-bridge/app/mongo_watcher.py` | Modified — replaced Phase-1 stub body of `resolve_team_scope` with module-level `_team_scope_cache` + `_TEAM_SCOPE_TTL = 300.0`, real per-user resolution via client, fallback without caching for unknown users, full try/except fail-soft wrapper. |
| `apps/librechat-bridge/tests/test_resolve_team_scope.py` | NEW — 5 unit tests (no Docker needed): slug returned and cached, None falls back to default, TTL cache hit (call_count==1), fallback not cached, exception returns default without raising. |

## Final registered route path

```
GET /v1/internal/resolve-team-scope?sub=<str>
```

The route is declared as `/internal/resolve-team-scope` in `routes/internal.py` and
included with `prefix="/v1"` in `main.py` — matching the exact convention used by all
other routers in the app.

## Commit hashes

- Part A (memory-api): `4d7d3ad` — `feat(memory-api): add /v1/internal/resolve-team-scope for bridge team resolution (260531-x8i)`
- Part B (librechat-bridge): `ca3e620` — `fix(librechat-bridge): resolve real per-user team_scope instead of default stub (260531-x8i)`

## Verification status

### librechat-bridge tests — RUN, 5/5 PASSED

```
apps/librechat-bridge/tests/test_resolve_team_scope.py .....  [100%]
5 passed, 1 warning in 0.73s
```

Tests ran locally with no Docker required (pure unit tests using AsyncMock).

### memory-api tests — SKIPPED (Docker not available in local env)

```
apps/memory-api/tests/test_internal_resolve_team_scope.py sss  [100%]
3 skipped, 1 warning in 0.09s
```

The 3 tests are correctly marked `@pytest.mark.integration` and skipped when Docker
is unavailable (same behaviour as all other integration tests in that suite). They
will run in the Docker Compose test environment.

### py_compile — ALL 6 FILES PASS

All 6 created/modified files pass `python -m py_compile` with no syntax errors.
