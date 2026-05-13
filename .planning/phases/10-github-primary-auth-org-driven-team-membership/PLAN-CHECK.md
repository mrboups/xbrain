# Phase 10 — PLAN-CHECK

**Phase:** 10 — GitHub-Primary Auth + Org-Driven Team Membership
**Plans verified:** 6 (10-01 through 10-06)
**Status:** NEEDS REVISION — 3 blockers, 3 major issues

---

## 1. Requirement Coverage Matrix

| Req ID | Description | Covering Plans | Status |
|--------|-------------|---------------|--------|
| GHA-01 | GitHub OAuth login (extension + app-site) | 10-02, 10-04, 10-05 | COVERED |
| GHA-02 | Identity merge (GitHub linked to existing Google account) | 10-01, 10-02 | COVERED |
| GHA-03 | Auto-grant team membership via GitHub Org | 10-02, 10-03 | COVERED |
| GHA-04 | Block enforcement at API layer | 10-01, 10-03 | COVERED |
| GHA-05 | Pre-block for future GitHub sign-ups | 10-01, 10-03 | COVERED |
| GHA-06 | Email notifications for auto-grants | 10-02 | COVERED |
| GHA-07 | App-site 4-state auth header | 10-04 | COVERED |
| GHA-08 | Alembic migration (head 0016) | 10-01 | COVERED |

All 8 requirements are claimed by at least one plan.

---

## 2. Success Criteria Coverage Matrix

| SC | Description | Covering Plans | Gap |
|----|-------------|---------------|-----|
| SC-1 | GitHub OAuth callback stores xbt_ token in extension storage | 10-05 | B-1: wrong storage keys written |
| SC-2 | Identity merge rewrites 11 FK tables without unique constraint violation | 10-01, 10-02 | B-2: github_id cleared too late |
| SC-3 | Auto-grant fires for org members on first login | 10-02, 10-03 | Covered |
| SC-4 | Blocked members receive 403 on all scoped API calls | 10-01, 10-03 | B-3: scoped xbt_ tokens bypass blocked_at check |
| SC-5 | Pre-block rows activate on future GitHub signup | 10-01, 10-03 | Covered |
| SC-6 | Email notification sent on auto-join (fail-soft) | 10-02 | Covered |
| SC-7 | verify-phase10.sh passes all 8 checks (a-h) | 10-06 | M-3: test (h) does not verify SC-7(h) |

---

## 3. Per-Plan Risk Assessment

### 10-01 (Wave 1) — Schema + Repos Foundation

**Risk: MAJOR**

**M-1 (MAJOR): block_member uses sa.func.now() — isoformat() will crash**

block_member sets member.blocked_at = sa.func.now() (a SQLAlchemy expression object, not a Python datetime).
The block endpoint in 10-03 calls updated.blocked_at.isoformat() on the returned value, which raises AttributeError.

Fix option A: use datetime.utcnow() instead of sa.func.now() in block_member.
Fix option B: after block_member() returns, call session.refresh(updated) before accessing .isoformat().

Otherwise the schema and repo layer look correct. FK list in RESEARCH.md Q3 confirmed complete (11 tables).

### 10-02 (Wave 2a, must run first) — GitHub OAuth Routes + Identity Merge

**Risk: BLOCKER**

**B-2 (BLOCKER): github_id unique constraint violation in _resolve_or_merge_user**

Step B in the plan sets by_email.github_id = github_id and then calls session.flush() BEFORE clearing the orphan row github_id.
At flush time, two rows hold the same github_id — PostgreSQL raises UniqueViolationError and the entire merge transaction rolls back.

Correct sequence in _resolve_or_merge_user Step B:
  1. orphan.github_id = None
  2. session.flush()  (index now clear)
  3. by_email.github_id = github_id
  4. session.flush()  (assign to survivor)

Wave-2 parallel conflict: 10-02 and 10-03 both modify deps.py and repos/teams.py. They cannot run in parallel. See Section 6.

### 10-03 (Wave 2b, after 10-02) — Block Enforcement + Team Endpoints

**Risk: BLOCKER + MAJOR**

**B-3 (BLOCKER): get_team_scope scoped-token branch exits before blocked_at check**

