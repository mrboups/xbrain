---
quick_id: 260601-gcf
slug: extension-optimistic-render-own-message
mode: quick
status: planned
---

# Quick Task 260601-gcf — Extension: optimistic render of own sent message

## Problem (user report)
"I send a message in the extension chat but it does not display — I have to close/reopen
to see it." Backend realtime verified WORKING live: the extension IS subscribed
(`team:<id>` num_clients:1) and memory-api publishes the message (`POST /api/publish 200 OK`,
channel `team:<id>`, `{"type":"message",...}`). Root cause is purely frontend:
`chrome-extension/popup.js::sendMessage` POSTs the message and clears the input but does
**NOT render it** — it relies entirely on the Centrifugo echo, which can lag or be missed
while the popup is backgrounded (Chrome action popups tear down their JS/WS on blur). So the
user's own message only appears via the (flaky-in-popup) echo or on reopen (HTTP refetch).

## Fix
`sendMessage`: capture the POST response (the route returns the serialized message at
team_chat.py:236 `return payload`) and render it immediately. `renderMessage()` de-dupes by
`msg.id` (popup.js:455), so the later `"message"` publication for the same id is a no-op —
no duplicate. This is the standard chat UX (render your own message optimistically).

## Tasks
- chrome-extension/popup.js — `sendMessage` renders `sent` (the POST response) via
  `renderMessage(sent, {prepend:false}) + scrollToBottom()` when `sent.id` exists.
- Refresh `chrome-extension/popup.js` inside `chrome-extension.zip` (untracked build artifact)
  so installing from the zip also gets the fix.

## Note
The extension runs LOCALLY in the user's Chrome — they must **reload the unpacked extension**
(chrome://extensions → reload) or reinstall the zip for the change to take effect.

## Constraints
English only. No behavior change beyond the optimistic render. Atomic commit (code only).
