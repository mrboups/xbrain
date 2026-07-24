---
phase: 25-team-join-by-code
verified: 2026-07-24T02:27:04Z
status: passed
score: 4/4 must-haves verified
overrides_applied: 0
---

# Phase 25: Team Join-by-Code Verification Report

**Phase Goal:** A team admin mints a shareable, revocable, expiring, max-uses-limited invite code; any authenticated user who submits it joins that team's chat. The code is a bearer secret to the team-scoped brain — stored hashed, returned once, every guard enforced at redemption, team_scope-bound.
**Verified:** 2026-07-24T02:27:04Z
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths (Success Criteria, ROADMAP.md Phase 25)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| SC1 | `POST /v1/teams/{id}/invite-codes` (admin only) mints a random `xbi_` code, stores ONLY its sha256 hash, returns plaintext exactly once; non-admin gets 403 | VERIFIED | `apps/memory-api/app/routes/teams.py:1264-1336` mint endpoint gated by `_require_team_admin` (routes/teams.py:1284). `app/repos/team_invite_codes.py:34-83` `generate_code()`/`mint_code()` persist only `code_hash`+`code_prefix`. Re-ran `pytest tests/test_join_by_code_gate.py -q` live against real Postgres testcontainer — **4 passed, 0 failed, 0 skipped** (29.17s). Gate block A directly reads the DB row (`_read_code`) and asserts `row["code_hash"] == sha256(plaintext)`, `plaintext not in row[col]` for every stored text column, and `"code_hash" not in mint` response. Block B proves non-admin 403 on mint/list/revoke. |
| SC2 | join-by-code adds caller to THAT team + increments uses; already-member is 200 no-op (uses unchanged); rate-limited; garbage code returns generic 404 identical to revoked/expired (no oracle) | VERIFIED | `routes/teams.py:390-476` `join_by_code`. Gate blocks D/E/F (test_join_by_code_gate.py:271-307) prove: valid join adds `team_members` row + `uses`→1; second join from same user is 200 no-op with `uses` still 1; garbage code → 404 with the SAME `detail` string captured as `generic_msg` and reused as the assertion oracle for every subsequent rejection (blocks G/H/I). Rate limit wired via `enforce_rate_limit(request, settings.JOIN_CODE_RATE_LIMIT, "join-by-code")` (routes/teams.py:408) before any DB work; `enforce_rate_limit` (app/services/rate_limit.py:43-52) is a real per-IP token-bucket check, not a stub. `grep -i mock` on the gate file returns 0 security-path matches (only descriptive prose about what a mocked test would miss). |
| SC3 | Every guard live: REVOKED/EXPIRED/max-uses-reached rejected; two racing redemptions of max_uses=1 → exactly one wins, uses ends at 1 (atomic conditional UPDATE, not read-then-write); team-A code can never add to team-B | VERIFIED | `redeem_atomic` (repos/team_invite_codes.py:97-127) is a single `UPDATE ... WHERE revoked_at IS NULL AND (expires_at IS NULL OR expires_at > :now) AND (max_uses IS NULL OR uses < max_uses) RETURNING ...` — the guard re-check is inside the UPDATE predicate, not a prior SELECT. Gate blocks G/H/I (lines 309-376) prove revoked/expired/max-uses-reached each 404. `test_double_spend_race_cannot_exceed_max_uses` (lines 407-522) drives `redeem_atomic` from **two independent `async_session_factory()` sessions via `asyncio.gather`** (line 476) — a true concurrent race, not sequential calls — and asserts `sum(results)==1`, `uses==1`, and membership XOR. Team isolation proven at gate block D: `_membership(joiner_id, team_b_slug) is None` against a real decoy team_b. Re-run confirmed **4 passed** including this race test. |
| SC4 | Migration 0027 (down_revision 0026, additive, no EDITION branch) upgrades clean under EDITION=oss AND saas; extension Settings has English-only mint/reveal-once/copy + paste-code-to-join rendered via textContent (not innerHTML); popup contract test extended and green | VERIFIED | `alembic/versions/0027_team_invite_codes.py`: `down_revision = "0026_team_member_last_read"`, `CREATE TABLE IF NOT EXISTS` (additive), zero occurrences of `EDITION` in the file. Re-ran `pytest tests/test_join_by_code_gate.py::test_migration_0027_team_invite_codes_forward_only` (parametrized oss+saas) as part of the 4-passed gate run, plus `pytest tests/test_migration_editions.py -q` → **4 passed** (confirms no branch-on-edition repo-wide). Extension: `chrome-extension/popup.html:293-325` `#invite-panel` overlay with mint/reveal/copy (`#invite-code-output`) + paste-code join (`#invite-join-code`); `popup.js:404` sets `$("invite-code-output").textContent = j.code` (never innerHTML — grep confirms no innerHTML use on the invite-code path). Re-ran contract test from a copy OUTSIDE `.claude/` (scratchpad, `{"type":"module"}` package.json) — `node tests/test_popup_contract.mjs` → **167 passed, 0 failed**; `node tests/run_tests.mjs` → **12/12 test files passed**. English-only guard included in that pass ("english-only: no accented Latin chars in popup.html + popup.js" → PASS); no French strings found in the invite surface. |