In deps.py, when the bearer token starts with xbt_, the user_api_token branch resolves team_id and returns immediately without checking blocked_at.
A blocked member who holds a scoped xbt_ token minted before the block can still call any scoped API endpoint.

Fix: after resolving the UserApiToken row, load the TeamMember row and raise HTTP 403 if blocked_at IS NOT NULL — same pattern as the gho_ token branch.

M-1 consequence: blocked_at.isoformat() crash in block_member_endpoint is also triggered from this plan.
Both M-1 and B-3 fixes must land before this plan is testable.

### 10-04 (Wave 3) — App-Site GitHub OAuth

**Risk: LOW (with one external prerequisite)**

Option B full-page redirect flow is correct. CSRF via sessionStorage is correct. 4-state renderAuthHeader covers all states.

External prerequisite (not a plan bug, but must not be forgotten before production testing):
  https://grooveos.app/account/teams/ must be added to the GitHub OAuth App authorized callback URIs.
This is a manual step by mrboups in the GitHub OAuth App developer settings page. Nothing in the plan scaffolds this.

No code-level blockers found in this plan.

### 10-05 (Wave 3) — Chrome Extension GitHub Auth

**Risk: BLOCKER**

**B-1 (BLOCKER): signinGithubFlow writes wrong chrome.storage keys**

Task 3 in the plan writes: { xbrain_xbt_token, xbrain_user_email, xbrain_source_user_id, xbrain_github_username }
The rest of the extension reads: { xbt_token, user_sub } (background.js line 884, WS reconnect at line 794)
The canonical key contract (onboarding.js line 140) writes: { xbt_token, user_sub, api_token_id }

After GitHub sign-in with the plan as written:
  - WS reconnect listener (watching changes.xbt_token) will not fire
  - Teams fetch will read undefined instead of the token
  - Background.js will behave as unauthenticated

Fix: replace the storage.set call in signinGithubFlow with:
  await chrome.storage.local.set({ xbt_token: xbt_token, user_sub: user && user.id, api_token_id: response.token_id });

Minor: MEMORY_API_BASE is used in signinGithubFlow but is not imported/declared in background.js.
Fix: add const MEMORY_API_BASE = "https://api.grooveos.app"; near the top of background.js, or import from a shared constants module.

### 10-06 (Wave 4) — Verification + Docs

**Risk: MAJOR**

**M-3 (MAJOR): verify-phase10.sh test (h) does not exercise SC-7(h)**

Test (h) as planned is test_follow_merge_pointer_idempotent — a trivial no-merge scenario that tests nothing about auth-time follow-pointer resolution.
SC-7(h) requires verifying that a merged orphan user presenting an xbt_ token gets resolved to the survivor account (not 401 or 403).

The correct test for (h):
  1. Create orphan user with xbt_ token
  2. Perform merge (orphan.merged_into_user_id = survivor.id)
  3. Call any authenticated endpoint with the orphan token
  4. Assert 200 and that the returned identity is the survivor

Fix: replace test_follow_merge_pointer_idempotent with the above sequence in both verify-phase10.sh and test_phase10_auth.py.

---

## 4. Cross-Cutting Concerns

| Concern | Finding | Verdict |
|---------|---------|---------|
| Identity merge FK completeness | RESEARCH.md Q3 lists 11 FK tables; merge_user_rows covers all 11 | PASS |
| Auth state machine (4 states) | 10-04 renderAuthHeader handles UNAUTHENTICATED / GITHUB_ONLY / GOOGLE_ONLY / BOTH correctly | PASS |
| Block enforcement coverage | gho_ token branch blocks correctly; scoped xbt_ token branch bypasses blocked_at | FAIL (B-3) |
| Email reliability (fail-soft) | 10-02 uses BackgroundTasks + aiosmtplib fail-soft matching Phase 7 pattern | PASS |
| client_secret exposure | Never present in extension, app-site, or any client-side code across all plans | PASS |
| CSRF state param | 10-04 generates state in sessionStorage and verifies before code exchange | PASS |
| Wave 2 file conflict | 10-02 and 10-03 both modify deps.py and repos/teams.py in same declared wave | RESOLVED in Section 6 |

---

## 5. Verdict

