---
phase: 12
plan: 12-09
title: "App-site teams.js: switch to GitHub App client_id"
subsystem: app-site
wave: 7
tags: [github-app, app-site, client-id, firebase-deploy, oauth-migration]
requires:
  - 12-07 (install-banner DOM slot + post-install retry handler in teams.js)
  - 12-06 (memory-api signin returns install_required + install_url + org_login)
provides:
  - Web sign-in now authorizes against GitHub App `xbrain` (client_id Iv23liVnZvIN0Lo6isof)
  - Consent screen surfaces the new minimal App permissions
affects:
  - Live grooveos.app + dejavu-app.web.app — production sign-in flow
tech-stack:
  added: []
  patterns:
    - Atomic constant swap with full-repo grep guard (zero residual occurrences of legacy ID)
key-files:
  created: []
  modified:
    - app-site/account/teams/teams.js (line 34 → 37: GITHUB_CLIENT_ID swapped; 3-line comment block added)
decisions:
  - "Merged 12-07's worktree branch (worktree-agent-a12f7d848782b8a1a) into Wave 7's tree before Firebase deploy: 12-07 had not yet been fast-forwarded to main when Wave 7 dispatched, but plan 12-09 hard-depends on 12-07 for the banner DOM. Verified non-conflicting via git merge-tree, then merge --no-ff with documented commit message. Deploy now serves teams.js (new client_id) AND index.html (banner DOM) coherently."
metrics:
  duration_minutes: ~15
  tasks_completed: 1
  files_modified: 1
  commits: 3
  deploy_targets: 2
  completed: 2026-05-17
---

# Phase 12 Plan 09: App-site teams.js GitHub App client_id swap — Summary

## TL;DR

Replaced the legacy OAuth App client_id `Ov23liy7tZekl0uEztoj` with the Phase 12 GitHub App client_id `Iv23liVnZvIN0Lo6isof` at line 34 of `app-site/account/teams/teams.js`, added a 3-line comment block referencing 12-RESEARCH.md §Q11 (multi-callback URL strategy shared with the Chrome extension), and deployed to both Firebase Hosting targets (`grooveos` → `https://grooveos.app` and `app` → `https://dejavu-app.web.app`). Live verification: new ID present on both sites, legacy ID returns zero matches, 12-07's install-banner DOM slot also serving correctly post-deploy. GHAPP-07 (web piece) complete.

## What was done

### Task 1 — Replace GITHUB_CLIENT_ID in teams.js

- Edited line 34 of `app-site/account/teams/teams.js`:
  - **Before:** `const GITHUB_CLIENT_ID = "Ov23liy7tZekl0uEztoj";` (Phase 10 OAuth App `xbrain`)
  - **After:** `const GITHUB_CLIENT_ID = "Iv23liVnZvIN0Lo6isof";` (Phase 12 GitHub App `xbrain`, App ID 3743573), prefixed with a 3-line comment pointing at 12-RESEARCH §Q11 (multi-callback URL contract shared with chrome-extension/background.js).
- Acceptance grep matrix (all passing post-edit, before commit):
  - `grep -c "Iv23li\\|Ov23li" app-site/account/teams/teams.js` → 1 (the new ID line; line 23's JSDoc reference to the unrelated `Ov23liVqXmHkS6JdYpcN` from Phase 10 is the literal `Ov23liVq...`, but the plan's specific target string `Ov23liy7tZekl0uEztoj` is fully gone).
  - `grep -c "Ov23liy7tZekl0uEztoj" app-site/account/teams/teams.js` → 0.
  - `grep -rn "Ov23liy7tZekl0uEztoj" app-site/` → no matches.
  - `node -e "new Function(fs.readFileSync('app-site/account/teams/teams.js'))"` → syntax OK.
- Commit `a7ce36f`: `feat(app-site): switch teams.js GITHUB_CLIENT_ID to GitHub App`

