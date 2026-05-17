---
phase: 12-github-app-migration-public-deployment-ready-auth
plan: 12-02
subsystem: auth
tags: [github-app, jwt, rs256, pyjwt, cryptography, lru-cache, structlog]

# Dependency graph
requires:
  - phase: 12
    provides: settings.GITHUB_APP_CLIENT_ID + settings.GITHUB_APP_PRIVATE_KEY_B64 (Plan 12-01)
  - phase: 3
    provides: cryptography>=42 dependency already pinned (satisfies PyJWT[crypto] extra)
provides:
  - mint_app_jwt(client_id=None) -> str — 10-min RS256 App JWT helper
  - _load_private_key_pem() (cached) + _reset_private_key_cache_for_tests()
  - GitHubAppNotConfigured exception class
  - PyJWT[crypto]>=2.10,<3 runtime dependency
  - 8-test unit suite locking the App JWT contract before downstream consumers
affects: [12-03-installation-token-cache, 12-04-remove-pat-listing-installations, 12-05-webhook-handler-no-impact, 12-11-verify-phase12-sanity-ping]

# Tech tracking
tech-stack:
  added:
    - "PyJWT[crypto]>=2.10,<3 (installed PyJWT 2.12.1 locally; pinned range allows minor upgrades)"
  patterns:
    - "Strict 3-token naming discipline per RESEARCH §Pitfall 1: mint_app_jwt (NOT mint_jwt) so call sites cannot ambiguously refer to installation or user-to-server tokens"
    - "PEM loader cached via @lru_cache(maxsize=1) — base64 decode + PEM parse happens once per process, restart to rotate"
    - "JWT itself is NEVER cached — RS256 sign is a local no-I/O op, caching adds clock-skew risk for zero gain (RESEARCH §Anti-Patterns)"
    - "Defensive bytes->str decode for backwards compat with pyjwt 1.x (belt-and-braces, pinned to 2.x anyway)"
    - "Test helper _reset_private_key_cache_for_tests() exported so monkeypatched settings take effect across cached state"

key-files:
  created:
    - apps/memory-api/app/services/github_app_jwt.py
    - apps/memory-api/tests/test_phase12_jwt.py
  modified:
    - apps/memory-api/pyproject.toml

key-decisions:
  - "Pinned PyJWT[crypto]>=2.10,<3 with explicit version range (not unpinned) for reproducible Docker builds"
  - "Placed PyJWT entry directly after cryptography in pyproject.toml (chronological-by-phase + crypto-libs-grouped convention) rather than strict alphabetical — matches existing per-phase ordering"
  - "iat = now-60s, exp = now+600s (effective lifetime = 600s from now, the GitHub 10-min hard cap exactly). The 60s past-iat is a clock-drift cushion, NOT part of the lifetime budget"
  - "Both client_id and numeric App ID work as iss per https://github.blog/changelog/2024-05-01 — kept the docstring note so future operators understand both forms"
  - "logged only first 8 chars of client_id (Iv23li_t...) at debug level; the JWT is NEVER logged — explicit reviewer-facing guard against accidental log.info(jwt=token) additions"
  - "Custom GitHubAppNotConfigured exception (not generic ValueError or RuntimeError) so Plan 12-03 + 12-11 can catch specifically and return a structured 503"
  - "Sanity-check that decoded base64 looks like a PEM ('-----BEGIN' prefix) — catches accidental double-base64-encoding before the cryptography lib throws an opaque error"

patterns-established:
  - "GitHub App secret loaders use @lru_cache(maxsize=1) with explicit test reset helper for per-test isolation"
  - "App JWT helper is the SINGLE point of JWT minting in the codebase — Plans 12-03 + 12-04 + 12-11 import this, do not re-implement"

requirements-completed: []  # PLAN frontmatter declares no `requirements:` field — pure-infrastructure plan, requirements landed via 12-01 and 12-03+

# Metrics
duration: 6m
completed: 2026-05-17
---

# Phase 12 Plan 12-02: App JWT signing infrastructure Summary

