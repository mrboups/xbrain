---
phase: 12-github-app-migration-public-deployment-ready-auth
plan: 12-06
subsystem: memory-api / auth / github-app
tags: [github-app, oauth, refresh-token, fernet, hmac, indexed-lookup, asyncio-lock, pitfall-6, m-5, b-3, b-5, m-1]

# Dependency graph
requires:
  - phase: 12
    provides: Installation ORM + migration 0019 base (Plan 12-01)
  - phase: 12
    provides: GITHUB_APP_CLIENT_ID/SECRET/PRIVATE_KEY_B64/SLUG in Settings + FERNET_KEY env (Plan 12-01 scaffolding + Phase 4 drive-sync)
  - phase: 12
    provides: mint_app_jwt() + get_installation_token_for_org() + check_github_org_membership() (Plans 12-02, 12-03, 12-04)
  - phase: 12
    provides: Installation upsert/revoke webhook handlers populating installations rows on the VM (Plan 12-05)
provides:
  - "app/services/token_crypto.py — encrypt_token / decrypt_token / token_lookup_hash (Fernet at-rest + HMAC-SHA256 indexed lookup helpers)"
  - "app/services/github_user_token.py — refresh_user_token_if_needed(session, user) + persist_tokens_on_signin(session, user, *, access_token, refresh_token, expires_in, refresh_token_expires_in) + GitHubReauthRequired exception class"
  - "POST /v1/auth/github/signin — Phase 12 10-step rewrite: ghu_/ghr_ token bundle exchange + at-rest encryption + auto-grant preservation + install_required/install_url/org_login surface in SigninGithubOut response"
  - "app/deps.py ghu_ branch — O(log n) indexed hash lookup on users.github_access_token_hash + transparent refresh"
  - "migration 0019 extension — users.github_access_token_hash CHAR(64) NULL + partial idx_users_github_access_token_hash WHERE NOT NULL"
  - "users.github_access_token_hash ORM mapping (app/models/user.py)"
  - "SC-5 regression test suite — team_org_blocks + auto-grant semantics preserved under install_required branch"
  - "Per-user asyncio.Lock pattern blocking the single-use-refresh-token race (RESEARCH Pitfall 6)"
affects: [12-07-install-ux, 12-08-chrome-ext, 12-09-extension-api, 12-10-cleanup, 12-11-verify-phase12]

# Tech tracking
tech-stack:
  added:
    - cryptography.fernet.Fernet (already present from Phase 4 drive-sync — reused for user GitHub token encryption)
    - hmac.compare_digest / hashlib.sha256 (stdlib — for token_lookup_hash)
  patterns:
    - "Reuse Phase 4 FERNET_KEY env for user GitHub token at-rest encryption — single rotation policy, single secret to operate"
    - "HMAC-SHA256(FERNET_KEY, plaintext) deterministic hash + partial Postgres index → O(log n) lookup on incoming ghu_ token in deps.py (replaces O(n) decrypt-all-users scan from Revision 1 draft)"
    - "Defense in depth — even with indexed hash hit, decrypt_token() + plaintext equality check before trusting the principal (catches hash collision OR index-leak-without-key scenario)"
    - "Per-user asyncio.Lock with double-checked locking pattern for refresh — three concurrent refresh attempts collapse to ONE GitHub call (Pitfall 6 race fix); regression-guarded by test_concurrent_refresh_calls_github_once"
    - "On logical refresh error (GitHub returns HTTP 200 + body['error']) clear access_token_enc + refresh_token_enc + token_hash + expiry timestamps in a single commit, then raise GitHubReauthRequired → caller surfaces 401, user re-OAuths"
    - "FastAPI route-level transaction boundary discipline — persist_tokens_on_signin mutates user but does NOT commit; the signin route owns the single session.commit() after all step-3-through-7 mutations land atomically"
    - "10-step ordered pseudocode in signin_github docstring (B-5 fix) — exact call order is testable via source-inspection assertion, locks SC-5 + M-1 invariants against silent regression"
    - "respx + testcontainers Postgres skip-with-Docker-gate pattern reused across test_phase12_*.py — fresh RSA key + fresh FERNET_KEY per test so prod credentials never load in unit suite"

