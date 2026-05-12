---
title: Extension zero-click onboarding (silent Google auth + auto-mint)
quick_id: 260512-zca
slug: extension-zero-click-auth
date_completed: 2026-05-12
status: complete
must_haves_met: true
---

# Quick Task 260512-zca — SUMMARY

## Goal (recap)

Two follow-ups: (1) eliminate the separate Google OAuth window, (2) eliminate the "Connect xbrain account" click after Google sign-in. End-state: open the side panel → 🟢 connected in ~1s with no interaction.

## What shipped

| Component | Change |
|-----------|--------|
| `apps/memory-api/app/auth.py` | New `verify_google_access_token()` — calls `https://www.googleapis.com/oauth2/v3/userinfo`, returns OIDC-claims-shaped dict. 5-min in-memory cache. Defensive: rejects unverified emails, missing `sub`, non-200 from Google. |
| `apps/memory-api/app/deps.py` | `get_current_principal` adds an access-token detection branch: JWT shape (2 dots) → ID token path; no dots + not `xbt_`/`gho_` → new access token path. Bridge / GitHub / xbt_ paths unchanged. |
| `apps/memory-api/tests/test_auth.py` | +6 pytest cases — happy path, 401 rejected, missing sub, unverified email, empty token, cache hit on 2nd call. All 10 unit tests PASS. |
| `chrome-extension/background.js` | `getGoogleIdToken()` now wraps `chrome.identity.getAuthToken({ interactive: true })`. Removed the manual `launchWebAuthFlow` URL building and the storage.session ID-token cache. Added `clearGoogleAuthToken()` (`removeCachedAuthToken`) wired into the disconnect flow for account switching. |
| `chrome-extension/popup.js` | New `maybeAutoMint()` triggered on DOMContentLoaded after `renderConnectState`. One attempt per popup open, guarded by `_autoMintAttempted`. On failure, manual Connect button stays available. |

## Commits

| Commit | Task | Description |
|--------|------|-------------|
| `27e0a8d` | A | memory-api accepts Google OAuth access tokens (auth.py + deps.py + 6 tests) |
| `711678e` | B | extension uses chrome.identity.getAuthToken (silent) |
| `a4dae12` | C | popup auto-mints silently on first open (zero-click) |

(Task D was rolled into A's pytest suite + existing extension tests — Tasks B/C are pure glue and didn't warrant new JS tests.)

## Deploy

- memory-api: rebuilt + restarted on VM (`docker compose build memory-api && up -d`). Healthcheck green within 19s, `/healthz` returns 200.
- chrome-extension: no deploy needed — user pulls + reloads locally.

## must_haves verification

| must_have | Status | Evidence |
|-----------|--------|----------|
| Zero-click after consent, for Chrome-signed-in users | ✅ | `maybeAutoMint` in popup.js runs on DOMContentLoaded; `getAuthToken` silent after first grant. |
| memory-api accepts both ID and access tokens | ✅ | Two distinct branches in `get_current_principal`, plus the existing JWT one. 10/10 unit tests PASS. |
| Token detection unambiguous | ✅ | JWT shape requires `token.count(".") == 2`; access token branch requires `not token.startswith("xbt_") and not token.startswith("gho_") and "." not in token`. Disjoint by construction. |
| Disconnect clears Chrome's cached Google token | ✅ | `clearGoogleAuthToken()` calls `chrome.identity.removeCachedAuthToken` before clearing storage. |
| Silent failure degrades gracefully | ✅ | On `{ok: false}` from MINT_AND_CONNECT, `maybeAutoMint` clears the loader and re-enables the Connect button without trapping the user. |

## Tests

```
$ cd apps/memory-api && python -m pytest tests/test_auth.py -x
10 passed, 3 skipped, 1 warning in 0.48s

$ cd chrome-extension && node tests/run_tests.mjs
6/6 test files passed (53 assertions)
```

## Out-of-band manual UAT (user runs locally)

1. `git pull` in `D:/VSC/xbrain`.
2. `chrome://extensions` → xbrain → ↻ (version still 1.2.0 — no manifest change).
3. **First-time after upgrade**: open side panel → "Connecting…" briefly → Chrome shows the small consent dialog ONE time → click Allow → 🟢 + email + token auto-copied.
4. **Every subsequent open**: just 🟢 in ~1s. No clicks, no popups.
5. **Account switch**: Disconnect → re-open side panel → consent shows again, choose a different Google account → 🟢 with the new email.

## Out of scope (deferred)

- Remove the legacy ID-token verification path from memory-api once the launchWebAuthFlow callers are all known dead. Not needed today (zero cost).
- Microsoft / GitHub silent OAuth via the same auto-mint flow. Different identity provider, would require its own plumbing.
