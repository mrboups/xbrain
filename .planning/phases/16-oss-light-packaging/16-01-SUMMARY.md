---
phase: 16-oss-light-packaging
plan: 01
subsystem: auth
tags: [oauth, oauth2.1, connector, local-auth, argon2id, pkce, fastapi, mcp]

# Dependency graph
requires:
  - phase: 18-local-auth
    provides: local_credentials repo, argon2id verify_password/verify_decoy, per-account lockout, LOCAL_AUTH_RATE_LIMIT rate limiter
  - phase: connector-oauth-as (quick-260604-glo)
    provides: OAuth 2.1 AS (signed state, PKCE code issuance, _finalize_consent, oauth_consent.html, oauth_store)
provides:
  - "Zero-key connector sign-in: GET /oauth/authorize renders a local login form when no GitHub App is configured (instead of a client_id-empty GitHub 302)"
  - "POST /oauth/authorize/local: argon2id credential proof converging into the existing _finalize_consent (single team) or reused consent page (multi team)"
  - "Enumeration/CSRF/open-redirect/brute-force-safe local authN leg for the OAuth 2.1 AS, fully reusing Phase-18 primitives"
affects: [16-oss-light-packaging clean-install test, SC#3 connector flow, Phase 20 extension zero-key sign-in]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Additive config branch: `if not settings.GITHUB_APP_CLIENT_ID` renders local auth, else the byte-unchanged GitHub 302 — no replacement of the working path"
    - "Reuse-not-reimplement convergence: the local leg re-signs the same post_github state keys github_callback uses and calls the SAME _finalize_consent / consent page"
    - "Generic-401 + verify_decoy timing equalizer mirrored from auth_local.login so absent/locked/wrong-password are byte-identical (no enumeration oracle)"

key-files:
  created:
    - apps/memory-api/app/templates/oauth_local_login.html
    - apps/memory-api/tests/test_oauth_authorize_local.py
  modified:
    - apps/memory-api/app/routes/oauth_authorize.py

key-decisions:
  - "Local login form re-renders with the ORIGINAL pre_github state on failure (still valid, 10-min TTL) and never echoes the submitted email — so absent/locked/wrong-password bodies are byte-identical for a given request context"
  - "Multi-team fork commits reset_failures then renders the reused consent page and mints NO code; the existing POST /oauth/authorize consent submit issues the code after team selection"
  - "redirect_uri + user_id are taken ONLY from the signed state / credential lookup, never the form body"

patterns-established:
  - "OAuth AS local-auth leg: verify_state(stage=pre_github) -> get_by_email -> decoy/lockout/verify_password -> reset_failures -> re-sign post_github -> _finalize_consent | consent page"

requirements-completed: [PKG-01]

# Metrics
duration: ~40min
completed: 2026-07-18
---

# Phase 16 Plan 01: Zero-Key Connector Local-Auth Sign-In Summary

**When no GitHub App is configured, the ChatGPT-web/Claude.ai connector's OAuth-AS sign-in authenticates the user via Phase-18 argon2id local auth and converges into the existing PKCE code-issuance / multi-team consent machinery — closing the D-16-02 / D-18-07 zero-key blocker with wiring, not new crypto.**

## Performance

- **Duration:** ~40 min
- **Started:** 2026-07-18T11:45:00Z (approx, first read)
- **Completed:** 2026-07-18T10:01:00Z (final GREEN commit 57f3936)
- **Tasks:** 2 (both TDD — 4 task commits: test -> feat x2)
- **Files modified:** 3 (2 created, 1 modified)

## Accomplishments
- `GET /oauth/authorize` now branches on GitHub configuration: zero-key installs get an English-only email/password login form (`action="/oauth/authorize/local"`, hidden signed `pre_github` state); GitHub-configured installs keep the byte-unchanged 302 into GitHub.
- `POST /oauth/authorize/local` proves credentials against the Phase-18 argon2id store and the same 5-attempt per-account lockout `auth_local.login` uses, then converges into the SAME `_finalize_consent` (single-team → minted PKCE-bound code) or the SAME reused `oauth_consent.html` (multi-team → `stage=post_github`, no code minted until the consent submit).
- Full STRIDE mitigation reuse: generic-401 + `verify_decoy` (no enumeration oracle), signed-state CSRF gate (`stage=pre_github` only), `redirect_uri`/`user_id` from the signed state only (no open redirect / horizontal escalation), and `enforce_rate_limit(LOCAL_AUTH_RATE_LIMIT)` + `record_failure` brute-force defense.
- 8 behavioral tests (≥7 required) against a REAL Postgres + REAL app (only `get_session` overridden), all passing; the existing 18-test `test_oauth_as.py` suite and the GitHub path remain green.

## Task Commits

Each task was committed atomically, honoring TDD RED → GREEN order:

1. **Task 1 (RED): failing GET-branch test** — `b15c7fc` (test)
2. **Task 1 (GREEN): local login template + GET branch** — `e5232e7` (feat)
3. **Task 2 (RED): failing POST /oauth/authorize/local tests** — `078b0b6` (test)
4. **Task 2 (GREEN): POST /oauth/authorize/local convergence** — `57f3936` (feat)

**Plan metadata:** committed with this SUMMARY (docs: complete plan).

## Files Created/Modified
- `apps/memory-api/app/templates/oauth_local_login.html` — English-only login form mirroring `oauth_consent.html`'s dark system-ui styling; POSTs `email`/`password`/hidden `state` to `/oauth/authorize/local`; `{{client_name}}`/`{{state}}`/`{{error}}` string-replace placeholders.
- `apps/memory-api/app/routes/oauth_authorize.py` — added `_LOCAL_LOGIN_TEMPLATE`, `_GENERIC_LOGIN_ERROR`, `_render_local_login()`, the `if not settings.GITHUB_APP_CLIENT_ID` branch in `authorize`, the `_rl_authorize_local` rate-limit dep, and the `POST /oauth/authorize/local` (`authorize_local_submit`) handler.
- `apps/memory-api/tests/test_oauth_authorize_local.py` — 8 integration tests: zero-key GET form, GitHub-configured GET 302 guard, single-team mint, multi-team consent fork (stage=post_github + no code), wrong-pw 401 + record_failure, absent==wrong byte-identical, locked==absent byte-identical, bad/foreign state → no code.

## Decisions Made
- On a failed credential proof, re-render the login form with the ORIGINAL `pre_github` state (still valid within its 10-min TTL) and never echo the submitted email, so absent/locked/wrong-password responses are byte-identical for a given request context — the enumeration-oracle guarantee is enforced by test, not just by a shared message string.
- The multi-team fork commits `reset_failures` explicitly before rendering the reused consent page (the consent submit runs in a separate request/transaction that would otherwise roll it back) and mints NO authorization code — issuance is deferred to the existing `POST /oauth/authorize` consent submit after team selection.
- `_finalize_consent` and the `oauth_consent.html` options block are reused verbatim (copied from `github_callback`), not re-implemented, keeping the membership check + PKCE code binding in one place.

## Deviations from Plan

None - plan executed exactly as written.

(One implementation detail worth noting, not a deviation: the `@router.post("/oauth/authorize/local", ...)` decorator is kept on a single line so it satisfies the plan's `grep -Eq '@router\.post\("/oauth/authorize/local"'` acceptance check — an initial multi-line form was collapsed before committing GREEN.)

## Authentication Gates

None - no interactive auth gate was hit during execution (the feature under test IS a local auth path, exercised end-to-end against a testcontainers Postgres).

## Issues Encountered
- First write of the test file targeted the shared-checkout path and was rejected (worktree isolation); re-issued against the worktree copy. No impact on output.

## Known Stubs

None - the local-auth leg is fully wired: real argon2id verification, real DB-backed lockout, real PKCE code minting, real membership enforcement. No placeholder data, no empty-value flows.

## Threat Flags

None - all security surface introduced (`GET /oauth/authorize` local branch, `POST /oauth/authorize/local`) is already enumerated in the plan's `<threat_model>` (T-16-01-01..07); every `mitigate` disposition is implemented and test-covered. No new endpoints/auth paths/schema changes beyond the plan.

## TDD Gate Compliance

RED → GREEN order honored for both tasks (test commit precedes feat commit in each): `b15c7fc` (test) → `e5232e7` (feat); `078b0b6` (test) → `57f3936` (feat). Each RED was run and confirmed failing before its GREEN. No REFACTOR gate was needed.

## User Setup Required

None - no external service configuration required. The feature specifically REMOVES the external GitHub-App requirement for the connector sign-in on zero-key installs.

## Next Phase Readiness
- SC#3's "connects via the ChatGPT-web connector" zero-key leg is now reachable end-to-end; the phase's scripted clean-install HTTP flow (later plans) can drive `GET /oauth/authorize` → `POST /oauth/authorize/local` → `/oauth/token` without any GitHub App.
- Phase 20's extension zero-key sign-in (D-16-03, descoped here) can reuse `POST /oauth/authorize/local` as the browser-facing local-auth entry point.
- No blockers.

## Self-Check: PASSED

- Files verified present: `oauth_local_login.html`, `test_oauth_authorize_local.py`, `oauth_authorize.py`, `16-01-SUMMARY.md`.
- Commits verified present: `b15c7fc`, `e5232e7`, `078b0b6`, `57f3936`.
- Test suite: 8/8 in `test_oauth_authorize_local.py` pass; 18/18 in `test_oauth_as.py` pass (no regression).
- File scope: `git diff --name-only 7884887 HEAD` shows only the 3 plan files; `deps.py`/`auth_github.py` untouched.

---
*Phase: 16-oss-light-packaging*
*Completed: 2026-07-18*