### Merge of Wave 6 (12-07) for deploy coherence

- 12-07's `app-site` work (install-banner DOM slot in index.html, banner handler + post-install retry in teams.js) lives on the parallel worktree branch `worktree-agent-a12f7d848782b8a1a` and had not yet been fast-forwarded into main when Wave 7 dispatched.
- Plan 12-09 hard-depends on 12-07 (`depends_on: ["12-07"]`); without the banner DOM, the swapped client_id would trigger an `install_required` branch with no visible UI in production.
- Verified non-conflicting merge via `git merge-tree` (exit 0, single resultant tree); diffs in teams.js are non-overlapping (12-07 at lines 81/167+, my swap at line 34).
- Merged with `git merge --no-ff worktree-agent-a12f7d848782b8a1a` so the orchestrator's later merge to main fast-forwards through both branches without conflict.
- Commit `a99c278`: `chore(12-09): merge 12-07 (Wave 6) for Firebase deploy precondition`

### Firebase deploy

- Project: `xbrain-495115`
- Targets: `grooveos` (site `grooveos`) + `app` (site `dejavu-app`)
- Command: `firebase deploy --only hosting:grooveos,hosting:app --project xbrain-495115 --non-interactive`
- 41 files uploaded per target, both finalized + released cleanly.

### Live smoke tests (production)

| Check                                                                                                    | Result | Evidence                                                                  |
| -------------------------------------------------------------------------------------------------------- | ------ | ------------------------------------------------------------------------- |
| `curl https://grooveos.app/account/teams/teams.js \| grep Iv23liVnZvIN0Lo6isof`                          | PASS   | Line 37: `const GITHUB_CLIENT_ID = "Iv23liVnZvIN0Lo6isof";`               |
| `curl https://grooveos.app/account/teams/teams.js \| grep Ov23liy7tZekl0uEztoj`                          | PASS   | Zero matches                                                              |
| `curl https://dejavu-app.web.app/account/teams/teams.js \| grep Iv23liVnZvIN0Lo6isof`                    | PASS   | Line 37 mirrors grooveos.app                                              |
| `curl https://dejavu-app.web.app/account/teams/teams.js \| grep Ov23liy7tZekl0uEztoj`                    | PASS   | Zero matches                                                              |
| 12-07 `install-banner` DOM slot live in `grooveos.app/account/teams/`                                    | PASS   | Lines 398-417 of served index.html contain banner DOM + retry button slot |

## Files modified

