---
plan: 18-05
title: Local-auth UI screens (register / sign-in / set-password)
status: complete-with-deferred-verification
requirements: [LAUTH-01]
key_files:
  created:
    - app-site/account/register/index.html
    - app-site/account/login/index.html
    - app-site/account/password/index.html
  modified: []
---

## What shipped

Three self-contained static auth screens under `app-site/account/`, mirroring the existing
`account/teams/` pattern exactly — one HTML page each, inline CSS, vanilla JS, no framework, no
build step, English-only copy:

- `register/index.html` → `POST /v1/auth/local/register`
- `login/index.html` → `POST /v1/auth/local/login`
- `password/index.html` → `POST /v1/auth/local/set-password`

Each uses the SAME hardcoded `MEMORY_API_BASE = "https://api.grooveos.app"` constant as every other
account page, and stores the returned `xbt_` token the same way. Static acceptance checks pass
(each page references its endpoint + the API base constant).

Commits: `d32cdce` (pages), `6f97cde` (checkpoint note).

## Human-verify checkpoint — APPROVED BY DECISION, visual verification DEFERRED

This plan is `autonomous: false`. Task 2 is a blocking human-verify checkpoint: click through
register → login → change-password → re-login in a real browser against a running stack, and confirm
the styling/copy match the existing account surface.

**That browser verification did NOT happen.** The user consciously deferred it (2026-07-13). The
reason is concrete: the pages hardcode `api.grooveos.app`, and the production VM is TERMINATED (cost
pause), so there is no memory-api answering that origin to click through against. Standing up a local
stack + a localhost-pointed copy was offered and declined in favor of deferring.

**What IS proven** (so this is not a blind approval): the backend the pages call is covered by 17
real-Postgres integration tests across plans 18-03/18-04 (register, login, the no-enumeration oracle,
lockout+recovery, convergence, set-password horizontal-priv-escalation). The request shapes the pages
send match those tested endpoints (static-checked). What remains unverified is purely the **visual
layer and the front→API wiring in an actual browser**.

**Deferred to Phase 16**, where it belongs: Phase 16 builds the real standalone web frontend and a
deployable OSS-light stack. The register/login/set-password loop should be clicked through in a
browser THERE, against a running install, as part of Phase 16's own UAT — at which point the
`grooveos.app` hardcode is also debranded (D-01c: app-site portability is Phase-16-owned). Recorded
in `18-HUMAN-UAT.md` so it surfaces in `/gsd:progress` and `/gsd:audit-uat` until done.
