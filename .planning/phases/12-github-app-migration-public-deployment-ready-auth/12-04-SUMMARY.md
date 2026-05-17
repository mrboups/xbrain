---
phase: 12
plan_id: 12-04
title: "Migrate org-membership check to installation token + remove GITHUB_API_PAT"
wave: 4
subsystem: memory-api / auth
tags: [github-app, installation-token, auth, migration]
requires: ["12-01", "12-02", "12-03"]
provides: ["check_github_org_membership-installation-backed", "OrgMembershipResult-enum", "github_install_required-principal-flag"]
affects: ["12-05", "12-06", "12-07"]
tech_stack:
  added: ["enum.StrEnum (Python 3.12+)"]
  patterns: ["three-state result enum", "force-refresh on 401 retry", "absolute-expiry cache TTL"]
key_files:
  created:
    - apps/memory-api/tests/test_phase12_org_membership.py
  modified:
    - apps/memory-api/app/auth.py
    - apps/memory-api/app/deps.py
    - apps/memory-api/app/routes/teams.py
    - apps/memory-api/app/routes/me_github.py
    - apps/memory-api/app/config.py
    - apps/memory-api/tests/test_onboarding_routes.py
    - .env.example
    - infrastructure/docker-compose.yml
decisions:
  - "OrgMembershipResult is StrEnum so the value serializes as a plain string when surfaced via FastAPI Pydantic response models"
  - "Cache stores absolute expiry timestamp (now + TTL) instead of insertion-time so MEMBER (300s) and INSTALL_REQUIRED (60s) entries coexist correctly"
  - "401-retry path is single-attempt (not loop) — second 401 falls through to NOT_MEMBER + structured warning log"
  - "_resolve_github_username switched to App JWT (not installation token) because /user/{id} and /users/{username}/orgs are App-level endpoints"
metrics:
  duration: "~38 min"
  completed: "2026-05-17T11:41:42Z"
  tasks_completed: 6
  files_modified: 8
  files_created: 1
  commits: 6
---

# Phase 12 Plan 12-04: GitHub App Migration — Org-Membership Check Summary

**One-liner:** Replaced long-lived `GITHUB_API_PAT` with installation-token-backed org-membership check + new `OrgMembershipResult` three-state enum (MEMBER / NOT_MEMBER / INSTALL_REQUIRED) + 401-retry path; all 9 consumer call sites migrated, env var fully removed.

## What shipped

### Task 1 — `check_github_org_membership` rewrite (commit `437058e`)

Replaced the Phase 5 `(github_token, org, server_pat)` signature with `(session, github_user_token, org)`. New behaviour:

1. Looks up the installation_id for `org` via `get_installation_token_for_org()` (hybrid DB + GitHub fallback from Plan 12-03).
2. If no installation → returns `result=INSTALL_REQUIRED` with `install_required=True` (caller surfaces install URL).
3. Else mints the installation token (`ghs_…`, cached 55min, force-refresh on 401).
4. Calls `/orgs/{org}/members/{username}` with the installation token.
5. Returns a dict with `login`, `github_id`, `email`, `name`, `is_org_member`, `install_required`, `result`.

**401 retry (M-4 fix):** If the membership endpoint returns 401 (installation token revoked between mint and use), the function calls `get_installation_token_for_org(force_refresh=True)` and retries ONCE. A second 401 falls through to `NOT_MEMBER` + a `github_app.membership_check.401_retry` warning log entry — no infinite loop.

**Cache changes:** Storage shifted from insertion-time to absolute-expiry-time so MEMBER/NOT_MEMBER (300s TTL) and INSTALL_REQUIRED (60s TTL) coexist correctly in the same dict. The shorter INSTALL_REQUIRED TTL ensures users retrying after install reach reality quickly.

### Task 2 — `deps.py` migration (commit `1fd789d`)

The `gho_` branch in `get_current_principal` now calls `check_github_org_membership(session, token, settings.GITHUB_ORG)`. Drops the `settings.GITHUB_API_PAT` guard (PAT removed by Task 3). Added `github_install_required` flag to the returned principal so route handlers (Plan 12-06) can surface the install URL to the frontend.

INSTALL_REQUIRED does NOT 403 here — Phase 12 UX policy is "sign in OK, but show install banner". The `get_team_scope` dependency still rejects non-members via the `team_members` membership check.

### Task 2b — `teams.py` migration (commit `cba1fef`) — B-1 fix

Migrated 4 endpoints + 1 helper that were calling GitHub with `settings.GITHUB_API_PAT`:

| Endpoint | New auth mechanism |
|----------|--------------------|
| `GET /v1/teams/my-teams` | `get_installation_token_for_org()` per team's `github_org`; skip teams whose org doesn't have the App installed |
| `GET /v1/teams/github-matches` | App JWT for `_resolve_github_username` + installation tokens for membership |
| `GET /v1/teams/my-github-orgs` | App JWT (non-installation endpoint) |
| `_resolve_github_username` helper | App JWT for `/user/{id}` |

**Note on `/users/{username}/orgs`:** With App JWT (not user token), GitHub returns only PUBLIC orgs. This matches the prior server PAT behaviour (the PAT only saw orgs its owner was a member of, and the test org `dejavudev` is public). Private-org case is deferred to Phase 13.

All GitHub-API soft-fail wrappers preserved — `/my-teams` NEVER fails on GitHub issues.

### Task 2c — `me_github.py` migration (commit `e358855`) — B-2 fix

The LibreChat link-github flow (`POST /v1/me/link-github`) is preserved per CONTEXT.md's "LibreChat OAuth App untouched" decision. Internal call updated:

- Now uses `check_github_org_membership(session, body.github_token, settings.GITHUB_ORG)`
- **Dropped** the redundant second `/user` API call — the new helper returns `github_id` directly (saves one round-trip per link-github)
- Added `install_required` to the response body so the frontend can prompt org admin to install the App

LibreChat client_id / client_secret unchanged. `gho_` tokens still accepted (GitHub `/user` validates either `gho_` or `ghu_` prefix).

### Task 3 — Remove `GITHUB_API_PAT` (commit `7934506`)

| File | Change |
|------|--------|
| `apps/memory-api/app/config.py` | Removed `GITHUB_API_PAT: str = ""` Settings field + deprecation comment |
| `.env.example` | Replaced active declaration with a REMOVED notice (operator runbook) |
| `infrastructure/docker-compose.yml` | Removed `GITHUB_API_PAT: ${GITHUB_API_PAT:-}` env var passthrough |

Operator action (NOT a file change): on the VM `.env`, remove `GITHUB_API_PAT=` and the legacy `GITHUB_ORG_PAT=` alias. Both are now ignored.

### Task 4 — Tests + STRICT GREP GATE (commit `6cbbc29`)

Created `tests/test_phase12_org_membership.py` with 6 cases:

| Test | Coverage |
|------|----------|
| `test_check_github_org_membership_returns_MEMBER_on_204` | Installed org + 204 |
| `test_check_github_org_membership_returns_NOT_MEMBER_on_404` | Installed org + 404 |
| `test_check_github_org_membership_returns_INSTALL_REQUIRED_when_no_installation` | App not installed |
| `test_check_github_org_membership_cached_on_second_call` | 5-min TTL hit |
| `test_check_github_org_membership_retries_on_401_with_force_refresh` | M-4 fix path |
| `test_link_github_route_with_gho_token_returns_200` | B-2 LibreChat regression |

Also removed the dead `os.environ.setdefault("GITHUB_API_PAT", "")` from `tests/test_onboarding_routes.py` (MINOR-3 cleanup).

**STRICT GREP GATE (Section 3 + Task 4 acceptance) — PASS:**

```
grep -rn 'GITHUB_API_PAT' apps/memory-api/app/    --include='*.py' | wc -l = 0
grep -rn 'GITHUB_API_PAT' apps/memory-api/tests/ --include='*.py' | wc -l = 0
```

Three of the six tests were also smoke-validated locally outside the testcontainers-Postgres harness (MEMBER, INSTALL_REQUIRED, 401-retry-with-force-refresh paths) — full pytest run is gated on Docker availability and will execute in the Phase 12 UAT pipeline.

## Verification artifacts

| Gate | Result |
|------|--------|
| `from app.auth import OrgMembershipResult; OrgMembershipResult.INSTALL_REQUIRED.value` | `'install_required'` |
| `inspect.signature(check_github_org_membership)` | `(session: AsyncSession, github_user_token: str, org: str) -> dict` |
| `inspect.signature(get_installation_token_for_org)` | includes `force_refresh: bool = False` kwarg |
| `python -m ruff check app/auth.py` | All checks passed |
| `python -m ruff check app/routes/teams.py` | 48 errors, all pre-existing B008 (FastAPI Depends idiom) |
| `python -m ruff check app/routes/me_github.py` | 4 errors, all pre-existing B008 |
| `python -m ruff check tests/test_phase12_org_membership.py` | All checks passed |
| `not hasattr(settings, 'GITHUB_API_PAT')` | `True` |
| docker-compose YAML parses | 30 services, `GITHUB_API_PAT` not in `memory-api` env keys |
| Real-call smoke (MEMBER) | PASS — installation token minted, 204 returned |
| Real-call smoke (INSTALL_REQUIRED) | PASS — no membership call after 404 from `/orgs/.../installation` |
| Real-call smoke (401 retry) | PASS — 2 mint calls, 2 members calls, final `is_org_member=True` |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 — Bug] Cache TTL semantics changed for correctness**

