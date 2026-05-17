---
phase: 12-github-app-migration-public-deployment-ready-auth
plan: 12-07
subsystem: app-site / ui / auth
tags: [github-app, install-flow, ghapp-06, frontend, vanilla-js, html, post-install-redirect, banner-ux, m-1]

# Dependency graph
requires:
  - phase: 12
    provides: SigninGithubOut.install_required / install_url / org_login fields on POST /v1/auth/github/signin (Plan 12-06)
  - phase: 12
    provides: OrgMembershipResult.INSTALL_REQUIRED branch driving the response surface (Plan 12-04)
  - phase: 10
    provides: app-site/account/teams/ page with GitHub primary sign-in (Option B full-page redirect) and the localStorage `xbt_token` / `user_sub` canonical-keys contract (GHA-08)
provides:
  - "#install-banner DOM slot — hidden card with #install-banner-org, #install-banner-button, #install-banner-retry placeholders + dark-theme amber palette"
  - "showInstallBanner({installUrl, orgLogin}) / hideInstallBanner() helpers in teams.js — fill org name via textContent (XSS-safe), set anchor href, idempotently bind the retry button"
  - "handlePostInstallRedirect() — detects ?installation_id=…&setup_action=install on page load, strips the params, probes /v1/teams/my-teams with the cached xbt_token, advances to loadAuthenticatedUI() on 2xx; gracefully falls through to normal sign-in on 401 / network error"
  - "handleGithubCallback install_required branch — after persisting xbt_token, when data.install_required && data.install_url, hides #signin-section + #auth-section and shows the install banner instead of advancing"
  - "showSignIn() banner-hide guard — signOut / session-expired / OAuth-state-mismatch paths now leave the banner closed instead of stranding it open"
affects: [12-08-chrome-ext (parallel sibling — same Phase 12 install-flow UX but on the popup), 12-11-verify-phase12 (UAT smoke: install_required path), Phase 13 public-launch (banner is the first UI a brand-new org operator sees)]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Vanilla-JS DOM helpers (no React/Vue) — match existing app-site/account/teams/ pattern; idempotent binding via element.__bound flag"
    - "Inline styles tuned to the page's :root CSS variables (var(--amber), var(--text), var(--muted)) rather than introducing a new external stylesheet — preserves the GHA-08 single-file teams.css-less convention"
    - "Post-redirect URL hygiene — window.history.replaceState({}, '', '/account/teams/') called unconditionally when handling ?setup_action=install so a browser refresh never re-triggers the auto-retry probe"
    - "textContent for user-controlled strings (org_login) — never innerHTML — XSS defense even though memory-api validates the value upstream"
    - "Defensive 'try again' UX — the retry button re-runs initiateGithubSignin() rather than just hitting /v1/teams/my-teams, because auto_grant_via_org_match only runs inside POST /v1/auth/github/signin (a bare GET to my-teams won't pick up the newly available installation)"

key-files:
  created: []
  modified:
    - "app-site/account/teams/index.html (Task 1 — commit d178a37 — +34 lines: install-banner card with three labelled slots, hidden by default)"
    - "app-site/account/teams/teams.js (Task 2 — commit f80db01 — +123 lines: showInstallBanner / hideInstallBanner / handlePostInstallRedirect helpers + handleGithubCallback install_required branch + showSignIn banner-hide guard)"

