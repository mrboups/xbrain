# Phase 26a: Collaborative Board (Excalidraw + Yjs) — Context

**Gathered:** 2026-07-19 (autonomous — the last backlog feature; decisions locked by the driver from 26-RESEARCH.md).
**Source:** the user's ask — "a Miro-style board callable from the chat, where the team can really work together, tied to our existing login, fully live-collaborative, where I can write text and send photos" — plus `26-RESEARCH.md` (read it before planning; every decision below traces to a verified finding there).

<domain>
## Phase Boundary

A team opens a **live collaborative Excalidraw board from the chat**. Two members drawing at once see each other's changes in real time; the board survives a reload; and a member of team B can never open team A's board. This is 26a — the **user-complete** slice: Excalidraw's native image paste already satisfies "write text and send photos".

**IN scope:** a new **board web app** (Vite + React SPA hosting `@excalidraw/excalidraw`, built in a multi-stage Docker image so Node is a BUILD-TIME dependency only); a new **Hocuspocus** (Yjs) websocket service in its OWN container; a **board token** minted by memory-api (the existing media-token shape + `board_id`) that Hocuspocus verifies in `onAuthenticate` against the requested `documentName` — the team-scope boundary; **Postgres persistence** (a `boards` metadata table + `board_docs.state bytea`, migration 0028) via Hocuspocus's database extension; a chat-side **"Open board"** action in the extension that mints a token and `chrome.tabs.create`s the board URL; the **Wave-0 spike** on the `y-excalidraw` binding (vendor it if it's stale, which is expected); and the **`verify-phase16.sh` amendment** so the OSS-light core stays exactly 10 services with `xbrain-board`/`xbrain-hocuspocus` as OPT-IN profile services on the deny-list.

**OUT of scope (→ 26b, a separate phase):** routing pasted images to MinIO instead of the doc's base64 (`BinaryFileData.dataURL` is a branded type — real work); ingesting board snapshots into the brain as tagged memory_items. Also OUT: multiple boards per team beyond the minimum the schema allows, board permissions finer than team membership, export/import, and any Tiptap Editor usage (not needed; its licensing is the thing the research had to disambiguate).
</domain>

<the_hard_facts_from_research>
Every one of these was verified against live code or upstream source in 26-RESEARCH.md. Do NOT re-litigate them from memory:
1. **The repo has ZERO frontend build tooling** — no `package.json` outside `.claude/`, no bundler, no `node_modules`, no `.tsx`. The extension is hand-written ESM tested by plain `node tests/*.mjs`. Excalidraw needs React and ships 2.76 MB of JS — it CANNOT go in the extension popup. Hence a separate SPA.
2. **Hocuspocus `@hocuspocus/server@4.4.0` and all seven extensions are MIT** (LICENSE.md, MIT © Tiptap GmbH). The "Pro requires a subscription" language applies to **Tiptap Editor**, which this phase does not use. This satisfies the OSS-only constraint. **No official Docker image** — build from `node:22-alpine` (engines: node >=22).
3. **`y-websocket` is rejected on evidence:** v3 no longer ships a server, and `@y/websocket-server`'s own README calls itself a dev server with no auth hook and points production users at Hocuspocus.
4. **The Yjs server MUST be its own process.** `UVICORN_WORKERS=2` (compose:134) means an in-FastAPI Yjs doc would diverge across workers. The `pycrdt-websocket`-in-memory-api shortcut is therefore dead.
5. **The auth precedent exists twice in-repo:** `POST /v1/me/centrifugo-token` (`team_chat.py:123`) and `mint_media_token`/`verify_media_token` (`media_helpers.py:56-116`). The board token is the media-token shape with `board_id` instead of `item_id`, signed with `BRIDGE_SHARED_SECRET`. Hocuspocus's `onAuthenticate` receives BOTH `token` and `documentName`, so a claim-vs-document match closes cross-team access. The provider's `token` option accepts an async supplier (verified in its `.d.ts`); the token travels in the Auth **message**, not the URL.
6. **Persistence is Postgres.** A `boards` metadata row is needed anyway; `board_docs.state bytea` as a sibling table keeps list queries out of TOAST. Hocuspocus hands `store()` already-compacted state and Yjs GCs internally — overwriting the one row IS the compaction strategy. Migration 0028, `down_revision = "0027"`.
7. **`verify-phase16.sh` WILL go red** — it asserts the bare core is EXACTLY 10 named services (an empty `diff`) and that `config --profiles` equals the literal `"integrations ops saas "`. Adding a `board` profile breaks both; the new containers must join the `OPT_IN_CONTAINERS` deny-list. Amending that script is a TASK.
8. **`y-excalidraw` is the soft spot:** MIT but the npm build is ~19 months stale, peers `^0.17.6` against current 0.18.1, and the `commitToHistory → captureUpdate` rename landed in that gap — the exact API a binding calls on every remote update. It is only ~23 KB across 3 files.
</the_hard_facts>

