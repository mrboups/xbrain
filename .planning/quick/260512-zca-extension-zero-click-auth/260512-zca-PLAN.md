---
title: Extension zero-click onboarding (silent Google auth + auto-mint)
quick_id: 260512-zca
slug: extension-zero-click-auth
mode: validate
date: 2026-05-12
must_haves:
  truths:
    - "After the first one-time Chrome consent, clicking the extension icon for a Chrome-signed-in user shows 🟢 + email automatically with no further clicks."
    - "memory-api accepts both Google ID tokens (legacy launchWebAuthFlow path) and Google OAuth2 access tokens (new getAuthToken path)."
    - "Token detection in deps.py is unambiguous: JWT-shape → ID token; xbt_/gho_/dotless opaque → respective non-JWT path."
    - "Disconnect revokes the Chrome-cached Google access token so the next Connect can pick a different account."
    - "When the silent path fails (e.g. user not signed into Chrome, consent cancelled), the manual Connect button stays available — degraded gracefully."
  artifacts:
    - apps/memory-api/app/auth.py (verify_google_access_token)
    - apps/memory-api/app/deps.py (auto-detect access token in get_current_principal)
    - apps/memory-api/tests/test_auth.py (6 new pytest cases)
    - chrome-extension/background.js (getGoogleIdToken via chrome.identity.getAuthToken, clearGoogleAuthToken)
    - chrome-extension/popup.js (maybeAutoMint, wired into DOMContentLoaded)
  key_links:
    - https://developer.chrome.com/docs/extensions/reference/api/identity#method-getAuthToken
    - https://developers.google.com/identity/protocols/oauth2/scopes#oauth2
    - .planning/quick/260512-eo1-extension-onboarding/260512-eo1-SUMMARY.md (parent task — manual Connect button)
---

# Quick Task 260512-zca — Extension zero-click onboarding

## Goal

Two follow-ups requested mid-UAT of the previous extension tasks:
1. The Google OAuth flow opens a separate Chrome window — can it be inside the extension UI? (No, Chrome security blocks it. But we can make the flow zero-window in 90 % of cases by using `chrome.identity.getAuthToken`.)
2. After Google connects, the user still has to click "Connect xbrain account" — can that be automatic too? (Yes — auto-dispatch MINT_AND_CONNECT when the popup opens and no token is stored.)

End-state UX for a Chrome-signed-in user: open side panel → 🟢 in ~1s, no clicks, no popups, no Google web view.

## Background — why getAuthToken vs launchWebAuthFlow

| | launchWebAuthFlow (current) | getAuthToken (new) |
|---|---|---|
| Provider | Any OAuth (Google/Microsoft/etc.) | Google only |
| Window | Always opens a separate browser window | Chrome-native consent on first call, silent afterwards |
| Returns | Whatever the redirect URL contains (we configured `id_token`) | OAuth2 access token (opaque) |
| Token cache | We had to manage our own `chrome.storage.session` cache | Chrome's identity provider handles caching + refresh |

Switching means the extension's side panel stays alive during sign-in (no focus-loss issue), no popup window flicker, and we get free token refresh — at the cost of needing memory-api to accept a different token shape. That's a 1-file change (`auth.py`) plus a deps.py detection tweak.

## Decisions (locked)

| Decision | Choice | Why |
|----------|--------|-----|
| Backend token shape | Both ID and access tokens accepted | Backward-compatible — no migration window. Bridge / GitHub / xbt_ paths unchanged. |
| Detection rule | JWT (2 dots) → ID token; no dots + not xbt_/gho_ → access token | Both are mutually-exclusive shapes; cheap regex-free check; no ambiguity. |
| userinfo cache TTL | 5 min | Same horizon as the JWKs cache; bounds Google API call rate. |
| Email verification | Require `email_verified != false` | Defensive against spoofable userinfo responses (even though Google's userinfo only returns verified emails today). |
| Auto-mint behavior on popup open | Once per popup open, guarded by `_autoMintAttempted` | Avoid infinite retry if the silent path fails. User can still click Connect to retry. |
| Disconnect cleanup | Also call `removeCachedAuthToken` | Otherwise next Connect silently reuses the same Google account — bad for account switching. |
| Token clipboard auto-copy | Keep from prior task | Still useful even when LibreChat autofill is on — user might want to paste elsewhere. |

## Tasks (each one is one atomic commit on `main`)

| # | Files | Description |
|---|-------|-------------|
| A | apps/memory-api/app/{auth,deps}.py + tests/test_auth.py | Add `verify_google_access_token` + integrate in `get_current_principal` |
| B | chrome-extension/background.js | Replace launchWebAuthFlow with getAuthToken; add `clearGoogleAuthToken` |
| C | chrome-extension/popup.js | New `maybeAutoMint()` triggered on DOMContentLoaded after `renderConnectState` |
| D | (covered by Task A's pytest + existing tests) | Existing `test_onboarding.mjs` already covers the mint logic; no new JS tests needed since Tasks B/C are thin glue |
| E | .planning/quick/260512-zca-.../{PLAN,SUMMARY}.md + .planning/STATE.md | Artifacts + push |

## Out-of-band manual UAT

1. `git pull` locally.
2. Reload extension in Chrome (version stays 1.2.0 — no manifest change).
3. **First-time path** (one consent shown):
   - Disconnect via popup if currently connected.
   - Open side panel → "Connecting your xbrain account…" loader → Chrome shows a small consent ("xbrain wants to access email/profile") → click Allow → 🟢 + email appear.
4. **Subsequent path** (zero clicks, zero popups):
   - Restart Chrome.
   - Open side panel → 🟢 appears in ~1s with no interaction.
5. **Account switch**: Disconnect → re-open → consent shows again (cache cleared via `removeCachedAuthToken`), pick a different Google account → 🟢 with the new email.
6. **Silent failure fallback**: Sign out of Chrome entirely → open side panel → loader briefly, then Connect button reappears (user can sign into Chrome and retry).

## Out of scope (deferred)

- Removing the legacy ID token verification path from memory-api. Keeping both for now — costs nothing, avoids a flag-day migration.
- Microsoft / GitHub OAuth via the same silent flow. Different identity providers, would need separate plumbing.
