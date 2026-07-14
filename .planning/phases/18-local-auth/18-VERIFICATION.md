---
phase: 18-local-auth
verified: 2026-07-14T13:00:00Z
status: human_needed
score: 12/12 must-haves verified
overrides_applied: 0
human_verification:
  - test: "Browser UAT of register/login/set-password screens (app-site/account/{register,login,password}/index.html) against a live, zero-OAuth memory-api"
    expected: "register -> sign-in -> change-password -> re-login loop works visually in a browser; copy is English and styling matches /account/teams/"
    why_human: "Deliberately deferred by the user (18-HUMAN-UAT.md, status: partial) — the prod VM is terminated and the pages hardcode api.grooveos.app (Phase-16-owned debranding, D-01c). Backend is fully proven by 28 real-Postgres tests plus a live zero-OAuth boot harness; only the pixels/browser wiring are unverified. Carried into Phase 16 UAT per the existing plan."
---

# Phase 18: Local Auth (OSS default) Verification Report

**Phase Goal:** A self-hoster registers and signs in with email+password, ZERO external OAuth setup; the resulting principal is indistinguishable downstream from existing ones (SC#1..SC#6).
**Verified:** 2026-07-14T13:00:00Z
**Status:** human_needed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | SC#1 — fresh install, no GOOGLE_CLIENT_ID/GITHUB_APP_*/GITHUB_CLIENT_ID, register+login completes end to end | VERIFIED | Re-ran `bash infrastructure/scripts/verify-phase18.sh` live (Docker up, real Postgres, no image built): check (c) boots a real memory-api with all 5 social-OAuth vars **explicitly emptied**, then `POST /v1/auth/local/register` -> xbt_ + team_scope, `POST /v1/auth/local/login` -> 200, `GET /v1/tasks` with the xbt_ + X-Team-Scope -> 200. Gate result: **PASS: 13/13 (SKIP: 0), exit 0**. Also proven by `test_clean_boot_no_oauth_e2e` in the real-Postgres suite (28/28 passed, re-run directly). |
| 2 | SC#2 — passwords stored only as a salted memory-hard KDF hash, never plaintext | VERIFIED | `apps/memory-api/app/services/password_hash.py` wraps unmodified `argon2.PasswordHasher()` (no m/t/p overrides — RFC_9106_LOW_MEMORY defaults). `test_password_hash_is_argon2id` (re-run, passed) SELECTs the real `local_credentials` row and asserts `password_hash.startswith("$argon2id$")` and that the literal plaintext appears in **no** column of the row. |
| 3 | SC#3/LAUTH-02 — resulting principal indistinguishable downstream: `get_current_principal` same shape, every team-scoped route authorizes identically, no sixth special case | VERIFIED | `git diff 100e6d9 HEAD -- apps/memory-api/app/deps.py apps/memory-api/app/routes/auth_github.py` returns **empty** — byte-identical to the pre-phase base commit; confirmed independently by the gate's check (b4) and (d) (zero `local_credentials`/`auth_local` marker strings in either file). `test_local_xbt_authorizes_team_route` (re-run, passed) hits the REAL `get_current_principal` -> `get_team_scope` -> `team_members` path with the register-minted xbt_ (no `dependency_overrides`) and gets `GET /v1/tasks` -> 200. |
| 4 | SC#4 — Google OAuth and the GitHub App still work unchanged when configured; local path is a default, not a replacement | VERIFIED | deps.py/auth_github.py byte-identical to base (above). Gate check (b3) — a gate-owned, correctly-shaped respx-mocked live exercise of the real `POST /v1/auth/github/signin` route (real Postgres, real FastAPI app) — PASSED live: mints an `xbt_`, 200. `test_edition_gating.py` (13/13, re-run) confirms `auth_local` is CORE-classified (not a replacement router). `test_phase10_auth.py` re-run: 1/7 newly fixed (`test_orphan_token_lands_on_survivor`, unblocked by the merge.py fix), 6/7 fail on the two documented pre-existing, Phase-10/12-vintage fixture bugs (stale `GITHUB_CLIENT_ID` naming + missing `refresh_token` mock) — zero NEW failures, confirmed via the gate's tolerant JUnit-XML diff against the known-broken set. |
| 5 | SC#5 — account surface complete: registration, sign-in, sign-out, password change (email reset explicitly out of scope, documented) | VERIFIED | `app-site/account/register/index.html`, `login/index.html`, `password/index.html` exist, call the correct endpoints (`grep` confirms `auth/local/register`/`login`/`set-password`, `Authorization: Bearer` on the password page), and reuse the **same** `xbt_token`/canonical-email localStorage keys as `teams.js` — so sign-out (already implemented in the shared `teams.js`, `localStorage.removeItem(STORAGE_TOKEN)`) applies to local-auth sessions without new code. `docs/local-auth-recovery.md` documents the operator DB/CLI reset and explicitly states email/SMTP reset is out of scope (LOCAL_AUTH the doc's own text: "email-based password RESET is intentionally OUT of scope"). |
| 6 | SC#6 — basic abuse resistance: rate limiting/lockout on credential endpoints, no default install trivially brute-forceable | VERIFIED | `record_failure`/DB lockout proven by `test_lockout_then_recovery` (re-run, passed): Nth failure sets a future `locked_until`; correct password still refused (byte-identical generic 401) while locked; recovers with `failed_attempts` reset to 0 after the window passes. `test_no_enumeration_oracle` (re-run, passed): absent-email vs wrong-password responses are byte-identical status + body. Rate-limit dependency (`enforce_rate_limit`) attached to register/login/**and now set-password** (see #7 below). |
| 7 | Code-review CR-1 fix landed: set-password now honors the same DB lockout as login (no brute-force bypass via a stolen session) | VERIFIED | `auth_local.py:246-249` — `_rl_set_password` rate-limit dependency now on the route decorator; the CHANGE branch (`auth_local.py:281-291`) checks `locked_until` (429 while locked) and calls `local_credentials_repo.record_failure` on a wrong `old_password`. `test_set_password_wrong_old_engages_lockout` (re-run live, PASSED): 5 wrong-old-password attempts lock the account; a 6th attempt with the CORRECT current password is refused 429. |
| 8 | Code-review CR-2 fix landed: `merge.py` targets `promotions`, not `memory_promotions` | VERIFIED | `apps/memory-api/app/repos/merge.py:82-85` reads `UPDATE promotions SET ...` (3 statements) — no `memory_promotions` reference remains in the source (only an unrelated migration filename `0002_memory_promotions.py`, which itself creates the `promotions` table, matched in a stale `.pyc`). Gate check (b2) confirms `test_orphan_token_lands_on_survivor` — which calls `merge_user_rows` directly — is now newly-fixed (was in the pre-existing-broken set, now passes). |
| 9 | D-18-05 convergence: existing-email register -> 409, no row created | VERIFIED | `test_register_collision_409` + `test_register_duplicate_credential_409` (re-run, both passed): 409, no `xbt_token` in the body, zero `local_credentials` rows for either the direct email-collision or the concurrent-duplicate-credential-INSERT race — never a 500. |
| 10 | D-18-05 convergence: authenticated set-password attaches a password to a non-local (GitHub/Google-style) account and login with it then works | VERIFIED | `test_convergence_github_style_user_attaches_password` (re-run, passed): a seeded GitHub-style user authenticates as `kind="user_api_token"` via a real xbt_ (confirmed via `GET /v1/me`), attaches a password with no `old_password` (first-attach), then a subsequent `POST /v1/auth/local/login` with that email+password succeeds. |
| 11 | D-18-05 / T-18-04-01: horizontal-privilege-escalation blocked — principal A cannot touch principal B's credential row | VERIFIED | `test_cannot_set_another_users_password` (re-run, passed): A's authenticated call with B's `user_id`/`email` smuggled into the request body only ever changes A's own row (B's hash and login are unaffected) — `SetPasswordBody` has no user-target field; identity comes solely from the resolved principal. |
| 12 | UI screens usable on a zero-OAuth install, English-only, no framework | VERIFIED (backend+static) / see human item | Static checks pass: all three pages exist, call the right endpoints, no CDN/framework import, no new French strings. Browser end-to-end (visual/UX) verification is the one deferred human item below — not a code gap. |

**Score:** 12/12 truths verified programmatically. 1 legitimate human-verification item remains (deferred, documented).

### Deferred Items

None beyond the single documented human-verification carry-forward below (Phase-16-owned browser UAT). No other roadmap Success Criteria were pushed to a later phase.

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `apps/memory-api/alembic/versions/0024_local_credentials.py` | migration head, cascade FK, no email unique index | VERIFIED | `revision = "0024_local_credentials"`, `down_revision = "0023_tasks_source_connector"` (full stem). `DATABASE_URL=... python -m alembic heads` re-run live -> prints `0024_local_credentials (head)`. `ON DELETE CASCADE` present; no email unique index. |
| `apps/memory-api/app/repos/local_credentials.py` | create/get_by_email/get_by_user_id/record_failure/reset_failures/update_hash/upsert | VERIFIED | All 7 helpers present, `flush()` only (0 `commit(` matches), case-insensitive `lower(u.email)` JOIN lookup. |
| `apps/memory-api/app/config.py` | LOCAL_AUTH_* knobs, no fail-fast validator | VERIFIED | 4 knobs present with safe defaults; no `field_validator` decorates them. |
| `apps/memory-api/app/services/password_hash.py` | argon2id hash/verify/decoy/needs_rehash | VERIFIED | `PasswordHasher()` with zero constructor args; decoy hash computed once at import; `verify_decoy` never raises. |
| `apps/memory-api/app/services/rate_limit.py` | in-process per-key budget + FastAPI dependency helper | VERIFIED | `check_rate`/`enforce_rate_limit` present; multi-worker caveat documented in-code. |
| `apps/memory-api/app/services/api_tokens.py` | shared `mint_xbt_for_user` helper | VERIFIED | Mirrors `auth_github.py`'s mint shape; `auth_github.py` itself untouched (confirmed via byte-diff against base). |
| `apps/memory-api/app/routes/auth_local.py` | register / login / set-password | VERIFIED | All three routes present, single-commit register, decoy-timed generic 401 login, lockout-honoring set-password (post-CR-1 fix). CORE-mounted in `app/main.py` (`CORE_ROUTERS`, not `SAAS_ONLY_ROUTERS`). |
| `apps/memory-api/app/repos/merge.py` | table name fix (CR-2) | VERIFIED | References `promotions`, not `memory_promotions` (5 occurrences, all correct). |
| `docs/local-auth-recovery.md` | operator recovery runbook, no SMTP | VERIFIED | Present; contains `UPDATE local_credentials`/`DELETE FROM local_credentials` examples; explicitly states email/SMTP reset is out of scope. |
| `docs/auth.html` (plan-stated path) | native auth doc | DEVIATION (documented, non-blocking) | Path does not exist; actual GitHub/Google auth doc lives at `app-site/docs/auth.html` — extended there instead (18-06-SUMMARY.md "Path correction (Rule 3)"). Content satisfies all plan acceptance criteria at the real path (`grep -qi 'auth/local'`, `github`, `email` count 24, `recovery\|reset` count 5) — re-verified directly, not just via SUMMARY claim. |
| `app-site/account/{register,login,password}/index.html` | 3 static auth screens | VERIFIED | All 3 exist, call the correct endpoints, share the canonical `xbt_token`/email localStorage keys with `teams.js` (so sign-out is inherited, not reinvented), no framework/CDN import, no new French strings. |
| `infrastructure/scripts/verify-phase18.sh` + `make verify-phase18` | SC#1..SC#6 acceptance gate, SKIP-as-FAIL | VERIFIED | Re-ran live twice (once with an environment misconfiguration on my part — global `MSYS_NO_PATHCONV=1` broke the pytest-junitxml path resolution for checks a/b1/b2/b3, producing false FAILs; re-run with only the script's own scoped `MSYS_NO_PATHCONV=1` on the docker-mount commands — as the script itself does internally — gives the correct result). Final result: **PASS: 13/13 (SKIP: 0), exit 0.** `make verify-phase18` target present in `Makefile`. |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `auth_local.register` | `teams_repo.create_team` (direct) | in-process repo call, same transaction | VERIFIED | `grep -c 'create_team'` in auth_local.py >=1, `grep -c 'self.solo'` = 0 — never calls the self-committing HTTP route. |
| `auth_local.register`/`login`/`login-success` | `user_api_tokens` via `mint_xbt_for_user` | same xbt_ INSERT shape as auth_github | VERIFIED | `test_local_xbt_authorizes_team_route` + `test_clean_boot_no_oauth_e2e` both prove the minted token resolves via the REAL `get_current_principal` xbt_ branch. |
| `auth_local.login` collision/absent/locked branches | `password_hash.verify_decoy` | decoy verify on every short-circuit | VERIFIED | Code inspection confirms `verify_decoy` call on absent + locked branches; `verify_password` (real, timing-matched) on the wrong-password branch; WR-1 fix adds an equalizing `session.commit()` to all three. |
| `set-password` | `local_credentials_repo.upsert` / `update_hash` / `record_failure` (post-fix) | first-attach = upsert, change = verify-then-update, wrong = record_failure | VERIFIED | Live-tested via `test_set_password_wrong_old_engages_lockout` (429 after 5 wrong old-password attempts) and `test_change_password_with_correct_old_password`. |
| `set-password` auth | `get_current_principal` | accepts kind in {user, user_api_token} via local `_require_user_any` | VERIFIED | `test_convergence_github_style_user_attaches_password` authenticates as `kind="user_api_token"` and succeeds; `me.py::_require_user` (which 403s this kind) is not reused — confirmed by code inspection of `_require_user_any`. |
| `verify-phase18.sh` | real memory-api + real Postgres (never a mock) | testcontainers pytest + a live no-build boot harness | VERIFIED | Directly re-ran; live boot check (c) reaches a real container over a real bind mount (`MSYS_NO_PATHCONV=1` scoped correctly inside the script), `alembic upgrade head` applied, real HTTP calls against a live server. |

### Data-Flow Trace (Level 4)

Not applicable in the conventional sense (no frontend state → API → DB rendering chain to trace) — this phase is a backend security surface plus static auth-form pages whose only "data flow" is form input → fetch → API → DB, which is exactly what Steps 3-5 above (and the live gate run) directly exercised end-to-end against a real Postgres, not a mock or fixture stand-in.

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Alembic chain resolves to 0024 head (no wrong down_revision) | `DATABASE_URL=... python -m alembic heads` | `0024_local_credentials (head)` | PASS |
| Full real-Postgres security suite | `pytest tests/test_local_auth.py tests/test_local_auth_set_password.py tests/test_local_credentials_repo.py -q` | `28 passed` | PASS |
| Router classification | `pytest tests/test_edition_gating.py -q` | `13 passed` | PASS |
| SC#4 regression (documented-tolerant) | `pytest tests/test_phase10_auth.py -q` (via gate's JUnit-XML tolerant diff) | no new failures; 1 newly-fixed (`test_orphan_token_lands_on_survivor`); 6 pre-existing documented failures unchanged | PASS |
| Live zero-OAuth boot: register -> login -> authorized GET | `bash infrastructure/scripts/verify-phase18.sh` full run | `PASS: 13 / 13 (SKIP: 0)`, exit 0 | PASS |
| deps.py / auth_github.py untouched across the whole phase | `git diff 100e6d9 HEAD -- app/deps.py app/routes/auth_github.py` | empty diff | PASS |
| Full memory-api test suite still collects cleanly (no import breakage from Phase 18 changes) | `pytest --collect-only -q` | `460 tests collected`, no errors | PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| LAUTH-01 | 18-01, 18-02, 18-03, 18-04, 18-05, 18-06 | Register/sign-in with email+password on a zero-OAuth install; passwords stored only as a salted memory-hard KDF hash | SATISFIED | SC#1/SC#2 truths above, live gate PASS 13/13 |
| LAUTH-02 | 18-03, 18-06 | Local-auth principal indistinguishable downstream; no per-route special case; Google/GitHub unchanged | SATISFIED | SC#3/SC#4 truths above; byte-identical deps.py/auth_github.py diff |

No orphaned requirements: REQUIREMENTS.md maps only LAUTH-01/LAUTH-02 to Phase 18, and both are declared in plan frontmatter. **Note (non-blocking, documentation-only):** `.planning/REQUIREMENTS.md` lines 31-32/59-60 still show LAUTH-01/LAUTH-02 as `[ ]` unchecked / "Pending" in the tracking table, even though ROADMAP.md marks Phase 18 `[x]` complete and the code evidence above satisfies both. This is a stale-bookkeeping gap in REQUIREMENTS.md, not a code gap — worth a housekeeping edit but does not block phase closure.

### Anti-Patterns Found

None blocking. Scanned `auth_local.py`, `local_credentials.py`, `password_hash.py`, `rate_limit.py`, `api_tokens.py`, `merge.py`, the 3 UI pages, and `verify-phase18.sh` for TODO/FIXME/placeholder/empty-implementation/hardcoded-empty patterns — none found that flow to user-visible output unaddressed. The one info-level finding from the code review (IN-01, bare `"@" not in email` substring check instead of a proper email-shape validator) is cosmetic — the value is only ever used as an opaque lookup key, never rendered or interpreted as a mailto target — and was left as a documented, non-blocking info item by the reviewer; it does not affect any of SC#1-6.

### Human Verification Required

### 1. Browser UAT of the three auth screens against a live, zero-OAuth stack

**Test:** Serve `app-site` locally or against a deployable instance; open `/account/register/`, register a new email+password, confirm signed-in state and the 409 message on a repeat register; open `/account/login/`, sign in, confirm the generic "Invalid email or password." on a wrong attempt; open `/account/password/`, change the password with the current one, confirm re-login with the new password works; confirm all visible copy is English and styling is consistent with `/account/teams/`.

**Expected:** The full register -> login -> change-password -> re-login loop works visually in a browser, matching `18-05-PLAN.md`'s Task 2 checkpoint script.

**Why human:** Explicitly and knowingly deferred by the user on 2026-07-13 (`18-HUMAN-UAT.md`, `status: partial`) — the prod VM (`api.grooveos.app`, hardcoded in these pages per the established `app-site` pattern) is terminated, so there is currently no live stack to browser-test against. The backend half of this exact flow (register/login/set-password logic, argon2id persistence, lockout, no-enumeration-oracle, convergence, horizontal-priv-escalation) is fully and independently proven by 28 real-Postgres integration tests plus the live zero-OAuth boot harness re-run in this verification — only the pixels/browser-fetch wiring is unverified. This is explicitly carried into Phase 16 UAT (when app-site is also debranded, D-01c) per the existing deferral plan, not a Phase-18 gap.

### Gaps Summary

No blocking gaps. All 12 derived truths (ROADMAP SC#1-6, LAUTH-01/02, plus the D-18-05 convergence/priv-escalation sub-truths and all 3 code-review fixes) verify against the actual codebase and REAL behavior — re-run live against Docker/Postgres, not inferred from source or trusted from SUMMARY.md claims. Two live re-runs of `verify-phase18.sh` were performed: the first run showed 4 false FAILs caused by my own environment mistake (`MSYS_NO_PATHCONV=1` set globally, which broke the JUnit-XML temp-file path resolution the script needs for its own pytest invocations — the script only needs that variable scoped to its own docker-mount commands, and does so correctly internally); the corrected re-run produced the genuine result: **13/13 PASS, 0 SKIP, exit 0**. All three code-review findings (CR-1 set-password lockout bypass, CR-2 merge.py table-name bug, WR-1 login timing residual) were independently confirmed fixed in the live code and by a live-executed regression test (`test_set_password_wrong_old_engages_lockout`), not merely by reading the fix commit's message. `deps.py` and `auth_github.py` are confirmed byte-identical to the pre-phase base commit (`100e6d9`) via direct `git diff`, satisfying LAUTH-02's "no sixth branch" requirement at the strongest level of proof available.

The single remaining item is the deliberately deferred browser/UI UAT (documented in `18-HUMAN-UAT.md`, owned by Phase 16 per the app-site debranding dependency) — this drives the overall status to `human_needed` per the verification decision tree (a non-empty human-verification section takes priority over an otherwise-clean score), not `gaps_found`. No override was needed for this item since it is not a failed must-have — it is an explicitly out-of-band, user-authorized deferral with its own tracking artifact.

---

_Verified: 2026-07-14T13:00:00Z_
_Verifier: Claude (gsd-verifier)_
