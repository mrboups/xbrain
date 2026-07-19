# Phase 22: Push-a-Link (nudge a member to open a URL) — Context

**Gathered:** 2026-07-19 (autonomous — backlog feature #2; user directive "continue with everything").
**Source:** BACKLOG "Push-a-link — nudge a specific member to open a page in their browser".

<domain>
## Phase Boundary

From the team chat, a member targets another team member with a URL. The target's extension shows a **native OS notification** (sender + full destination URL) and, **only on the target's explicit click**, opens the URL as a new browser tab. Consent-gated, never silent. No new infrastructure — reuses Centrifugo + the extension.

**IN scope:** a `POST` nudge endpoint (memory-api) that validates sender+target are members of the SAME team and publishes a targeted Centrifugo event to the target's existing `user:<source_user_id>` channel; the extension subscribing to its own user channel + handling the `open_url` event → `chrome.notifications.create` (sender + real URL) → on click `chrome.tabs.create`; a recipient-side "allow open-link requests" toggle; per-sender rate limiting; URL safety (https/http only, reject/expand shorteners, show the true destination); a small "send this link to a member" affordance in the chat UI.

**OUT of scope:** guaranteed delivery when the browser is fully closed (Centrifugo is live-only; offline delivery documented as a residual — a "pending nudges" fetch-on-reconnect can be a follow-up, not this phase); Web Push; cross-team nudges (same-team only).
</domain>

<the_hard_facts_from_live_code>
1. **The `user:<source_user_id>` channel already exists and is already granted.** `apps/memory-api/app/routes/team_chat.py:136` — the centrifugo-token endpoint appends `user:{user.source_user_id}` to the channels claim ("for direct notifications (Phase 2)"). Centrifugo enforces the channels claim server-side. So the transport for a direct-to-user event is ALREADY provisioned — the extension just doesn't subscribe to it yet, and nobody publishes to it yet.
2. **The publish path exists.** `apps/memory-api/app/services/centrifugo_client.py:83` — `async def publish(channel, data) -> bool`. team_chat already fire-and-forgets to `team:<id>`. The nudge publishes to `user:<target.source_user_id>` with `{type:'open_url', url, from, team_id}`.
3. **Membership + rate-limit helpers exist.** `team_chat.py:63 _resolve_team_and_check_membership(session, user_id, team_id)`; `app/services/rate_limit.py:43 enforce_rate_limit(request, limit_str, bucket)`. The sender must be a member (resolve their membership) AND the target must be a member of the same team (new check).
4. **The extension already has the permissions + primitives.** `chrome-extension/manifest.json:7` permissions include `notifications`. `background.js:1189-1190,1297-1298` already call `chrome.notifications.create`. `popup.js:143` already calls `chrome.tabs.create`. `popup.js:360-399` builds a Centrifuge instance and subscribes to `team:<id>` — it must ALSO subscribe to `user:<source_user_id>` and route the `open_url` event.
5. **`chrome.tabs.create` needs no extra manifest permission** (it is not gated by the "tabs" permission — that only gates reading tab URLs/titles). The consent CLICK is both the safety gate and the required user gesture.
</the_hard_facts>

<decisions>
## Implementation Decisions (locked)

### D-22-01 — Endpoint: `POST /v1/teams/{team_id}/nudge-open`, same-team only.
Body `{ target_user_id (or target_source_user_id), url }`. The route: resolve the SENDER's membership of `team_id` (`_resolve_team_and_check_membership`); assert the TARGET is also a member of that team (403 otherwise — never publish to a non-member's channel); validate the URL (D-22-03); publish to `user:<target.source_user_id>` the event `{type:'open_url', url, from:{display_name/sub}, team_id, team_slug}`. Fire-and-forget publish like the existing chat fan-out; return 202/200. NO cross-team, NO arbitrary user targeting.

### D-22-02 — CONSENT-GATED, never silent (the whole security point).
The extension MUST NOT auto-open. On receiving `open_url` it shows `chrome.notifications.create` with the **sender's name + the full, un-shortened destination URL**, and opens the tab (`chrome.tabs.create`) ONLY in the notification's click/button handler — an explicit user gesture. A message from another user must never move the recipient's browser without their click. (Browsers also require a user gesture for programmatic tab-open, so this is enforced two ways.)

