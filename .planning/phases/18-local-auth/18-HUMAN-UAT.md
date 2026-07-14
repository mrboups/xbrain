---
status: partial
phase: 18-local-auth
source: [18-05-PLAN.md Task 2 human-verify checkpoint]
started: 2026-07-13
updated: 2026-07-13
---

## Current Test

[awaiting human browser testing — deferred to Phase 16]

## Tests

### 1. Register / login / set-password screens work in a real browser
expected: Against a running memory-api with the local-auth routes and NO Google/GitHub configured,
the full loop works end to end — register a new email+password (signed in, token stored; same email
again shows the 409 "already has an account"); sign in (wrong password shows the generic
"Invalid email or password." with no hint the email exists); change password supplying the current
one, then sign in with the new password; all copy English and visually consistent with
`/account/teams/`.
result: [pending — deferred by user 2026-07-13; the prod VM is terminated so the hardcoded
`api.grooveos.app` base has no stack to test against. To be verified in Phase 16 against the real
standalone frontend + a deployable OSS-light install, when app-site is also debranded (D-01c).]

## Summary

total: 1
passed: 0
issues: 0
pending: 1
skipped: 0
blocked: 0

## Gaps

- UI visual + front→API browser verification for the three auth screens is unverified. Backend is
  fully covered (17 real-Postgres tests, plans 18-03/18-04). Carry into Phase 16 UAT.
