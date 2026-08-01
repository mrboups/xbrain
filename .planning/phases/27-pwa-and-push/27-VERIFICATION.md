# Phase 27 — Verification

**Gate:** `infrastructure/scripts/verify-phase27.sh` (`make verify-phase27`)
**Run:** 2026-08-01, against the deployed origin and the live API
**Result:** `PASS: 50 / 50  (SKIP: 0)  FAIL: 0` — exit 0

| Surface under test | Value |
|---|---|
| PWA origin | `https://grooveos.app/app/` (Firebase Hosting, both targets deployed) |
| API | `https://api.grooveos.app` (memory-api on the GCP VM, image rebuilt at `1805117`) |
| Realtime | `wss://centrifugo.grooveos.app/connection/websocket` — read from the API response, never supplied by the gate |
| Migration head | `0029_push_subscriptions` |

Nothing here was proven from a local file: the gate contains no `file://` path, a check that
cannot run records a failure rather than a skip, and the run above skipped nothing.
Credentials were passed by environment and never printed — `grep -c 'xbt_[A-Za-z0-9]'` over the
captured log returns **0**.

---

## (a) Manifest, served from the deployed origin — 13/13

`GET https://grooveos.app/app/manifest.webmanifest` → **200**, content-type
`application/manifest+json`.

- `start_url` = `/app/`, `scope` = `/app/`, `display` = `standalone`, `theme_color` = `#0A0A0A`
- a 192×192, a 512×512 and a `maskable` icon are all declared
- every declared icon was fetched over HTTPS: `/app/icons/icon-192.png`,
  `/app/icons/icon-512.png`, `/app/icons/icon-maskable-512.png` — each **200 image/png**

## (b) Service worker, served from the deployed origin — 14/14

`GET https://grooveos.app/app/sw.js` → **200**, `text/javascript`, and
`cache-control: no-cache, must-revalidate`.

That header is not a formality. `firebase.json` sets `public, max-age=3600` for `**/*.@(css|js)`
*before* the `/app/sw.js` rule, so Firebase's header precedence was the open question; production
answers it — the later rule wins, and the worker is not cached.

Assertions run on a **comment-stripped** copy (8123 → 3837 bytes), because sw.js's own comments
discuss `/v1/` and `Authorization`; the gate proves the stripper is not inert and that the removed
comments really did mention `/v1/` (3 raw vs 1 in code) — a raw grep here would have been
self-validating.

- handles `push`, `notificationclick`, and `pushsubscriptionchange`
- all four no-caching guards present: GET-only, same-origin only, never a `/v1/` path, never a
  credentialed request
- the `/v1/` guard (byte 1594) **precedes** the first `respondWith` (byte 1675) — it returns
  before anything can be served from cache
- the served worker never calls `cache.put`

## (c) Start URL — 4/4

`GET https://grooveos.app/app/` → **200**; the served page links its manifest and registers the
service worker, and contains neither `requestPermission` nor `pushManager.subscribe` — no
permission prompt can fire on load (D-27-05).

## (d) CORS, with both controls — 3/3

- positive: preflight from `Origin: https://grooveos.app` → `access-control-allow-origin:
  https://grooveos.app`, `access-control-allow-credentials: true`
- **negative:** the same preflight from `https://attacker.example` is **not** echoed (`<none>`)

A permissive config that passes the positive test alone is not proof; this check has both halves.

## (e) Sign-in → chat against the real API — 6/6

`GET /v1/me` → 200 with a `source_user_id`; `GET /v1/teams/my-teams` → 200 non-empty;
`POST /v1/me/centrifugo-token` → 200 carrying `ws_url` and a signed client token (length not
shown — it is a credential); `GET /v1/teams/{id}/messages?limit=5` → 200;
`POST /v1/teams/{id}/messages` → **201**. Every request carried
`Origin: https://grooveos.app`, so the API was exercised the way the PWA exercises it.

## (f) Realtime — the arrival proof — PASS

```
clients:    two DIFFERENT accounts (VERIFY_XBT_TOKEN_2 supplied)
socket url: wss://centrifugo.grooveos.app/connection/websocket   (read from the API response)
RECEIVED nonce=1d658391-040d-4b9b-9670-bd002f861662 after 335ms
```

A message posted over plain HTTP by one account arrived, **matched by content**, at a websocket
client belonging to a different account that never made that HTTP call. The assertion is arrival,
not absence-of-error.

## (g) Push — real encryption, real socket, exact prune matrix — 18/18

Driven inside the memory-api container against the real `pywebpush` install, the real VAPID key
from the container's environment, and the real Postgres. Nothing in the send path is stubbed
(`grep -cE '\bmock\b|patch\('` = 0).

Observed request headers for the 201 endpoint (credential values redacted):

```
authorization:    vapid <redacted, 333 chars>
content-encoding: aes128gcm
ttl:              86400
<body>:           206 bytes
```

| Endpoint answers | Subscription row | `send_to_user` reported |
|---|---|---|
| 201 | **KEPT** | one successful delivery |
| 410 | **DELETED** | one prune |
| 404 | **DELETED** | one prune |
| 500 | **KEPT** | no prune |

A dead device is forgotten; a push service having a bad afternoon is not.

## (h) Push config, deployed — 4/4

`GET /v1/push/config` → 200, `enabled: true`, and a `vapid_public_key` that base64url-decodes to
a 65-byte uncompressed P-256 point. The private key was read from the container purely to assert
its **absence** from the response — it does not appear, and was never printed.

## (i) The suites that must still be green — 2/2

The extension suite passes, and the shared `chat-core` copies are byte-identical across both
surfaces (D-27-04: one implementation, two shims — not two chats).

