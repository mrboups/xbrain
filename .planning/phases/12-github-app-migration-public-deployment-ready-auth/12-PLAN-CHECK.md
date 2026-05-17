# Phase 12 Plan-Check -- Iteration 1

**Phase:** 12 -- GitHub App Migration (Public-Deployment-Ready Auth)
**Date:** 2026-05-17
**Checker:** gsd-plan-checker (Revision Gate, max 3 iterations)
**Plans checked:** 12-01 through 12-11 (11 plans)
**Verdict:** REVISE -- 5 blockers, 6 majors, 4 minors must be addressed before /gsd-execute-phase 12

---

## 1. Goal-Backward Trace

### Phase Goal (verbatim from ROADMAP.md line 286)

> Migrate xbrain authentication from OAuth App to GitHub App so the platform is ready for public deployment. GitHub Apps support multiple callback URLs natively (eliminating the per-frontend OAuth App proliferation), use short-lived installation tokens (eliminating long-lived GITHUB_API_PAT and unbounded user tokens), enable org-level installation, and unlock higher rate limits per installation. Clean break -- no dual-auth maintained; the single existing user (mrboups) re-authorizes once via the new GitHub App.

### Success Criteria Coverage (8 SC from ROADMAP)

| SC | Criterion (summary) | Plans | Status |
|----|--------------------|-------|--------|
| SC-1 | Web + ext sign-in both via new GitHub App, no legacy client_id active | 12-06, 12-08, 12-09, 12-10 | Covered |
| SC-2 | Org install flow populates installations table; auto-grant preserved | 12-05, 12-06, 12-07 | Partially -- see B-1 |
| SC-3 | 8h user token expiry triggers transparent refresh | 12-06 | Covered |
| SC-4 | /orgs/{org}/members/{username} works via installation token | 12-03, 12-04 | Partially -- see B-1 |
| SC-5 | team_org_blocks + auto-grant semantics unchanged from Phase 10 | (none explicit) | Missing -- see B-3 |
| SC-6 | mrboups re-authorizes once; same teams, same brain | 12-06, 12-11 UAT | Covered |
| SC-7 | GITHUB_API_PAT removed from .env.example + docker-compose + runtime | 12-04, 12-01 | Partially -- see B-1 |
| SC-8 | verify-phase12.sh PASS N/N with 8 sub-assertions | 12-11 (17 assertions) | Covered |

### GHAPP Requirement Coverage (8 reqs from ROADMAP)

| Req | Description (summary) | Plans | Status |
|-----|----------------------|-------|--------|
| GHAPP-01 | Create new GitHub App + multi-callback + min permissions + PEM | 12-08 (keypair), 12-11 Task 3 (operator runbook) | Covered (operator-side) |
| GHAPP-02 | Backend JWT signing + installation token cache | 12-02, 12-03 | Covered |
| GHAPP-03 | installations table + webhook handler | 12-01, 12-05 | Covered |
| GHAPP-04 | Org-membership via installation token + remove GITHUB_API_PAT | 12-04 | Partially -- B-1 (incomplete consumer migration) |
| GHAPP-05 | User-to-server refresh flow + token storage | 12-01 (schema), 12-06 (logic) | Covered |
| GHAPP-06 | Install flow UI + banner | 12-06 (API surface), 12-07 (app-site banner) | Partially -- M-1 (org_login missing) |
| GHAPP-07 | Update frontend client_id + manifest key | 12-08, 12-09 | Covered |
| GHAPP-08 | Remove OAuth App xbrain from active code path + docs/auth.html | 12-10 | Partially -- M-2 (wrong docs filename) |

---

## 2. Findings

### BLOCKERS (must fix before execution)

---

**B-1 [12-04] GITHUB_API_PAT consumer migration is INCOMPLETE -- routes/me_github.py + routes/teams.py NOT in Plan 12-04 scope**

Plan 12-04 Section 0 includes a grep advisory ("If grep reveals additional consumers, surface them ... they MUST be migrated too"). Reality verified on current codebase (2026-05-17):

```
apps/memory-api/app/auth.py:130             check_github_org_membership signature
apps/memory-api/app/deps.py:108,111         gho_ branch caller (covered by 12-04 Task 2)
apps/memory-api/app/routes/me_github.py:55  link-github calls check_github_org_membership(..., PAT)
apps/memory-api/app/routes/teams.py:210,226 my-teams enriches via raw httpx + PAT
apps/memory-api/app/routes/teams.py:323,340 github-matches lists orgs via PAT
apps/memory-api/app/routes/teams.py:362,372 my-github-orgs lists user orgs via PAT
apps/memory-api/tests/test_onboarding_routes.py:15  setdefault PAT (test-only)
```

The advisory grep was correct, but Plan 12-04 `files_modified` lists only `auth.py`, `deps.py`, `config.py`, `team_autogrant.py`, `.env.example`, `docker-compose.yml`. It does NOT list `routes/me_github.py` or `routes/teams.py`. Task 3 then removes `GITHUB_API_PAT` from config.py -- which makes 8 references in me_github.py + teams.py raise `AttributeError: 'Settings' object has no attribute 'GITHUB_API_PAT'` at first call. Phase 5/7/10 endpoints break at runtime:
- POST /v1/me/link-github
- GET /v1/teams/my-teams
- GET /v1/teams/github-matches
- GET /v1/teams/my-github-orgs

team_autogrant.py is correctly left out (verified: zero matches in app/services/team_autogrant.py). But the silent omission of me_github.py and teams.py is exactly the failure mode RESEARCH Runtime State Inventory flagged.