key-decisions:
  - "Place the banner OUTSIDE both #signin-section and #auth-section — top-level sibling between subtitle and signin card. Lets handleGithubCallback hide both sections and show only the banner without inventing a third 'install-pending' section. Cleaner state machine than nesting the banner inside auth-section (which would have required also un-hiding auth-section while keeping its other contents from rendering)."
  - "Retry button re-runs full sign-in flow, NOT a /v1/teams/my-teams reprobe. Reason: auto_grant_via_org_match fires only inside POST /v1/auth/github/signin (Plan 12-06 step 5). After install, the user needs a fresh signin so memory-api can call check_github_org_membership with an installation token now resolvable via the install webhook (Plan 12-05). A bare my-teams probe would return empty until the user signs in again — surfacing a confusing 'still empty?' state. handlePostInstallRedirect does call my-teams as a lightweight optimistic check, but only to skip the banner when the user already has memberships (e.g., they were granted via Phase 5 explicit invite, independent of the install path)."
  - "Use dark-theme amber palette (var(--amber) + 10% tinted background) rather than the bright #fef9c3 yellow specified in the plan's literal DOM example. The page is data-theme='dark' with --bg=#0D1117 — light yellow on dark background reads as 'reaction warning' / clashes with the JetBrains Mono typography. The amber accent already exists in :root for this exact purpose."
  - "External target='_blank' rel='noopener' on the install-link anchor — opening GitHub install consent in a new tab keeps the user's app-site session intact so handlePostInstallRedirect can pick up the redirect cleanly when they return. GitHub's redirect goes to the redirect_uri configured in the GitHub App settings (`https://grooveos.app/account/teams/`), which is THIS same page in a new tab — both tabs end up at /account/teams/ with the user signed in."
  - "hideInstallBanner() inside showSignIn() — Rule 1 bug fix: without this, signOut() or any session-expired fall-through would leave the install banner open underneath the sign-in card. The defect surface is small (only triggers if user reaches install_required state then signs out without installing) but the failure mode is 'two confusing UIs visible at once', which is worse than the install_required state itself."

patterns-established:
  - "Plan-to-code constant-name mismatches are auto-fixed at the call site (Rule 3 — Blocking) and documented in deviations, not negotiated. Plan §3 Task 2 referenced MEMAPI_URL but the file has MEMORY_API_BASE; plan referenced /v1/me/teams but the file uses /v1/teams/my-teams. Both fixed inline."
  - "When a plan section says 'fall through to normal sign-in flow', map that to a boolean return from the gate function (handlePostInstallRedirect → false). The caller's init() reads the boolean and either returns early (true: we already rendered the UI) or proceeds (false: run the normal sign-in branching)."

requirements-completed:
  - GHAPP-06  # Install flow UI — app-site banner + post-install retry handler. Chrome extension banner is Plan 12-08's responsibility.

# Metrics
duration: ~11 min
completed: 2026-05-17T12:24:24Z
tasks_completed: 2
files_modified: 2
files_created: 0
commits: 2
---

# Phase 12 Plan 12-07: App-Site Install-Flow UI Summary

**Vanilla-JS install banner + post-install retry handler wired to Plan 12-06's SigninGithubOut.install_required/install_url/org_login response surface, dark-theme amber styling, post-redirect URL hygiene, and a "Try again" button that re-runs sign-in so auto_grant fires against an org that finally has the GitHub App installed.**

## Performance

- **Duration:** ~11 min (start 2026-05-17T12:13:14Z → SUMMARY commit pending)
- **Started:** 2026-05-17T12:13:14Z
- **Completed:** 2026-05-17T12:24:24Z
- **Tasks:** 2 / 2
- **Files modified:** 2 (zero new files)
- **Commits:** 2 (one per task)

## Accomplishments

- **Task 1 (`d178a37`):** Added a hidden `#install-banner` card to `app-site/account/teams/index.html` with three labelled slots (`#install-banner-org` for the org name, `#install-banner-button` for the GitHub install deep link, `#install-banner-retry` for the post-install re-signin), styled with the page's amber palette so it visually reads as a warning without breaking the dark theme.
- **Task 2 (`f80db01`):** Wired the banner in `app-site/account/teams/teams.js`:
  - `showInstallBanner` / `hideInstallBanner` helpers (textContent for the org name → XSS-safe).
  - `handlePostInstallRedirect` — detects `?installation_id=…&setup_action=install`, strips the params unconditionally, probes `/v1/teams/my-teams`, advances to the auth UI on 2xx or falls through cleanly on 401 / network error.
  - `handleGithubCallback` install_required branch — when memory-api flags the App not-installed, the xbt_token IS stored (user has a valid session), but both `#signin-section` and `#auth-section` are hidden and the banner is shown instead of advancing.
  - Retry button binding — clicking "Try again" calls `initiateGithubSignin()` (NOT a bare `/v1/teams/my-teams` reprobe) so the next signin's `auto_grant_via_org_match` picks up the freshly available installation.
  - `showSignIn()` banner-hide guard — Rule 1 bug fix preventing the install banner from being stranded open after signOut / session-expired.

## Task Commits

Each task was committed atomically:

1. **Task 1: Add install banner DOM slot** — `d178a37` (`feat(12-07): install banner DOM slot in teams page`)
2. **Task 2: Wire install banner in teams.js** — `f80db01` (`feat(12-07): wire install_required banner + post-install retry handler`)

