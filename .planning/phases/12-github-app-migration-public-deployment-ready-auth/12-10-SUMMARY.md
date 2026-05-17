---
phase: 12
plan: 12-10
title: "Remove OAuth App dispatch + revoke runbook + docs/github-auth.html update"
subsystem: memory-api + marketing-site + KB
wave: 8
tags: [github-app, oauth-app-deprecation, docs, kb-runbook, cleanup]
requires:
  - 12-04 (GITHUB_API_PAT removed from app/+tests/ — verified zero references on entry)
  - 12-06 (auth_github.py rewritten to GITHUB_APP_CLIENT_* — verified zero settings.GITHUB_CLIENT_ID code references on entry)
  - 12-08 (chrome ext client_id swap — entry gate confirmed zero legacy ID in chrome-extension/)
  - 12-09 (app-site teams.js client_id swap — entry gate confirmed zero legacy ID in app-site/)
provides:
  - Phase 12 GitHub App public documentation (marketing-site/docs/github-auth.html)
  - Operator runbook for revoking legacy OAuth App `xbrain` (.planning/KB/oauth-app-revocation.md)
  - LibreChat-vs-Phase-12 settings attribution comments in apps/memory-api/app/config.py
  - GitHub-App-flavored module docstring on apps/memory-api/app/routes/auth_github.py (no literal legacy client_id; points readers at KB doc for historical IDs)
affects:
  - End-user-facing auth documentation (replaces Phase 5 OAuth App description with Phase 12 GitHub App flow)
  - Future operator action gated 24h post-deploy (legacy OAuth App revocation — gate runs in Plan 12-11)
tech-stack:
  added: []
  patterns:
    - Documentation-only cleanup wave (Wave 8 SOLO — no code-path changes; runtime unaffected)
    - Comment block above settings field naming the unrelated LibreChat App by client_id so future readers cannot conflate it with GITHUB_APP_CLIENT_ID
    - KB runbook with 24h observation gate (4 pre-conditions) + 3-row triage table distinguishing the three xbrain-related GitHub apps
key-files:
  created:
    - .planning/KB/oauth-app-revocation.md (84 lines — operator runbook gated on Plan 12-11 ship-pass)
  modified:
    - apps/memory-api/app/config.py (+6 / -0 — Phase 5 LibreChat OAuth App comment block above GITHUB_CLIENT_ID/SECRET)
    - apps/memory-api/app/routes/auth_github.py (+8 / -0 then refined: Phase 12 docstring note, no literal legacy client_id)
    - marketing-site/docs/github-auth.html (+184 / -157 — main content fully rewritten for Phase 12 architecture; <head>, nav, sidebar, breadcrumb, footer preserved)
decisions:
  - "Filename canonical per plan: `.planning/KB/oauth-app-revocation.md` (not `-runbook.md`). Plan 12-10 Section 2 + Acceptance both spell out `oauth-app-revocation.md` and the executor prompt's `-runbook.md` variant is a paraphrase; plan body wins."
  - "Genericized auth_github.py docstring to refer to 'a legacy OAuth App' without the literal `Ov23liy7tZekl0uEztoj` so Section 0 entry gate (zero matches in source code) stays clean post-revocation. KB doc retains the historical client_ids — explicitly allowed per the plan ('KB references are OK')."
  - "Did NOT touch `marketing-site/docs/auth.html` (does not exist — plan-check Iter 1 M-2 fix confirmed `github-auth.html` is the canonical file). Verified once more before write."
  - "LibreChat OAuth App `xbrain LibreChat` (Client ID Ov23li0XHV3NL8Git7Dk) only referenced in documentation comments (config.py + auth_github.py docstring) and the KB triage table — no code-flow change to me_github.py, the LibreChat link-github route, or any infrastructure file. Confirmed via git diff against HEAD."
metrics:
  duration_minutes: ~25
  tasks_completed: 3
  files_modified: 3
  files_created: 1
  commits: 4
  completed: 2026-05-17
---

# Phase 12 Plan 10: OAuth App dispatch removal + revocation runbook + github-auth.html — Summary

## TL;DR

Wave 8 (solo, cleanup) closes Phase 12 by stripping the last documentation traces of the legacy OAuth App from xbrain's runtime source tree (auth_github.py docstring, config.py comment block), rewriting the public end-user auth doc (`marketing-site/docs/github-auth.html`) for the Phase 12 GitHub App architecture (installation tokens, transparent refresh, org install flow, minimal-permission table), and authoring the operator revocation runbook for the legacy OAuth App `xbrain` (Client ID `Ov23liy7tZekl0uEztoj`) at `.planning/KB/oauth-app-revocation.md` — gated on a 24-hour observation window enforced by Plan 12-11. Zero runtime behavior change. All M-2 (canonical filename) and M-6 (zero GITHUB_API_PAT references) plan-check fixes verified.

