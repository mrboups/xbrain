---
phase: 26-collaborative-board
plan: 04
subsystem: ui
tags: [excalidraw, yjs, hocuspocus, vite, react, crdt, nginx, docker, spa]

# Dependency graph
requires:
  - phase: 26-collaborative-board (plan 26-01)
    provides: "apps/board-web scaffold (Vite+React, exact pins, process.env.IS_PREACT define) + the vendored 0.18.1-compatible Yjs<->Excalidraw binding at src/yjs-binding/ (CaptureUpdateAction.NEVER baked in)"
  - phase: 26-collaborative-board (plan 26-02)
    provides: "the open_url contract — {BOARD_PUBLIC_BASE_URL}/?b=<board_uuid>#t=<board_token> — the fragment-handoff shape this SPA consumes"
provides:
  - "apps/board-web/src/session.ts — readSession(): UUID-validated board id from ?b=, one-time token read from the #t= fragment then stripped via history.replaceState before any network call, and a location-derived same-origin wss://<host>/collab URL. Zero network calls in 26a."
  - "apps/board-web/src/Board.tsx — client-only Excalidraw mount bound to a Y.Doc over HocuspocusProvider; token delivered by an async supplier into the Auth message (name === boardId); onAuthenticationFailed disconnects then shows one non-disclosing English access-denied state (no reconnect loop)."
  - "apps/board-web/src/main.tsx, styles.css, vite-env.d.ts — createRoot client-side mount, full-viewport sizing for Excalidraw, vite/client types."
  - "apps/board-web/Dockerfile — multi-stage node:22-alpine build (npm ci + vite build) -> nginx:1.27-alpine static runtime on 8107, Node build-time only."
  - "apps/board-web/nginx.conf — SPA fallback, /healthz probe, cache headers, Referrer-Policy: no-referrer + nosniff + X-Frame-Options SAMEORIGIN; no /collab, no proxy_pass."
  - "apps/board-web/.dockerignore — excludes node_modules + dist from the build context."
affects: [26-06, 26-07]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Fragment-handoff for a bearer secret: read #t=, decodeURIComponent, then history.replaceState(pathname+search) BEFORE any network call; deliver it to the socket via the provider's async token supplier so it rides the Hocuspocus Auth message, never a URL query (T-26-23)."
    - "Location-derived transport URL (wss/ws from location.protocol + location.host) so one static image works in both editions and both deploy shapes with no rebuild."
    - "Excalidraw stays mounted during the connecting state so its excalidrawAPI callback fires; connection status is an overlay, not a canvas-replacing gate. Denied/no-board/no-token are rendered instead of the canvas."
    - "Provider held in a ref + one-shot effect keyed on boardId so React 18 StrictMode's double mount/unmount destroys the first provider in cleanup instead of leaking a second socket."
    - "Multi-stage SPA image: node build stage (npm ci from the committed lock + vite build) -> nginx:1.27-alpine runtime with zero Node; local docker build is a proof step only, never the shipped artifact (arm64 dev / amd64 prod, Pitfall 5)."

key-files:
  created:
    - apps/board-web/src/session.ts
    - apps/board-web/src/Board.tsx
    - apps/board-web/src/main.tsx
    - apps/board-web/src/styles.css
    - apps/board-web/src/vite-env.d.ts
    - apps/board-web/Dockerfile
    - apps/board-web/.dockerignore
    - apps/board-web/nginx.conf
  modified: []

key-decisions:
  - "CaptureUpdateAction.NEVER lives INSIDE the vendored binding (yjs-binding/index.ts, four remote/init call sites — fixed in 26-01), so Board.tsx adds NO history-control call site. Verified by reading the binding before wiring."
  - "Yjs container names are the conventional 'elements' (Y.Array) + 'assets' (Y.Map); every client is this same SPA so the names are consistent and clients converge; the server persists the whole Y.Doc blob and never inspects the keys."
  - "One combined access-denied message covers both 'wrong team' and 'expired link' (no oracle, T-26-25)."
  - "D-26-06 honoured: images stay Excalidraw-native base64 in the document; no object-storage routing, asset map, signed-fetch cache or ingestion wired here — that is 26b."