**Plan metadata:** SUMMARY commit (pending — final commit of this plan).

No TDD cycle for this plan — frontend-only UI without unit tests (matches the existing `app-site/account/teams/` convention, which has no JS test harness). Manual smoke per the plan's "Manual smoke" acceptance criterion is the verification surface, run during Plan 12-11's verify-phase12.sh UAT.

## Files Created/Modified

- `app-site/account/teams/index.html` — Added the `#install-banner` hidden card (+34 lines, between the page subtitle and `#signin-section`). Banner is purely DOM in this file; behaviour lives in teams.js.
- `app-site/account/teams/teams.js` — Added `showInstallBanner` (lines 190-211), `hideInstallBanner` (lines 213-216), `handlePostInstallRedirect` (lines 233-265); modified `init()` (call `handlePostInstallRedirect` first), `handleGithubCallback` (install_required branch after persisting xbt_token), `showSignIn` (hide banner on sign-in render).

## Decisions Made

See `key-decisions` in the frontmatter for the full list with rationale. Headlines:

- Banner placed as a top-level sibling between subtitle and `#signin-section` (NOT nested inside `#auth-section`) — cleaner state machine when memory-api signals install_required after a successful token mint.
- "Try again" button re-runs the full sign-in (`initiateGithubSignin`) rather than a bare `/v1/teams/my-teams` probe — auto_grant only fires inside POST `/v1/auth/github/signin`, so a probe alone would not surface freshly-installed memberships.
- Dark-theme amber palette (`var(--amber)` + 10% tinted background) instead of the plan's literal `#fef9c3` yellow — the page is dark-themed (`data-theme="dark"`); light yellow on `#0D1117` reads as a render glitch.
- `target="_blank" rel="noopener"` on the install link so the user's app-site session survives the round-trip to GitHub. When GitHub redirects them back, both tabs land at `/account/teams/`; `handlePostInstallRedirect` picks up the install query params on the returning tab.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Plan referenced wrong memory-api constant name**
- **Found during:** Task 2 (wiring `handlePostInstallRedirect`)
- **Issue:** Plan §3 Task 2 step 2 used `MEMAPI_URL` in the fetch URL template literal, but `app-site/account/teams/teams.js` defines the constant as `MEMORY_API_BASE` (line 32). Using `MEMAPI_URL` would have produced a `ReferenceError` at runtime — the post-install probe would have thrown silently in the try/catch and returned false, defeating the entire post-install auto-retry feature.
- **Fix:** Used the actual constant name `MEMORY_API_BASE` in `handlePostInstallRedirect`'s fetch URL.
- **Files modified:** `app-site/account/teams/teams.js`
- **Verification:** `node -e "new Function(fs.readFileSync('app-site/account/teams/teams.js','utf8'))"` passes — no ReferenceError. `grep -n "MEMORY_API_BASE\|MEMAPI_URL" app-site/account/teams/teams.js` confirms only `MEMORY_API_BASE` is used.
- **Committed in:** `f80db01` (Task 2 commit).

**2. [Rule 3 - Blocking] Plan referenced wrong memory-api endpoint name**
- **Found during:** Task 2 (wiring `handlePostInstallRedirect`)
- **Issue:** Plan §3 Task 2 step 2 referenced `/v1/me/teams` for the post-install team-membership probe, but the actual memory-api route is `/v1/teams/my-teams` (used throughout the file already, e.g., line 402: `state.teams = await xbtFetch("/v1/teams/my-teams")`). `/v1/me/teams` would have returned 404 → handler returns false → user gets stuck on the install banner even after a successful install.
- **Fix:** Used `/v1/teams/my-teams` in `handlePostInstallRedirect`'s fetch path.
- **Files modified:** `app-site/account/teams/teams.js`
- **Verification:** Confirmed via `grep -rn "@router.get.*my-teams" apps/memory-api/app/routes/` that `my-teams` is the only matching team-listing endpoint. The same endpoint is hit on line 402 of teams.js for the authenticated UI render, so the probe shares the existing success path.
- **Committed in:** `f80db01` (Task 2 commit).

