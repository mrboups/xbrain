---
phase: 9
plan: 03
subsystem: chrome-extension
tags: [chrome-extension, websocket, mv3, session-bridge]
status: complete
completed: 2026-05-12
requires:
  - "09-01: session-bridge WS endpoint + register frame contract"
  - "09-02: claude_ai_client.js handleClaude + getOrgId exports"
  - "09-04: memory-api upsert (consumed by session-bridge on register handshake)"
provides:
  - "Chrome extension v1.1.0 manifest with claude.ai + bridge host_permissions"
  - "Persistent WS service-worker → wss://bridge.grooveos.app/ws/{user_sub}"
  - "Exponential-backoff reconnect + chrome.alarms watchdog (MV3-safe)"
  - "register handshake frame on every WS open (populates user_external_sessions)"
  - "chat_request dispatcher → handleClaude with sendFrame callback"
  - "ws_status_query message handler (consumed by 09-05 popup)"
affects:
  - chrome-extension/manifest.json
  - chrome-extension/background.js
  - chrome-extension/ws_keepalive.js
  - chrome-extension/tests/test_ws_keepalive.mjs
tech-stack:
  added:
    - "chrome.alarms (1-minute watchdog)"
    - "WebSocket API in MV3 service worker (with type:module manifest)"
  patterns:
    - "Idempotent openBridgeWS (no-op if OPEN/CONNECTING)"
    - "Exponential backoff 2^n*1000 ms, +/-20% jitter, capped 30s"
    - "Storage-change reactive open: WS re-opens when xbt_token appears"
    - "Sender-id gating on chrome.runtime.onMessage (T-09-03-02)"
key-files:
  created:
    - chrome-extension/ws_keepalive.js
    - chrome-extension/tests/test_ws_keepalive.mjs
  modified:
    - chrome-extension/manifest.json
    - chrome-extension/background.js
decisions:
  - "register frame is fire-and-forget on ws.onopen — failures to fetch email_logged/org_id are non-fatal and send nulls; session-bridge tolerates partial metadata"
  - "20s ping (< MV3 30s SW idle) + 1-min chrome.alarms watchdog gives both warm-keepalive and cold-recovery"
  - "Close codes 4401/4403 do NOT trigger auto-reconnect — auth gate, waits for storage refresh"
  - "ESM imports require manifest background.type='module'; ws_keepalive.js stays Chrome-free for node test isolation"
metrics:
  duration_minutes: 12
  tasks_completed: 3
  files_touched: 4
  test_assertions_added: 6
commits:
  - "0b997b9 feat(phase-9-03): bump extension to v1.1.0 with claude.ai host_permissions"
  - "f6413b9 feat(phase-9-03): add ws_keepalive.js backoff/ping module with tests"
  - "d36b5bc feat(phase-9-03): wire background.js to session-bridge WebSocket"
---

# Phase 9 Plan 03: Chrome Extension WS Layer — Summary

Persistent WebSocket from the extension's MV3 service worker to `wss://bridge.grooveos.app/ws/{user_sub}`, with register-on-open handshake and chat_request dispatch to `handleClaude` (from 09-02). Closes the round-trip: bridge → user's browser → claude.ai with Pro/Max cookies.

## WS frame contracts honored

**Extension → bridge:**
- `{type:"register", provider:"claude", extension_id, email_logged, org_id}` — sent immediately on `ws.onopen`. `email_logged` fetched best-effort from `https://claude.ai/api/auth/current_account` (credentials:include); `org_id` from `getOrgId()`. Both nullable.
- `{type:"chunk"|"end"|"error", request_id, ...}` — emitted by `handleClaude` via injected `sendFrame` callback.
- `{type:"ping", ts}` — every 20 s while WS is open.

**Bridge → extension:**
- `{type:"chat_request", request_id, openai_body}` → dispatched to `handleClaude(msg, sendFrame)`.
- `{type:"register_ack"|"ping"|"pong"}` → silently ignored.

## Files touched