- **Found during:** Task 1
- **Issue:** Plan example stored `(now, result)` in the cache; the read path compared `now - ts < TTL`. With two different TTLs (300s for MEMBER, 60s for INSTALL_REQUIRED), the read path would need to know which TTL applies — but the entry doesn't say. Either the wrong TTL is applied or a separate dict per TTL is needed.
- **Fix:** Store `(expiry_unix_ts, result)` instead. Read path simply checks `expiry > now`. Single dict, two TTLs coexist correctly.
- **Files modified:** `app/auth.py`
- **Commit:** `437058e`

**2. [Rule 2 — Critical] StrEnum used instead of `(str, Enum)`**

- **Found during:** Task 1 ruff check
- **Issue:** Plan exemplified `class OrgMembershipResult(str, Enum)` which triggers `UP042` (deprecated pattern since Python 3.11 introduced `StrEnum`).
- **Fix:** Migrated to `enum.StrEnum`. Behaviour identical (`isinstance(MEMBER, str) == True` still holds — verified).
- **Files modified:** `app/auth.py`
- **Commit:** `437058e`

**3. [Rule 1 — Bug] Worktree directory discipline — initial Edits hit main repo**

- **Found during:** Task 1 acceptance gate
- **Issue:** First Edit calls used paths starting with `D:/VSC/xbrain/...` (the main repo) instead of `D:/VSC/xbrain/.claude/worktrees/agent-a1556c2f15c4db870/...` (the worktree). Edits persisted to the main repo's working tree; the worktree's `auth.py` was untouched.
- **Fix:** Copied the modified file from main to worktree (`cp`), then `git checkout -- apps/memory-api/app/auth.py` on the main repo to restore it. All subsequent Edits used the full worktree path. Recovery verified — main repo is clean, worktree has the rewrite.
- **Files modified:** `app/auth.py` (correctly placed in worktree this time)
- **Commit:** `437058e` (no impact on commit — the worktree branch is what gets merged)

### Out-of-scope discoveries (logged, not fixed)

None — the plan covered the entire `GITHUB_API_PAT` removal surface.

### Pre-existing issues (not deviations)

- `ruff` flags 48 `B008` (Depends-in-argument-defaults) in `teams.py` and 4 in `me_github.py`. These are the FastAPI idiom, not project bugs.
- `tests/test_onboarding_routes.py` has 1 pre-existing `I001` (unsorted imports) — not caused by this plan's Edit (which only deleted a line).

## Auth Gates

None — all credential management is internal (App JWT + installation tokens, both server-side).

## Known Stubs

None — every code path produced is wired end-to-end.

## Threat Flags

None — the threat surface is reduced (one fewer long-lived credential in the runtime environment).

## Self-Check: PASSED

| Claim | Verification |
|-------|--------------|
| `app/auth.py` modified | `git log --oneline main..HEAD -- apps/memory-api/app/auth.py` → `437058e` |
| `app/deps.py` modified | `git log --oneline main..HEAD -- apps/memory-api/app/deps.py` → `1fd789d` |
| `app/routes/teams.py` modified | `git log --oneline main..HEAD -- apps/memory-api/app/routes/teams.py` → `cba1fef` |
| `app/routes/me_github.py` modified | `git log --oneline main..HEAD -- apps/memory-api/app/routes/me_github.py` → `e358855` |
| `app/config.py` modified | `git log --oneline main..HEAD -- apps/memory-api/app/config.py` → `7934506` |
| `tests/test_phase12_org_membership.py` created | `ls apps/memory-api/tests/test_phase12_org_membership.py` → file exists, 358 lines |
| STRICT GREP GATE passes | `grep -rn 'GITHUB_API_PAT' apps/memory-api/{app,tests}/ --include='*.py'` → 0 matches |
| STATE.md untouched | `git log --oneline main..HEAD -- .planning/STATE.md` → empty |
| ROADMAP.md untouched | `git log --oneline main..HEAD -- .planning/ROADMAP.md` → empty |

## Unblocked downstream plans

- **12-05 (webhook handler)** — INSTALL_REQUIRED state ready to be flipped by `installation.deleted` webhook
- **12-06 (signin route)** — `principal["github_install_required"]` ready to surface in `SigninGithubOut` schema
- **12-07 (install URL UI)** — frontend can branch on `install_required` field in `/v1/me/link-github` response