## What was done

### Task 1 — Clean dead OAuth-App branches in auth_github.py + clarify config.py LibreChat scope

**File:** `apps/memory-api/app/routes/auth_github.py` (+ subsequent refinement commit `efca96e`)

- Verified zero `settings.GITHUB_CLIENT_ID` code references already (Plan 12-06 had migrated all to `settings.GITHUB_APP_CLIENT_ID` per its rewrite).
- Initial docstring update (commit `c32c0cd`) included explicit `Ov23liy7tZekl0uEztoj` / `Ov23li0XHV3NL8Git7Dk` literals to maximally clarify LibreChat-vs-GitHub-App distinction.
- Refinement (commit `efca96e`): genericized the docstring to refer to "a legacy OAuth App" without the literal Client ID so Section 0 entry gate (`grep -rn "Ov23liy7tZekl0uEztoj" apps/ app-site/ chrome-extension/` → zero matches) stays satisfied post-revocation; the KB doc now serves as the authoritative source for historical client_ids ("KB references are OK" per plan).

**File:** `apps/memory-api/app/config.py`

- Added a 6-line `# === Phase 5 — LibreChat OAuth App (still active — separate from xbrain auth) ===` comment block immediately above `GITHUB_CLIENT_ID: str = ""` / `GITHUB_CLIENT_SECRET: str = ""`, naming the LibreChat App by Client ID `Ov23li0XHV3NL8Git7Dk` and pointing future readers at `app/routes/me_github.py` as the sole consumer. Distinguishes it from the new `GITHUB_APP_CLIENT_ID` block below.

**Acceptance (all PASS):**

| Check                                                                                  | Result |
| -------------------------------------------------------------------------------------- | ------ |
| `grep -n "settings\.GITHUB_CLIENT_ID\b" apps/memory-api/app/routes/auth_github.py`     | 0      |
| `grep -c "Phase 5 — LibreChat OAuth App" apps/memory-api/app/config.py`                | 1      |
| `python -m ruff check apps/memory-api/app/routes/auth_github.py apps/memory-api/app/config.py` | Same as HEAD (only B008 pre-existing on `Depends(get_session)`; not introduced by this plan) |

**Commits:** `c32c0cd` (initial refactor) + `efca96e` (docstring genericization to satisfy entry gate)

### Task 2 — Update marketing-site/docs/github-auth.html for Phase 12 GitHub App

**File:** `marketing-site/docs/github-auth.html` (UPDATED — file already existed since Phase 10; M-2 fix confirmed canonical filename via `ls marketing-site/docs/`)

Replaced lines 79-289 (entire main content body, from `<!-- Page title -->` through the closing `</ul>` of Security Notes) with the Phase 12 architecture description. The full `<head>` block (incl. nav meta + Tailwind/CSS includes), the `<nav class="xb-nav">` block, the `<aside class="sidebar">` block (incl. `aria-current="page"` on the `github-auth.html` link), the `<nav class="breadcrumb">`, the `</main>` close, and the entire `<footer class="xb-footer">` block were preserved byte-for-byte.

Also updated the `<meta name="description">` and `<title>` to match the new Phase 12 framing.

New content covers, in order:
- Overview: GitHub App + minimal scopes + 6-month refresh
- "Migrated from OAuth (Phase 12)" callout for users who remember the old App
- Sign-in flow ASCII diagram (consent → code → server-side swap → ghu_/ghr_ → xbt_)
- Automatic token refresh + encrypted storage callout
- Org install flow: app-installed-vs-not, install banner copy ("Install xbrain on `<org>`"), org-admin-required warning, minimal-permission table (3 rows: Members/Email/Profile, all Read-only)
- Installation tokens (server-side App JWT → ghs_ via mint endpoint, 55-min cache)
- Install/uninstall webhook (HMAC-SHA256 on raw body)
- Database schema (migration 0019 SQL block)
- Session semantics (xbt_ for frontend, ghu_/ghr_ never leave VM)
- Security notes (6 bullets: client_secret server-only, encrypted storage, HMAC, state CSRF, minimal scopes, refresh rotation)

**Acceptance (all PASS):**