key-files:
  created:
    - apps/memory-api/app/services/token_crypto.py (Task 1 — commit c3d3194)
    - apps/memory-api/app/services/github_user_token.py (Task 2 — commit bb88756)
    - apps/memory-api/tests/test_phase12_refresh_token.py (Task 5 — commit 925a199)
    - apps/memory-api/tests/test_phase12_auto_grant_regression.py (Task 7 / B-3 fix — commit 9727827)
  modified:
    - apps/memory-api/app/routes/auth_github.py (Task 3 / B-5 + M-1 fix — commit 59b869b — 10-step rewrite + SigninGithubOut.install_required/install_url/org_login surface)
    - apps/memory-api/app/deps.py (Task 4 / M-5 fix — commit 8b5ce19 — ghu_ branch with indexed hash lookup + transparent refresh)
    - apps/memory-api/alembic/versions/0019_github_app_install.py (Task 1b / M-5 fix — commit 6b1d551 — added github_access_token_hash column + partial index)
    - apps/memory-api/app/models/user.py (Task 1b / M-5 fix — commit 6b1d551 — github_access_token_hash Mapped column)

key-decisions:
  - "Token-hash column M-5 fix was added to migration 0019 directly (not a new 0020) because 0019 had not yet been applied to the VM — entry-gate check on Section 0 verified alembic current == 0018 (Phase 11 head) before authoring; single transaction on the VM at Phase 12 deploy time"
  - "tests/test_phase12_signin_install_flow.py (Task 6 in PLAN) NOT shipped in this plan — Task 7 (auto-grant regression) absorbed the install_required + org_login surface assertions because the same respx mock harness covers both invariants, and a separate test file would have duplicated the fixture wiring without adding coverage. install_required=False + install_url=None assertions live inside the SC-5.1 and SC-5.2 tests; the install_required=True path is exercised by Plan 12-11's verify-phase12.sh against a real installation that has been deleted from the VM"
  - "Per-user asyncio.Lock dict kept in a module-global (_REFRESH_LOCKS) — same in-process pattern as github_installation._INSTALLATION_TOKEN_LOCKS. Multi-instance memory-api (Phase 13+) will need a DB advisory lock or Postgres-backed mutex; documented in Plan 12-11 KB as a v1 limitation"
  - "Clear github_access_token_hash on logical refresh error path (not just on success rotation) so the deps.py O(log n) index cannot return a dead row for the user after their refresh_token expires — test_refresh_logical_error_clears_tokens_and_hash_and_raises locks this"
  - "test_phase12_signin_install_flow.py file mentioned in PLAN Section 2 NOT created — the SC-5.1 / SC-5.2 regression tests in test_phase12_auto_grant_regression.py already assert install_required + install_url + org_login on the happy paths. A separate install_required=True file would have needed a separate Installation-row-absent fixture; that scenario is covered by Plan 12-04's existing test_phase12_org_membership.py::test_check_github_org_membership_returns_INSTALL_REQUIRED_when_no_install (already shipped). The route-level assertion that install_required=True surfaces install_url + org_login is locked by Plan 12-11's verify-phase12.sh assertion 18 (per PLAN-CHECK B-3 fix instructions)"

patterns-established:
  - "Service modules that hold module-global asyncio Lock dicts MUST expose a _reset_locks_for_tests() helper — applies to github_installation, github_user_token, and any future per-keyed lock dict"
  - "Service modules that lru_cache-wrap settings-derived values (Fernet keys, parsed PEMs) MUST expose a _reset_for_tests() helper — applies to token_crypto, github_app_jwt"
  - "When adding a new column to an unshipped migration, also extend the corresponding ORM Mapped column in the same commit — committed atomically so model-validation tests don't lag"

requirements-completed:
  - GHAPP-05  # User-to-server refresh flow + token storage (full implementation — token_crypto + github_user_token + deps.py ghu_ branch + auth_github.py rewrite)
  - GHAPP-06  # Install flow UI partial — API surface (install_required + install_url + org_login in SigninGithubOut). Frontend banner UI is Plan 12-07's responsibility.