| File | Status | Purpose |
|------|--------|---------|
| `chrome-extension/manifest.json` | modified | v1.0.0 → v1.1.0, host_permissions for claude.ai/api.claude.ai/bridge.grooveos.app, `"type":"module"` SW, `alarms` permission |
| `chrome-extension/ws_keepalive.js` | created | Pure module: `computeBackoffMs`, `PING_INTERVAL_MS`, `WATCHDOG_PERIOD_MIN`, `MAX_ATTEMPT` |
| `chrome-extension/background.js` | modified | Imports handleClaude/getOrgId; `openBridgeWS`, ping/reconnect/watchdog, register-on-open, chat_request dispatcher, `ws_status_query` reply, sender.id guard. Phase 4/8 Web Clipper handlers preserved |
| `chrome-extension/tests/test_ws_keepalive.mjs` | created | 6 assertions — backoff math + jitter bounds + MV3 invariant |

## Verification

- `node --check background.js` → OK
- `node tests/run_tests.mjs` → **3/3 test files passed, 14 assertions PASS** (translate_sse + openai_to_claudeai regression + new ws_keepalive)
- All `must_haves.key_links` grep gates satisfied (WS URL, storage.session.get, alarms.create, handleClaude import, register frame, fetchClaudeEmail)
- No `window.postMessage` outside comments

## Reload instructions (manual smoke — pre-Wave 3)

L'extension est en code uniquement, jamais publiée sur le Chrome Web Store. Pour la recharger :

1. Ouvre `chrome://extensions/`
2. Active le **Mode développeur** (toggle en haut à droite) s'il ne l'est pas déjà
3. Clique **Charger l'extension non empaquetée** et sélectionne le dossier `D:\VSC\xbrain\chrome-extension`
   - Si l'extension est déjà chargée depuis une précédente phase : clique simplement le bouton **↻ recharger** de sa carte
4. Vérifie que la version affichée est **1.1.0**
5. Clique **service worker** sur la carte de l'extension pour ouvrir DevTools du SW
6. Dans la console DevTools du SW, tu dois voir :
   - `[xbrain] no token/sub yet, deferring WS open` (si pas encore loggé) — c'est attendu
   - **OU** `[xbrain] opening WS` puis `[xbrain] WS open` puis `[xbrain] register sent { email_logged: <set>, org_id: <set> }` si déjà loggé sur xbrain et claude.ai
7. Si l'extension n'a jamais reçu de xbt_token, ouvre la popup et lance le flow OAuth (Phase 8) — la WS s'ouvrira automatiquement dès que `chrome.storage.session.xbt_token` est posé (listener `chrome.storage.onChanged`)

**Smoke rapide WS reconnect** : dans DevTools du SW, tape `ws.close()` — tu dois voir `[xbrain] WS closed 1005` puis `[xbrain] reconnect in ~2000ms (attempt 1)` puis re-open et `register sent` à nouveau.

## Threat coverage

| ID | Threat | Mitigated by |
|----|--------|--------------|
| T-09-03-01 | xbt_token in WS URL leak | Token only in TLS-wrapped WSS body; nginx will elide query in 09-04 |
| T-09-03-02 | Cross-extension onMessage spoof | `sender.id === chrome.runtime.id` guard added to onMessage listener |
| T-09-03-03 | Reconnect storm | `computeBackoffMs` capped at 30 s; chrome.alarms 1 min watchdog |
| T-09-03-04 | handleClaude error swallow | Try/catch around `await handleClaude` → emits `type:error` frame |
| T-09-03-05 | email_logged leak | Acceptée — l'email est celui de l'utilisateur, transmis uniquement à xbrain via TLS |

## Deviations from Plan

None — les 3 tâches ont été exécutées strictement comme spécifié, contrats `must_haves` respectés à la lettre.

## What 09-05 / 09-06 still need

- **09-05** (parallèle, déjà partiellement shippé d'après git log) — popup.js doit envoyer `chrome.runtime.sendMessage({kind:"ws_status_query"})` et lire la réponse `{readyState, last_open_ms}` pour afficher 🟢/🔴 + ago. Le handler côté SW est en place.
- **09-06** (Wave 3 verify) — `verify-phase9.sh` doit charger l'extension en headless Chrome OU se fier au flow UAT manuel décrit ci-dessus. Aucun blocker côté extension.

## Self-Check: PASSED

- `chrome-extension/manifest.json` (v1.1.0) — FOUND
- `chrome-extension/ws_keepalive.js` — FOUND
- `chrome-extension/tests/test_ws_keepalive.mjs` — FOUND
- `chrome-extension/background.js` (WS layer appended) — FOUND
- Commit `0b997b9` — FOUND in git log
- Commit `f6413b9` — FOUND in git log
- Commit `d36b5bc` — FOUND in git log
- 3/3 test files green, 14/14 assertions PASS