| Check                                                                  | Result          |
| ---------------------------------------------------------------------- | --------------- |
| `[ -f marketing-site/docs/github-auth.html ]`                          | PASS (exists)   |
| `grep -q "GitHub App" marketing-site/docs/github-auth.html`            | PASS            |
| `grep -q "Install xbrain" marketing-site/docs/github-auth.html`        | PASS            |
| `[ ! -f marketing-site/docs/auth.html ]`                               | PASS (no stray) |
| `grep -E "OAuth App\b" marketing-site/docs/github-auth.html`           | 0 matches       |
| `grep -q "GITHUB_API_PAT" marketing-site/docs/github-auth.html`        | 0 matches       |

**Commit:** `f1acc5d`

### Task 3 — Operator runbook for OAuth App revocation

**File:** `.planning/KB/oauth-app-revocation.md` (CREATED — 84 lines)

Authored the runbook documenting:
- **When to revoke** — 4 pre-conditions (24h live, mrboups sign-in success on both web+ext, verify-phase12.sh PASS ≥16/18 per plan-check Iter 2 §SC-8, no auth/github errors in 6h log window).
- **Revocation steps** — visit https://github.com/settings/applications, find Client ID `Ov23liy7tZekl0uEztoj` (legacy management URL https://github.com/settings/applications/3585830), Edit → Delete application → type `xbrain` to confirm.
- **Verification post-revocation** — sign-out, sign-in via new App, DevTools `client_id` must be `Iv23liVnZvIN0Lo6isof`.
- **Rollback** — revert Plans 12-08+12-09+12-06 frontend swaps if needed; memory-api can stay on Phase 12 (LibreChat `GITHUB_CLIENT_ID` env unchanged).
- **Environment cleanup** — note that `GITHUB_API_PAT` env vars on the VM can be deleted alongside revocation but it's optional.
- **3-row triage table** distinguishing the three xbrain-related GitHub apps (legacy OAuth App `Ov23liy7tZekl0uEztoj` REVOKE; LibreChat OAuth App `Ov23li0XHV3NL8Git7Dk` KEEP; new GitHub App `Iv23liVnZvIN0Lo6isof` KEEP). This addresses Plan 12-10 Risk 4 ("KB document highlights the client_id distinction explicitly, twice") — the LibreChat Client ID appears 3 times in the file with explicit KEEP markers.

**Acceptance (all PASS):**

| Check                                                                       | Result |
| --------------------------------------------------------------------------- | ------ |
| `[ -f .planning/KB/oauth-app-revocation.md ]`                               | PASS   |
| `grep -q "Ov23liy7tZekl0uEztoj" .planning/KB/oauth-app-revocation.md`       | PASS (3 matches — section header, step 2, verification step 5) |
| `grep -q "When to revoke" .planning/KB/oauth-app-revocation.md`             | PASS   |
| Plan Risk 4 mitigation: LibreChat ID appears ≥2 times in file               | PASS (3 occurrences) |

**Commit:** `2b2d851`

## Plan-level gates (post-task)

| Gate                                                                                                                | Result | Notes                                                                                       |
| ------------------------------------------------------------------------------------------------------------------- | ------ | ------------------------------------------------------------------------------------------- |
| M-6: `grep -r "GITHUB_API_PAT" apps/memory-api/app/ apps/memory-api/tests/ --include='*.py'` → 0                    | PASS   | Wave 4 work; re-verified                                                                    |
| Section 0 entry gate: `grep -rn "Ov23liy7tZekl0uEztoj" apps/ app-site/ chrome-extension/` → 0                       | PASS   | Required docstring genericization (commit `efca96e`)                                        |
| KB references to legacy ID are allowed                                                                              | PASS   | 3 mentions in `.planning/KB/oauth-app-revocation.md`                                        |
| LibreChat OAuth App `Ov23li0XHV3NL8Git7Dk` untouched (no code-flow change)                                          | PASS   | Only appears in two documentation comments + the KB triage table; `me_github.py` unchanged  |
| AST-level check: zero code-level (non-docstring) Constant nodes matching the legacy ID in auth_github.py            | PASS   | Verified via `python -c "import ast; ..."` — see Decisions                                  |

## Files modified

| File                                              | Change                                                                                                                                                                                                                                                |
| ------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `apps/memory-api/app/config.py`                   | +6 / -0 — added `Phase 5 — LibreChat OAuth App` comment block above the legacy LibreChat `GITHUB_CLIENT_ID`/`GITHUB_CLIENT_SECRET` settings.                                                                                                          |
| `apps/memory-api/app/routes/auth_github.py`       | +8 / -1 net (two-step: c32c0cd added a docstring with literals; efca96e refined to a generic version + KB pointer). Result: docstring clarifies Phase 12 GitHub App usage, names the LibreChat App by phrase only (no literal ID).                  |
| `marketing-site/docs/github-auth.html`            | +184 / -157 — main content fully rewritten (Page-title block through Security-notes). `<head>` (meta + title), nav, sidebar (`aria-current="page"` preserved), breadcrumb, `</main>` boundary, footer all preserved byte-for-byte.                  |
| `.planning/KB/oauth-app-revocation.md`            | NEW (84 lines) — operator runbook with 24h gate, revocation steps, post-revocation verification, rollback, env cleanup, 3-row triage table.                                                                                                           |