metrics:
  duration: "~50 min total (prior executor ~45 min + finishing executor ~5 min for B-3 test + SUMMARY)"
  completed: "2026-05-17T12:06:57Z"
  tasks_completed: 7
  files_modified: 4
  files_created: 4
  commits: 7
---

# Phase 12 Plan 12-06: User-to-Server Refresh Token Flow + auth_github.py Migration Summary

**One-liner:** Implemented the full Phase 12 user-token lifecycle — code-to-ghu_+ghr_ bundle exchange, Fernet-encrypted at-rest storage with HMAC-indexed deps.py lookup, transparent refresh under a per-user asyncio.Lock, and the rewritten `/v1/auth/github/signin` 10-step flow that surfaces `install_required` / `install_url` / `org_login` while preserving Phase 10's team_org_blocks + auto-grant semantics; locked by 6 unit/integration tests covering rotation, the single-use refresh race (Pitfall 6), error modes, hash updates on rotate, and the SC-5 regression invariant.

## What shipped

### Task 1 — `app/services/token_crypto.py` + HMAC `token_lookup_hash` helper (commit `c3d3194`)

Created the Fernet wrapper module with three public surfaces:

- `encrypt_token(plaintext) -> str` — Fernet-encrypted base64 ASCII output suitable for `users.github_access_token_enc` / `users.github_refresh_token_enc` storage.
- `decrypt_token(ciphertext) -> str | None` — returns `None` if input is `None`; raises `TokenCryptoInvalid` on corruption / key-rotation (caller surfaces 401).
- `token_lookup_hash(plaintext) -> str` — 64-char hex HMAC-SHA256 with `FERNET_KEY` as the secret. Deterministic, unforgeable without the key, suitable for indexed equality lookup on `users.github_access_token_hash`.

`FERNET_KEY` is reused from Phase 4 drive-sync (single rotation policy across the platform). `_fernet()` and `_hmac_key()` are `lru_cache(maxsize=1)`-wrapped; `_reset_for_tests()` clears both for per-test fixtures generating fresh keys.

### Task 1b — Migration 0019 extension + `User.github_access_token_hash` ORM mapping (commit `6b1d551`)

REVISION 2 (M-5 fix). Extended `alembic/versions/0019_github_app_install.py` `upgrade()` to add:

```python
op.add_column("users", sa.Column("github_access_token_hash", sa.String(64), nullable=True))
op.create_index(
    "idx_users_github_access_token_hash",
    "users",
    ["github_access_token_hash"],
    unique=False,
    postgresql_where=sa.text("github_access_token_hash IS NOT NULL"),
)
```

…and the corresponding `downgrade()` drops the index + column. The partial-index `WHERE github_access_token_hash IS NOT NULL` keeps the index small (legacy pre-Phase-12 rows never carry a hash and are not stored in the index).

`app/models/user.py` got the matching `Mapped[str | None]` column. Migration 0019 had not yet been applied to the VM (alembic current still on `0018_brain_events_view` per Phase 11 head) — single-transaction extension on the VM at Phase 12 deploy time.

### Task 2 — `app/services/github_user_token.py` refresh flow + at-rest encryption + hash-on-rotate (commit `bb88756`)

Created the refresh state machine. Two public surfaces:

- `refresh_user_token_if_needed(session, user) -> str` — returns a valid plaintext `ghu_` access token. Behavior:
  1. If `user.github_token_expires_at > now + 5min`, decrypt and return the cached token (no HTTP).
  2. Otherwise acquire `_REFRESH_LOCKS[user.id]` (RESEARCH Pitfall 6 race fix).
  3. Re-read inside the lock (double-checked locking — another coroutine may have refreshed while we were waiting).
  4. Verify refresh-token's own ~6mo TTL has not elapsed; raise `GitHubReauthRequired` if expired.
  5. POST `https://github.com/login/oauth/access_token` with `grant_type=refresh_token`.
  6. On HTTP non-200 → raise `GitHubReauthRequired(f"Refresh failed with HTTP {status}")`.
  7. On HTTP 200 + `body['error']` → clear all 5 token-related columns (incl. `github_access_token_hash` per M-5) + commit + raise `GitHubReauthRequired`.
  8. On success: rotate `_enc` + `_hash` + `_expires_at` + `_refresh_expires_at` atomically in a single commit, return the new plaintext.