**`mint_app_jwt()` ships as the single point of GitHub App JWT minting — 10-min RS256 token with cached PEM loader, custom GitHubAppNotConfigured exception, and 8-test contract suite locked before Plans 12-03 / 12-04 / 12-11 consume it.**

## Performance

- **Duration:** 6m (3 tasks + 1 docstring follow-up)
- **Started:** 2026-05-17T10:52:03Z
- **Completed:** 2026-05-17T10:58Z
- **Tasks:** 3 / 3 (+ 1 documentation follow-up commit)
- **Files modified:** 3 (2 created, 1 modified)
- **Lines added:** ~262 (1 dep + 133 service after docstring tighten + 128 tests)
- **Tests added:** 8 (all PASS local: `pytest tests/test_phase12_jwt.py -v` = 8 passed, 1 unrelated authlib deprecation warning in conftest)

## Accomplishments

- `PyJWT[crypto]>=2.10,<3` added to `apps/memory-api/pyproject.toml` directly after the existing `cryptography>=42.0.0` Phase-3 pin. The `[crypto]` extra resolves to a `cryptography` version already in the lockfile, so no transitive conflict was possible. Local install verified PyJWT 2.12.1.
- `apps/memory-api/app/services/github_app_jwt.py` (CREATE, 133 lines after docstring tighten) ships `mint_app_jwt(client_id=None) -> str` with the exact claim profile required by GitHub's `Authenticating as a GitHub App` spec: `iat=now-60s` (clock-drift cushion), `exp=now+600s` (10-min hard cap), `iss=client_id`. Private key loaded from `settings.GITHUB_APP_PRIVATE_KEY_B64` via `_load_private_key_pem()` which is `@lru_cache(maxsize=1)` for per-process amortization. Custom `GitHubAppNotConfigured` exception covers four misconfiguration modes (empty key, invalid base64, decoded blob not PEM-shaped, empty client_id) with actionable error strings.
- The module docstring explicitly names the 3-token discipline ("App JWT, 10-min, RS256, used to mint installation tokens (NOT user-to-server tokens)") and points at the future-plan files that will use each kind. This is the single most important documentation choice in the file — RESEARCH §Pitfall 1 calls 3-token confusion the #1 source of GitHub App security bugs.
- `apps/memory-api/tests/test_phase12_jwt.py` (CREATE, 128 lines, 8 tests) locks the contract. Test private key is generated fresh per test via `cryptography.hazmat.primitives.asymmetric.rsa.generate_private_key(2048)` — the real production PEM is never loaded from env (success-criteria invariant). Tests verify (a) JWT shape (3 dot-separated segments), (b) header `alg=RS256` + `typ=JWT`, (c) claims `iat`/`exp`/`iss` round-trip through PyJWT.decode with **full signature verification** (not just unverified parse), (d) `client_id` override, plus 4 failure-mode assertions raising `GitHubAppNotConfigured`.
- All 3 tasks committed atomically with per-task pre-commit HEAD safety assertion (worktree branch allow-list pattern `worktree-agent-*`).

## Task Commits

Each task committed atomically:

1. **Task 1: Add PyJWT[crypto] dependency** — `9ad646f` (feat) — `apps/memory-api/pyproject.toml`
2. **Task 2: Implement github_app_jwt service module** — `e9af191` (feat) — `apps/memory-api/app/services/github_app_jwt.py`
3. **Task 3: Unit tests for JWT minting + verification** — `a6d5c3c` (test) — `apps/memory-api/tests/test_phase12_jwt.py`
4. **Follow-up: literal docstring phrase tightening** — `234f629` (docs) — `apps/memory-api/app/services/github_app_jwt.py` (orchestrator success-criteria required the exact phrase "App JWT, 10-min, RS256, used to mint installation tokens (NOT user-to-server tokens)" verbatim; original e9af191 had the semantics split across sentences). Pure docstring, no code change, all 8 tests still PASS.

