# Phase 18 Plan 05: register/login/set-password screens — Checkpoint state

**Status:** Task 1 committed. Paused at Task 2 (`checkpoint:human-verify`, blocking). No SUMMARY.md yet — plan is not complete until Task 2 is approved.

## Task 1 — DONE (commit d32cdce)

Created three self-contained pages under `app-site/account/`, mirroring the existing `app-site/account/teams/` pattern (inline `<style>`, vanilla `<script>`, no framework, no build step, dark theme, JetBrains Mono):

- `app-site/account/register/index.html` — email + password -> `POST /v1/auth/local/register`
- `app-site/account/login/index.html` — email + password -> `POST /v1/auth/local/login`
- `app-site/account/password/index.html` — new (+ optional current) password -> `POST /v1/auth/local/set-password` with `Authorization: Bearer <xbt_token>`

All three declare `const MEMORY_API_BASE = "https://api.grooveos.app";` — the SAME hardcoded constant as `teams.js:32` / `admin.js:30` / `wipe.js:16` / `brain.js:32` (Phase 16-owned debranding item; not touched here per the plan's `<known_deferred_item>`). Token stored under the canonical `xbt_token` / `user_sub` localStorage keys `teams.js` uses.

Automated verify command from the plan passed:
```
for f in register login password; do test -f app-site/account/$f/index.html || { echo "missing $f"; exit 1; }; done
grep -q 'auth/local/register' app-site/account/register/index.html && grep -q 'auth/local/login' app-site/account/login/index.html && grep -q 'auth/local/set-password' app-site/account/password/index.html && grep -q 'MEMORY_API_BASE' app-site/account/register/index.html && echo 'ui-static-checks-ok'
```
-> `ui-static-checks-ok`

## Task 2 — PENDING (checkpoint:human-verify, blocking)

Not yet run. Requires a human to exercise the full register -> login -> change-password -> re-login loop in a browser against a running memory-api (Plans 01-04 merged) with no Google/GitHub configured. See `18-05-PLAN.md` Task 2 `<how-to-verify>` for the exact steps, or the orchestrator's checkpoint message for this run.

## Resuming this plan

A continuation agent should:
1. Verify commit `d32cdce` (or later) exists on this branch.
2. NOT redo Task 1.
3. Present Task 2's `<how-to-verify>` steps to the user and await "approved" or a description of what's wrong.
4. On approval: write `18-05-SUMMARY.md`, run the self-check, update STATE.md/ROADMAP.md/REQUIREMENTS.md, and make the final metadata commit (orchestrator-owned per this run's instructions — do not do this if instructed otherwise).
5. On a reported problem: apply deviation rules (1-3 auto-fix, 4 ask) against the described issue, then re-present the checkpoint.