**Fix:**
- Add `apps/memory-api/app/routes/me_github.py` and `apps/memory-api/app/routes/teams.py` to Plan 12-04 files_modified.
- Add Task 2b: rewrite the 4 endpoints in teams.py (my-teams enrichment, github-matches, my-github-orgs, plus `_resolve_github_username`) to use `get_installation_token_for_org(session, team.github_org)` instead of `settings.GITHUB_API_PAT`. For `_resolve_github_username` (which calls GET /user/{id}) -- that endpoint works via App JWT alone (no installation needed), so use `mint_app_jwt()` directly.
- Add Task 2c: rewrite me_github.py link_github to call the new `check_github_org_membership(session, body.github_token, settings.GITHUB_ORG)` signature, AND remove the redundant /user follow-up call (me_github.py:74-84) since the new helper already returns login.
- Add a grep gate to Task 4 acceptance: `grep -rn "GITHUB_API_PAT" apps/memory-api/app/ --include="*.py" | grep -v __pycache__ | wc -l` must equal 0.

**Dimension:** D1 requirement_coverage / D4 key_links_planned
**Severity:** BLOCKER

---

**B-2 [12-04 + 12-10] LibreChat OAuth App link-github flow regression -- "untouched" decision violated by side-effect of B-1**

CONTEXT.md locks: "xbrain LibreChat Client ID Ov23li0XHV3NL8Git7Dk remains untouched -- separate concern." Plans 12-01 + 12-04 + 12-10 honor this verbally. But:

routes/me_github.py imports check_github_org_membership at line 23 -- the function Plan 12-04 rewrites. The rewrite changes signature from `(github_token, org, server_pat)` to `(session, github_user_token, org)`. After Plan 12-04 ships, the unmigrated me_github.py call site raises TypeError. The endpoint is used to link a Google-authenticated user to their GitHub-via-LibreChat account -- this regresses Phase 5 GHA-04 which is explicitly out-of-scope-but-preserved per CONTEXT.md.

**Fix (combined with B-1):**
- B-1's required edits to me_github.py migrate the call site to the new signature, eliminating the regression.
- Confirm test coverage: tests/test_phase12_org_membership.py (Plan 12-04 Task 4) should also test the link-github flow path -- import the route's logic OR add an integration test that POSTs /v1/me/link-github with mocked GitHub responses.

**Dimension:** D7 context_compliance (LibreChat untouched is locked; this would touch it as a regression)
**Severity:** BLOCKER

---

**B-3 [phase] SC-5 (team_org_blocks + auto-grant preservation) has ZERO regression test in any plan**

ROADMAP SC-5: "team_org_blocks + auto-grant team membership semantics unchanged from Phase 10 (regression tests pass): blocked GitHub login still cannot join even if org is installed; auto-grant still triggers on first sign-in for installed-org members."

The team_org_blocks table (Phase 10 migration 0016) blocks specific GitHub logins from auto-joining teams. auto_grant_via_org_match() consults it. Plan 12-06 modifies routes/auth_github.py (signin handler) and calls auto_grant_via_org_match(...) preserved-as-is. The blocking semantics depend on:
1. auto_grant_via_org_match correctly consulting team_org_blocks (Phase 10 logic -- assumed intact).
2. The new install_required branch NOT short-circuiting before auto-grant runs.

Looking at the proposed signin flow in 12-06 Task 3: `_resolve_or_merge_user -> auto_grant_via_org_match -> install-status check -> return`. This LOOKS correct, but:
- No plan adds a regression test asserting "blocked user with installed org gets NO team membership."
- No plan adds a regression test for "newly-joining user with installed org auto-grants via existing Phase 10 path."
- test_phase12_signin_install_flow.py (12-06 Task 6) tests install_required vs not, but does NOT verify the auto-grant side-effect.
- verify-phase12.sh (12-11) has 17 assertions; none cover team_org_blocks.

This is a Phase 10 regression risk that the SC explicitly calls out, and no test in Phase 12 will catch it.

**Fix:**
- Add test_phase12_auto_grant_regression.py to Plan 12-06: (a) user.github_login in team_org_blocks for an installed org -> POST /v1/auth/github/signin returns xbt_token but zero teams_joined; (b) user.github_login NOT blocked, org installed -> teams_joined contains the org's team slug.
- Add assertion 18 to verify-phase12.sh in Plan 12-11: POST a sign-in for a fixture user with org_login in team_org_blocks -> response.user.teams_joined == [].

**Dimension:** D1 requirement_coverage (SC-5)
**Severity:** BLOCKER

---

**B-4 [12-08 + 12-09] Operator-assisted setup gate mis-worded; risks executor halt-and-loop**

Plan 12-08 Section 0 ABORT instruction: "If the operator has NOT yet generated the keypair or registered the App, ABORT this plan. Execute Plan 12-11 Task 1 (operator runbook) FIRST."

Plan 12-11 depends_on: ["12-01"..."12-10"], wave: 9. So Plan 12-08 (wave 6) cannot literally execute Plan 12-11 (wave 9) before itself. The runbook itself is for the OPERATOR to execute manually -- it doesn't need to be authored as a file in the repo first. The cross-reference is informational only.

But the ABORT instruction says "Execute Plan 12-11 Task 1 FIRST" -- this is mis-phrased: it means "have the operator perform the steps documented in 12-11 Task 3 (operator runbook)" -- NOT "execute plan 12-11 in advance of 12-08." Same mis-wording in 12-09 Section 0.

Mis-wording risks the executor halting and looping back to plan-check thinking there is a wave dependency.

**Fix:**
- Reword 12-08 Section 0 ABORT instruction: "If the operator has NOT yet (a) generated the Chrome ext keypair (see 12-08 Task 1 commands inline) AND (b) registered the GitHub App on github.com with the keypair-derived callback URL AND provided the new client_id, ABORT. These manual steps are documented in .planning/KB/github-app-operator-runbook.md (authored by Plan 12-11) but operator can follow the inline guidance in this plan too."
- Same fix for 12-09 Section 0 (which depends on operator providing the same client_id).