**Score:** 4/4 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `apps/memory-api/app/models/team.py::TeamInviteCode` | ORM model, D-25-02 columns, CASCADE FK to teams, CHECK role | VERIFIED | Lines 142-198. All columns present (`code_hash`, `code_prefix`, `role`, `created_by_user_id` SET NULL, `expires_at`, `max_uses`, `uses`, `revoked_at`, `created_at`). |
| `apps/memory-api/alembic/versions/0027_team_invite_codes.py` | migration, down_revision 0026, additive, no EDITION branch | VERIFIED | Confirmed above. `downgrade()` present for symmetry only. |
| `apps/memory-api/app/repos/team_invite_codes.py` | mint/get_by_hash/redeem_atomic/revoke/list | VERIFIED | All 6 functions present and substantive (generate_code, mint_code, get_by_hash, redeem_atomic, revoke_code, list_codes) — no stubs, real SQLAlchemy queries. |
| `apps/memory-api/app/routes/teams.py` (4 endpoints) | mint/list/revoke (admin) + join-by-code (any-auth) | VERIFIED | `POST /teams/{team_id}/invite-codes` (1264), `GET /teams/{team_id}/invite-codes` (1339), `DELETE /teams/{team_id}/invite-codes/{code_id}` (1370), `POST /teams/join-by-code` (390) — all wired to the repo, all audited, mint/list/revoke gated by `_require_team_admin`. |
| `apps/memory-api/app/config.py` JOIN_CODE knobs | rate limit + defaults | VERIFIED | `JOIN_CODE_RATE_LIMIT="10/minute"`, `JOIN_CODE_DEFAULT_EXPIRY_DAYS=7`, `JOIN_CODE_DEFAULT_MAX_USES=0` (lines 286-288), referenced live in routes/teams.py. |
| `apps/memory-api/tests/test_join_by_code_gate.py` | THE security gate, real-PG, non-mocked | VERIFIED | 680 lines, 3 test groups (HTTP gate, race, migration×2 editions). Re-run: **4 passed, 0 failed, 0 skipped**, 29.17s wall time (real container spin-up, not instant/mocked). |
| `apps/memory-api/tests/test_invite_code_repo_unit.py` | pure hash-at-rest unit test | VERIFIED | Re-run: **5 passed**. |
| `apps/memory-api/tests/test_migration_editions.py` | edition-branch guard | VERIFIED | Re-run: **4 passed** (includes `test_no_migration_branches_on_edition`). |
| `chrome-extension/popup.html/.js/.css` invite overlay | mint+reveal-once+copy, paste-code-join | VERIFIED | `#invite-panel`, `#btn-invite-mint`, `#invite-code-output`, `#btn-invite-copy`, `#invite-join-code`, `#btn-invite-join` all present and wired in `wireInvite()`. CSS uses `var(--mono)/--muted/--border/--radius` tokens, radius 0. |
| `chrome-extension/tests/test_popup_contract.mjs` | extended contract, 12 invite ids frozen | VERIFIED | 12 ids present under `// Plan 25-04` comment (lines 78-90 of contract test). Re-run from outside-`.claude` copy: **167 passed, 0 failed**. |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|----|--------|---------|
| `routes/teams.py::join_by_code` | `repos/team_invite_codes.redeem_atomic` | resolve `get_by_hash` → `redeem_atomic` → `teams_repo.add_member` | WIRED | Confirmed by reading routes/teams.py:410-460; confirmed live by the gate's block D (valid join) and block I (max-uses ceiling). |
| `routes/teams.py::mint_invite_code` | `repos/team_invite_codes.mint_code` | direct call, plaintext returned once in `InviteCodeMintOut.code` | WIRED | routes/teams.py:1305-1336; `InviteCodeOut` (list model) has NO `code`/`code_hash` field by construction — the secret cannot leak through the list endpoint even if a future dev forgets to scrub it. |
| `test_join_by_code_gate.py` | `repos/team_invite_codes.redeem_atomic` | two independent `async_session_factory()` sessions via `asyncio.gather` | WIRED (proven concurrent) | test_join_by_code_gate.py:454-483; not sequential calls dressed up as a race — genuinely two live DB connections racing the same row-locked UPDATE. |
| `test_join_by_code_gate.py` | migration `0027_team_invite_codes` | `alembic upgrade head` under patched `EDITION=oss`/`saas`, fresh `PostgresContainer` per edition | WIRED | test_join_by_code_gate.py:564-679; probes `information_schema.columns` + `pg_indexes` directly, not ORM introspection. |
| `popup.js::mintInvite` | `POST /v1/teams/{id}/invite-codes` | `fetch(...)` + `.json()` + `$("invite-code-output").textContent = j.code` | WIRED | popup.js:378-420; response consumed and rendered, not discarded. |
| `popup.js::joinByCode` | `POST /v1/teams/join-by-code` | `fetch(...)` + refresh team list on success | WIRED | popup.js:445-485; on 200 calls `refreshTeamsAfterJoin()` which either re-`boot()`s or re-fetches `my-teams` — the UI actually reflects the new membership, not just a toast. |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|---------------------|--------|
| `#invite-code-output` (popup.js) | `j.code` from mint response | `POST /invite-codes` → `InviteCodeMintOut.code` = `plaintext` from `repos.mint_code` → `generate_code()` (CSPRNG `secrets.token_urlsafe(24)`) | Yes | FLOWING |
| `#invite-join-status` (popup.js) | `j.display_name` from join response | `POST /join-by-code` → `JoinByCodeOut.display_name` = `team.display_name` read from the real `teams` table via `teams_repo.get_team_by_id` | Yes | FLOWING |
| `list_invite_codes` response | `r.code_prefix`, `r.uses`, etc. | `repos.list_codes` → real `SELECT ... FROM team_invite_codes WHERE team_id = ...` | Yes | FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Security gate runs green against real Postgres (SKIP=FAIL) | `MSYS_NO_PATHCONV=1 python -m pytest tests/test_join_by_code_gate.py -q` | `4 passed, 4 warnings in 29.17s` | PASS |
| Repo unit test (pure, hash-at-rest) | `python -m pytest tests/test_invite_code_repo_unit.py -q` | `5 passed, 1 warning in 1.18s` | PASS |
| Migration-editions guard (repo-wide, no branch) | `python -m pytest tests/test_migration_editions.py -q` | `4 passed, 9 warnings in 22.95s` | PASS |
| Popup contract test (outside `.claude/`) | `node tests/test_popup_contract.mjs` | `167 passed, 0 failed` | PASS |
| Full extension node suite (outside `.claude/`) | `node tests/run_tests.mjs` | `12/12 test files passed` | PASS |
| Task commits exist in git history | `git log --oneline --all \| grep -E "f223048\|f006a95\|77e8960\|0200cb6\|95a09c9\|46a51b2\|94a0225\|5991677"` | all 8 commits found | PASS |
| No mocking on the security gate's redemption path | `grep -in "mock" tests/test_join_by_code_gate.py` | 3 hits, all descriptive prose ("A mocked-DB test... would pass even with security broken") — zero `Mock(`/`MagicMock`/`monkeypatch.setattr` calls | PASS |
| No innerHTML on the invite-code reveal path | `grep -n innerHTML popup.js` (invite lines) | 0 matches on `#invite-code-output`; all other `innerHTML` hits are unrelated pre-existing UI (team selector loading states) | PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| JOINCODE-01 | 25-01, 25-02, 25-03, 25-04 | Team admin mints shareable hashed bearer-secret invite code; any authenticated user redeems to join; revocable/expiring/max-uses-limited; every guard enforced at redemption; team_scope-bound; mint/revoke/list admin-gated; join rate-limited + idempotent | SATISFIED | All 4 SC verified above with live re-run evidence. No orphaned requirement IDs found in REQUIREMENTS.md for Phase 25 beyond JOINCODE-01. |