patterns-established:
  - "Fragment-handoff token read + immediate replaceState strip + async-supplier delivery."
  - "location-derived wss/ws //<host>/collab URL with no import.meta.env in the transport path."

requirements-completed: [BOARD-01]

# Metrics
duration: 21min
completed: 2026-07-24
---

# Phase 26 Plan 04: Board Web SPA (Excalidraw + Hocuspocus) Summary

**A Vite+React SPA that mounts Excalidraw 0.18.1 bound to a Y.Doc over HocuspocusProvider 4.4.0 — the one-time board token is read from the URL fragment, stripped with history.replaceState before any network call, and delivered through the provider's async supplier into the Auth message; the WS URL is derived from location; and the whole thing builds into a Node-free nginx:1.27-alpine static image via a multi-stage Dockerfile (proven by a real local docker build, size 87.1 MB).**

## Performance

- **Duration:** ~21 min
- **Started:** 2026-07-24T04:10:00Z (worktree reset to base db85255)
- **Completed:** 2026-07-24T04:31:00Z
- **Tasks:** 2
- **Files modified:** 8 created

## Accomplishments

- Built the page a team member actually sees: `Board.tsx` mounts `@excalidraw/excalidraw@0.18.1` client-side, binds a `Y.Doc` to it through the vendored `src/yjs-binding/` binding, and syncs it over `@hocuspocus/provider@4.4.0` on the same-origin `/collab` socket. Remote cursors come free from provider awareness (no second presence channel).
- Closed the token-leak surface (T-26-23): `session.ts` reads the token from the URL **fragment** (`#t=`), `decodeURIComponent`s it, and immediately calls `history.replaceState(null, "", location.pathname + location.search)` so the secret never lingers in the address bar or history — then hands it to the provider through `token: async () => token`, so it travels in the Hocuspocus **Auth message**, never a WebSocket query parameter. A comment-filtered grep confirms no `?t=` / `&token=` / `?token=` form anywhere in `src/`.
- Derived the WS URL from `location` (`wss:`/`ws:` + `location.host` + `/collab`) with **no** `import.meta.env` — one image works for every self-hoster and the SaaS deploy, in both editions, with no rebuild.
- Handled the refusal path safely: `onAuthenticationFailed` calls `provider.disconnect()` before rendering one non-disclosing English access-denied state (T-26-25, T-26-26 — no reconnect loop, no wrong-team-vs-expired oracle).
- Shipped a multi-stage `Dockerfile` (node build-time only) + `nginx.conf` (SPA fallback, `/healthz`, cache headers, `Referrer-Policy: no-referrer`, no `/collab`/`proxy_pass`) and **proved it builds** with a real `docker build` — the runtime image carries `index.html` and **no Node**.

## Task Commits

Each task was committed atomically:

1. **Task 1: session.ts (fragment handoff + same-origin WS URL) + Board.tsx (Excalidraw + Hocuspocus + binding) + main.tsx / styles.css / vite-env.d.ts** — `2a71002` (feat)
2. **Task 2: multi-stage Dockerfile (node build -> nginx static) + .dockerignore + nginx.conf, proven by a real docker build** — `234d385` (feat)

**Plan metadata:** committed separately with this SUMMARY.

## Files Created/Modified