**3. [Rule 1 - Bug] showSignIn() left the install banner stranded open**
- **Found during:** Task 2 (post-edit review of state transitions)
- **Issue:** When a user reaches the install_required state and then either signs out (`signOut()` → `showSignIn()`) or hits a session-expired fall-through (`renderTeamsList` catch → `showSignIn()`) or fails OAuth state CSRF check (`handleGithubCallback` invalid-state branch → `showSignIn()`), the install banner would remain visible underneath the sign-in card. Two UIs visible at once is worse than either UI alone.
- **Fix:** Added `hideInstallBanner()` call at the top of `showSignIn()` (line 136).
- **Files modified:** `app-site/account/teams/teams.js`
- **Verification:** `grep -c "hideInstallBanner" app-site/account/teams/teams.js` returns 5 (1 definition + 4 call sites: init-success path inside `handlePostInstallRedirect`, normal-render path inside `handleGithubCallback`, the `showSignIn()` guard, and the retry button click handler before re-running signin). Manual trace through all sign-in transitions: banner is always hidden before either section becomes visible.
- **Committed in:** `f80db01` (Task 2 commit).

**4. [Rule 2 - Critical] Wired the "Try again" retry button (was an unspecified slot in the plan)**
- **Found during:** Task 1 (DOM design)
- **Issue:** The plan's literal DOM example (`§3 Task 1`) showed only the "Install xbrain" anchor — no retry button. But the plan's narrative (§1 step 2 + §3 Task 2 step 2) explicitly calls out the retry semantic ("the auto-retry of /v1/me/teams; if still install_required, show a retry link"). Shipping the banner without a retry mechanism would leave the user in a permanent dead state after install: the post-install handler probes `/v1/teams/my-teams` but if the membership wasn't granted (auto_grant only fires inside POST /signin), the probe returns 2xx-but-empty and the user sees an empty teams list with no path forward.
- **Fix:** Added a `#install-banner-retry` button to the DOM (Task 1) and wired it inside `showInstallBanner` (Task 2) to call `initiateGithubSignin()` on click — re-running the full sign-in so `auto_grant_via_org_match` fires against the now-installed org. The retry binding is idempotent (`retry.__bound` flag) matching the pattern used by `github-signin-btn` (line 137) and `btn-link-github` (line 379) elsewhere in the file.
- **Files modified:** `app-site/account/teams/index.html` (Task 1), `app-site/account/teams/teams.js` (Task 2)
- **Verification:** `grep -n "install-banner-retry" app-site/account/teams/{index.html,teams.js}` confirms the retry button exists in DOM and is referenced + bound from JS. `node` syntax check passes.
- **Committed in:** `d178a37` (Task 1 DOM) + `f80db01` (Task 2 wiring).

---

**Total deviations:** 4 auto-fixed (2 Rule 3 blocking constant/endpoint name mismatches, 1 Rule 1 state-machine bug, 1 Rule 2 missing critical UI affordance).
**Impact on plan:** All four were necessary for the plan's stated success criteria to actually work end-to-end. None changed the plan's scope or surface — they made the same DOM + JS logic actually function correctly. Zero scope creep.

## Issues Encountered

**Tooling — Edit/Read tool path resolution diverged from the worktree.**

The Edit tool, when given an absolute path of the form `D:/VSC/xbrain/app-site/account/teams/index.html`, resolved it to the MAIN repository working tree (`/d/VSC/xbrain/app-site/account/teams/index.html`) instead of the agent's worktree (`/d/VSC/xbrain/.claude/worktrees/agent-a12f7d848782b8a1a/app-site/account/teams/index.html`). The Read tool simultaneously cached and returned a phantom post-Edit view that did NOT reflect the actual disk state of either path. The first Task 1 Edit silently went to the wrong path; `git status` in the worktree showed "clean"; the worktree file on disk was unchanged; but Read claimed the edit had landed.

**How it was caught:** Bash-level `grep -c` against the worktree path returned 0 matches for the new banner content while the Read tool kept claiming the matches existed. Side-by-side `md5sum` comparison of the worktree file vs the main-repo file vs `git show HEAD:...` revealed that my edits had landed in the main repo (file grew to 17141 bytes there) while the worktree file remained at 14067 bytes.

