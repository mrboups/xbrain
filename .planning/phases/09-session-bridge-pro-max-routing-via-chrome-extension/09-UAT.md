---
phase: 9
plan: 06
type: uat
generated: 2026-05-12
maps_to: ROADMAP Phase 9 success criteria 1-6
---

# Phase 9 UAT — Session Bridge (Pro/Max routing)

| Field           | Value                       |
| --------------- | --------------------------- |
| Verifier        | _______________________     |
| Date            | _______________________     |
| VM IP           | __VM_HOST__              |
| Bridge host     | https://bridge.example.com |
| Chat host       | https://chat.example.com   |
| Extension ver.  | 1.1.0                       |

This document is walked manually on the VM after `verify-phase9.sh` passes.
It maps directly to the six ROADMAP Phase 9 success criteria. SKIPPED in the
script never blocks — only FAIL == 0 is required.

> **Note on health checks:** `bridge.example.com/nginx-health` returns 200 from
> the nginx layer (no upstream call) — use this for external monitoring. The
> session-bridge app's `/healthz` endpoint (returning
> `{"status":"ok","active_sockets":N}`) is intentionally only reachable from
> inside the Docker network — exposing it publicly would leak app internals.
> Any reference to `/healthz` in this UAT against `127.0.0.1:8105` or `BRIDGE_LOCAL`
> targets the in-cluster bridge directly (still correct); external probes always
> go through `/nginx-health`.

## Pre-checks (must be true before starting)