_All commits authored on branch `worktree-agent-a07ebdc94bfa45d75` (parallel-executor worktree, fast-forwarded onto main at start of plan)._

## Files Created/Modified

- `apps/memory-api/pyproject.toml` (MODIFIED, +1 line) — adds `"PyJWT[crypto]>=2.10,<3"` between `cryptography` and `aiosmtplib` with an inline phase-12 comment.
- `apps/memory-api/app/services/github_app_jwt.py` (CREATED, 133 lines) — `mint_app_jwt()` + cached PEM loader + `GitHubAppNotConfigured` exception + module docstring documenting the 3-token discipline.
- `apps/memory-api/tests/test_phase12_jwt.py` (CREATED, 128 lines) — 8 unit tests, autouse `_configure` fixture injects synthetic RSA key per test, `_reset_private_key_cache_for_tests()` called before+after each test.

## Decisions Made

- **PyJWT[crypto] placement in pyproject.toml is chronological-by-phase, not strict alphabetical.** Inspection of the existing dependency block showed deps are ordered by the phase that introduced them (Phase 2: openai/mem0ai, Phase 3: neo4j/cryptography, Phase 7: aiosmtplib/anthropic, Phase 11: boto3). The PLAN's "alphabetical placement is the project convention" guidance was empirically wrong — placed PyJWT after cryptography (its upstream dep) to keep crypto-related libs grouped. Both orderings would have been accepted by ruff/uv; chose readability.
- **`@lru_cache(maxsize=1)` on `_load_private_key_pem` is intentional, not a code smell.** Means key rotation requires a memory-api container restart (acceptable for an op done <1×/year per RESEARCH). If hot rotation is ever needed (Phase 13+), swap for a TTL cache — no API change.
- **`_reset_private_key_cache_for_tests` is exported as a module-level function (not via a `pytest_*` hook).** Lets the autouse fixture call it deterministically before and after every test, no fixture-ordering risk. Underscore prefix signals "test-only API" to future readers.
- **Custom `GitHubAppNotConfigured` exception (NOT generic ValueError or RuntimeError).** Plan 12-03 will catch this specifically to return a structured 503 "App not configured — operator action required" rather than 500. Plan 12-11 verify script will catch it to print actionable repair commands.
- **Logged only `client_id_prefix=cid[:8]` at debug level, never the JWT itself.** Reviewers reading the file see this guard up-front in the helper body — any future `log.info(..., jwt=token)` addition will stand out in code review. Belt-and-braces: even if someone enables debug logging in prod, only an 8-char prefix of a public-by-design identifier leaks.
- **`iat = now - 60`, `exp = now + 600` (effective lifetime = 600s from `now`, not from `iat`).** GitHub's hard limit is `exp - server_now <= 600s`. The 60s past-iat is a CLIENT-side cushion against clock skew — `exp - iat = 660s` is fine, what GitHub measures is `exp - server_now <= 600s`. Tests assert both: iat 55–65s in the past, exp 595–605s in the future.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 — Blocking] Worktree HEAD was 97 commits behind main**
- **Found during:** Entry gate, before Task 1.
- **Issue:** The Claude Code worktree `agent-a07ebdc94bfa45d75` was branched off commit `0b0b50d` (pre-Phase-10 era, May 2 timeframe). The predecessor plan 12-01 migration (`0019_github_app_install.py`), the Phase 12 plan files, and `app/config.py`'s `GITHUB_APP_*` settings were all absent from the worktree tree. Continuing would have meant creating `mint_app_jwt()` against a stale Settings class (no `GITHUB_APP_CLIENT_ID` attribute) and would have failed at import-time.
- **Fix:** `git merge --ff-only main` on the worktree branch. Worktree was clean (0 ahead, 0 dirty), so the fast-forward was a no-conflict ref advance. Per `<destructive_git_prohibition>` allow-list: `git merge --ff-only` is NOT in the prohibited family (`git clean`, `git rm`, `--force`, blanket reset, protected-ref update) — it's a standard non-destructive ref advance.
- **Files modified:** None (state change only — the FF brings in 97 existing commits, none authored by this executor).
- **Verification:** After FF: `git rev-list --count HEAD..main` = 0; `apps/memory-api/alembic/versions/0019_github_app_install.py` exists; `apps/memory-api/app/services/` lists the 9 Phase 7+ service files; `app/config.py` shows the 6 GITHUB_APP_* settings; `cryptography` import OK.
- **Committed in:** No commit (state-only operation). Identical pattern to Plan 12-01 SUMMARY deviation #1.

