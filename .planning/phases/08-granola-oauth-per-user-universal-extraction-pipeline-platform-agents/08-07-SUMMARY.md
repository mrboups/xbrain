---
phase: 08-granola-oauth-per-user-universal-extraction-pipeline-platform-agents
plan: 07
status: complete
completed: 2026-05-09
---

# Summary: Onboarding Granola Key Step (Plan 08-07)

## Position
Step 4 (optional) — appears after renderWelcome via "Connect Granola (optional)" secondary button. User can always skip via "Get started →" (primary) or "Skip for now".

## Dual mode
- **Connect mode**: if GET /v1/me/granola-key → {connected:false}, shows password input + Save/Skip
- **Disconnect mode**: if GET /v1/me/granola-key → {connected:true}, shows "✓ Granola connecté" + Disconnect button → calls DELETE /v1/me/granola-key

## Skip-friendly UX
Two skip paths: "Get started →" on welcome screen (never enters Granola flow) OR "Skip for now" on Granola step itself. Non-blocking in both cases.

## Security
- type=password hides key during input
- autocomplete=off prevents browser caching
- getToken() refreshed JUST before each API submit (Pitfall 7)
- errorDiv uses .textContent not .innerHTML (XSS safe)

## Rebuild required
`docker compose up -d --build librechat` on VM — onboarding.js is a build-time patch.