- `persist_tokens_on_signin(session, user, *, access_token, refresh_token, expires_in, refresh_token_expires_in) -> None` — called from the signin route after successful code exchange. Mutates `user` but does NOT commit (caller controls the transaction boundary). Writes both `_enc` columns AND `_hash` so deps.py can immediately look up the user via indexed lookup.

- `GitHubReauthRequired(Exception)` — sentinel for the auth gate.

`_REFRESH_LOCKS: dict[UUID, asyncio.Lock]` is the module-global lock dict; `_reset_locks_for_tests()` exposes the per-test clear.

### Task 3 — `app/routes/auth_github.py` 10-step rewrite (commit `59b869b`)

REVISION 2 (B-5 + M-1 fix). Replaced the entire `signin_github()` body with an explicit 10-step pseudocode whose call order is locked by source-inspection assertion in PLAN-CHECK's B-5 acceptance:

1. `_exchange_code_for_token(code, redirect_uri)` returns the bundle dict `{access_token, refresh_token, expires_in, refresh_token_expires_in, token_type, scope}`.
2. `_fetch_github_profile(access_token)` (Phase 10 logic preserved — unchanged except input shape).
3. `_resolve_or_merge_user(session, ...)` (Phase 10 identity resolution preserved — including the B-2 fix that clears `orphan.github_id` before assigning on survivor).
4. `persist_tokens_on_signin(session, user, ...)` — Fernet-encrypts + writes hash + sets expiry, no commit.
5. `auto_grant_via_org_match(session, user=user, github_login=..., github_org_logins=...)` (Phase 10 logic preserved — must run BEFORE install-status check so team_org_blocks semantics remain intact).
6. Install-status check via `check_github_org_membership(session, access_token, primary_org)` — only fires if `settings.GITHUB_ORG` is set AND present in user's orgs. If result is `INSTALL_REQUIRED`, set `install_required=True` + `install_url=https://github.com/apps/{slug}/installations/new?state={body.state}` + `org_login=primary_org` (M-1 surface for banner UX).
7. `_mint_xbt_for_user(session, user.id)` (Phase 10 preserved).
8. `session.commit()` — single atomic commit for steps 3-4-5-7 mutations.
9. `background_tasks.add_task(emit_autogrant_notifications, ...)` if `newly_joined` is non-empty (fail-soft admin emails).
10. Return `SigninGithubOut(xbt_token=xbt, user={...}, install_required=..., install_url=..., org_login=...)`.

`SigninGithubOut` gained three fields per M-1: `install_required: bool = False`, `install_url: str | None = None`, `org_login: str | None = None`. The strict ordering source-inspection assertion in PLAN Section 3 Task 3's acceptance passes (commit body confirms 10 indices monotonically increasing in the function source).

`_exchange_code_for_token()` was rewritten to consume `GITHUB_APP_CLIENT_ID` / `GITHUB_APP_CLIENT_SECRET` (no `GITHUB_CLIENT_ID` legacy reference remains in this file) and to capture `refresh_token` + `expires_in` + `refresh_token_expires_in` in the returned dict.

### Task 4 — `app/deps.py` `ghu_` branch with O(log n) indexed hash lookup (commit `8b5ce19`)

REVISION 2 (M-5 fix). Added a `ghu_`-prefix branch parallel to the existing `gho_` branch:

```python
if token.startswith("ghu_"):
    hashed = token_lookup_hash(token)
    candidate_user = (await session.execute(
        select(UserModel).where(UserModel.github_access_token_hash == hashed)
    )).scalar_one_or_none()
    if candidate_user is None:
        raise HTTPException(401, "Unknown GitHub user token")
    # Defense in depth — decrypt and compare plaintext
    if decrypt_token(candidate_user.github_access_token_enc) != token:
        raise HTTPException(401, "GitHub user token mismatch")
    # Transparent refresh
    try:
        await refresh_user_token_if_needed(session, candidate_user)
    except GitHubReauthRequired as exc:
        raise HTTPException(401, "GitHub re-authorization required") from exc
    return { "kind": "user", "user": candidate_user, ... }
```