## (j) Server unit suites — PASS

`98 passed, 3 skipped` for the push endpoints, endpoint-safety, web-push send and user-mention
detector suites.

Run against `apps/memory-api` on the gate host rather than in the container, and the gate **prints
which**: the runtime image COPYs `app/` and `alembic/` but not `tests/`, and `pytest` is a `[dev]`
extra, so the in-container path can never satisfy this check. That is not a weakening — whether
this deployment's own dependency set actually works is answered by (g), which drives the real
pywebpush inside the real container and cannot be satisfied by a checkout at all.

## (k) Migration applied where the API runs — PASS

`0029_push_subscriptions`, read through the API container's own configured connection.

Confirmed independently in Postgres: `push_subscriptions` exists with
`ux_push_subscriptions_endpoint` **UNIQUE on `endpoint` alone** — the ownership-transfer design,
so a shared device changes owner instead of duplicating, and revoking one device does not silence
the others.

---

## Two gate defects found and fixed during this run

Both were defects in the **instrument**, not the product. Recorded here because a gate quietly
adjusted to go green is the exact failure mode this discipline exists to prevent.

**(k) invoked a binary the runtime image does not ship.** `docker compose exec memory-api alembic
current` failed with *"executable file not found in $PATH"*: the image installs the alembic
package (the container runs its migrations at boot) but not the console script. Changed to
`python -m alembic current`, which reads the identical `alembic_version` row through the API
container's own connection — so the check still answers "what is applied where the API actually
runs", not "what files exist on disk". Same root cause as (j): a runtime image is not a dev image.

**(f) gated on an event this deployment never fires.** The probe waited for the *subscription
object's* `subscribed` event and timed out after 15 s — while realtime was working perfectly.
`POST /v1/me/centrifugo-token` returns a `channels` claim, so Centrifugo subscribes the connection
**server-side** at connect time; by the time `subscription.subscribe()` runs the channel is
already live, Centrifugo answers error **105 "already subscribed"**, and the subscription object's
own `subscribed` event never arrives.

This was diagnosed rather than assumed: a purpose-built probe listened on **both** delivery paths
at once and posted a message. Verdict — `delivered by CLIENT-SIDE subscription: true`,
`delivered by SERVER-SIDE subscription: false`. The production client
(`packages/chat-core/realtime.js`) is correct and unchanged. The barrier now accepts either
`subscribed` event and treats code 105 as "the channel is live", which is what the barrier was
ever asking about. A barrier that reports a healthy system as broken is as much a defect as one
that reports the reverse.

**(c) a naive comment stripper deleted the rest of the document.** Found on the re-run after
the PWA adopted the extension's composer. The stripper's block-comment range was
`/\/\*/,/\*\//` — unanchored, so it also matches `/*` anywhere on a line. The composer's file
input carries `accept="image/*,application/pdf,…"`, which opened a range that never found a
closing `*/`, so `sed` deleted **everything after it** — including the `serviceWorker.register`
call the very next assertion looks for. The gate reported a missing service worker on a page
that registers it perfectly well.

Anchored the opener to the start of a line (leading whitespace allowed), which is how a block
comment is actually written. Re-verified that the stripper is still doing its job rather than
merely passing: 11323 → 6623 bytes on the served page, and `serviceWorker` still drops from 3
raw occurrences to 2, so prose still cannot satisfy a code assertion.

Three instrument defects in one phase is a pattern worth naming: each one made the gate report a
**healthy** system as broken. That direction is the safer failure, but it is still a defect —
a gate that cries wolf gets ignored, and an ignored gate is a gate that no longer catches the
real thing.

## The gate now cleans up after itself

Checks (e) and (f) post real messages into a real team, because that is the only honest way to
prove a message travels end to end. The owner opened the chat and found six probe lines burying
the actual conversation. The gate now soft-deletes its own probe messages once the checks that
needed them are done — recoverable, nothing erased. It records no PASS and no FAIL, so tidiness
can never turn a green run red, but an unreachable database prints the manual SQL instead of
failing quietly. Confirmed live: the run above reported *"2 probe message(s) hidden"* and the
team chat was left carrying only its real messages.

## Five environment variables recovered from silent loss

`CORS_ALLOWED_ORIGIN_REGEX`, `EDITION`, `AGENT_MENTION_ALIASES`, `COMPOSE_PROFILES` and
`XBRAIN_BASE_DOMAIN` had vanished from the VM `.env`, along with `OAUTH_ISSUER_URL` and
`OAUTH_RESOURCE_URL`. The running containers held the correct values in memory from an earlier
start, so nothing looked wrong — until memory-api was rebuilt and restarted, at which point it
fell back to compiled defaults: **`EDITION=oss`** (SaaS-only routers unmounted), a CORS regex
excluding `grooveos.app`, and only the `@agent` alias. The OAuth pair crash-looped the container
outright, which is the merciful failure; the other three degrade silently.

All seven were restored from `.env.bak-boardurl-064304` and re-verified in the running container.
**This was latent before Phase 27 and would have been triggered by any restart of any service.**

---

## Still outstanding — device verification (plan 27-09, task 3)

The gate proves everything a script can reach. Two facts remain that only a person on a real
device can confirm, and they are the reason plan 27-09 is marked `autonomous: false`:

1. **No permission prompt fires on load.** The gate proves the served start-url document contains
   no prompt-raising call — it cannot prove what a real browser does.
2. **A push becomes a notification someone actually sees**, arrives with the screen off, and opens
   the app rather than a foreign site when tapped.

Until that checkpoint is answered, `PWA-01` and `PUSH-01` remain **unchecked** and Phase 27 is not
marked complete. The nine-step device script is in `27-09-PLAN.md`.