### Anti-Patterns Found

None. Scanned `routes/teams.py`, `repos/team_invite_codes.py`, `models/team.py`, `0027_team_invite_codes.py`, `popup.js`, `popup.html` for TODO/FIXME/HACK/placeholder/"not yet implemented"/empty-return stubs on the invite-code surface — zero hits. `Known Stubs: None` claims in 25-02/25-03/25-04 SUMMARY.md are corroborated by direct code reading, not merely trusted.

### Deferred Items Honored (per 25-CONTEXT.md)

| Deferred item | Confirmed absent |
|---|---|
| Email-delivery of invite codes | No SMTP/email-send code path touches `team_invite_codes`; the only email references in teams.py belong to the pre-existing, unrelated invite-by-email flow. |
| Hosted `/join/<code>` landing page | No route, no app-site page, no file matching `*join*` outside the existing `team_join_requests`/test/join-by-code naming. |
| app-site (`app-site/account/teams`) invite-code UI | `grep -rn "invite-code\|join-by-code" app-site/` → 0 matches. |
| Per-code analytics beyond `uses` | `InviteCodeOut` carries only `uses` (a plain counter), no analytics fields. |

### Human Verification Required

None. All 4 success criteria and all must-haves were verified via direct code reading plus live re-execution of the real test suites (backend against real Postgres, extension contract tests from an isolated outside-`.claude/` copy) — no visual, real-time, or subjective behavior remains unverified.

### Gaps Summary

No gaps. All 4 ROADMAP success criteria for Phase 25 are verified with fresh, real evidence (not merely SUMMARY.md claims): the security gate was re-run live against a real Postgres testcontainer (4/4 passed, including the true concurrent double-spend race via `asyncio.gather` over two independent sessions), the repo unit and migration-editions tests were re-run (5/5, 4/4), and the extension popup contract + full suite were re-run from a copy outside `.claude/` (167/167, 12/12). The gate file contains no mocking on any security-bearing path. The atomic redeem is a genuine single conditional `UPDATE ... RETURNING`, not read-then-write. Team isolation, idempotency, no-oracle generic-404, admin-gating, hash-at-rest, and the migration's edition-agnostic additive shape are all directly observable in the code and confirmed by live test execution. Deferred scope items (email delivery, hosted join landing page, app-site UI, per-code analytics) are confirmed genuinely absent, matching the CONTEXT.md deferral list.

---

_Verified: 2026-07-24T02:27:04Z_
_Verifier: Claude (gsd-verifier)_