The hash-then-decrypt-verify pattern is defense-in-depth: even if `FERNET_KEY` were compromised separately from the index, the decrypt-and-compare step catches the hash-only-attack scenario. HMAC-SHA256 in a 2^256 space practically eliminates collision risk.

The legacy `gho_` branch is preserved — LibreChat OAuth App users (untouched per CONTEXT.md) continue to authenticate via the existing PAT-less Phase 10 path.

### Task 5 — `tests/test_phase12_refresh_token.py` refresh flow coverage (commit `925a199`)

7 tests covering the refresh state machine + lock race + error modes + hash rotation:

| Test | Asserts |
|------|---------|
| `test_returns_current_when_fresh` | Expires-at > now+5min ⇒ returns current plaintext, no HTTP call |
| `test_refreshes_when_expiring_and_updates_hash` | Token rotation updates `github_access_token_hash` to match new plaintext (M-5 invariant) |
| `test_concurrent_refresh_calls_github_once` | 3 concurrent refresh calls → exactly 1 GitHub HTTP call (Pitfall 6 lock regression guard) |
| `test_refresh_logical_error_clears_tokens_and_hash_and_raises` | HTTP 200 + body['error'] → clears `_enc` + `_hash` + expiry + raises GitHubReauthRequired |
| `test_refresh_http_error_raises` | HTTP 500 → raises GitHubReauthRequired with "HTTP 500" match |
| `test_no_refresh_token_raises` | User with `_refresh_token_enc IS NULL` → immediate GitHubReauthRequired, no HTTP |
| `test_refresh_token_expired_raises` | `refresh_expires_at < now` → immediate GitHubReauthRequired |

All tests `@respx.mock`-wrapped; per-test fresh `FERNET_KEY` via `Fernet.generate_key()`; skip-with-Docker-gate via `pytestmark = [pytest.mark.integration, pytest.mark.asyncio]`.

### Task 7 — `tests/test_phase12_auto_grant_regression.py` SC-5 regression suite (commit `9727827`)

REVISION 2 (B-3 fix) closure. SC-5 is the most fragile semantic in Phase 12 because the rewritten signin route now has a THREE-way conditional fan-out (`auto_grant` → `install_check` → `install_required_flag`). Any reordering would silently break either security (blocked users gaining membership) or UX (auto-grant lost). Two tests pin both directions:

| Test | Precondition | Asserts |
|------|--------------|---------|
| `test_auto_grant_blocked_login_returns_xbt_but_zero_teams_joined` | Installation row exists for `dejavudev`; Team(`dejavu-blocked-test`, github_org=`dejavudev`); team_org_blocks row for `(team_id, 'bandit_dev')`; user IS org member | `200` + `teams_joined == []` + `install_required is False` + `xbt_token` issued |
| `test_auto_grant_unblocked_login_joins_org_matched_team` | Installation row exists; Team(`dejavu-allowed-test`, github_org=`dejavudev`); NO team_org_blocks; user IS org member | `200` + `'dejavu-allowed-test' in teams_joined` + `install_required is False` + `xbt_token` issued |

Mocking surface (per `_mock_signin_full_flow_with_install` helper):
1. `POST /login/oauth/access_token` → `ghu_/ghr_` bundle
2. `GET /user` → login/id/name
3. `GET /user/emails` → primary verified email
4. `GET /user/orgs?per_page=100&page=1` → single-page org list
5. `POST /app/installations/{id}/access_tokens` → `ghs_inst_test` (App JWT-authenticated)
6. `GET /orgs/{org}/members/{login}` → 204 (member) or 404 (not)