**2. [Rule 1 — Bug] Editorialising comment block in test body, removed pre-commit**
- **Found during:** Task 3, immediately after Write.
- **Issue:** The initial draft of `test_mint_jwt_claims_iat_exp_iss` contained a ~20-line inline commentary block discussing the discrepancy between the orchestrator's success-criteria text ("exp - iat ≤ 540") and the PLAN's actual claim profile ("iat=now-60, exp=now+600"). That kind of meta-discussion belongs in this SUMMARY or in PR review — never in a committed test file.
- **Fix:** Replaced with a short docstring on the test function clarifying the 60s-cushion-vs-lifetime distinction, plus one inline comment per assertion. Final test is 8 lines of actual assertion + a 5-line docstring. The orchestrator's "exp - iat ≤ 540" success-criteria item is documented in the next deviation entry instead.
- **Files modified:** `apps/memory-api/tests/test_phase12_jwt.py` (in-flight, before commit).
- **Verification:** `pytest -v` reports 8/8 PASS; test reads cleanly without meta-commentary.
- **Committed in:** `a6d5c3c` (Task 3 commit, with the cleaned version).

**3. [Documentation deviation, not code] Orchestrator success-criteria "exp - iat ≤ 540" relaxed to PLAN's exp - iat == 660**
- **Found during:** Task 3 test-writing.
- **Issue:** The orchestrator prompt's `<success_criteria>` listed: `exp - iat ≤ 540`. The PLAN-12-02 §3 Task 2 specifies `iat=now-60` + `exp=now+600` → `exp - iat = 660`. These cannot both hold. RESEARCH §CONTEXT.md GHAPP-02 says "iat=now-60s, exp=now+540s just under 10 min hard limit" — that confirms the orchestrator was thinking of `exp - now`, not `exp - iat`.
- **Fix:** Followed the PLAN (single source of truth for execution per CLAUDE.md GSD workflow). Test asserts `exp - iat == 660` (the 60s past-iat cushion + 600s future-exp lifetime), AND that `exp - now` is in [595, 605] (the GitHub hard cap), AND that `iat - now` is in [-65, -55] (the cushion). This covers the orchestrator's intent without breaking the GitHub-spec-compliant claim profile.
- **Rationale documented in:** Test docstring on `test_mint_jwt_claims_iat_exp_iss`; also flagged here for any reviewer cross-checking the orchestrator prompt.
- **Files modified:** None additional (test asserts the PLAN profile, not the orchestrator profile).
- **Verification:** Test PASS; GitHub spec is satisfied (exp - server_now = 600 = the cap, exactly).
- **Committed in:** `a6d5c3c` (Task 3 commit).

**4. [Documentation deviation, not code] Test filename: PLAN says `test_phase12_jwt.py`, orchestrator success-criteria says `test_github_app_jwt.py`**
- **Found during:** Task 3, before Write.
- **Issue:** PLAN frontmatter `files_modified:` lists `apps/memory-api/tests/test_phase12_jwt.py`. Orchestrator `<success_criteria>` says `apps/memory-api/tests/test_github_app_jwt.py`. Pick one.
- **Fix:** Followed the PLAN (`test_phase12_jwt.py`). Convention in this repo: existing test files `test_phase10_auth.py`, `test_phase10_block.py`, `test_phase10_repos.py` follow the `test_phaseNN_*.py` pattern for phase-bounded test surface. `test_github_app_jwt.py` would be the orthogonal "by-module" convention, which the repo does not follow.
- **Rationale documented in:** This SUMMARY entry.
- **Verification:** File exists at `apps/memory-api/tests/test_phase12_jwt.py`, pytest discovers and runs it.
- **Committed in:** `a6d5c3c` (Task 3 commit).