- [ ] `bash infrastructure/scripts/verify-phase9.sh` returned `PASS: N / N (SKIPPED: M)` on the VM, with `FAIL == 0` (M ≥ 0 is fine)
- [ ] Cloudflare A record `bridge.example.com` resolves to a Cloudflare anycast IP (proxied / orange cloud)
- [ ] Cloudflare zone `example.com` → Network → **WebSockets toggle ON**
- [ ] `docker ps` shows `xbrain-session-bridge` (or whatever name the container resolves to) in state `running` / `healthy`
- [ ] `docker exec xbrain-memory-api alembic current` shows head `0014` (or later)
- [ ] xbrain Chrome extension v1.1.0 loaded as unpacked (chrome://extensions → reload card after `git pull`)
- [ ] User logged in to https://chat.example.com (xbt_ token present in `chrome.storage.session.xbt_token`)
- [ ] User logged in to https://claude.ai in the **same browser profile** (cookies must be reachable to the extension)

## Acceptance items

### SC-1: E2E quota consumption (LibreChat → extension → claude.ai with Pro/Max)

ROADMAP criterion 1 — end-to-end routing of a chat request through the user's own Claude Pro/Max subscription.

- [ ] In LibreChat (https://chat.example.com) open the endpoint dropdown — **"Claude (mon abonnement)"** is present
- [ ] Select it. LibreChat asks for an API key — paste your `xbt_` token (NOT an Anthropic key)
- [ ] Send the message: `ping from xbrain phase 9 UAT`
- [ ] Response streams back word-by-word (SSE working end-to-end)
- [ ] Open https://claude.ai/settings/usage in a new tab — the message count on Pro/Max usage incremented (NOT a debit on the team Anthropic key in Langfuse)
- [ ] (Optional) Confirm in Langfuse that NO trace landed under the team Anthropic key for this conversation

### SC-2: Explicit error when extension absent / not logged in

ROADMAP criterion 2 — when the bridge can't reach the user's browser, LibreChat surfaces a useful error instead of falling back silently.

- [ ] Disable the xbrain extension (chrome://extensions → toggle off) OR close all Chrome windows briefly so the WebSocket drops
- [ ] Wait ~30 s for the session-bridge to detect the disconnect (the Pool entry expires last-write-wins style)
- [ ] In LibreChat, send another message on "Claude (mon abonnement)"
- [ ] LibreChat surfaces an error containing **"install xbrain extension and login to claude.ai"** (the `no_session` code from session-bridge)
- [ ] NO silent fallback to the team Anthropic key — confirmed by checking Langfuse: no new trace under the team key for this attempt
- [ ] Re-enable the extension → next message streams normally (recovery path works)

### SC-3: Infrastructure reachability (container + nginx vhost)

ROADMAP criterion 3 — `session-bridge` container running, `bridge.example.com` reachable end-to-end via the nginx vhost.

- [ ] `curl -fsS https://bridge.example.com/nginx-health` returns `ok` (proves Cloudflare → nginx → bridge upstream chain)
- [ ] `curl -s -o /dev/null -w '%{http_code}' -X POST https://bridge.example.com/v1/chat/completions` returns `401` (route exists, auth check active)
- [ ] With a valid `xbt_` token: `curl -s -X POST https://bridge.example.com/v1/chat/completions -H "Authorization: Bearer xbt_..." -H "Content-Type: application/json" -d '{"messages":[{"role":"user","content":"x"}]}'` returns 200 (if extension is connected and streaming) OR 503 with `{"error":"...","code":"no_session"}` (if extension is not connected) — both prove route + auth wired
- [ ] Optional: `wscat -c "wss://bridge.example.com/ws/test-probe?token=invalid"` closes with code 4401 (auth gate at WS layer)

### SC-4: Popup status + `user_external_sessions` table populated

ROADMAP criterion 4 — extension popup shows session status, and the `user_external_sessions` table has a row reflecting the user's logged-in claude.ai email.

- [ ] Open the xbrain extension popup → Sessions section shows 🟢 (green dot) next to "Claude (mon abonnement)"
- [ ] The email displayed matches your current claude.ai logged-in account (`metadata.email_logged` rendered from the row)
- [ ] The "last seen" relative time is recent (seconds or minutes ago, not stale)
- [ ] Click the **Refresh** button → popup re-renders, dot stays 🟢
- [ ] Run on the VM: `docker exec xbrain-postgres psql -U xbrain -d xbrain -c "SELECT user_id, provider, extension_id, last_seen_at, metadata FROM user_external_sessions WHERE provider='claude' ORDER BY last_seen_at DESC LIMIT 5"` — the row for your user exists, `metadata->>'email_logged'` matches your claude.ai email
- [ ] Click the **Disconnect** button → confirm dialog → popup re-renders 🔴 (dot turns red), and the DB row is gone (`DELETE` cascaded). Next message in LibreChat returns `no_session` until you re-open claude.ai and trigger a new register handshake (reload the extension card if it doesn't reconnect automatically within 30 s)

### SC-5: claude.ai SSE → OpenAI SSE translation correctness

ROADMAP criterion 5 — the translator in `chrome-extension/translate_sse.js` converts the claude.ai internal SSE format into OpenAI ChatCompletions streaming SSE without losing tokens or producing malformed JSON.

- [ ] In LibreChat (with "Claude (mon abonnement)" selected), send: `list 5 fruits one per line, no numbering, no commentary`
- [ ] Each fruit appears **progressively** (not all at once after a pause — this is the streaming evidence)
- [ ] No "JSON parse error" or "malformed response" surfaces in LibreChat
- [ ] (Optional, on dev machine) `cd chrome-extension && node tests/run_tests.mjs` exits 0 with 14+ assertions PASS (regression coverage for the translator)
- [ ] (Optional) Open the extension service worker DevTools console — no `[xbrain] error parsing event` logs for this conversation

### SC-6: `verify-phase9.sh` clean run

ROADMAP criterion 6 — the gating script itself reports a clean pass.

- [ ] On the VM, from `/opt/xbrain`: `bash infrastructure/scripts/verify-phase9.sh`
- [ ] Final line prints: `PASS: N / N (SKIPPED: M)` with `FAIL == 0`
- [ ] Script exit code is 0: `echo $?` → `0`
- [ ] Test 3 (DNS) is PASS once the Cloudflare A record is live; SKIPPED while it's still propagating — both are acceptable as long as FAIL == 0
- [ ] Test 5 (WS upgrade) is PASS when `VERIFY_XBT_TOKEN` is exported; SKIPPED otherwise — both are acceptable

## Sign-off

- [ ] All 6 acceptance items above passed
- [ ] Issues logged (if any): _________________________________________________
- [ ] Verifier signature: _________________________________________________
- [ ] Date: _________________________________________________

When all six are ticked, reply `uat-pass` to the orchestrating GSD command.
If any SC fails, reply `uat-fail: SC-N` with notes; a gap-closure plan will follow.