## Commits

| Hash      | Type     | Subject                                                                                                |
| --------- | -------- | ------------------------------------------------------------------------------------------------------ |
| `c32c0cd` | refactor | `refactor(12-10): remove dead OAuth App branches + clarify settings comments`                          |
| `f1acc5d` | docs     | `docs(12-10): github-auth.html — describe Phase 12 GitHub App sign-in + install flow`                  |
| `2b2d851` | docs     | `docs(12-10): operator runbook for OAuth App xbrain revocation`                                        |
| `efca96e` | refactor | `refactor(12-10): genericize auth_github docstring — remove literal legacy client_id`                  |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 — Critical correctness] Docstring genericization to keep Section 0 entry gate clean**

- **Found during:** Plan-level verification immediately before SUMMARY writing
- **Issue:** My initial Task 1 docstring (commit `c32c0cd`) explicitly named `Ov23liy7tZekl0uEztoj` to maximally clarify the legacy-vs-Phase-12 distinction. This created a single match for `grep -rn "Ov23liy7tZekl0uEztoj" apps/` — which **violates the plan Section 0 entry-gate expectation** ("zero matches in source code, KB references are OK"). The match is in a docstring (not executed code), confirmed via Python AST (`ast.Module.body[0].value.lineno == 1`), but the plan acceptance is a literal grep, and the same gate is re-checked by Plan 12-11 `verify-phase12.sh`.
- **Fix:** Refined docstring to refer to "a legacy OAuth App" without the literal Client ID, pointed readers at `.planning/KB/oauth-app-revocation.md` for the historical IDs. Documentary value preserved (LibreChat App distinction still called out by phrase + name).
- **Files modified:** `apps/memory-api/app/routes/auth_github.py`
- **Commit:** `efca96e`

### Auth gates

None — Wave 8 is doc-only and required no authentication beyond standard git push (handled at orchestrator level, not here).

### Decisions documented inline

- **Canonical KB filename `oauth-app-revocation.md`** (not `oauth-app-revocation-runbook.md`). Plan body Section 2 + Acceptance both spell out `oauth-app-revocation.md`; the executor prompt used `-runbook.md` as a paraphrase. Following the plan body since it has explicit grep-based acceptance checks for `oauth-app-revocation.md`.

## Threat Flags

None. Wave 8 introduces no new surface area — it modifies documentation comments + a public-facing docs page + adds a KB markdown. Zero new network endpoints, no auth path changes, no schema modifications. The KB runbook describes a future operator action (legacy OAuth App revocation) but does NOT execute it; the ship gate runs in Plan 12-11 24h after Phase 12 LIVE.

## Known Stubs

None.

## Out-of-scope / deferred

- **Actual OAuth App revocation** — operator-only step gated on 24h post-deploy observation. Plan 12-11 ship-pass triggers it.
- **LibreChat OAuth App migration** — out of scope per `12-CONTEXT.md`. Touching it would regress Phase 5 GHA-04.
- **`verify-phase12.sh` + UAT** — Plan 12-11.
- **VM env cleanup** (`GITHUB_API_PAT`, `GITHUB_ORG_PAT`) — already absent from code; env values may be deleted alongside revocation per Section "Environment cleanup" in the runbook (optional).

## Self-Check: PASSED

### Files (4/4 found)

- `apps/memory-api/app/config.py` — FOUND
- `apps/memory-api/app/routes/auth_github.py` — FOUND
- `marketing-site/docs/github-auth.html` — FOUND
- `.planning/KB/oauth-app-revocation.md` — FOUND

### Commits (4/4 found in git log)

- `c32c0cd` — FOUND
- `f1acc5d` — FOUND
- `2b2d851` — FOUND
- `efca96e` — FOUND

### Gates (all PASS)

- M-6 GITHUB_API_PAT in app/+tests/: 0 matches
- Entry gate Ov23liy7tZekl0uEztoj in apps/+app-site/+chrome-extension/: 0 matches
- KB legacy ID references allowed: 3 mentions in `.planning/KB/oauth-app-revocation.md`
- LibreChat OAuth App `Ov23li0XHV3NL8Git7Dk` no code-flow change: confirmed (only docstring + comment + KB triage table)
- All 3 task-level acceptance gates: PASS (see per-task tables above)
