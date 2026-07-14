---
phase: 18-local-auth
plan: 02
subsystem: auth
tags: [argon2id, argon2-cffi, limits, rate-limiting, password-hashing, xbt-token, fastapi]

# Dependency graph
requires:
  - phase: 18-local-auth (plan 01, parallel)
    provides: local_credentials table + repo (built in a sibling worktree, not read here)
provides:
  - argon2id password hashing service (hash_password/verify_password/needs_rehash) with a non-raising decoy verify for login-timing equalization
  - in-process per-key rate limiter (check_rate/enforce_rate_limit) over `limits`' MovingWindowRateLimiter + MemoryStorage
  - shared xbt_ mint helper (mint_xbt_for_user) mirroring auth_github.py's INSERT exactly, parameterized by name
  - argon2-cffi + limits declared as memory-api dependencies
affects: [18-03 (register/login/set-password routes will import all three services built here)]

# Tech tracking
tech-stack:
  added: ["argon2-cffi>=25.1.0 (argon2id KDF)", "limits>=5.8.0 (in-process rate limiting, pure-Python, no Redis)"]
  patterns: ["module-level singleton PasswordHasher()/MemoryStorage()/MovingWindowRateLimiter() constructed once at import", "decoy-hash timing equalizer for auth timing side-channels"]

key-files:
  created:
    - apps/memory-api/app/services/password_hash.py
    - apps/memory-api/app/services/rate_limit.py
    - apps/memory-api/app/services/api_tokens.py
    - apps/memory-api/tests/test_password_hash.py
    - apps/memory-api/tests/test_rate_limit.py
  modified:
    - apps/memory-api/pyproject.toml

key-decisions:
  - "PasswordHasher() constructed with NO arguments — confirmed live against installed argon2-cffi 25.1.0 that the shipped default IS the RFC_9106_LOW_MEMORY profile (m=65536, t=3, p=4), so no hand-picked cost params were needed."
  - "mint_xbt_for_user lives in a NEW app/services/api_tokens.py rather than importing auth_github.py's private _mint_xbt_for_user — keeps this plan's blast radius zero on the live GitHub sign-in path (auth_github.py is provably untouched: git diff --name-only never lists it)."
  - "rate_limit.py takes the rate-limit string as a caller-supplied argument and never imports app.config, keeping this plan disjoint from Plan 01's config.py work per the plan's explicit isolation instruction."

requirements-completed: [LAUTH-01]

# Metrics
duration: 16min
completed: 2026-07-14
---

# Phase 18 Plan 02: Local-Auth Crypto Primitives Summary

**argon2id password hashing (argon2-cffi, shipped RFC_9106_LOW_MEMORY defaults) with a decoy-hash timing equalizer, an in-process `limits`-based rate limiter, and a shared `xbt_` mint helper — the three pure building blocks Plan 03's register/login routes assemble.**

## Performance

- **Duration:** 16 min
- **Started:** 2026-07-14T01:17:56Z (base commit 100e6d9)
- **Completed:** 2026-07-14T01:33:10Z
- **Tasks:** 2/2 completed
- **Files modified:** 6 (1 modified, 5 created)

## Accomplishments
- `app/services/password_hash.py`: `hash_password`/`verify_password`/`needs_rehash` wrap `argon2.PasswordHasher()` with zero constructor overrides; `verify_decoy()` burns comparable argon2 CPU on the login route's account-absent/locked branches without ever raising — closes the user-enumeration timing oracle (T-18-02-02, D-18-06).
- `app/services/rate_limit.py`: `check_rate`/`enforce_rate_limit` over `limits`' `MovingWindowRateLimiter` + `MemoryStorage`, keyed per caller-supplied bucket + identifier (route wires it to `request.client.host`). Docstring documents the `--workers 2` per-process bypass honestly (T-18-02-03) — the DB-backed lockout (Plan 01) is the durable defense, this is spray-blunting only.
- `app/services/api_tokens.py`: `mint_xbt_for_user` reproduces `auth_github.py:350-367`'s exact INSERT shape (raw `xbt_` + `secrets.token_urlsafe(32)`, SHA-256 at rest, `team_scope=''` multi-team sentinel), parameterized by `name` so local-auth routes can pass `'local-register'`/`'local-login'` instead of the hardcoded `'github-signin'`.
- `pyproject.toml`: added `argon2-cffi>=25.1.0` and `limits>=5.8.0` — both confirmed installable via prebuilt wheels (verified locally with `pip install`; win_amd64 wheels resolved here, manylinux aarch64+x86_64 wheels confirmed by 18-RESEARCH.md for the actual deploy targets).

## Task Commits

Each task was committed atomically:

1. **Task 1: argon2id password-hash service (+ decoy) and deps** - `75e1534` (feat)
2. **Task 2: in-process rate limiter + shared xbt_ mint helper** - `11693e0` (feat)

**Plan metadata:** (this commit) `docs(18-02): complete crypto-primitives plan`

## Files Created/Modified
- `apps/memory-api/pyproject.toml` - added argon2-cffi + limits to `dependencies`
- `apps/memory-api/app/services/password_hash.py` - argon2id hash/verify/decoy/needs_rehash
- `apps/memory-api/app/services/rate_limit.py` - in-process rate limiter + enforce_rate_limit dependency helper
- `apps/memory-api/app/services/api_tokens.py` - shared xbt_ mint helper
- `apps/memory-api/tests/test_password_hash.py` - 6 pure unit tests, no Docker
- `apps/memory-api/tests/test_rate_limit.py` - 4 pure unit tests + 1 `@pytest.mark.integration` mint test (real Postgres via testcontainers)

## Decisions Made
- Confirmed live (not just from docs) that `argon2.PasswordHasher()`'s zero-arg default already equals the RFC_9106_LOW_MEMORY profile on the installed argon2-cffi 25.1.0 — no custom `m`/`t`/`p` needed, matching D-18-04's discretion note.
- Confirmed live that `limits.strategies.MovingWindowRateLimiter.hit(rate, *identifiers)` returns `True`/`False` and that different identifier tuples get independent budgets — de-risks 18-RESEARCH.md's Q3 LOW-confidence flag on exact `limits` kwargs.
- Kept `mint_xbt_for_user` as a brand-new module rather than importing/refactoring `auth_github.py`'s private `_mint_xbt_for_user`, per the plan's explicit instruction to leave that file untouched (minimizes SC#4 regression risk).

## Deviations from Plan

None — plan executed exactly as written. Both tasks' `<behavior>` bullets and `<acceptance_criteria>` were verified directly (live argon2-cffi/`limits` API calls, greps, and a real-Postgres integration test), not just written to match the plan's suggested shape.

## Issues Encountered

- `pip install -e '.[dev]'` (the plan's suggested verify command) fails locally with "Multiple top-level packages discovered in a flat-layout: ['app', 'alembic']" — this is a PRE-EXISTING local-dev-environment artifact of how `apps/memory-api`'s `Dockerfile` installs (it only copies `pyproject.toml` into the build context before `pip install -e .`, so `app`/`alembic` aren't present as sibling top-level dirs at that step; running the same command directly in `apps/memory-api/` locally does see both dirs and setuptools' auto-discovery refuses to guess). Verification was instead done by installing `argon2-cffi`/`limits` directly (`pip install "argon2-cffi>=25.1.0" "limits>=5.8.0"`, confirmed both resolve to prebuilt wheels) and running `pytest` directly against the already-importable `app` package — which is how this environment's other tests already run. Not fixed (pre-existing, out of scope for this plan; not caused by the two dependency lines added here).
- Full non-integration regression run (`pytest tests/ -m "not integration"`) surfaced ONE pre-existing failure, `tests/test_github_sync.py::test_sync_repo_multi_chunk_ids` (a `uuid5` determinism assertion, last touched by an unrelated commit `a34e7f7` well before this plan). Confirmed out of scope (`git diff --name-only` shows this plan touched only `pyproject.toml` + the 3 new `app/services/*` files + 2 new test files) and logged to `.planning/phases/18-local-auth/deferred-items.md`, not fixed.

## User Setup Required

None — no external service configuration required. Both new dependencies (`argon2-cffi`, `limits`) install from prebuilt wheels with no compiler and no Redis/external service.

## Next Phase Readiness

- Plan 03 (register/login/set-password routes) can now import `hash_password`/`verify_password`/`verify_decoy`/`needs_rehash` from `app.services.password_hash`, `enforce_rate_limit` from `app.services.rate_limit`, and `mint_xbt_for_user` from `app.services.api_tokens` — all three are tested and stable.
- No blockers. `auth_github.py` is provably untouched (verified via `git diff --name-only` after every commit), so SC#4 (Google/GitHub unchanged) carries zero risk from this plan.
- One pre-existing, unrelated test failure (`test_github_sync.py`) and one pre-existing local-dev packaging quirk (`pip install -e .` flat-layout ambiguity) are logged in `deferred-items.md` for someone outside Phase 18 to pick up — neither blocks Plan 03.

---
*Phase: 18-local-auth*
*Completed: 2026-07-14*

## Self-Check: PASSED

All created files verified present on disk; both task commit hashes (`75e1534`, `11693e0`) verified present in `git log --oneline --all`.
