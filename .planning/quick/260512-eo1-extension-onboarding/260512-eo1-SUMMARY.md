---
title: Extension onboarding + popup/LibreChat FR→EN migration
quick_id: 260512-eo1
slug: extension-onboarding
date_completed: 2026-05-12
status: complete
must_haves_met: true
---

# Quick Task 260512-eo1 — SUMMARY

## Goal (recap)

Close the Phase 9 onboarding gap surfaced during 2026-05-12 UAT — popup expected `xbt_token` + `user_sub` in `chrome.storage.session` with no UI to mint or store them — by adding a single-click Connect flow, persisting to `chrome.storage.local`, and migrating popup + LibreChat strings to English per the new `CLAUDE.md` language policy.

## What shipped

| Component | Change |
|-----------|--------|
| `chrome-extension/onboarding.js` (NEW) | Pure module: `readStoredAuth`, `mintAndConnect`, `disconnectAuth` with deps injection — node-testable. |
| `chrome-extension/background.js` | Thin SW wrappers, `MINT_AND_CONNECT` + `DISCONNECT` runtime handlers, `openBridgeWS` uses local-then-session fallback, `storage.onChanged` listens on both areas. |
| `chrome-extension/popup.html` + `.css` | `lang="en"`, Connect-button skeleton, inline `#connect-status` div, sessions section toggled via `hidden` attr. All visible strings English. |
| `chrome-extension/popup.js` | `renderConnectState()` source of truth = `chrome.storage.local.xbt_token`. Wired Connect/Disconnect to new SW handlers. All strings English. |
| `chrome-extension/manifest.json` | `description` field translated. |
| `chrome-extension/tests/test_onboarding.mjs` (NEW) | 8 PASS — readStoredAuth (3 cases), mintAndConnect (3 cases: success + 2 failure paths), disconnectAuth (2 cases: happy path + network failure resilience). |
| `infrastructure/librechat/librechat.yaml` | Endpoint renamed `"Claude (mon abonnement)"` → `"Claude Pro/Max"` (name + modelDisplayLabel). FR comments → EN. |
| `infrastructure/scripts/verify-phase9.sh` | Test 7 greps the new label. |

## Commits (atomic, on `main`)

| Commit | Task | Description |
|--------|------|-------------|
| `6db5f2a` | 1 | popup HTML + CSS — Connect button skeleton + FR→EN |
| `1b6c9ff` | 2 | onboarding helpers + storage.local fallback in SW |
| `544db2d` | 3 | popup.js — single-click Connect/Disconnect + FR→EN |
| `3d7d453` | 4 | Rename LibreChat endpoint + manifest + verify FR→EN |
| `5809d8b` | 5 | test_onboarding.mjs — 8 PASS, full suite still green |

## must_haves verification

| must_have | Status | Evidence |
|-----------|--------|----------|
| Popup shows "Connect" button when no xbt_ token stored, full mint+WS flow runs in one click | ✅ | `popup.html` `#connect-row`, `popup.js` `renderConnectState`, `handleConnect → MINT_AND_CONNECT`. |
| Token + user_sub persist in `chrome.storage.local`, survives browser restart | ✅ | `onboarding.js` `mintAndConnect` writes to `storage` (bound to `chrome.storage.local` in SW). Tests #4, #7 cover writes. |
| Popup shows 🟢 + email + last seen when connected, Disconnect button revokes token + clears storage | ✅ | `popup.js` `renderClaudeSessionInfo` reads from `local`, `handleDisconnect → DISCONNECT` revokes via `DELETE /v1/me/api-token/{id}`. Test #7 covers. |
| All popup user-facing strings English | ✅ | `popup.html`, `popup.js`, `popup.css`, `manifest.json` all EN. `grep -iE 'é\|è\|ê\|à\|ç' chrome-extension/popup.{html,js,css}` → 0 matches. |
| LibreChat endpoint label is "Claude Pro/Max", verify-phase9.sh test 7 matches | ✅ | `librechat.yaml` line 66, `verify-phase9.sh` test 7 PASS on VM (6/6 PASS post-deploy). |

## Verification on VM

```
=== Phase 9 Verification ===
[1/8] session-bridge container running              PASS
[2/8] /healthz reachable                            PASS (active_sockets: 1)
[3/8] bridge.example.com DNS resolves              PASS
[4/8] nginx vhost 50-bridge.conf loaded             PASS
[5/8] WebSocket endpoint reachable end-to-end       SKIPPED (no VERIFY_XBT_TOKEN)
[6/8] user_external_sessions table exists           PASS
[7/8] librechat.yaml contains "Claude Pro/Max"      PASS
[8/8] translator tests                              SKIPPED (no node on VM)
PASS: 6/6 (SKIPPED: 2)
```

Local: `node chrome-extension/tests/run_tests.mjs` → 4/4 test files PASS (32 individual assertions).

## Out-of-scope (deferred)

- French strings in `apps/*` (internal logs, comments, READMEs) — not user-facing through the popup/chat surfaces. Grep across `apps/` flagged 11 files (`memory-api/main.py`, `openwebui-pipeline/*`, `librechat-bridge/task_intent_detector.py`, etc.) — schedule a follow-up quick task `apps-french-comments` when the developer wants to clean up the codebase-wide language drift.
- Historical Phase 9 planning artifacts (`09-UAT.md`, `09-CAPTURE.md`, etc.) — touch only when modifying for other reasons.

## Out-of-band manual UAT (user runs locally)

1. `git pull` in the local working copy.
2. Reload extension in Chrome (`chrome://extensions` → ↻ on xbrain).
3. Click extension icon → see "🔑 Connect xbrain account" button.
4. Click Connect → Google consent flow (or cached) → "Connected as <email> ✓" message → 🟢 + Disconnect button appears.
5. Close Chrome entirely → reopen → click extension → still 🟢 (token persisted in `chrome.storage.local`).
6. Open LibreChat (https://chat.example.com) → confirm endpoint dropdown shows "Claude Pro/Max" (not French).
7. Click Disconnect → confirm dialog → button reverts to "Connect"; verify in DB that the `user_api_tokens` row is `revoked_at IS NOT NULL`.