- `apps/board-web/src/session.ts` — `readSession()`: UUID-validated `?b=` board id, `#t=` fragment token read + `history.replaceState` strip before any network call, `location`-derived `wss://<host>/collab`. Zero network calls (26a) — hence no CORS change this slice (Pitfall 6).
- `apps/board-web/src/Board.tsx` — client-only Excalidraw mount + `HocuspocusProvider` (`name: boardId`, `token: async () => token`) + the vendored binding; connecting/live overlay; denied/no-board/no-token states rendered instead of the canvas; `provider.disconnect()` on auth failure.
- `apps/board-web/src/main.tsx` — `createRoot` client-side mount inside `<StrictMode>`; session read once at module scope (strips the fragment before React renders).
- `apps/board-web/src/styles.css` — full-viewport height chain (Excalidraw needs a sized parent) + status indicator + centred message styles.
- `apps/board-web/src/vite-env.d.ts` — `/// <reference types="vite/client" />`.
- `apps/board-web/Dockerfile` — `node:22-alpine AS build` (`npm ci` + `npm run build`) -> `nginx:1.27-alpine AS runtime` serving `/usr/share/nginx/html`, `EXPOSE 8107`.
- `apps/board-web/.dockerignore` — `node_modules`, `dist`, `.git`.
- `apps/board-web/nginx.conf` — `listen 8107`, SPA `try_files` fallback, `/healthz`, `/assets/` 1y immutable, `index.html` no-store, `nosniff` + `no-referrer` + `SAMEORIGIN`; no `/collab`, no `proxy_pass`.

## Plan-required record (from `<output>`)

- **Exact access-denied string** (one message for BOTH wrong-team and expired, no oracle):
  > `Access denied. This board belongs to another team, or your board link has expired. Reopen the board from the team chat.`
- **Where `CaptureUpdateAction.NEVER` is applied:** **inside the vendored binding** (`apps/board-web/src/yjs-binding/index.ts`), at all four remote/init `updateScene` call sites (`_remoteElementsChangeHandler`, `_remoteAwarenessChangeHandler`, scene-init, initial-collaborators — fixed in 26-01). `Board.tsx` therefore adds **no** history-control call site. Confirmed by reading the binding before wiring; `grep -c 'CaptureUpdateAction'` = `Board.tsx:1` (a comment reference) + `yjs-binding/index.ts:6`.
- **Built bundle size** (`vite build`): `dist/index.html` 0.49 kB; entry `dist/assets/index-CZXDiRI_.js` **1,385.94 kB** (gzip 439.73 kB); CSS `index-BUfO075e.css` 142.56 kB (gzip 22.53 kB); largest lazy chunk `chunk-EIO257PC-GWAO-83z.js` 1,821.03 kB (gzip 744.22 kB, Excalidraw/mermaid deps, code-split and lazily loaded). The >500 kB chunk warning is expected for Excalidraw and non-fatal.
- **Final image size:** `xbrain/board-web:phase26-local` = **87.1 MB** (nginx:1.27-alpine runtime + the static dist).
- **The locally built (arm64) image is a PROOF artifact only and was NOT pushed or deployed.** The shipped image comes from CI's amd64 bake once 26-06 declares the compose `build:` key (Pitfall 5 / T-26-30).

## Verification (real output)

- `cd apps/board-web && npx tsc --noEmit -p tsconfig.json` — exit 0 (the SPA + the vendored binding typecheck against `@excalidraw/excalidraw@0.18.1` under `strict: true`).
- `npx vite build` — exit 0, wrote `dist/index.html` + the hashed bundle (`test -f dist/index.html` = present).
- `MSYS_NO_PATHCONV=1 docker build -t xbrain/board-web:phase26-local apps/board-web` — exit 0.
- `docker run --rm --entrypoint sh xbrain/board-web:phase26-local -c "test -f /usr/share/nginx/html/index.html && ! command -v node && echo 'static-only runtime OK'"` — printed **`static-only runtime OK`** (index.html present, Node absent in the runtime image).
- **Task 1 greps (all pass):** `token: async` present; `history.replaceState` + `location.hash` in session.ts; `location.host` present and `import.meta.env` count 0 (non-comment); `name: boardId` present; `@excalidraw/excalidraw/index.css` imported; `onAuthenticationFailed` + `disconnect()` present; no `commitToHistory` (non-comment count 0); `?t=`/`&token=`/`?token=` non-comment count 0; 26b scope absent — `grep -ci 'minio\|/v1/media\|addFiles\|memory_items\|brain' src/Board.tsx` = **0**.
- **Task 2 greps (all pass):** `FROM node:22-alpine AS build` + `FROM nginx:1.27-alpine`; `npm ci` present, `npm install` non-comment count 0; `.dockerignore` `node_modules` count 2; `try_files $uri $uri/ /index.html` + `/healthz` + `listen 8107`; `X-Content-Type-Options` + `Referrer-Policy` + `no-referrer`; `collab`/`proxy_pass` count **0**.
- **English-only check:** `grep -nP '[^\x00-\x7F]' src/*.ts src/*.tsx` returns only lines carrying the em-dash `—` (U+2014, inside comments) and the ellipsis `…` (U+2026, in the `"Connecting to the board…"` status string) — both typographic characters. No accented / non-English prose anywhere in `src/`.