**Dimension:** D9 operator_assisted gate clarity
**Severity:** BLOCKER (would cause execution halt + revision-gate loop)

---

**B-5 [12-06 Task 3] signin_github() rewrite described in prose-style sequence without unified diff; misorder risk**

Plan 12-06 Task 3 describes the auth_github.py rewrite as a list of "AFTER X, call Y" instructions. Looking at existing routes/auth_github.py:316-322:

```python
user = await _resolve_or_merge_user(session, github_id=..., login=..., display_name=..., email=...)
newly_joined = await auto_grant_via_org_match(session, user=user, github_login=..., github_org_logins=...)
xbt = await _mint_xbt_for_user(session, user.id)
await session.commit()  # line 332
```

The new instructions:
- After `_resolve_or_merge_user`, call `persist_tokens_on_signin(session, user, ...)`.
- After `auto_grant_via_org_match`, check primary org's installation status (which calls `check_github_org_membership(session, ...)` from Plan 12-04).
- Add install_required + install_url to SigninGithubOut return.

The plan does not show the FULL revised signin_github function body. An executor following the prose verbatim might:
- Place check_github_org_membership(session, token_body["access_token"], primary_org) -- which calls /user again. Wasteful but not wrong.
- Miss that the route's `await session.commit()` (line 332) happens BEFORE _mint_xbt_for_user OR after auto_grant. The new persist_tokens_on_signin mutates user but doesn't commit (correct per the helper docstring); the route's existing commit must come AFTER all mutations.
- Misplace install-status check relative to auto_grant (order matters for SC-2 + SC-5 semantics).

**Fix:**
- Plan 12-06 Task 3 should include a FULL after-diff of signin_github(...) body (15-25 lines) instead of textual instructions like "AFTER X, call Y." The executor copies-pastes; no interpretation risk.
- Acceptance: assert specific call sequence in the diff: _exchange_code_for_token -> _fetch_github_profile -> _resolve_or_merge_user -> persist_tokens_on_signin -> auto_grant_via_org_match -> install-status check -> _mint_xbt_for_user -> session.commit() -> background_tasks -> return.

**Dimension:** D2 task_completeness
**Severity:** BLOCKER (ambiguous sequencing = silent bugs)

---


### MAJORS (should fix before execution)

---

**M-1 [12-06 + 12-07] org_login for install banner UX is not in API response -- 12-07 banner says "your organization" generically**

Plan 12-07 Task 2 inline TODO: "Derive org_login from install_url ... For v1, fall back to a generic message". GHAPP-06 specifies "Banner messaging in app-site/account/teams/index.html" -- generic copy degrades UX. RESEARCH Q8 install-flow sequence shows the install URL would include the org context for the user.

**Fix:** Plan 12-06 Task 3 should add `org_login: str | None` to SigninGithubOut (the primary_org being install-required). Plan 12-07 Task 2 consumes it.

**Severity:** MAJOR

---

**M-2 [12-10 Task 2] Plan references marketing-site/docs/auth.html but actual file is marketing-site/docs/github-auth.html**

Verified by `ls marketing-site/docs/`: agents.html, api-reference.html, architecture.html, brain-monitor.html, chat.html, chrome-ext.html, configuration.html, crm.html, deployment.html, drive-sync.html, **github-auth.html**, graphiti.html, index.html, mcp-tools.html, meetings.html, memory.html, onboarding.html, tasks.html, teams.html.

auth.html does NOT exist. ROADMAP GHAPP-08 says "Document migration in docs/auth.html" -- the roadmap is also wrong, but the actual file to update is github-auth.html.

Plan 12-10 "If MISSING: Create a minimal version" would result in a NEW auth.html orphan file while the canonical github-auth.html stays stale.

**Fix:** Plan 12-10 Task 2 -- change file path to `marketing-site/docs/github-auth.html`. Update content to describe Phase 12 GitHub App flow (replacing the Phase 10 OAuth App content).

**Severity:** MAJOR

---

**M-3 [12-01 Task 2] "DateTime import already present per the Read" is parenthetical, not a hard pre-task check**

Verified by reading apps/memory-api/app/models/user.py:6 -- imports are `from sqlalchemy import BigInteger, DateTime, ForeignKey, String, func`. DateTime IS imported, so the specific concern is false-positive on this codebase. HOWEVER:

The plan parenthetical "verify before editing" instruction leaves room for executor skipping the verification. Phase 11 plan-checker found similar parenthetical risks become real bugs.

**Fix:** Convert to explicit pre-task step: "Before editing, run `grep -n 'from sqlalchemy import' apps/memory-api/app/models/user.py` and confirm DateTime is in the import list. If not, add it."

**Severity:** MAJOR

---

**M-4 [12-03/12-04] 401-on-membership-check (vs 401-on-mint) has no retry path**

Plan 12-03 Task 1 places the 401 retry in get_installation_token_for_org (which combines find+mint). The retry catches the exception from get_installation_token(inst_id) and force-refreshes -- correct for the mint endpoint.

BUT the actual call by Plan 12-04 (check_github_org_membership) uses the token NOT in this combined helper but in a downstream `/orgs/.../members/...` call. A 401 from THAT endpoint is also possible (token revoked between mint and use) -- the 401-on-membership-check has no retry path. Probability is low for v1 but worth documenting.

**Fix:** Document in 12-03 Section 4 Risks that retry is only at mint layer; for membership-check 401s, the caller (12-04) should optionally retry. Alternative: 12-04 could wrap its `httpx.get(/members/...)` in a try/except for 401 with force_refresh retry.