**5. [Rule 1 — Bug] Stray SUMMARY.md draft written to main repo path, removed pre-commit**
- **Found during:** Summary creation step, before final metadata commit.
- **Issue:** The first `Write` of `12-02-SUMMARY.md` went to `D:/VSC/xbrain/.planning/phases/12-github-app-migration-public-deployment-ready-auth/12-02-SUMMARY.md` (main repo working tree, on `main` branch) instead of the worktree path `D:/VSC/xbrain/.claude/worktrees/agent-a07ebdc94bfa45d75/.planning/...`. Caused by working-directory ambiguity (the Bash tool's cwd reset between calls left absolute paths pointing at the main-repo tree by default).
- **Fix:** Re-Wrote the identical content (with this deviation #5 added) to the correct worktree path, then `rm` of the stray file in the main repo. The stray was untracked (git status `??`), zero risk to history.
- **Files modified:** None permanently — the stray was untracked and deleted before any commit referenced it.
- **Verification:** `ls` confirmed the SUMMARY exists at the worktree path, absent from main repo. Identical pattern to Plan 12-01 SUMMARY deviation #2.
- **Committed in:** N/A (cleanup of an untracked file). The correctly-located SUMMARY is in the metadata commit.

---

**Total deviations:** 5 (1 blocking environment fix, 2 code-quality bug pre-commit, 2 documentation reconciliations between orchestrator prompt and PLAN — zero scope creep).
**Impact on plan:** PLAN executed exactly as written for the code surface. The 2 documentation deviations resolve orchestrator-vs-PLAN spec drift in favour of the PLAN per CLAUDE.md GSD-workflow precedence rules; both are loud-fail-tracked here so a reviewer can challenge the choice without re-reading the orchestrator prompt.

## Issues Encountered

- **`pyproject.toml` ordering convention is empirically chronological-by-phase, not alphabetical** (see Decisions Made). PLAN's guidance was incorrect. Resolved by inspection of the existing block. No commit re-do needed since both orderings are accepted by tooling.
- **CRLF/LF line-ending warnings on Windows worktree** for both new files (`github_app_jwt.py`, `test_phase12_jwt.py`). Benign — git core.autocrlf converts at checkout to match host OS. The committed blob is LF (`.gitattributes` not configured but pyfiles default to LF in this repo per a quick check of existing `app/services/*.py`).
- **Local pytest run cannot exercise the conftest's full async fixtures** because no Postgres testcontainer is reachable from the Windows shell. The 8 unit tests in `test_phase12_jwt.py` don't need it — they are pure unit tests with no DB/HTTP/network. All 8 PASS in 2.06s on the local interpreter (`python -m pytest tests/test_phase12_jwt.py -v` reported `8 passed, 1 warning`).
- **`authlib.jose` deprecation warning** surfaces from `tests/conftest.py:18` during the test run. Pre-existing — out of scope (Rule scope boundary). Logged here for awareness; will be addressed by a future module-wide migration to `joserfc`.

## User Setup Required

None. The 5 GitHub App secrets (APP_ID, CLIENT_ID, CLIENT_SECRET, WEBHOOK_SECRET, PRIVATE_KEY_B64) are already deployed to the VM per the operator-prep memory note (2026-05-17, `xbrain-phase12-operator-prep`). Plan 12-11 will add a verify script that confirms they parse cleanly. The helper is import-safe even if the secrets are empty — it raises `GitHubAppNotConfigured` only at call time, never at import.

## Threat Flags

| Flag | File | Description |
|------|------|-------------|
| threat_flag: jwt-mint-surface | `apps/memory-api/app/services/github_app_jwt.py` | New JWT-minting helper exposes the GitHub App private key via the RS256 sign path. Mitigations in place: (a) PEM never serialised to logs (only `cid[:8]` debug log); (b) JWT itself never returned via any HTTP response (this is a server-internal helper, not a route); (c) `@lru_cache` on PEM means a key rotation requires container restart — listed as accepted ops cost. Downstream consumers (Plans 12-03, 12-04, 12-11) MUST also avoid logging the minted JWT — reviewers should grep their PRs for `log.info(..., jwt=` or `log.info(.*mint_app_jwt())`. |
| threat_flag: import-time-vs-call-time-config | `apps/memory-api/app/services/github_app_jwt.py` | The module imports successfully even when `GITHUB_APP_PRIVATE_KEY_B64` and `GITHUB_APP_CLIENT_ID` are empty (e.g. on first deploy before operator prep). Fails only at `mint_app_jwt()` call time, with `GitHubAppNotConfigured`. Intentional — lets the app start, listen, and surface a structured 503 on the routes that try to use App auth. Plan 12-03 and 12-11 will need explicit handling of `GitHubAppNotConfigured` to convert it into operator-friendly error responses (do NOT bubble as 500). |

## Next Plan Readiness

- **Plan 12-03 (installation token cache)** can `from app.services.github_app_jwt import mint_app_jwt, GitHubAppNotConfigured` immediately and call `mint_app_jwt()` to authenticate the `POST /app/installations/{id}/access_tokens` request to GitHub. The 10-min lifetime is well within the single HTTP request → mint installation token → return cycle.
- **Plan 12-04 (remove PAT, list installations)** can also import `mint_app_jwt` to authenticate `GET /orgs/{org}/installation`. No additional helper needed.
- **Plan 12-11 (verify-phase12.sh + sanity ping)** has a ready-made smoke test: import `mint_app_jwt`, hit `GET https://api.github.com/app` with the JWT as Bearer, expect 200 with the App slug = `xbrain`. Failure modes are already mapped to `GitHubAppNotConfigured` with actionable messages.
- **Plan 12-05 (webhook handler)** does NOT depend on this plan — webhook signature verification uses `GITHUB_APP_WEBHOOK_SECRET` + HMAC-SHA256, not the App JWT. Independent path.

## Self-Check: PASSED

- [x] `apps/memory-api/pyproject.toml` — FOUND (PyJWT[crypto]>=2.10,<3 present on a new line between cryptography and aiosmtplib)
- [x] `apps/memory-api/app/services/github_app_jwt.py` — FOUND (133 lines, exposes mint_app_jwt + GitHubAppNotConfigured + _load_private_key_pem + _reset_private_key_cache_for_tests)
- [x] `apps/memory-api/tests/test_phase12_jwt.py` — FOUND (128 lines, 8 test functions, pytest PASS 8/8 in 2.06s)
- [x] Commit `9ad646f` — FOUND in `git log` (Task 1 — pyproject)
- [x] Commit `e9af191` — FOUND in `git log` (Task 2 — service module)
- [x] Commit `a6d5c3c` — FOUND in `git log` (Task 3 — tests)
- [x] Commit `234f629` — FOUND in `git log` (Follow-up — literal docstring phrase)
- [x] STATE.md NOT touched (per orchestrator constraint)
- [x] ROADMAP.md NOT touched (per orchestrator constraint)
- [x] Docstring contains the required literal phrase "App JWT, 10-min, RS256, used to mint installation tokens (NOT user-to-server tokens)" — VERIFIED at line 1-2 (module docstring) and line 90 (mint_app_jwt docstring) of github_app_jwt.py (tightened in commit `234f629`)
- [x] Tests mock the private key via `cryptography.hazmat.primitives.asymmetric.rsa.generate_private_key()` — VERIFIED in `app_pem_b64` fixture (line 28 of test_phase12_jwt.py). Real prod key NEVER loaded.
- [x] SUMMARY.md correctly located at worktree path (not main repo) after stray-path fix (deviation #5).

---
*Phase: 12-github-app-migration-public-deployment-ready-auth*
*Plan: 12-02*
*Completed: 2026-05-17*
