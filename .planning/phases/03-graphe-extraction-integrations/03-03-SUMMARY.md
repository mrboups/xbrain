---
phase: 03-graphe-extraction-integrations
plan: "03"
subsystem: integrations
tags: [oauth, google-drive, google-calendar, docs, runbook]
dependency_graph:
  requires: []
  provides: [docs/google-oauth-scope-upgrade.md]
  affects: [drive-sync, mcp-calendar, mcp-drive-read]
tech_stack:
  added: []
  patterns: [incremental-auth, fernet-encryption]
key_files:
  created:
    - docs/google-oauth-scope-upgrade.md
  modified: []
decisions:
  - "drive.file scope deliberately excluded from initial consent — bundled consent increases friction and reduces adoption"
  - "Runbook framed for human admin re-execution, not the Playwright automation already completed in parallel"
metrics:
  duration: "100s"
  completed_date: "2026-05-04"
  tasks_completed: 1
  tasks_total: 1
  files_changed: 1
---

# Phase 03 Plan 03: Google OAuth Scope Upgrade Runbook — Summary

**One-liner:** Admin runbook documenting incremental auth flow to add drive.readonly + calendar.readonly + drive.file scopes to the existing Google OAuth client without disrupting current users.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Créer le runbook Google OAuth scope upgrade | ab11336 | docs/google-oauth-scope-upgrade.md |

## What Was Built

`docs/google-oauth-scope-upgrade.md` — a 322-line admin runbook covering:

1. **Context** — why the scopes are required and why existing users see no disruption (incremental auth)
2. **Scopes** — the 3 OAuth scopes with Google classification (Sensitive), usage mapping, and the rule on when `drive.file` is NOT added to the initial consent
3. **Google Cloud Console steps** — step-by-step: add scopes, verify redirect URIs, enable Drive/Calendar APIs
4. **Environment variables** — Phase 3 additions to `.env` including `OAUTH_CREDENTIALS_ENCRYPTION_KEY` with Fernet generation command
5. **Incremental auth flow** — full step-by-step including `state` anti-CSRF param, `include_granted_scopes=true`, token encryption and storage in `team_drive_mappings.oauth_credentials_enc`
6. **drive.readonly vs drive.file separation** — rationale table + write-back flow reference (consent fatigue prevention)
7. **Post-setup verification** — `docker exec` commands for `drive-sync`, `curl` for `mcp-calendar`, log inspection commands

## Deviations from Plan

None — plan executed exactly as written.

The prompt noted that the orchestrator was adding OAuth scopes via Playwright in parallel. The runbook is therefore framed as "what to do if you ever need to redo the OAuth setup" — aimed at a human admin, not the current automated session. This matches the plan intent exactly.

## Threat Surface Scan

No new network endpoints, auth paths, file access patterns, or schema changes introduced — this is a docs-only plan.

The runbook documents existing threat mitigations from the plan's `<threat_model>`:

| Threat | Documented in runbook |
|--------|-----------------------|
| T-03-03-01: OAuth callback state anti-CSRF | Section 5 — `state` param with HMAC signature |
| T-03-03-02: OAUTH_CREDENTIALS_ENCRYPTION_KEY never in git | Section 4 — `__FILL_FERNET_KEY__` placeholder, security warning |
| T-03-03-03: drive.file scope bundled with drive.readonly | Section 6 — explicit separation rationale + table |

## Known Stubs

None — docs-only plan, no UI or data pipeline wired.

## Self-Check: PASSED

- [x] `docs/google-oauth-scope-upgrade.md` exists and contains all 7 sections
- [x] `drive.readonly`, `calendar.readonly`, `drive.file` present
- [x] `include_granted_scopes=true` present
- [x] `OAUTH_CREDENTIALS_ENCRYPTION_KEY` present
- [x] Commit `ab11336` exists
