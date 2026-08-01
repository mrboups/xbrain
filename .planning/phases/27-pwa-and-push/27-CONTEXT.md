# Phase 27: PWA + Web Push — Context

**Gathered:** 2026-08-01 (autonomous — owner asked for "la PWA et les notifications", confirmed neither exists).
**Source:** `PROJECT.md` line 39 — "UI web chat autonome — extraire le chat du popup de l'extension Chrome vers une web app hébergée (mutualisée avec la future PWA)". Phase 20 deferred this in favour of polishing the extension UI; this phase does it.

<domain>
## Phase Boundary

Today the team chat exists ONLY inside the Chrome extension popup, and notifications exist ONLY as `chrome.notifications` (desktop, extension installed, service worker alive). A teammate on a phone, or on a browser without the extension, has no way in and receives nothing.

**27a — the PWA.** An installable web app serving the SAME team chat: sign in with Google, pick a team, read history, send messages, receive them in realtime. Hosted as static files on the existing Firebase Hosting site (`app-site/`), so it adds **zero new infrastructure**.

**27b — web push.** Real notifications on that PWA: a VAPID keypair, a per-user subscription stored server-side, a service-worker `push` handler, and server-side sends on the two events that already matter — being **@mentioned** and being **nudged a link** (Phase 22).

**OUT of scope:** replacing or retiring the extension (it keeps its clipper, context menus and tab actions — things a web page cannot do); porting the board, the invite overlay, the people overlay or catch-me-up into the PWA (the chat itself is the slice); offline message *composition* (the service worker caches the shell, not a write queue); iOS-specific push caveats beyond documenting them.
</domain>

<the_hard_facts_from_live_code>
Verified on 2026-08-01, not assumed:
1. **No PWA artefact exists** — no `*.webmanifest`, no service worker, no `serviceWorker.register`, no VAPID/web-push reference anywhere in the repo. The word "PWA" appears only in planning prose.
2. **No web push** — `chrome.notifications` (9 call sites, permission declared in the extension manifest) is the only notification path.
3. **The realtime URL is already public and already served to clients.** `CENTRIFUGO_WS_URL_PUBLIC=wss://centrifugo.grooveos.app/connection/websocket` on the VM, and the client receives it from `POST /v1/me/centrifugo-token` as `ws_url` (popup.js:1419 `new Centrifuge(tokenInfo.ws_url, …)`). The PWA must read it the same way — never hardcode it.
4. **CORS already admits the PWA origin.** `CORS_ALLOWED_ORIGIN_REGEX=(chrome-extension://.*|https://([a-z0-9-]+\.)?grooveos\.app)` — `https://grooveos.app` matches.
5. **Web Google sign-in is proven.** `app-site/join/` mints an `xbt_` via Google Identity Services → `POST /v1/me/api-token` with the Google credential → `/v1/me`. Same OAuth client, `https://grooveos.app` already an Authorized JavaScript origin. Reuse that exact flow.
6. **The chat logic is largely portable.** `chat_stream.js` has ONE `chrome.*` call in 291 lines. `popup.js` has 56 in 2940 lines, and **32 of them are `chrome.storage.local`** — a thin storage shim covers the majority; the rest are `tabs`/`runtime` (extension-only affordances that the PWA simply does not offer).
7. **No web-push library server-side yet** — `pywebpush` is not in `apps/memory-api/pyproject.toml` and must be added (pure-Python; check both arches per the Phase-24 lesson).
</the_hard_facts>

<decisions>
## Implementation Decisions (locked)

### D-27-01 — The PWA is static, hosted on the EXISTING Firebase site.
It lives under `app-site/app/` and ships with the same `firebase deploy --only hosting` already in use. No new container, no new VM RAM, no new DNS record. `start_url` and `scope` are `/app/`.

### D-27-02 — Auth reuses the /join/ flow verbatim.
Google Identity Services → `POST /v1/me/api-token` with the Google credential → `/v1/me` → store `xbt_token` + `user_sub` under the SAME canonical localStorage keys the rest of app-site uses. A person signed in on `/join/` is already signed in here. No second identity system.

### D-27-03 — Realtime is Centrifugo, with the URL taken from the API.
`POST /v1/me/centrifugo-token` returns both the token and `ws_url`; the PWA uses what it is given (D-27-01 fact 3). Subscribe to `team:<id>` for messages and `user:<sub>` for personal events, exactly as the extension does.