**NEEDS REVISION**

3 blockers and 3 major issues must be resolved before execution begins.

| ID | Severity | Plan | Issue |
|----|----------|------|-------|
| B-1 | BLOCKER | 10-05 | signinGithubFlow writes wrong chrome.storage keys; extension stays unauthenticated after GitHub sign-in |
| B-2 | BLOCKER | 10-02 | _resolve_or_merge_user violates github_id unique index at flush time; identity merge crashes on every first login |
| B-3 | BLOCKER | 10-03 | get_team_scope scoped xbt_ token branch exits before blocked_at check; blocked members bypass enforcement |
| M-1 | MAJOR | 10-01, 10-03 | block_member sets blocked_at = sa.func.now() (SQL expression); blocked_at.isoformat() raises AttributeError at runtime |
| M-2 | MAJOR | 10-02, 10-03 | Wave 2 plans both modify deps.py and repos/teams.py; parallel execution would produce merge conflicts |
| M-3 | MAJOR | 10-06 | Test (h) in verify-phase10.sh is a trivial no-op; SC-7(h) follow-pointer auth resolution is not exercised |

---

## 6. Suggested Execution Order

Due to M-2 (Wave 2 file conflict), 10-02 and 10-03 must execute sequentially.

| Wave | Plans | Mode |
|------|-------|------|
| Wave 1 | 10-01 | Solo |
| Wave 2a | 10-02 | Solo — modifies deps.py and repos/teams.py first |
| Wave 2b | 10-03 | Solo — applies on top of 10-02 changes |
| Wave 3 | 10-04 + 10-05 | Parallel — touch independent files |
| Wave 4 | 10-06 | Solo — integration tests and docs |

---

## 7. Polish Backlog (MINOR / NIT — deferred to post-execution)

- 10-02: _mint_xbt_for_user creates tokens with team_scope=NULL. Acceptable for multi-team tokens but should be documented as intentional in the code.
- 10-02: get_user_by_email in repos/users.py does not filter merged_into_user_id IS NULL. Low risk in Phase 10 (orphan email is unique after merge) but worth adding a filter to prevent subtle future bugs.
- 10-04: STORAGE_TOKEN constant in teams.js currently holds the Google token. After Phase 10, the page holds both a Google and GitHub token. Consider a distinct STORAGE_GHO_TOKEN constant when the two coexist.
- 10-05: MEMORY_API_BASE is duplicated across multiple files. A shared constants module would prevent future divergence.
- 10-06: The xbrain_product_kb.md update appends unstructured prose to a KB file, inconsistent with the structured memory tagging contract. Acceptable for Phase 10 but should be migrated to a tagged entry in Phase 11.


---

## Revision 1 verification (date: 2026-05-13)

| Issue | Verdict | Notes |
|-------|---------|-------|
| B-1 | ✓ | 10-05 Task 3 writes canonical keys xbt_token, user_sub, api_token_id. Grep smoke-test included in the plan. |
| B-2 | ✓ | 10-02 _resolve_or_merge_user Step B.1 clears orphan.github_id and flushes BEFORE assigning on survivor (Step B.2). 5-step sequence explicit. Regression test test_merge_does_not_violate_github_id_unique present. |
| B-3 | ✓ | 10-03 Task 4 adds blocked_at 403 guard to both xbt_ and user branches. Regression test test_xbt_token_blocked_user_gets_403 present. |
| M-1 | ✓ | 10-01 Task 3 block_member assigns datetime.now(tz=timezone.utc). DDL server_default=func.now() unchanged (INSERT-only, correct). |
| M-2 | ✓ | 10-02 frontmatter wave: 2a. 10-03 frontmatter wave: 2b + depends_on: [10-01-PLAN.md, 10-02-PLAN.md]. |
| M-3 | ✓ | 10-06 Task 1 adds test_orphan_token_lands_on_survivor (full HTTP-layer flow). verify-phase10.sh test (h) targets this test. 10-01 merge_user_rows re-parents user_api_tokens FK + test_merge_migrates_api_tokens present. |

## Final verdict after revision 1

PASS — tous les 6 correctifs sont correctement intégrés sans régression introduite. Les plans sont prêts pour `/gsd:execute-phase 10`.