### D-22-03 — URL safety (server-side validation).
Accept only `http`/`https` schemes (reject `javascript:`, `data:`, `file:`, etc. → 422). Enforce a max length. The notification shows the REAL URL. Shorteners: for v1, do NOT auto-expand server-side (an SSRF vector) — instead show the literal URL to the recipient so they see exactly what they'd open; a documented follow-up can add opt-in expansion. Reject obviously malformed URLs (422).

### D-22-04 — Recipient opt-out + per-sender rate limit.
A recipient-side setting "Allow open-link requests" (default ON), stored in `chrome.storage` — when OFF, the extension ignores `open_url` events (no notification). Server-side: `enforce_rate_limit` per sender (e.g. a modest cap/min) so nudge can't be used to spam-notify. Bucket keyed by sender sub.

### D-22-05 — Chat UI affordance (small).
A lightweight way to send a link to a specific member from the chat (e.g. a per-member action, or a "send link" control that picks a member + URL). Keep it minimal and consistent with the Phase-20 shadcn styling; do NOT add navigation. English-only strings. Claude's discretion on exact placement.

### D-22-06 — Offline = documented residual, not promised.
If the target's extension/popup is closed, Centrifugo does not deliver live. This phase does NOT guarantee closed-browser delivery. Document it; a "pending nudges persisted server-side, fetched on reconnect" enhancement is a follow-up, not this phase.

### Claude's Discretion
- Target identified by `user_id` (UUID) vs `source_user_id` — pick what the client already has cheaply; the publish channel is keyed by `source_user_id`.
- Whether the recipient opt-out is client-only (v1) or also a server-stored preference — client-only is acceptable for v1 (the recipient's own extension enforces it), note it.
- Exact chat UI placement of the "send link to member" affordance.
</decisions>

<canonical_refs>
## Canonical References — read before planning/executing
- `apps/memory-api/app/routes/team_chat.py` — `_resolve_team_and_check_membership` (:63), the centrifugo-token channel grant (:119-145, note `user:<sub>` already granted), the existing `centrifugo_client.publish` fire-and-forget pattern (~:231).
- `apps/memory-api/app/services/centrifugo_client.py` — `publish(channel, data)` (:83).
- `apps/memory-api/app/services/rate_limit.py` — `enforce_rate_limit` (:43).
- `apps/memory-api/app/repos/teams.py` — membership lookup helpers (to assert the target is a member).
- `chrome-extension/popup.js` — the Centrifuge instance + `team:<id>` subscription (:340-399) to extend with a `user:<sub>` subscription + `open_url` routing; `chrome.tabs.create` (:143).
- `chrome-extension/background.js` — existing `chrome.notifications.create` usage (:1189, :1297) to mirror.
- `chrome-extension/options.js` / `options.html` — where the recipient "Allow open-link requests" toggle lives.
- `chrome-extension/tests/` — the node test harness (run from OUTSIDE `.claude/` — its package.json forces commonjs).
- CLAUDE.md — product strings English-only.
</canonical_refs>

<specifics>
## The gate lesson applies
A "nudge works" claim proves nothing until the event traverses the real path. Verification MUST: against a REAL Postgres testcontainer, POST the nudge as sender A → assert it publishes an `open_url` event addressed to target B's `user:<sub>` channel (assert via a captured/stubbed publish recorder — NOT by mocking the membership/validation); assert a non-member target → 403 (no publish); assert a `javascript:`/`file:` URL → 422 (no publish); assert cross-team target → 403; assert the per-sender rate limit trips. Plus a client test: the extension's `open_url` handler builds a notification with the real URL and does NOT open a tab without the consent click, and honors the opt-out toggle. SKIP=FAIL. Dev arm64 / prod amd64 — no cross-arch deploy; Git Bash docker needs MSYS_NO_PATHCONV=1.
</specifics>

<deferred>
- Closed-browser / offline delivery (pending-nudge persistence, Web Push) — residual, documented.
- Server-side shortener expansion — a follow-up (SSRF-sensitive); v1 shows the literal URL.
- Cross-team or arbitrary-user targeting — out of scope (same-team only).
</deferred>

---
*Phase: 22-push-a-link*
*Context gathered: 2026-07-19 (autonomous)*