**Severity:** MAJOR

---

**M-5 [12-06 Task 4 deps.py O(n) lookup] "decrypt one at a time" pattern is unstable with multiple test users**

deps.py ghu_ branch iterates all users with non-null github_access_token_enc. Plan documents this as O(n) acceptable for n=1 (mrboups). But the test suite seeds multiple test users; for the test fixture parallel asyncio cases, iteration could match the wrong user if encryption keys collide.

Edge case: a test creates user_A with token ghu_a, then user_B with token ghu_b. A request arrives with ghu_a. The loop tries to decrypt user_B token first (DB iteration order is unpredictable without ORDER BY); decryption succeeds with the SAME FERNET_KEY -> returns ghu_b plaintext -> != "ghu_a" -> continue. Then decrypts user_A -> matches. Slow but correct.

But: if a test happens to seed two users with the same plaintext token (unlikely but possible), the FIRST hit wins -- silent incorrectness.

**Fix:** Add `ORDER BY id DESC` to the SELECT, and document the limit clearly. Better: in the test fixture, generate unique random plaintext tokens for each user.

**Severity:** MAJOR (low probability, would surface as flaky test failures)

---

**M-6 [12-11 Task 3 KB doc] github-app-architecture.md claims GITHUB_API_PAT removed -- TRUE only if B-1 is fixed**

The KB doc claims a cleaner reality than the plans deliver. If B-1 is not fixed, this KB is misleading from day one.

**Fix:** After B-1 is fixed, KB is accurate as written. If B-1 stays unfixed, this line must be qualified ("...except in routes/me_github.py and routes/teams.py which still use GITHUB_API_PAT pending future migration"). Best path: fix B-1.

**Severity:** MAJOR (KB-truthiness)

---

### MINORS

---

**MINOR-1 [12-01 Task 4]** Migration smoke test uses existing tests/conftest.py DB fixture; the `users` schema requires `source_user_id` (String 256, unique, NOT NULL). Test "INSERT users without github_access_token_enc succeeds" assertion needs a fixture providing unique source_user_id.

**Fix:** Specify INSERT row shape: (id=gen_random_uuid(), source_user_id='test:0019', email='t@x.io').

**Severity:** MINOR

---

**MINOR-2 [12-05 Task 3]** Test file uses `client: AsyncClient` fixture but conftest fixture name unverified. The fixture may be `client`, `async_client`, `http_client`, or absent (Phase 11 plan-checker found similar mismatches).

**Fix:** Plan 12-05 Task 3 author should read tests/conftest.py BEFORE finalizing the test code template, surface the actual fixture name.

**Severity:** MINOR

---

**MINOR-3 [12-04 Task 4 grep gate]** Plan does not address GITHUB_API_PAT reference in tests/test_onboarding_routes.py:15 (setdefault to empty). After B-1, the test fixture defaulting should also be cleaned up.

**Fix:** Add tests/ grep to Task 4 acceptance and clean up dead test reference (the setdefault is harmless but stale documentation).

**Severity:** MINOR

---

**MINOR-4 [12-09 Section 0]** Verify step uses bash env `$NEW_GITHUB_APP_CLIENT_ID` which is not set when the executor reads the plan -- ambiguity.

**Fix:** Phrase as: "Before executing this plan, the operator MUST `export NEW_GITHUB_APP_CLIENT_ID=Iv23li...` in the executor shell. The next command sanity-checks the export:"

**Severity:** MINOR

---


## 3. Wave / Dependency Analysis

