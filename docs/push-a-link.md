# Push-a-link — nudge a teammate to open a URL

Push-a-link lets one team member ask another to open a web page. From the chat
popup you pick a **same-team** member and a URL; their browser extension raises a
native OS notification showing **your name and the full destination URL**, and the
page opens as a new tab **only when they click that notification**. It is a nudge,
never a remote-control: a message from another user can never move your browser
without your explicit click.

Phase: 22 (`22-push-a-link`). Requirement: `NUDGE-01`.

## Flow end to end

1. **Send.** In the popup, the "send link" control lists the current team's other
   members and takes a URL. On submit the extension does a client-side
   `isSafeHttpUrl` pre-check, then `POST /v1/teams/{team_id}/nudge-open` with
   `{ target_user_id, url }` and the caller's `xbt_` token.
2. **Server (memory-api).** `nudge_open` enforces, in order, before any publish:
   sender is a member of the team → else `403`; target is a member of the **same**
   team → else `403` (this covers non-members and other teams' members); URL is a
   well-formed `http`/`https` string within the length cap → else `422`; the
   per-sender rate limit is not exhausted → else `429`. Only then does it
   fire-and-forget publish `{ type: "open_url", url, from, team_id, team_slug }`
   to the target's own `user:<source_user_id>` Centrifugo channel and return `202`.
   The channel is derived server-side from a **verified** membership — the request
   body never names a channel, and the URL is validated purely lexically with no
   server-side fetch, DNS, or shortener expansion (an SSRF-safe choice).
3. **Receive (extension popup).** `popup.js` subscribes once to its own
   `user:<source_user_id>` channel (a channel the centrifugo-token endpoint
   already grants) and routes every `open_url` frame to `nudge_open.handleOpenUrl`.
   That handler honors the recipient's opt-out, re-checks the URL scheme, and — if
   both pass — raises a `chrome.notifications.create` notification carrying the
   sender's name and the literal URL. It **cannot** open a tab; it only stashes the
   url as `chrome.storage.session["nudge_<id>"]`.
4. **Consent click (extension background).** `background.js`'s
   `chrome.notifications.onClicked` listener looks up `nudge_<id>` and calls
   `chrome.tabs.create({ url })`. This is the **only** place a nudge opens a tab.
   The click is both the user's consent and the browser-required user gesture. The
   pending url is removed from session storage the instant the tab opens.

## Security posture

- **Same-team only.** The server re-resolves the target's membership of the exact
  team in the path. Cross-team and arbitrary-user targeting are rejected (`403`)
  and never reach a publish.
- **Consent-gated, never silent.** The tab opens only on the recipient's explicit
  notification click. The receive handler (`nudge_open.js`) has no tab API at all,
  so "no click, no navigation" is a structural guarantee, not a convention.
- **URL-validated.** Only `http`/`https` are accepted, length-capped, and shown
  literally (un-shortened) so the recipient sees exactly where they would go.
  Client validation is UX only; the server is the authoritative boundary.
- **Per-sender rate-limited.** A modest per-sender cap (`429` when exceeded) stops
  the nudge being used to spam-notify a teammate. The bucket is keyed on the
  sender's `sub`, not the client IP.
- **Recipient opt-out (default ON).** The "Allow open-link requests" setting is
  stored client-side in `chrome.storage.sync`. When a recipient turns it OFF their
  own extension drops incoming `open_url` events with no notification. It is
  enforced by the recipient's own extension (client-only for v1).

## Known residual — offline / closed-browser delivery (D-22-06)

Delivery is **live-only** through Centrifugo. If the target's popup / side panel is
**closed** when the nudge is published, the `open_url` event is not delivered and
the notification never appears — there is no server-side queue and no retry. This
phase does **not** promise closed-browser delivery. This is a documented residual,
not a bug.

A follow-up could persist pending nudges server-side and fetch-on-reconnect, and/or
add Web Push for true offline delivery. Both were explicitly out of scope for this
phase — **and the Web Push half has since shipped, see below.**

## Update — Web Push shipped in Phase 27 (2026-08-01)

**Half of the residual above is closed.** The PWA at `/app/` carries real web push:
a VAPID keypair, per-user *and* per-device subscriptions in `push_subscriptions`
(migration 0029), a service-worker push handler, and server-side pruning of any
endpoint that answers 404/410 instead of retrying it. `web_push.send_to_user_bg`
fires on exactly two events — an `@mention`, and **a nudge** (`build_nudge_payload`,
`routes/team_chat.py`). So a nudge to a member who has opted in on the PWA now
reaches them with the browser closed.

Three things this does **not** change:

- **The Chrome extension still has no offline path.** It uses
  `chrome.notifications` over a live Centrifugo connection; the residual above is
  still true there.
- **Push is opt-in on an explicit click**, and only from the one control in the PWA
  header. A member who never opted in gets nothing.
- **Pending-nudge persistence was never built.** There is still no server-side
  queue, so a target with neither the PWA open nor a push subscription misses the
  nudge entirely.

## Deferred (out of scope this phase)

- ~~Offline / closed-browser delivery — pending-nudge persistence + Web Push.~~
  Web Push **SHIPPED Phase 27**; pending-nudge persistence still open.
- Server-side shortener expansion — SSRF-sensitive; v1 shows the literal URL.
- Cross-team or arbitrary-user targeting — same-team only, by design.
- Server-stored recipient opt-out — v1 enforces the opt-out client-side only.

## Key files

- `apps/memory-api/app/routes/team_chat.py` — `POST /v1/teams/{team_id}/nudge-open`.
- `apps/memory-api/app/services/url_safety.py` — lexical `is_safe_nudge_url`.
- `chrome-extension/popup.js` — `user:<sub>` subscription + `open_url` routing +
  the send-link affordance.
- `chrome-extension/nudge_open.js` — the consent-core receive handler (no tab API).
- `chrome-extension/background.js` — `chrome.notifications.onClicked` → tab open.