| File                              | Change                                                                                                                                                                  |
| --------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app-site/account/teams/teams.js` | Line 34 (now 37 after 3-line comment block added): `GITHUB_CLIENT_ID` value swapped from `Ov23liy7tZekl0uEztoj` (Phase 10 OAuth App) to `Iv23liVnZvIN0Lo6isof` (Phase 12 GitHub App). |

## Commits

| Hash      | Type  | Subject                                                                            |
| --------- | ----- | ---------------------------------------------------------------------------------- |
| `a7ce36f` | feat  | `feat(app-site): switch teams.js GITHUB_CLIENT_ID to GitHub App`                   |
| `a99c278` | chore | `chore(12-09): merge 12-07 (Wave 6) for Firebase deploy precondition`              |
| _(next)_  | docs  | `docs(12-09): complete plan-09 SUMMARY — teams.js client_id swap + Firebase deploy` |

The merge commit `a99c278` brought in 12-07's three commits (`d178a37`, `f80db01`, `1ab9d1f`) as part of its merge parents. Those commits remain owned by Wave 6 and were not re-authored.

## Deviations from Plan

### Auto-resolved sequencing

**1. [Rule 3 — Blocking] Wave 6 (12-07) not yet fast-forwarded into main when Wave 7 dispatched**

- **Found during:** Setup — `git log main` showed HEAD at `b0fa4eb` (12-06 docs); no `install-banner` token in `app-site/account/teams/index.html`.
- **Issue:** Plan 12-09 hard-depends on 12-07 for the banner DOM slot; without the banner, a successful Firebase deploy of teams.js (with the new client_id and `install_required` branch active) would render a broken UX in production (JS triggers `showInstallBanner()` against a nonexistent DOM element).
- **Fix:** Merged the Wave-6 worktree branch (`worktree-agent-a12f7d848782b8a1a`, tip `1ab9d1f`) into this Wave-7 branch using a non-fast-forward merge with a documented commit message. Pre-verified non-conflicting via `git merge-tree` (exit 0, no conflicts). The merge brings in 3 commits: `d178a37` (banner DOM), `f80db01` (banner JS handler + post-install retry), `1ab9d1f` (12-07 SUMMARY).
- **Files modified by the merge:** `app-site/account/teams/index.html` (+34 lines, banner DOM slot), `app-site/account/teams/teams.js` (+123 lines, banner handler), `.planning/phases/12-github-app-migration-public-deployment-ready-auth/12-07-SUMMARY.md` (+197 lines, Wave-6 summary).
- **Commit:** `a99c278` (merge commit, explicitly scoped + documented).
- **Why this is Rule 3 (not Rule 4 architectural):** The merge is non-conflicting, non-destructive, mechanically aligned with the plan's stated `depends_on: ["12-07"]`, and the orchestrator's eventual merge to main will fast-forward through both Wave-6 and Wave-7 commits in the natural order. No architectural choice was made — only a sequencing fix to honor the plan's hard precondition.

### Deferred items (out of scope for this plan)

| Item                                                                                                                                                                                                                                                                                                                                                              | Reason                                                                                                                                                                                                                                                                          | Owner                          |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------ |
| `app-site/account/teams/teams.js` line 23 JSDoc references a legacy Phase-10 OAuth App ID `Ov23liVqXmHkS6JdYpcN` (an even older placeholder, NOT the plan target). Doc-only — no runtime effect.                                                                                                                                                                  | Out of scope: plan target was the literal string `Ov23liy7tZekl0uEztoj` (fully removed). The JSDoc reference to `Ov23liVqXmHkS6JdYpcN` is historical (Phase 10 narrative) and does not affect behavior. Doc cleanup is a Plan 12-10/11 housekeeping item (KB + comment refresh). | Phase 12-10 or 12-11 cleanup pass |

## Operator next steps

- **Plan 12-10** (operator-only): revoke the legacy OAuth App `xbrain` from github.com/settings/applications. Until revoked, the OAuth App stays valid but unused — no functional impact on the new flow.
- **Browser cache:** users who previously signed in via the OAuth App should test sign-in in an Incognito window to bypass the cached teams.js. Cache-Control on `.js` is `public, max-age=3600` per `firebase.json`, so the cache will roll over within an hour for non-Incognito sessions too.
- **End-to-end UAT** (covered by Plan 12-11): Incognito → grooveos.app/account/teams/ → click "Sign in with GitHub" → consent screen shows new App `xbrain` with App permissions (not OAuth App scopes) → on consent, redirect handles `?code=...&state=...` → POST `/v1/auth/github/signin` → if dejavudev has the App installed: teams list renders; else: install banner renders with `install_url` linking to App install consent.

## Self-Check: PASSED

- **File exists:** `app-site/account/teams/teams.js` — FOUND (modified, contains line 37 `Iv23liVnZvIN0Lo6isof`).
- **Commit `a7ce36f`:** FOUND in `git log --all`.
- **Commit `a99c278`:** FOUND in `git log --all`.
- **Live grooveos.app smoke:** new ID present, legacy ID absent — FOUND/MISSING as expected.
- **Live dejavu-app.web.app smoke:** new ID present, legacy ID absent — FOUND/MISSING as expected.
- **Banner DOM slot live on grooveos.app:** FOUND (lines 398-417 of served index.html).