### D-27-04 — Share the chat's portable core, do not fork it.
Extract the genuinely portable logic (message stream handling, mention detection, rendering helpers, API calls) into modules both surfaces import, behind a tiny platform shim (`storage.get/set`, `openUrl`, `notify`). The extension keeps its chrome-backed shim; the PWA gets a localStorage/`window.open`/`Notification` one. A second copy of the chat would drift within a week.

### D-27-05 — Push is opt-in, per-device, and revocable.
The browser permission prompt fires only on an explicit user click (never on load). The subscription (`endpoint`, `p256dh`, `auth`) is stored per user AND per device, so revoking one device does not silence the others. A push that fails with 404/410 (subscription gone) is deleted server-side rather than retried forever.

### D-27-06 — Push fires on the two events that already exist, not on everything.
Send on: (a) a message that **@mentions** you (mention_detector already decides this server-side), and (b) a **nudge** (Phase 22 `open_url`). NOT on every team message — a group chat that pushes每 message trains people to disable notifications. The payload carries no message body beyond a short preview, and never a token.

### Claude's Discretion
- Exact PWA layout: reuse popup.css tokens (shadcn Neutral, radius 0) so the two surfaces look like one product.
- Whether the service worker caches only the shell (recommended) or also assets.
- Where the VAPID public key is exposed to the client (a config endpoint vs a build-time constant — prefer the endpoint so a key rotation needs no rebuild).
- Push payload shape, within D-27-06's limits.
</decisions>

<canonical_refs>
## Canonical References — read before planning/executing
- `app-site/join/index.html` — the PROVEN web Google sign-in + api-token mint. Copy its auth, its storage keys, and its fragment/strip discipline.
- `chrome-extension/popup.js` (chat boot, `switchTeam`, `renderMessage`, Centrifugo wiring at ~1419) + `chat_stream.js` (`StreamBuffer`) + `mention_detector`-adjacent client bits — the logic to extract per D-27-04.
- `chrome-extension/popup.css` — the shadcn Neutral tokens the PWA must reuse.
- `apps/memory-api/app/routes/team_chat.py` — `/v1/me/centrifugo-token` (token + ws_url) and the chat endpoints.
- `apps/memory-api/app/services/mention_detector.py` — server-side truth for "this message mentions the agent"; the same module is where a "mentions THIS user" check belongs for push.
- `apps/memory-api/app/routes/media_helpers.py` — the mint/verify token pattern to mirror if push needs a signed anything.
- `app-site/firebase.json` — hosting config (headers, two targets) the PWA ships through.
- `apps/memory-api/pyproject.toml` — add `pywebpush`; verify BOTH runtime arches (Phase-24 lesson: `python-docx` pulled a C extension nobody checked).
- CLAUDE.md — English-only UI strings; dev arm64 / prod amd64.
</canonical_refs>

<specifics>
## The gate lesson applies — and here it is mostly about "does it actually reach a phone"
A PWA that renders locally proves nothing. Verification MUST include:
- **The manifest + service worker are actually served and valid** from the deployed origin (fetch them over HTTPS, parse the manifest, assert `start_url`/`scope`/icons resolve 200).
- **Sign-in → chat works end to end against the REAL API** from `https://grooveos.app` (CORS included — a CORS failure is invisible until a browser tries it).
- **Realtime is real**: two clients on the same team, one sends, the OTHER receives without a reload (the same convergence discipline the board gate used — assert the message arrives, not "no error").
- **Push is real**: a subscription stored server-side, a send that the service worker turns into a visible notification, and a 404/410 endpoint that PRUNES the subscription instead of retrying.
- **The permission prompt never fires on load** — only on an explicit click (D-27-05).
SKIP=FAIL. Docker is up on this host; the API is live on `api.grooveos.app`. Git Bash docker needs `MSYS_NO_PATHCONV=1`.
</specifics>

<deferred>
- Retiring or replacing the Chrome extension — it keeps the clipper, context menus and tab actions.
- Board / invite / people / catch-me-up surfaces inside the PWA.
- Offline message composition (a write queue) — the SW caches the shell only.
- Native app wrappers.
</deferred>

---
*Phase: 27-pwa-and-push*
*Context gathered: 2026-08-01 (autonomous)*