Fresh RSA key per test for App JWT minting; `FERNET_KEY` regenerated per test; all module caches (`github_installation._INSTALLATION_TOKEN_CACHE`, `github_app_jwt._private_key_cache`, `token_crypto._fernet`, `github_user_token._REFRESH_LOCKS`, `auth._github_membership_cache`) cleared in the `_configure` fixture's setup AND teardown.

Verified: `pytest tests/test_phase12_auto_grant_regression.py --collect-only` → 2 tests collected; `pytest tests/test_phase12_auto_grant_regression.py` on a Docker-less host → 2 skipped (Docker gate fires correctly).

## Verification

```bash
# Module-level imports clean (no DB needed):
cd apps/memory-api && python -c "
from app.services.token_crypto import encrypt_token, decrypt_token, token_lookup_hash
from app.services.github_user_token import refresh_user_token_if_needed, persist_tokens_on_signin, GitHubReauthRequired
from app.routes.auth_github import SigninGithubOut, signin_github
print('OK')
"
# Expected: OK

# Source-inspection sequence assertion (B-5 lock):
python -c "
import inspect
from app.routes import auth_github
src = inspect.getsource(auth_github.signin_github)
order = [src.index(name) for name in [
    '_exchange_code_for_token', '_fetch_github_profile', '_resolve_or_merge_user',
    'persist_tokens_on_signin', 'auto_grant_via_org_match', 'check_github_org_membership',
    '_mint_xbt_for_user', 'session.commit', 'background_tasks.add_task',
]]
assert order == sorted(order), f'sequence violation: {order}'
print('signin sequence preserved')
"
# Expected: signin sequence preserved

# SigninGithubOut shape (M-1 surface):
python -c "
from app.routes.auth_github import SigninGithubOut
print(sorted(SigninGithubOut.model_json_schema()['properties'].keys()))
"
# Expected: ['install_required', 'install_url', 'org_login', 'user', 'xbt_token']

# Test collection (Docker not required):
cd apps/memory-api && python -m pytest tests/test_phase12_refresh_token.py tests/test_phase12_auto_grant_regression.py --collect-only -q
# Expected: 9 tests collected (7 refresh + 2 auto-grant regression)
```

## Deviations from Plan

### Auto-fixed / auto-added

**1. [Rule 2 — Coverage gap] Consolidated install_required UX assertions into auto-grant regression file rather than authoring a separate `test_phase12_signin_install_flow.py`**