<decisions>
## Implementation Decisions (locked)

### D-26-01 — The board is a separate SPA, built in Docker; the extension only opens it.
A Vite + React app under a new top-level dir (e.g. `apps/board-web/`) hosting `@excalidraw/excalidraw`, built by a multi-stage Dockerfile (`node:22-alpine` builder → a static server stage). Node/npm enter the repo as BUILD-TIME only — no Node runtime dependency is added to the API path and no build step is added to the extension. The extension's chat gets an "Open board" action that mints a board token and `chrome.tabs.create`s the board URL (the extension already uses `chrome.tabs.create` — mirror that path).

### D-26-02 — Hocuspocus 4.4.0 in its own container; Postgres persistence via its database extension.
A new `xbrain-hocuspocus` service (built from `node:22-alpine`, pinned `@hocuspocus/server@4.4.0` + `@hocuspocus/extension-database`). It NEVER runs inside memory-api (D-26-01 fact 4). Persistence: `fetch`/`store` against `board_docs.state bytea` (migration 0028, down_revision 0027, additive, no EDITION branch).

### D-26-03 — The board token is the media-token shape; Hocuspocus's onAuthenticate is the team-scope boundary.
memory-api mints a short-lived board token (mirror `mint_media_token`: same signing with `BRIDGE_SHARED_SECRET`, same expiry discipline) carrying at least `{board_id, team_scope/team_id, sub}`. `onAuthenticate({token, documentName})` verifies the signature AND asserts the token's board/team claim MATCHES the requested `documentName` — a token for team A's board must be rejected for team B's document. A missing/expired/mismatched token closes the connection. This is the load-bearing security invariant of the phase.

### D-26-04 — Both new containers are OPT-IN profile services; the OSS-light core stays exactly 10.
`xbrain-board` + `xbrain-hocuspocus` go behind a compose profile (never in the bare core), and `verify-phase16.sh` is amended in the SAME phase: the 10-service core assertion stays green, the profile list literal is updated, and both containers join `OPT_IN_CONTAINERS`. A red Phase-16 gate at the end of this phase is a FAILURE, not an acceptable side effect.

### D-26-05 — Wave 0 is a spike on the y-excalidraw binding; vendoring is the expected outcome.
Before building on it, verify `y-excalidraw` against Excalidraw 0.18.1 (the `commitToHistory → captureUpdate` rename). If it is broken/stale — the expected finding — VENDOR the ~23 KB (MIT, attribution preserved) into `apps/board-web/` and fix it there, rather than pinning Excalidraw back to 0.17.6. Record which path was taken and why.

### D-26-06 — Images stay native (base64 in the doc) for 26a.
Excalidraw's built-in paste satisfies "send photos" today. MinIO routing + brain ingestion are 26b. Do NOT half-build them here.