| Wave | Plans | Parallel Safety | Verdict |
|------|-------|----------------|---------|
| 1 | 12-01 | N/A (solo) | Valid |
| 2 | 12-02 | N/A (solo, depends on 12-01) | Valid |
| 3 | 12-03 | N/A (solo, depends on 12-01, 12-02) | Valid |
| 4 | 12-04 | N/A (solo) | Valid graph; B-1 incomplete content |
| 5 | 12-05, 12-06 (PARALLEL) | Files disjoint: 12-05 (routes/webhooks_github.py, repos/installations.py, main.py, tests/test_phase12_webhook.py) vs 12-06 (services/{github_user_token,token_crypto}.py, routes/auth_github.py, deps.py, tests/test_phase12_*) | SAFE -- confirmed disjoint |
| 6 | 12-07, 12-08 (PARALLEL) | Files disjoint: 12-07 (app-site/account/teams/{teams.js,index.html}) vs 12-08 (chrome-extension/*, .planning/KB/chrome-extension-key.md) | SAFE -- BUT 12-08 has B-4 mis-worded operator gate |
| 7 | 12-09 | depends_on: ["12-07"] correct (shared teams.js) | Valid |
| 8 | 12-10 | depends_on: ["12-04", "12-06", "12-08", "12-09"] -- cleanup-only | Valid |
| 9 | 12-11 | depends_on: ALL prior | Valid |

**Dependency graph correctness:** No cycles. Forward references valid. Wave numbers consistent with depends_on.

**One soft issue:** Plan 12-05 could theoretically run in Wave 3 (only depends on 12-01, 12-02) but the planner placed it in Wave 5 -- documented as "to keep the dep graph readable." Acceptable conservative choice; no correctness issue.

---

## 4. RESEARCH Integration Verification

| Pitfall / Pattern | Plan(s) | Verdict |
|-------------------|---------|---------|
| Pitfall 1: 3-token conflation | 12-02 (App JWT), 12-03 (Installation token), 12-06 (User-to-server) -- each helper named after its token type | Honored |
| Pitfall 2: Webhook delivery best-effort + reconciliation | 12-03 find_installation_for_org hybrid lookup (DB then GitHub fallback) | Honored |
| Pitfall 3: installation vs installation_target | 12-05 subscribes to installation + installation_repositories only | Honored |
| Pitfall 4: 302 on /orgs/{org}/members/{user} | 12-04 keeps "204 = member, anything else = not" -- 302 handled by 204-check | Honored |
| Pitfall 5: Raw body before Pydantic for webhook HMAC | 12-05 Task 2 uses Request: Request + await request.body() before json.loads | Honored |
| Pitfall 6: Single-use refresh token race | 12-06 Task 2 uses per-user asyncio.Lock, re-reads inside lock | Honored |
| Pitfall 7: Chrome ext key field changes runtime ID | 12-08 KB documents operator must reload + communicate to mrboups | Honored |
| Pitfall 8: Webhook IP allowlisting | 12-05 explicitly Section 5 out-of-scope: "NOT implemented (HMAC is the auth)" | Honored |
| Library: PyJWT[crypto] (NOT python-jose) | 12-02 Task 1 specifies PyJWT[crypto]>=2.10,<3 | Honored |
| Library: Hybrid lookup webhook+on-demand | 12-03 find_installation_for_org | Honored |
| Library: Per-user asyncio.Lock | 12-06 Task 2 (_REFRESH_LOCKS) | Honored |
| Chrome ext key bash one-liner | 12-08 Task 1 + KB doc reproduce RESEARCH Q6 commands verbatim | Honored |

**Critical pattern: Installation token end-to-end works.** Verified Plan 12-03 get_installation_token matches RESEARCH Q3 + Q14 + the Code Example. The cache TTL (55min) + force_refresh + double-check inside lock pattern is correct.

---

## 5. CONTEXT.md Decision Compliance

| Decision | Plans that honor / contradict | Verdict |
|----------|-------------------------------|---------|
| Sequencing: Phase 12 after Phase 11 | 12-01 Section 0 hard gate against alembic head >= 0018_brain_events_view | Honored |
| Clean break (no dual-auth) | 12-04 removes GITHUB_API_PAT, 12-06 replaces auth_github.py, 12-10 cleanup | Mostly honored -- B-1 leaves dual-PAT-installation hybrid in teams.py/me_github.py |
| App owner: mrboups personal | 12-11 operator runbook Step 2 uses github.com/settings/apps (personal) | Honored |
| Permissions minimal (read:org + user:email + read:user) | 12-11 Step 2 specifies Members:Read + Email:Read + Profile:Read + UNCHECK "all others" | Honored |
| Chrome ext stable ID via manifest key NOW, Web Store DEFERRED | 12-08 implements key field, KB documents Store-publish deferred | Honored |
| mrboups force re-authorize once | 12-11 UAT Step 1 + 12-10 KB rollback section both expect mrboups to re-auth | Honored |
| LibreChat OAuth App xbrain LibreChat UNTOUCHED | 12-10 Task 1 config.py comment preserves GITHUB_CLIENT_ID for LibreChat | Mostly honored -- B-2 risk that B-1 breaks LibreChat link-github by side effect |
| Chrome Web Store publish DEFERRED | All plans treat as out-of-scope | Honored |
| repo:read permission DEFERRED | No plan adds repo perms | Honored |

---

## 6. Dimension Summary

| Dimension | Result | Notes |
|-----------|--------|-------|
| D1: Requirement Coverage | FAIL | B-1 (incomplete PAT migration), B-3 (no SC-5 regression test), M-1 (org_login UX) |
| D2: Task Completeness | FAIL | B-5 (ambiguous sequencing in 12-06 Task 3), M-3 (defensive verify) |
| D3: Dependency Correctness | PASS | No cycles, wave order valid |
| D4: Key Links Planned | FAIL | B-1 (teams.py/me_github.py callers unwired) |
| D5: Scope Sanity | PASS | All plans 1-6 tasks, file counts within budget |
| D6: Verification Derivation | PASS | must_haves implicit via 12-11 17 assertions |
| D7: Context Compliance | FAIL | B-2 (LibreChat untouched is violated by silent regression) |
| D7b: Scope Reduction | PASS | No "v1/static/simplified" language found |
| D7c: Architectural Tier | PASS | App JWT signing server-side, installation token cache in API tier, manifest key client-side |
| D8: Nyquist | SKIPPED (nyquist_validation disabled per config.json defaults) |
| D9: Cross-Plan Data Contracts | PASS | No conflicting transforms on shared data |
| D10: CLAUDE.md Compliance | PASS | App/code in English (UI banner text verified English), OSS-only deps |
| D11: Research Resolution | PASS | RESEARCH Open Questions all RESOLVED |
| D12: Pattern Compliance | SKIPPED (no PATTERNS.md for this phase) |
| Migration safety (chain) | PASS | 0018 -> 0019 only, downgrade() included |
| Operator-assisted gates | FAIL | B-4 wording risk in 12-08 / 12-09 Section 0 |
| Reversibility / staging | PASS | 12-10 cleanup AFTER 12-09 swap, 12-11 verify on VM |

---

## 7. Structured Issues (YAML)

```yaml
issues:
  - plan: "12-04"
    dimension: requirement_coverage
    severity: blocker
    description: "GITHUB_API_PAT consumer migration incomplete -- routes/me_github.py (1 ref) and routes/teams.py (6 refs) NOT in files_modified. Removing GITHUB_API_PAT in Task 3 will break link-github + 3 teams endpoints."
    task: null
    fix_hint: "Add me_github.py + teams.py to files_modified. Add Task 2b: rewrite teams.py endpoints to use get_installation_token_for_org or mint_app_jwt. Add Task 2c: rewrite me_github.py link_github call. Acceptance grep gate must equal 0 in app/."

  - plan: "12-04"
    dimension: context_compliance
    severity: blocker
    description: "LibreChat OAuth App link-github flow (out-of-scope-but-preserved) silently breaks because me_github.py call site uses the legacy check_github_org_membership signature."
    task: null
    fix_hint: "Combined with previous fix -- migrate me_github.py to new signature. Add integration test for link-github route in test_phase12_org_membership.py."

  - plan: "phase"
    dimension: requirement_coverage
    severity: blocker
    description: "SC-5 (team_org_blocks + auto-grant regression) has zero test coverage. No plan asserts blocked user gets empty teams_joined, no plan asserts unblocked installed-org user auto-grants."
    task: null
    fix_hint: "Add test_phase12_auto_grant_regression.py to Plan 12-06 with two cases. Add assertion 18 to verify-phase12.sh in Plan 12-11."

  - plan: "12-08"
    dimension: operator_gate_clarity
    severity: blocker
    description: "Section 0 ABORT says 'Execute Plan 12-11 Task 1 FIRST' but 12-11 is wave 9 (after 12-08 wave 6). Mis-wording risks executor loop. Also affects 12-09."
    task: null
    fix_hint: "Reword 12-08 / 12-09 Section 0: operator must complete the runbook STEPS manually (not execute plan 12-11); reference runbook doc optionally."

  - plan: "12-06"
    dimension: task_completeness
    severity: blocker
    description: "Task 3 describes signin_github() rewrite in prose-style sequence (AFTER X, call Y) without a unified diff. Misorder risk on persist_tokens_on_signin vs auto_grant vs install-status check vs session.commit."
    task: 3
    fix_hint: "Provide full after-diff of signin_github() function body. Acceptance: assertion on exact call sequence in the new code."

  - plan: "12-06"
    dimension: task_completeness
    severity: major
    description: "Banner UX uses generic 'your organization' because SigninGithubOut does not return org_login. GHAPP-06 banner experience is degraded."
    task: 3
    fix_hint: "Add org_login: str | None field to SigninGithubOut. 12-07 Task 2 consumes it."

  - plan: "12-10"
    dimension: task_completeness
    severity: major
    description: "Task 2 references marketing-site/docs/auth.html but actual file is marketing-site/docs/github-auth.html (verified ls). 'If MISSING create' would orphan a new file."
    task: 2
    fix_hint: "Change file path to marketing-site/docs/github-auth.html. Replace Phase 10 OAuth App content with Phase 12 GitHub App content."

  - plan: "12-01"
    dimension: task_completeness
    severity: major
    description: "Task 2 'DateTime import already present per Read' is parenthetical, not enforced. Defensive style requires hard pre-task check."
    task: 2
    fix_hint: "Convert to explicit pre-task: grep + assert DateTime in imports before editing."

  - plan: "12-04"
    dimension: task_completeness
    severity: major
    description: "401-on-membership-check (vs 401-on-mint) has no retry path. Probability low for v1 but worth documenting."
    task: null
    fix_hint: "Document in 12-03 Section 4 Risks OR wrap /orgs/.../members/... call in 12-04 Task 1 with try/except for 401 with force-refresh retry."

  - plan: "12-06"
    dimension: task_completeness
    severity: major
    description: "deps.py O(n) ghu_ branch iteration could match wrong user if multiple users have same plaintext (low probability) or if iteration order is unstable. Tests with multiple fixture users may flake."
    task: 4
    fix_hint: "Add ORDER BY id DESC to the SELECT. Document the limit. Generate unique random plaintext per test user."

  - plan: "12-11"
    dimension: claude_md_compliance
    severity: major
    description: "github-app-architecture.md KB doc claims GITHUB_API_PAT removed -- true only if B-1 is fixed. KB risks being inaccurate from day one."
    task: 3
    fix_hint: "After fixing B-1, KB is accurate as written. If B-1 unresolved, qualify the statement."

  - plan: "12-01"
    dimension: task_completeness
    severity: minor
    description: "Task 4 INSERT users test fixture spec is hand-waved (with all required cols)."
    task: 4
    fix_hint: "Specify INSERT row shape: (id, source_user_id='test:0019', email='t@x.io')."

  - plan: "12-05"
    dimension: task_completeness
    severity: minor
    description: "Task 3 leaves 'client' fixture name discovery to execution time. Conftest fixture may be named differently."
    task: 3
    fix_hint: "Read tests/conftest.py before finalizing test code template, surface actual fixture name."

  - plan: "12-04"
    dimension: task_completeness
    severity: minor
    description: "Plan does not address GITHUB_API_PAT reference in tests/test_onboarding_routes.py:15 (setdefault to empty)."
    task: 4
    fix_hint: "Add tests/ grep to Task 4 acceptance and clean up dead test reference."

  - plan: "12-09"
    dimension: operator_gate_clarity
    severity: minor
    description: "Section 0 uses $NEW_GITHUB_APP_CLIENT_ID env var without instructing operator to export it first."
    task: null
    fix_hint: "Add explicit pre-step: operator must export NEW_GITHUB_APP_CLIENT_ID=Iv23li... in executor shell."
```

---

## 8. Final verdict (Iter 1)

**REVISE** -- 5 blockers must be resolved before /gsd-execute-phase 12 can succeed:

1. **B-1 [12-04]** -- Add routes/me_github.py + routes/teams.py to GITHUB_API_PAT migration scope (8 references currently unhandled).
2. **B-2 [12-04]** -- Side effect of B-1: ensure LibreChat link-github flow (out-of-scope-preserved per CONTEXT) does not regress.
3. **B-3 [phase]** -- Add team_org_blocks + auto-grant regression test (SC-5 is currently unverified).
4. **B-4 [12-08 + 12-09]** -- Reword Section 0 operator-assisted gates so they do not trigger phantom wave dependency on 12-11.
5. **B-5 [12-06]** -- Provide full after-diff of signin_github() body to remove sequencing ambiguity.

6 majors should be addressed in the same revision (M-1 org_login API surface, M-2 docs filename, M-3 defensive verify, M-4 401-on-membership documented, M-5 deps.py ordering, M-6 KB doc truthiness).

4 minors are quality polish.

Plans 12-01, 12-02, 12-03 (waves 1, 2, 3) are individually well-formed and can begin execution AFTER blockers above are addressed in the revision pass -- but since 12-04 chains from them and is blocked, the entire phase is gated on revision.

This is Iteration 1 of max 3.

---

## Final verdict (Iter 2 post-revision)

**Date:** 2026-05-17
**Plans audited:** 12-01, 12-03, 12-04, 12-06, 12-07, 12-08, 12-09, 12-10, 12-11 (revision 2)
**Plans NOT re-audited (no findings):** 12-02 (revision 1), 12-05 (revision 1)
**Checker:** gsd-plan-checker (Iteration 2 of max 3)

### Revision metadata cross-check

| Plan  | Revision | Wave | depends_on                       | Estimate (h) |
|-------|----------|------|----------------------------------|--------------|
| 12-01 | 2        | 1    | []                               | 2            |
| 12-02 | 1        | 2    | 12-01                            | 2            |
| 12-03 | 2        | 3    | 12-01, 12-02                     | 3            |
| 12-04 | 2        | 4    | 12-01, 12-02, 12-03              | 3            |
| 12-05 | 1        | 5    | 12-01, 12-02                     | 2            |
| 12-06 | 2        | 5    | 12-01, 12-02, 12-03, 12-04       | 4            |
| 12-07 | 2        | 6    | 12-06                            | 2            |
| 12-08 | 2        | 6    | []                               | 2            |
| 12-09 | 2        | 7    | 12-07                            | 1            |
| 12-10 | 2        | 8    | 12-04, 12-06, 12-08, 12-09       | 1            |
| 12-11 | 2        | 9    | 12-01 .. 12-10                   | 3            |

Total estimate: 25h. Dependency graph still acyclic, no forward-references, wave numbers consistent.

### Blocker resolution verification

| # | Blocker | Fix applied? | Evidence | Verdict |
|---|---------|--------------|----------|---------|
| B-1 | PAT migration incomplete (me_github.py + teams.py) | YES | 12-04 files_modified now includes me_github.py, teams.py, test_onboarding_routes.py. New Task 2b rewrites 4 teams.py endpoints using get_installation_token_for_org + mint_app_jwt. New Task 2c migrates me_github.py call site. Task 4 grep gate enforces 0 matches in app/ AND tests/. Section 0 has strict inventory gate. | RESOLVED |
| B-2 | LibreChat link-github regression | YES | 12-04 Task 2c migrates me_github.py lines 50-69 to new signature + removes redundant /user lookup. Task 4 adds test_link_github_route_with_gho_token_returns_200 integration test asserting shape preserved. 12-UAT Step 8 also covers post-Phase 12. | RESOLVED |
| B-3 | SC-5 untested (team_org_blocks + auto-grant preservation) | YES | New file tests/test_phase12_auto_grant_regression.py in 12-06 Task 7 with 2 cases: blocked user gets empty teams_joined; unblocked user auto-joins. verify-phase12.sh Assertion 18 covers live-VM half (SKIPPED with unit-test fallback if fixtures absent). | RESOLVED |
| B-4 | Operator gate forward-ref (12-11 cited from 12-08 wave 6) | YES | 12-08 Section 0 now contains full inline 3-step operator checklist (App registration fields, permissions, callback URLs spelled out). KB doc reference relegated to optional cross-ref. Same pattern in 12-09 Section 0. | RESOLVED |
| B-5 | signin_github sequencing ambiguity | YES | 12-06 Task 3 contains FULL after-diff of signin_github body (10 explicit numbered steps) + new SigninGithubOut schema. Acceptance includes strict ordering assertion using inspect.getsource() + index comparison on 9 anchor calls. Sequence invariants documented inline. | RESOLVED |

**Blocker resolution: 5/5 RESOLVED.**

### Major resolution verification

| # | Major | Fix applied? | Evidence | Verdict |
|---|-------|--------------|----------|---------|
| M-1 | org_login missing from SigninGithubOut | YES | 12-06 Task 3 adds org_login field to SigninGithubOut. signin body sets it on INSTALL_REQUIRED branch. 12-07 Task 2 consumes via signinResponse.org_login with fallback. Test asserts field. verify-phase12.sh Assertion 14 includes org_login. | RESOLVED |
| M-2 | Wrong docs filename (auth.html vs github-auth.html) | YES | 12-10 files_modified lists marketing-site/docs/github-auth.html (verified by ls -- only that file exists, auth.html does NOT). Task 2 UPDATEs existing Phase 10 file. Defensive check that auth.html does not exist + no stale OAuth-App language. | RESOLVED |
| M-3 | DateTime import parenthetical | YES | 12-01 Task 2 has explicit pre-task gate that greps the sqlalchemy import line and fails hard if DateTime missing. Acceptance re-runs the check. | RESOLVED |
| M-4 | 401-on-membership-check no retry | YES | 12-03 get_installation_token_for_org gains force_refresh kwarg. 12-04 Task 1 wraps /members/... call in 401-retry. Tests in 12-03 (test_get_token_for_org_force_refresh_kwarg_bypasses_cache) + 12-04 (test_membership_401_retry_force_refreshes_token) lock both behaviors. Cross-plan signature contract asserted in 12-04 Task 1. | RESOLVED |
| M-5 | deps.py O(n) lookup unstable | YES | New Task 1b in 12-06 extends migration 0019 with github_access_token_hash CHAR(64) + partial index. token_crypto.token_lookup_hash() HMAC-SHA256 helper. persist_tokens_on_signin + refresh_user_token_if_needed write hash on rotate. deps.py uses indexed SELECT + defense-in-depth decrypt+compare. Migration-already-applied fallback to 0020 documented. verify-phase12.sh Assertion 3 now requires 5 columns. | RESOLVED |
| M-6 | KB GITHUB_API_PAT claim untruthful | YES | Defensible: verify-phase12.sh Assertion 6 checks BOTH app/ AND tests/. KB doc cites the command verbatim. KB acceptance verifies the line is present. | RESOLVED |

**Major resolution: 6/6 RESOLVED (100% -- exceeds 80% threshold).**

### Minor resolution verification

| # | Minor | Fix applied? | Evidence | Verdict |
|---|-------|--------------|----------|---------|
| MINOR-1 | Vague INSERT row shape | YES | 12-01 Task 4 specifies exact INSERT row + SELECT verification. | RESOLVED |
| MINOR-2 | Client fixture name unverified | (False alarm) | 12-05 unchanged. Pattern matches test_phase10_auth.py consistently across 12-04 + 12-06 test files. Accepting planner judgment. | ACCEPTED |
| MINOR-3 | Stale setdefault in test_onboarding_routes.py | YES | 12-04 Task 4 cleans up line 15. | RESOLVED |
| MINOR-4 | NEW_GITHUB_APP_CLIENT_ID env ambiguity | YES | 12-09 Section 0 + 12-08 Section 0 both explicitly export the var with format-validate regex. | RESOLVED |

**Minor resolution: 3/3 actionable RESOLVED + 1 ACCEPTED.**

### Anti-regression checks

| Check | Result |
|-------|--------|
| Wave 5 file conflict (12-05 parallel 12-06) | SAFE -- disjoint trees (12-05: webhooks/main/repos; 12-06: services/routes/deps/alembic/models). |
| Wave 6 file conflict (12-07 parallel 12-08) | SAFE -- 12-07 in app-site/, 12-08 in chrome-extension/ + .planning/KB/. |
| Cross-wave sequential conflicts | SAFE -- docker-compose.yml, app-site/account/teams/teams.js, app/config.py, alembic/versions/0019_github_app_install.py, app/models/user.py all modified by multiple plans but always in sequential waves. Entry-gate fallback to migration 0020 documented if 0019 deployed mid-phase. |
| Dependency graph cycles | NONE -- 01 to 02 to 03 to 04 to 06 to 07 to 09 to 10 to 11; 01 to 05 to 11; 04 to 10; 08 to 10; 06 to 10. No back-edges. |
| Wave number consistency | YES -- every plan wave equals max(deps waves) + 1. |
| New blocker from hash-column extension | NONE -- HMAC-SHA256 collision space is 2^256; defense-in-depth decrypt+compare catches edge. Partial index keeps index small. |
| New blocker from full signin diff | NONE -- diff matches Phase 10 sequence; sequence-assertion Python in Acceptance is regression guard. |
| New blocker from 401-retry on membership check | NONE -- single retry only; if second 401, falls through to NOT_MEMBER. No infinite loop. Test asserts the path. |
| Files-modified inventory drift (12-04 Section 0) | MINOR WARNING -- Section 0 claims auth.py=1 / teams.py=6 (total 11); actual is auth.py=0 / teams.py=8 (total 13). The Section 0 explicitly says drift must be surfaced and Tasks updated -- self-correcting via the grep gate. Not a blocker. |

### Context compliance (CONTEXT.md decisions)

All 9 locked decisions remain honored:
- Sequencing (Phase 12 after Phase 11) -- entry gate in 12-01 OK
- Clean break (no dual-auth) -- preserved across 12-04 + 12-06 + 12-10 OK
- App owner: mrboups personal -- 12-11 runbook OK
- Minimal permissions -- 12-08/12-11 spec out Members:Read + Email:Read + Profile:Read OK
- Chrome ext manifest key NOW, Web Store DEFERRED -- 12-08 + KB OK
- mrboups force re-authorize once -- 12-11 UAT OK
- LibreChat OAuth App UNTOUCHED -- 12-04 B-2 fix actively preserves the link-github flow OK
- Chrome Web Store publish DEFERRED -- out-of-scope across all plans OK
- repo:read permission DEFERRED -- no plan adds repo perms OK

### New blockers (if any)

NONE. No new blockers introduced by Iter 2 revisions.

### Final verdict

**PASS -- plans ready for /gsd-execute-phase 12.**

All 5 blockers from Iter 1 are RESOLVED with concrete plan text and acceptance gates. All 6 majors are RESOLVED. 3 of 4 minors are RESOLVED; the 4th (MINOR-2 client fixture) was correctly classified as a false alarm.

No new blockers introduced by the revisions. The wave/dependency graph remains acyclic and consistent. File-modification overlaps are exclusively across-wave (sequential, no race). The hash-column extension to migration 0019 carries a documented fallback to migration 0020 if the VM is deployed mid-phase.

Two non-blocking observations to file for the executor:

1. **Inventory count drift in 12-04 Section 0** -- claims 11 total GITHUB_API_PAT occurrences (auth=1, teams=6); actual is 13 (auth=0, teams=8). Self-corrects via the strict grep gate that requires 0 matches after Task 4. Section 0 explicitly anticipates drift.
2. **Total estimate is 25h, not 22h** -- 12-04 went 2h to 3h, 12-06 went 3h to 4h. Planner remark of "still ~22h" was slightly optimistic. Within budget.

Plan-check loop closes successfully at Iter 2 of max 3.