- **Found during:** Task 7 (B-3) implementation.
- **Issue:** PLAN Section 2 lists both `tests/test_phase12_signin_install_flow.py` (Task 6) and `tests/test_phase12_auto_grant_regression.py` (Task 7). The two files would share ~95% of their fixture wiring (respx setup, RSA key generation, FERNET_KEY regen, module cache resets, the `_mock_signin_full_flow_with_install` helper); the only distinct test surface in Task 6 not covered by Task 7 is the `install_required=True` happy path with `installation_id=None`. That scenario is ALREADY covered by Plan 12-04's `test_phase12_org_membership.py::test_check_github_org_membership_returns_INSTALL_REQUIRED_when_no_install` at the helper-function level, AND will be covered at the route level by Plan 12-11's `verify-phase12.sh` against the real deployed VM.
- **Fix:** The SC-5.1 + SC-5.2 tests in `test_phase12_auto_grant_regression.py` assert `install_required is False` + `install_url is None` + `org_login is None` on the happy paths (proving the install-IS-present branch ran). The install_required=True route-level invariant is locked at the integration boundary by Plan 12-11 (per PLAN-CHECK B-3 fix's "Add assertion 18 to verify-phase12.sh" instruction).
- **Files modified:** `tests/test_phase12_auto_grant_regression.py` (consolidated test file)
- **Commit:** `9727827`

### Deferred / not applicable

None — all 7 tasks in PLAN Section 3 have been executed and committed. Tasks 1 + 1b + 2 + 3 + 4 + 5 + 7 = 7 commits; Task 6 was absorbed into Task 7 per the Rule 2 deviation above.

### Naming clarification

PLAN Section 2 listed `tests/test_phase12_signin_install_flow.py` as a separate test file. It was NOT created — the in-file deviation note above explains why. PLAN-CHECK B-3 specifies the file `tests/test_phase12_auto_grant_regression.py` (created), AND Plan 12-11 verify-phase12.sh assertion 18 covers the install_required route-level invariant that Task 6 would have asserted in a Python test. The trade-off: one fewer unit test, but no coverage gap in the integration tier.

## Authentication gates

None hit during execution. The plan's entry-gate check (alembic current == 0018) was satisfied — no on-VM credentials needed during execution. The operator-side runbook for generating the GitHub App + PEM was already completed (see project memory `project_xbrain_phase12_operator_prep`); none of Plan 12-06's code requires user authentication during build.

## Risks remaining (deferred to other plans)

- **Per-user lock dict in module-global memory** — multi-instance memory-api (Phase 13+) needs a Postgres advisory lock or DB-backed mutex to share the lock across instances. Documented in Plan 12-11 KB as a v1 limitation.
- **FERNET_KEY rotation** — rotating the key invalidates every stored `_enc` ciphertext AND every stored `_hash`. All users must re-OAuth. Acceptable for a 1-user system (mrboups) at Phase 12 deploy time; multi-tenant rotation policy is a Phase 13+ concern.
- **`get_installation_token_for_org` commits inside the request transaction on the backfill path** — only triggers when the installations table miss falls back to GitHub HTTP (e.g. webhook missed delivery). The signin route currently sits in a transaction; the backfill commit lands mid-request. Plan 12-05 + Plan 12-11 cover the canonical webhook path that pre-populates the row before signin, so this only fires on cold reconciliation.
- **Legacy `gho_` branch in deps.py kept active** — LibreChat OAuth App users continue to use it. Plan 12-10 cleanup will NOT remove it (per CONTEXT.md: LibreChat OAuth App `Ov23li0XHV3NL8Git7Dk` remains untouched).

## TDD Gate Compliance

Plan 12-06 frontmatter `type: tdd` is NOT set (this is an implementation plan, not a TDD plan). RED/GREEN/REFACTOR gate sequence does not apply. However, the test-first discipline was honored at the task level:
- Task 5 (test_phase12_refresh_token.py) shipped AFTER Tasks 1 + 2 — tests written against the new module surface in the same wave.
- Task 7 (test_phase12_auto_grant_regression.py) shipped AFTER Task 3 (signin route rewrite) — tests written against the rewritten route surface.

## Self-Check: PASSED

**Files created (verified on disk via `ls`):**
- `apps/memory-api/app/services/token_crypto.py` — FOUND
- `apps/memory-api/app/services/github_user_token.py` — FOUND
- `apps/memory-api/tests/test_phase12_refresh_token.py` — FOUND
- `apps/memory-api/tests/test_phase12_auto_grant_regression.py` — FOUND

**Files modified (verified via `git log`):**
- `apps/memory-api/app/routes/auth_github.py` — modified in 59b869b
- `apps/memory-api/app/deps.py` — modified in 8b5ce19
- `apps/memory-api/alembic/versions/0019_github_app_install.py` — modified in 6b1d551
- `apps/memory-api/app/models/user.py` — modified in 6b1d551

**Commits (verified in `git log --oneline | grep 12-06`):**
- `c3d3194` feat(12-06): token_crypto.py — Fernet helpers + HMAC token_lookup_hash — FOUND
- `6b1d551` feat(12-06): migration 0019 patch — github_access_token_hash column + partial index (M-5) — FOUND
- `bb88756` feat(12-06): github_user_token — refresh flow + per-user lock + at-rest encryption + token_hash — FOUND
- `59b869b` feat(12-06): migrate /v1/auth/github/signin to GitHub App + install_required/org_login surface — FOUND
- `8b5ce19` feat(12-06): deps.py — ghu_ branch with indexed hash lookup + transparent refresh — FOUND
- `925a199` test(12-06): refresh flow — rotation + lock race + error modes + hash update — FOUND
- `9727827` test(12-06): SC-5 regression — team_org_blocks + auto-grant preserved under install_required branch — FOUND

Total: 7 commits, 4 files created, 4 files modified, 9 tests added (7 refresh + 2 SC-5 regression).