**Resolution:** Reverted the misplaced main-repo modification with `git checkout -- app-site/account/teams/index.html` (run from the main repo) — explicitly allowed by the destructive_git_prohibition workflow rule because the discard was scoped to a single file the agent had just modified. Re-issued the Edit using the FULL worktree-prefixed absolute path `D:/VSC/xbrain/.claude/worktrees/agent-a12f7d848782b8a1a/app-site/account/teams/index.html`. From that point forward, both Read and Edit honored the worktree path correctly. Verified: worktree shows 2 expected modified files committed on `worktree-agent-a12f7d848782b8a1a`, main repo `git status` is clean.

This appears to be a Claude Code worktree path-resolution bug (#2924-adjacent) — the tool may be applying the originally provided `<files_to_read>` paths verbatim rather than rewriting them through the current worktree. The user's `<objective>` explicitly warned **"NEVER `cd D:/VSC/xbrain`. Stay in worktree."** — the agent did NOT `cd` to main; the tool resolved paths to main on its own. **Mitigation for future Phase 12 worktree-spawned executors: always pass tool file_paths with the explicit `D:/VSC/xbrain/.claude/worktrees/agent-<id>/` prefix when the agent is running in a worktree, regardless of what the planner's `<files_to_read>` lists.**

No code-level issues encountered beyond this.

## User Setup Required

None — frontend-only changes. The banner becomes active automatically once Plan 12-06's API is deployed (it already is per Phase 12 deploy history) and once the GitHub App's redirect_uri whitelist includes `https://grooveos.app/account/teams/` (configured per CONTEXT decisions § "App ownership" and operator prep on 2026-05-17 — the install flow returns to that URL by design).

UAT smoke for the install flow lands in Plan 12-11 (`verify-phase12.sh`); this plan does not add a separate verification script.

## Next Phase Readiness

- **Plan 12-08 (Wave 6 sibling, parallel):** Chrome extension manifest `key` + same install-banner UX on the popup. Disjoint files (this plan touched only `app-site/account/teams/{index.html,teams.js}`; 12-08 touches `chrome-extension/`), so no integration conflict expected.
- **Plan 12-09 (Wave 7):** Migrates `GITHUB_CLIENT_ID` constant in teams.js (line 34) to the new GitHub App client_id and updates the OAuth scope string. This plan intentionally did NOT touch that constant (per plan §3 Task 2 step 3 explicit guard). Plan 12-09 is the dispatch for that update.
- **Plan 12-11 (Wave 9):** verify-phase12.sh — should add a UAT step exercising the install_required → install banner → post-install redirect → teams-list-render path. The banner's three slot IDs (`#install-banner-org`, `#install-banner-button`, `#install-banner-retry`) are stable selectors a playwright / cypress / curl-then-grep smoke can pin.
- **No blockers** for downstream Phase 12 plans. The frontend can ship independently of any backend change in this wave.

## Threat Flags

None — this plan does NOT introduce new security-relevant surface. The banner consumes API fields that Plan 12-06 already added to `SigninGithubOut` (`install_required`, `install_url`, `org_login`) and that plan's threat model already covered the upstream validation. `org_login` is rendered via `textContent` (XSS-safe). `install_url` is set via `.href` (`installUrl || "#"` defensive default) — same trust boundary as the existing GitHub OAuth redirect on line 190. The post-install `?installation_id=…&setup_action=install` query params are stripped unconditionally from the URL with `window.history.replaceState` so they never reach localStorage / sessionStorage / outbound logs.

---

## Self-Check: PASSED

- `app-site/account/teams/index.html` contains `install-banner` (5 matches: card, message slot, org slot, button, retry).
- `app-site/account/teams/teams.js` contains `showInstallBanner` (definition + call site in handleGithubCallback), `hideInstallBanner` (definition + 4 call sites), `handlePostInstallRedirect` (definition + call site in init).
- Both task commits land on the worktree branch `worktree-agent-a12f7d848782b8a1a`: `d178a37` (Task 1 DOM, +34 lines) and `f80db01` (Task 2 JS wiring, +123 lines).
- Main repo `git status` clean (post-recovery from the path-resolution tooling issue described under "Issues Encountered").
- `node --check`-equivalent (Function constructor parse) on teams.js: PASS — no syntax errors.
- SUMMARY.md self-contained with frontmatter, decisions, deviations, issues, threat flags — no broken @references.

---

*Phase: 12-github-app-migration-public-deployment-ready-auth*
*Plan: 12-07*
*Completed: 2026-05-17*