## Decisions Made

- **Read the binding first, then wired to match it.** The 26-01 DECISION.md + `yjs-binding/index.ts` confirm `CaptureUpdateAction.NEVER` is already applied at every remote/init call site, and the public constructor is `new ExcalidrawBinding(yElements, yAssets, api, awareness?)`. Board.tsx consumes exactly that surface — no second history call site, no re-vendoring.
- **`provider.awareness ?? undefined`** is passed to the binding: HocuspocusProvider always creates an awareness instance (type `Awareness | null`), and the binding's init path dereferences awareness unconditionally, so a real instance must reach it. The single hoisted `y-protocols@1.0.7` means the provider's `Awareness` and the binding's `Awareness` are the same type — no cast needed.
- **StrictMode kept on** (main.tsx) precisely because the provider-in-a-ref + one-shot effect is built to survive its double invocation; production builds don't double-invoke.

## Deviations from Plan

None - plan executed exactly as written. The plan's action step 2 shows `import { Excalidraw, CaptureUpdateAction }`, but the same step instructs "if the binding already does this internally, do not add a second call site" — the binding does, so `Board.tsx` imports only `Excalidraw` (an unused `CaptureUpdateAction` import would serve no purpose). This is the plan's own conditional, not a deviation.

## Issues Encountered

- `apps/board-web/node_modules` was absent in the worktree (only the 26-01 manifest + lock were committed). Ran `npm ci` (a pure-JS resolution step, safe on the arm64 dev host per 26-01) to obtain the tree for the local `tsc`/`vite build`; the deployed image builds its own tree inside Docker. Not a code issue.

## Known Stubs

None. The SPA is fully wired to a real `HocuspocusProvider` and the real vendored binding; the token is the real fragment-handoff value (not a placeholder), and there is no hardcoded empty/mock data feeding the canvas. Live convergence and team-scope refusal depend on the Hocuspocus server (26-03) and the ingress `/collab` route (26-06) being present — those are cross-plan dependencies proven in 26-07's non-mocked gate, not stubs in this slice.

## Threat Flags

None. Every surface introduced here (the fragment-handoff read/strip, the async-supplier delivery, the derived WS URL, the non-disclosing denied state, the no-reconnect-on-auth-fail, the nginx security headers, the proof-only local image) is already enumerated and mitigated in the plan's `<threat_model>` (T-26-23 … T-26-30). No new endpoint, auth path, or trust boundary beyond it.

## Next Phase Readiness

- **26-06 (compose + ingress nginx):** this image is ready to be picked up by a compose `build:` (context `apps/board-web`, EXPOSE 8107) and an ingress vhost that routes `/collab` to the Hocuspocus container and `/` to this static container. This container deliberately does NOT proxy `/collab`.
- **26-07 (non-mocked gate):** two browsers converging + a team-B token being refused is 26-07's proof; this slice supplies the client that exercises `onAuthenticate` (name === boardId) and the fragment handoff.
- **Blocker/concern:** none. The docker build was verified locally on arm64 as a proof only and was neither pushed nor deployed.

## Self-Check: PASSED

- All 8 created files verified present on disk (session.ts, Board.tsx, main.tsx, styles.css, vite-env.d.ts, Dockerfile, .dockerignore, nginx.conf).
- Both task commits verified in git history: `2a71002` (Task 1), `234d385` (Task 2).
- `tsc --noEmit` exit 0; `vite build` wrote `dist/index.html`; `docker build` exit 0; runtime image is static-only (index.html present, no Node); image size 87.1 MB.

---
*Phase: 26-collaborative-board*
*Completed: 2026-07-24*
