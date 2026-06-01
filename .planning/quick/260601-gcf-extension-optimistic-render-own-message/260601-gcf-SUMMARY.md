---
quick_id: 260601-gcf
status: complete
---

# Summary — 260601-gcf — Extension optimistic render

**Commit:** 79e32d5 — `fix(extension): optimistically render own sent message instead of waiting for WS echo`

## Diagnosis (verified live on VM)
Backend realtime is healthy: extension subscribed to `team:0de901c5…` (Centrifugo num_clients:1),
secrets match (HMAC + API key), and memory-api publishes `{"type":"message",…}` to `team:<id>`
(`POST /api/publish 200 OK`). The bug was purely frontend: `popup.js::sendMessage` POSTed the
message and cleared the input but never rendered it — it depended on the Centrifugo echo, which
lags/misses while a Chrome action popup is backgrounded (popups tear down JS/WS on blur).

## Change
- `chrome-extension/popup.js` — `sendMessage` now captures the POST response (`team_chat.py:236`
  returns the serialized message) and calls `renderMessage(sent,{prepend:false})` + `scrollToBottom()`.
  `renderMessage` de-dupes by `msg.id` (popup.js:455), so the later WS `"message"` publication for
  the same id is a no-op — no duplicate.
- Refreshed `chrome-extension/popup.js` inside `chrome-extension.zip` (untracked build artifact).

## Verification
- `node --check chrome-extension/popup.js` → OK.
- Logic: POST returns the same serialized shape the WS echo carries → de-dup by id holds.

## User action required
The extension runs locally in Chrome — reload the unpacked extension (chrome://extensions →
reload) or reinstall the zip. Backend untouched (no deploy).

## Not fixed here (separate, backend-confirmed-working)
Incoming messages from OTHER users / @claude streams still rely on the WS echo; that path is
backend-healthy. If those also lag in the popup, it's the popup-blur lifecycle — the Chrome
side panel (Chrome 114+) is the persistent-realtime surface.