### Claude's Discretion
- The static-serving stage of the board image (nginx vs a tiny static server) — pick what matches the repo's existing nginx conventions.
- One board per team vs a boards table that ALLOWS several (the research assumed one-per-team; the schema should not forbid more).
- The exact board URL shape and how the token reaches the SPA (query param vs a mint-on-load call — prefer the SPA calling memory-api for its own token over putting a secret in a URL).
- Board-token TTL (mirror the media-token TTL unless there's a reason to differ).
</decisions>

<canonical_refs>
## Canonical References — read before planning/executing
- `.planning/phases/26-collaborative-board/26-RESEARCH.md` — **read this first**; it carries the verified versions, licenses, file sizes, the 8 logged assumptions and 6 open questions.
- `apps/memory-api/app/routes/team_chat.py:123` (`POST /v1/me/centrifugo-token`) + `app/services/media_helpers.py:56-116` (`mint_media_token`/`verify_media_token`) — the two token precedents D-26-03 mirrors.
- `infrastructure/docker-compose.yml` (service + profile conventions; `UVICORN_WORKERS=2` at :134 — the reason the Yjs server is its own process) and `infrastructure/scripts/verify-phase16.sh` (the 10-core assertion + `OPT_IN_CONTAINERS` deny-list that D-26-04 must amend).
- `apps/memory-api/alembic/versions/0027_*.py` — chain migration 0028 off it; mirror its forward-only, no-EDITION shape.
- `chrome-extension/popup.js` + `background.js` — the existing `chrome.tabs.create` usage the "Open board" action mirrors; `tests/test_popup_contract.mjs` must be extended for any new bound ids.
- `apps/memory-api/app/config.py` — where the board/Hocuspocus URLs + TTL knobs go (safe defaults, no field_validator).
- CLAUDE.md — OSS-only + self-hostable, VM sizing (a new container costs RAM — state the footprint), English-only, dev arm64 / prod amd64 (the board image is built in Docker; do NOT build locally and ship).
</canonical_refs>

<specifics>
## The gate lesson applies — and here it is mostly about the SECURITY boundary and the REAL socket
"Two people can draw together" is not provable by a unit test of a React component. Verification MUST include, non-mocked:
- **Team-scope, the load-bearing invariant:** a board token minted for team A's board is REJECTED by `onAuthenticate` when used against team B's `documentName`; an absent, malformed, expired, or wrong-signature token is rejected. Prove this against the REAL Hocuspocus `onAuthenticate` handler (a direct handler test with real tokens minted by the real memory-api minting code is acceptable; a mocked verifier is NOT).
- **Real round-trip persistence:** a Y.Doc update stored through the real database extension against a REAL Postgres (testcontainers) and re-fetched into a fresh doc — the content survives. SKIP=FAIL (Docker is up).
- **Two-client convergence:** two Yjs clients connected to the running server converge on the same document state (a headless node test against a real server process is the honest proof; assert convergence, not just "no error").
- **The Phase-16 gate stays GREEN** (D-26-04) — run `verify-phase16.sh` at the end and show it.
- The board image BUILDS (the multi-stage Docker build succeeds) — a Vite app that doesn't build is not a deliverable. Build in Docker (arm64 host / amd64 prod — do not ship a locally built artifact).
Git Bash docker needs `MSYS_NO_PATHCONV=1`. English-only.
</specifics>

<deferred>
- **26b (its own phase):** images routed to MinIO instead of base64-in-doc; board snapshots ingested into the brain as tagged memory_items (mirroring Phase 24's chunk/embed shape, `source="board:snapshot"`).
- Board permissions finer than team membership; multiple named boards UX; export/import; presence avatars beyond what Excalidraw gives free.
- Any Tiptap Editor usage (not needed — and it is the component whose licensing is NOT MIT-clean).
</deferred>

---
*Phase: 26-collaborative-board (26a)*
*Context gathered: 2026-07-19 (autonomous, decisions locked from 26-RESEARCH.md)*
