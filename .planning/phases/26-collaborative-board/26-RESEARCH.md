# Phase 26: Collaborative Board (Excalidraw + Yjs) — Research

**Researched:** 2026-07-24
**Domain:** Real-time collaborative canvas (CRDT) + a first React frontend in a repo that has none
**Confidence:** HIGH on constraints and licensing, MEDIUM on the Excalidraw↔Yjs binding, LOW on nothing that blocks planning

---

## Summary

The user wants a Miro-style board, callable from the team chat, tied to the existing login, live-collaborative, with text and photos. The captured answer — **Excalidraw + Yjs** — holds up under verification: `@excalidraw/excalidraw@0.18.1` is MIT `[VERIFIED: npm view]`, `yjs@13.6.31` is MIT `[VERIFIED: npm view]`, and `@hocuspocus/server@4.4.0` plus **every** extension in its monorepo (database, s3, sqlite, redis, webhook, throttle, logger) is MIT, Copyright Tiptap GmbH `[VERIFIED: raw.githubusercontent LICENSE.md + npm view on 7 packages]`. There is no paid gate on the self-hosted collaboration server; the "Pro extensions need a subscription" language in Tiptap's marketing applies to **Tiptap Editor** extensions, which this phase does not use `[CITED: tiptap.dev/open-source-to-platform]`. The OSS-only constraint is satisfied.

Three findings reshape the shape of the phase. **First, the extension cannot host this.** `chrome-extension/` has zero build tooling — no `package.json` anywhere in the repo except `.claude/`, no bundler config, no `.tsx`/`.jsx`, no `node_modules` `[VERIFIED: find across repo]`. Excalidraw's production dist is 2.76 MB of raw JS + 144 KB CSS + ~15 MB of lazily-loaded fonts `[VERIFIED: data.jsdelivr.com file manifest for 0.18.1]`, needs React as a peer, and does not support SSR `[CITED: docs.excalidraw.com/.../integration]`. Putting that behind an MV3 popup means adding a bundler to a codebase whose entire JS convention is hand-written ESM tested by plain `node *.mjs` files. **The board must be a separate web page opened in a tab.** The extension already calls `chrome.tabs.create` in two places, so the chat-side trigger is nearly free `[VERIFIED: chrome-extension/background.js:1353, popup.js:166]`.

**Second, the Yjs server cannot live inside memory-api.** memory-api runs `UVICORN_WORKERS=2` by default (1 only in OSS-light) `[VERIFIED: infrastructure/docker-compose.yml:134]`. Two worker processes means two independent in-memory `Y.Doc` copies and two clients that silently never sync. A CRDT server must be single-process or broker-backed. That kills the otherwise-attractive "just add `pycrdt-websocket` to FastAPI" shortcut and makes a dedicated container the correct answer. **Third, the auth pattern already exists twice in this repo** — `POST /v1/me/centrifugo-token` mints an HS256 JWT whose `channels` claim the broker enforces `[VERIFIED: apps/memory-api/app/routes/team_chat.py:123-147]`, and `mint_media_token` / `verify_media_token` mint a short-lived scoped token validated against `BRIDGE_SHARED_SECRET` with an explicit `item_id` claim-match `[VERIFIED: apps/memory-api/app/routes/media_helpers.py:56-116]`. The board token is the same shape with `board_id` in place of `item_id`, and Hocuspocus's `onAuthenticate` receives both `token` and `documentName`, so the same claim-match closes cross-team access `[CITED: tiptap.dev/docs/hocuspocus/server/hooks]`.

**Primary recommendation:** Ship a new `board` compose profile containing ONE container (`apps/board`) that is both a Hocuspocus Yjs server and the static host for a Vite-built Excalidraw SPA; authenticate the WebSocket with a short-lived HS256 board token minted by memory-api and claim-matched to `documentName`; persist the Y.Doc as a `bytea` in Postgres through a memory-api internal endpoint; and **split the phase in two** — 26a is board + live collab + team-scoped auth + persistence, 26b is images-to-MinIO + brain ingestion.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Canvas rendering, drawing tools, text, image paste | Browser / Client (new SPA) | — | `@excalidraw/excalidraw` is a client-only React component; SSR is explicitly unsupported `[CITED: docs.excalidraw.com]` |
| CRDT merge + awareness (cursors) fan-out | Board realtime server (new container) | Browser (Yjs runs client-side too) | Must be single-process; memory-api is multi-worker `[VERIFIED: docker-compose.yml:134]` |
| Who may open which board | API / Backend (memory-api) | Board server (verifies the minted token) | memory-api owns teams + membership; mirrors `/v1/me/centrifugo-token` |
| Board metadata (id, team, title, created_by) | API / Backend (memory-api) | — | Team-scope enforcement lives in memory-api, per the project invariant |
| Y.Doc binary persistence | Database / Storage (Postgres `bytea`) | API / Backend (memory-api proxies) | A `boards` row is needed anyway; a sibling blob column is nearly free and rides the existing pg backup |
| Image bytes | Database / Storage (MinIO) | API / Backend (`/v1/media/*`) | The upload + signed-serve path already exists `[VERIFIED: app/routes/media.py]` |
| Board text → brain memory | API / Backend (memory-api) | Board server (extracts + POSTs) | Only memory-api may write `memory_items` with the 7-field tagging |
| "Open the board" trigger | Extension (Chrome MV3) | Browser | `chrome.tabs.create` already in use; no new permission needed |
| Public routing / TLS | CDN / Ingress (nginx) | — | New `board.<domain>` vhost, same shape as the centrifugo vhost |

---

<user_constraints>
## User Constraints

**No CONTEXT.md exists for this phase** (`has_context: false` `[VERIFIED: gsd-sdk query init.phase-op 26]`). The constraints below are transcribed from the phase brief and from the project-level constraints that bind every phase.

### Locked (from the phase brief and CLAUDE.md)

- **Excalidraw + Yjs** is the agreed approach; Hocuspocus or y-websocket as the transport.
- **Board snapshots ingested into the brain.**
- **Tied to the existing login** — the extension's `xbt_token` / memory-api principal.
- **Open-source + self-hostable only.** No managed-cloud-only service in the critical path.
- **Multi-frontend invariant** — nothing may lock data to one frontend.
- **7-field tagging contract** on every data point written to the brain: `team_scope`, `project_scope`, `visibility`, `confidence`, `truth_level`, `source`, `validation_status`.
- **English only** in product/app/code, including all board UI strings.
- **Dev machine is arm64, prod is amd64** — never `docker build` locally and deploy to the VM.
- **VM RAM is the binding constraint** — OSS-light core is budgeted at ~4 GB for 10 services `[VERIFIED: docs/INSTALL.md:58]`.

### Claude's Discretion

- Where the board frontend lives and how it is served.
- Yjs transport choice (Hocuspocus vs y-websocket vs Python).
- Persistence store (Postgres vs MinIO) and snapshot/compaction strategy.
- Whether images route to MinIO in the first slice or a follow-up.
- Phase split.

### Deferred / Out of Scope

- Nothing was captured as deferred. See **Sizing / Phase Split** for what this research recommends pushing to 26b.
</user_constraints>

---

<phase_requirements>
## Phase Requirements

**No `BOARD-*` requirement exists in `.planning/REQUIREMENTS.md` yet** `[VERIFIED: grep for BOARD/excalidraw/miro returned only unrelated "dashboard" matches]`. Phases 21–25 each introduced exactly one requirement ID (`ALIAS-01`, `NUDGE-01`, `CATCHUP-01`, `DOCBODY-01`, `JOINCODE-01`). Proposed, for the roadmapper to confirm:

| ID | Description | Research Support |
|----|-------------|------------------|
| BOARD-01 | A team member opens a team-scoped collaborative Excalidraw board from the chat; edits sync live between members; the board survives a restart; a member of team B can never open team A's board. | Standard Stack, Architecture Patterns, `## Auth: Team-Scope on the WebSocket`, `## Persistence` |
| BOARD-02 | Images pasted onto a board are stored in MinIO (not base64 in the doc), and a board's text content is extracted, chunked, embedded, and retrievable from the brain with full 7-field tagging. | `## Images`, `## Brain Ingestion` |
</phase_requirements>

---

## Project Constraints (from CLAUDE.md)

| Directive | Impact on this phase |
|-----------|---------------------|
| Open-source + self-hostable only; no managed-cloud-only service in the critical path | Excalidraw, Yjs, Hocuspocus and every Hocuspocus extension verified MIT. **Never** reach for Liveblocks, Tiptap Cloud, PartyKit Cloud, Excalidraw+. |
| GCP VM, Docker Compose, e2-medium/standard-2 class | One new container, `mem_limit` capped, opt-in via a profile so the OSS-light 4 GB core is untouched. |
| Multi-frontend invariant | The board is a URL. LibreChat/Open WebUI/ChatGPT can link to it; nothing is extension-only. |
| 7-field tagging contract; new schemas without it are flagged | Board **text→brain** items carry all 7. The `boards`/`board_docs` rows are infrastructure tables (like `team_messages`), not `memory_items` — they carry `team_id` FK instead. |
| App/code/UI in **English only** | All board UI strings, buttons, errors in English. |
| Dev = arm64, prod = amd64 | Build the board image in CI (amd64) or on the VM. `node:22-alpine` publishes both arches; every JS dep here is pure JS (no native modules). |
| GSD is the build system; hooks enforce it | All edits go through `/gsd-execute-phase 26`. |

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `@excalidraw/excalidraw` | **0.18.1** (MIT, published 2026-07-13) | The canvas component | The user named it; MIT; the npm package is the supported embed surface `[VERIFIED: npm view]` |
| `yjs` | **13.6.31** (MIT, 2026-05-28) | CRDT engine | The reference Yjs implementation `[VERIFIED: npm view]` |
| `@hocuspocus/server` | **4.4.0** (MIT, 2026-07-13) | Yjs WebSocket backend | The only *production* Yjs server that is MIT and self-hostable with an auth hook `[VERIFIED: npm view + LICENSE.md]` |
| `@hocuspocus/provider` | **4.4.0** (MIT) | Client-side Yjs provider | Ships awareness + async token supplier `[VERIFIED: dist/index.d.ts]` |
| `@hocuspocus/extension-database` | **4.4.0** (MIT) | `fetch`/`store` persistence hooks | Backend-agnostic; we point it at memory-api `[CITED: tiptap.dev/docs/hocuspocus/server/extensions/database]` |
| `react` + `react-dom` | 18.x or 19.x | Excalidraw peer | `peerDependencies: react ^17.0.2 \|\| ^18.2.0 \|\| ^19.0.0` `[VERIFIED: npm view]` |
| `vite` | 5.x or 6.x | Build the SPA (build-time only) | Excalidraw's own docs give Vite-specific integration guidance `[CITED: docs.excalidraw.com/.../integration]` |
| `node` | **22-alpine** | Runtime for the board server | `@hocuspocus/server` declares `engines: { node: ">=22" }` `[VERIFIED: npm view]` |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `y-excalidraw` | 2.0.12 (MIT, npm 2024-12-10) | Binds `Y.Array` ↔ Excalidraw elements + awareness | **See the warning below** — evaluate first, likely vendor |
| `y-protocols` | 1.0.7 (MIT) | Awareness protocol | Pulled transitively; only import directly if hand-rolling awareness |
| `@hocuspocus/extension-logger` | 4.4.0 (MIT) | Structured connection logging | Dev/debug; keep `quiet: true` in prod to respect the 100 MB log cap |
| `@hocuspocus/extension-throttle` | 4.4.0 (MIT) | Connection throttling | If the board is publicly routable, add it |
| `pycrdt` | 0.14.1 (MIT, Beta, 2026-06-17) | Decode a stored Y.Doc update **in Python** | Only if memory-api must re-derive text from the blob. cp312 manylinux wheels for aarch64 + x86_64 exist `[VERIFIED: pypi.org/pypi/pycrdt/json]` |

> **`y-excalidraw` warning — read before planning around it.** The npm build is **19 months old** and declares `peerDependencies: { "@excalidraw/excalidraw": "^0.17.6" }` `[VERIFIED: npm view]`. `main` on GitHub is still at 2.0.12 with the same peer range; the 0.18 peer bump and an i18n/stability fix sit in **unmerged** PR #13 (opened 2026-04-23) `[VERIFIED: GitHub API pulls + raw package.json]`. Between 0.17 and 0.18, Excalidraw **replaced `commitToHistory` with `captureUpdate`** on `updateScene` — the exact API a binding calls on every remote update `[CITED: docs.excalidraw.com/.../excalidraw-api]`. Open issues include a frame-creation crash (#11) and a `langCode` error storm (#12). Repo has 36 stars. It IS MIT (`LICENSE` file present, "The MIT License (MIT), Copyright (c) 2024 Rahul R Badenkal") `[VERIFIED: raw.githubusercontent LICENSE]` — GitHub's API reports `NOASSERTION` only because licensee doesn't match the modified header. **The whole binding is 3 files / ~23 KB of TypeScript** (`diff.ts` 11.7 KB, `index.ts` 9.9 KB, `helpers.ts` 1.2 KB) `[VERIFIED: GitHub git/trees API]`. Plan a Wave-0 spike: try `y-excalidraw@2.0.12` against `@excalidraw/excalidraw@0.18.1` with a peer override; if remote updates fail or history is polluted, **vendor the three files** into `apps/board/web/src/yjs-binding/` with the MIT notice retained and fix `captureUpdate` there. Vendoring is the recommended default outcome — a 19-month-stale, 36-star package in the CRDT critical path is not a dependency, it's a liability you don't control.

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Hocuspocus | `@y/websocket-server` 0.1.5 (MIT) | **Rejected.** y-websocket v3 "does not ship a y-websocket server anymore"; the extracted server's own README says it "is intended as a development server or as a starting point" and that "if you need durable, production-grade persistence, use YHub or Hocuspocus instead of rolling your own." It has **no auth hook** `[VERIFIED: raw README, github.com/yjs/y-websocket-server]` |
| Hocuspocus | `pycrdt-websocket` 0.16.4 (MIT, Beta) inside memory-api | **Rejected as primary.** Would need `UVICORN_WORKERS=1` forced everywhere (SaaS default is 2 `[VERIFIED: compose:134]`), putting long-lived WS + CRDT merges on the same event loop that serves chat and embeddings. Keep as a documented fallback if adding Node to the runtime is vetoed. |
| Yjs entirely | `excalidraw-room` (MIT, 526★, last push 2024-07-12) | **Rejected.** It is Excalidraw's official Socket.IO relay, but it is **end-to-end encrypted and stateless** — the server cannot read the scene, so it can neither persist server-side nor feed the brain. That defeats two of this phase's three goals. Also unmaintained since mid-2024 `[VERIFIED: GitHub API]` |
| Postgres for the Y.Doc | `@hocuspocus/extension-s3` → MinIO | Viable (MinIO is already core), but you need a `boards` metadata row anyway; a blob column on/next to it is simpler and transactional. Revisit if docs exceed a few MB. |
| A new container | Bundle the board into an existing service | Nothing in the stack is a Node HTTP server today; `librechat` is, but it's `saas`-profile-only and not ours to extend. |
| Liveblocks / Tiptap Cloud / PartyKit Cloud / Excalidraw+ | — | **Forbidden** by the OSS-only constraint. |

**Installation (inside `apps/board`, resolved at Docker build time — nothing installed on the host):**

```bash
# server
npm i @hocuspocus/server@4.4.0 @hocuspocus/extension-database@4.4.0 yjs@13.6.31
# web (built by vite, output copied into the runtime image)
npm i @excalidraw/excalidraw@0.18.1 react@18 react-dom@18 \
      @hocuspocus/provider@4.4.0 yjs@13.6.31
npm i -D vite @vitejs/plugin-react typescript
```

**Version verification:** every version above was confirmed against the live npm registry on 2026-07-24 via `npm view <pkg> version license engines peerDependencies time.modified`. Pin exact versions — Excalidraw's unreleased `master` changelog already lists breaking `setActiveTool`, `scrollToContent → setViewport`, and toolbar DOM changes `[VERIFIED: raw CHANGELOG.md]`, so a caret range on Excalidraw will break this binding.

---

## Architecture Patterns

### System Architecture Diagram

```
┌──────────────────────┐                    ┌──────────────────────┐
│  Chrome extension    │                    │  Browser tab         │
│  popup (team chat)   │                    │  board SPA           │
│                      │  chrome.tabs       │  (React+Excalidraw)  │
│  [board] button ─────┼───.create(url)────►│                      │
└──────────┬───────────┘                    └───┬──────────┬───────┘
           │                                    │          │
           │ Bearer xbt_token                   │ Bearer   │ WS + token
           │ POST /v1/teams/{id}/boards         │ xbt_token│ (Auth msg,
           ▼                                    ▼          │  not URL)
    ╔══════════════════════════════════════════════════╗   │
    ║              nginx  (api.<d> / board.<d>)        ║   │
    ╚════════╦═════════════════════════════╦═══════════╝   │
             │                             │               │
             ▼                             ▼               ▼
    ┌────────────────────┐        ┌────────────────────────────────┐
    │   memory-api       │        │   board container (single proc)│
    │   (FastAPI, N wkr) │        │  ┌──────────────────────────┐  │
    │                    │        │  │ static /  → built SPA    │  │
    │ /boards      list  │        │  ├──────────────────────────┤  │
    │ /board-token mint ─┼─HS256─►│  │ Hocuspocus /collab       │  │
    │              (JWT) │        │  │  onAuthenticate:         │  │
    │                    │        │  │   verify HS256           │  │
    │ /internal/boards/  │◄───────┼──┤   claims.board_id ===    │  │
    │   {id}/doc GET/PUT │  bridge│  │   documentName  → else ✗ │  │
    │        (bytea)     │  secret│  │  Database ext: fetch/store│ │
    │                    │        │  │  awareness relay (cursors)│ │
    │ /media/upload  ────┼──►MinIO│  └──────────────────────────┘  │
    │ /media/{id}/img ◄──┼───     └──────────────┬─────────────────┘
    │                    │                       │ 26b: debounced
    │ board_snapshot ◄───┼───────────────────────┘ text extract POST
    │  → chunk → embed   │
    └───┬────────────┬───┘
        ▼            ▼
  ┌──────────┐  ┌─────────┐
  │ Postgres │  │ Qdrant  │
  │ boards   │  │ vectors │
  │ board_   │  └─────────┘
  │  docs    │
  └──────────┘
```

Read the primary path top-left to bottom: the member clicks **board** in the chat popup → the extension asks memory-api for (or creates) the team's board and opens `https://board.<domain>/?b=<board_id>` in a tab → the SPA exchanges the user's `xbt_token` for a short-lived board token → the Hocuspocus provider connects and sends that token in the Auth message → `onAuthenticate` verifies HS256 and asserts `claims.board_id === documentName` → on first open the Database extension `fetch`es the stored Y update from memory-api → every edit fans out to the other connected members and, debounced, `store`s back through memory-api.

### Recommended Project Structure

```
apps/board/
├── Dockerfile             # multi-stage: node:22-alpine builder (vite build) → node:22-alpine runtime
├── package.json           # server deps + a "build:web" script
├── src/
│   ├── server.ts          # Hocuspocus + static file serving, one process
│   ├── auth.ts            # verifyBoardToken(token, documentName) — HS256, claim-match
│   └── persistence.ts     # Database extension → memory-api /v1/internal/boards/{id}/doc
└── web/
    ├── vite.config.ts     # define: { "process.env.IS_PREACT": ... }  (see Pitfall 3)
    ├── index.html
    └── src/
        ├── main.tsx       # mounts <Board/>, reads ?b=<board_id>
        ├── Board.tsx      # <Excalidraw> + HocuspocusProvider + binding
        ├── token.ts       # xbt_token → POST /v1/boards/{id}/token (async supplier)
        └── yjs-binding/   # vendored y-excalidraw (MIT) if the spike says so

apps/memory-api/app/
├── routes/boards.py       # list/create/token (user-facing) + internal doc get/put
├── repos/boards.py
├── models/board.py
└── alembic/versions/0028_boards.py     # down_revision = "0027"
```

### Pattern 1: Mint-scoped-token-then-connect (mirror of `/v1/me/centrifugo-token`)

**What:** memory-api is the only component that knows team membership. It mints a short-lived HS256 token naming exactly what the bearer may open. The realtime server verifies it offline against a shared secret — no per-connection round trip.

**When to use:** every realtime surface in this repo. It is already used twice.

**Example (existing code this must mirror):**

```python
# Source: apps/memory-api/app/routes/team_chat.py:123-147 (VERIFIED in repo)
@router.post("/me/centrifugo-token")
async def issue_centrifugo_token(principal=Depends(get_current_principal),
                                 session=Depends(get_session)) -> dict[str, Any]:
    user = _require_user_principal(principal)
    teams = await teams_repo.get_all_teams_for_user(session, user_id=user.id)
    channels = [f"team:{t.id}" for t in teams]
    channels.append(f"user:{user.source_user_id}")
    return centrifugo_client.issue_client_token(
        user_sub=user.source_user_id, user_id=user.id,
        display_name=getattr(user, "display_name", None),
        email=getattr(user, "email", None), channels=channels,
    )
```

```python
# Source: apps/memory-api/app/routes/media_helpers.py:87-116 (VERIFIED in repo)
def verify_media_token(token: str, item_id: str) -> str:
    claims = authlib_jwt.decode(token, settings.BRIDGE_SHARED_SECRET)
    claims.validate()                                  # exp / iat / nbf
    if claims.get("scope") != "media":      raise HTTPException(403, "wrong scope")
    if claims.get("item_id") != item_id:    raise HTTPException(403, "item_id mismatch")
    team_scope = claims.get("team_scope")
    if not team_scope:                      raise HTTPException(403, "missing team_scope")
    return str(team_scope)
```

The board token is `{"scope": "board", "board_id": ..., "team_scope": ..., "user_id": ..., "display_name": ..., "read_only": bool, "iat", "exp"}` signed with `BRIDGE_SHARED_SECRET`, TTL ~5-15 minutes, and the Node side performs the identical three assertions.

### Pattern 2: `onAuthenticate` with a documentName claim-match

**What:** Hocuspocus calls `onAuthenticate` per document with `{ token, documentName, requestHeaders, requestParameters, request, socketId, connection }`; throwing terminates the connection `[CITED: tiptap.dev/docs/hocuspocus/server/hooks]`.

**When to use:** always. Without the `documentName` match, a valid token for board A is a valid token for board B — which is a cross-team leak, since board B may belong to another team.

**Example:**

```ts
// Source shape: tiptap.dev/docs/hocuspocus/server/hooks (CITED)
async onAuthenticate({ token, documentName, connection }) {
  const c = verifyBoardTokenHS256(token, process.env.BRIDGE_SHARED_SECRET!);  // throws on bad sig/exp
  if (c.scope !== "board")        throw new Error("Not authorized!");
  if (c.board_id !== documentName) throw new Error("Not authorized!");        // ← the isolation gate
  connection.readOnly = !!c.read_only;
  return { user: { id: c.user_id, name: c.display_name, team: c.team_scope } };
}
```

The client supplies the token via an **async supplier**, so a fresh one is minted on every (re)connect:

```ts
// Source: @hocuspocus/provider@4.4.0 dist/index.d.ts:319 (VERIFIED)
//   token: string | (() => string) | (() => Promise<string>) | null
new HocuspocusProvider({
  url: `wss://board.${DOMAIN}/collab`,
  name: boardId,                       // === documentName on the server
  document: ydoc,
  token: async () => (await mintBoardToken(boardId)).token,
  onAuthenticationFailed: () => showAccessDenied(),
});
```

The token travels in the Hocuspocus **Auth message**, not the URL `[VERIFIED: OutgoingMessageArguments includes `token`, dist/index.d.ts:110]` — so it stays out of nginx access logs and browser history.

### Pattern 3: Persistence through memory-api, not around it

**What:** the board server holds no database credentials. `@hocuspocus/extension-database` takes two async callbacks; point them at memory-api internal endpoints authenticated with `BRIDGE_SHARED_SECRET` (the pattern `mcp-brain` already uses via `X-Internal-Secret` `[VERIFIED: docker-compose.yml mcp-brain env block]`).

```ts
// Source shape: tiptap.dev/docs/hocuspocus/server/extensions/database (CITED)
new Database({
  fetch: async ({ documentName }) => {
    const r = await fetch(`${MEMORY_API_URL}/v1/internal/boards/${documentName}/doc`,
                          { headers: { "X-Internal-Secret": SECRET } });
    if (r.status === 404) return null;              // new board
    return new Uint8Array(await r.arrayBuffer());   // MUST be the same bytes store() saved
  },
  store: async ({ documentName, state }) => {
    await fetch(`${MEMORY_API_URL}/v1/internal/boards/${documentName}/doc`, {
      method: "PUT", body: state,
      headers: { "X-Internal-Secret": SECRET, "Content-Type": "application/octet-stream" },
    });
  },
});
```

`store` is debounced by the server: `debounce: 2000` (2 s), `maxDebounce: 10000` (10 s) `[VERIFIED: tiptap.dev/docs/hocuspocus/server/configuration]`. That is also the natural trigger point for 26b's brain snapshot.

### Anti-Patterns to Avoid

- **Adding React/Vite to `chrome-extension/`.** The extension is hand-written ESM with `node tests/*.mjs` as its whole test story `[VERIFIED: chrome-extension/tests/run_tests.mjs + 13 .mjs files, zero package.json]`. Introducing a bundler there breaks the edit-and-reload workflow and every contract test's import path, for a 2.76 MB payload in a 400 px popup.
- **Running the Yjs server inside memory-api.** `UVICORN_WORKERS=2` → two divergent in-memory docs.
- **Deriving `team_scope` on the board server.** It must arrive as a verified claim, never be read from a query param or a header the client controls — the exact discipline `doc_body_ingest.py` documents as "the only cross-team leak vector, so it is closed at construction" `[VERIFIED: apps/memory-api/app/services/doc_body_ingest.py:~92]`.
- **Storing an append-log of Yjs updates.** Store the compacted `state` the Database extension hands you; Yjs already garbage-collects.
- **Caret-ranging Excalidraw.** Its `master` changelog is full of unreleased breaking API changes.
- **Serving the board SPA from the Firebase app-site.** `app-site/firebase.json` sets `X-Frame-Options: DENY` on `**` and is a SaaS-only deploy target `[VERIFIED: app-site/firebase.json]`; an OSS self-hoster would get no board.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Conflict-free concurrent editing | Operational-transform / last-write-wins over Centrifugo | `yjs` | Yjs is a proven CRDT; OT correctness under network partition is a research problem |
| Yjs sync protocol over WS | A custom `ws` server implementing sync step1/step2 | `@hocuspocus/server` | y-websocket's own maintainers point production users at Hocuspocus |
| Presence / remote cursors | A parallel Centrifugo channel for cursors | `y-protocols/awareness` via the Hocuspocus provider | Awareness handles join/leave, timeouts, and clock skew; a second transport would desync from the doc |
| Debounced persistence + retry | A `setTimeout` save loop | `@hocuspocus/extension-database` | Built-in debounce/maxDebounce, retry-on-throw, and a shutdown flush `[CITED: hooks docs]` |
| Realtime token issuance | A new bespoke secret + scheme | `mint_media_token` / `issue_client_token` shape | Two working precedents in-repo; reuse `BRIDGE_SHARED_SECRET` |
| Text chunking + embedding | New chunker | `app/services/doc_extract.chunk_text` + the `doc_body_ingest` upsert loop | Phase 24 already solved caps, overlap, deterministic uuid5 ids, and tagging inheritance |
| Image storage + browser-safe serve | Base64 in the CRDT doc forever | `POST /v1/media/upload` + `GET /v1/media/{id}/img?t=` | The signed-token serve endpoint exists precisely so a bare `<img src>` works without a Bearer header |

**Key insight:** almost every non-canvas concern in this phase already has an implementation in this repo. The genuinely new code is (a) a Node process configuring Hocuspocus, (b) a React page mounting Excalidraw, and (c) the Excalidraw↔Yjs binding. Everything else is a mirror of Phase 22/24/25 patterns.

---

## Auth: Team-Scope on the WebSocket

The invariant to prove: **a member of team B can never open team A's board.** Three independent gates, all required:

1. **Board creation/listing is team-scoped in memory-api.** `POST /v1/teams/{team_id}/boards` and `GET /v1/teams/{team_id}/boards` go through `_resolve_team_and_check_membership(session, user.id, team_id)` — the existing helper that 404s an unknown team and 403s a non-member `[VERIFIED: apps/memory-api/app/routes/team_chat.py:67-81]`.
2. **Token minting re-checks membership at mint time.** `POST /v1/boards/{board_id}/token` loads the board, resolves its `team_id`, re-runs the membership check, and only then signs. Short TTL (5-15 min) means a revoked member loses access within one token lifetime.
3. **The board server claim-matches `documentName`.** As in Pattern 2. This is what stops a legitimately-minted token for one board from opening another.

Additional hardening available out of the box in Hocuspocus 4 `[VERIFIED: tiptap.dev/docs/hocuspocus/server/configuration]`:

| Setting | Default | Why it matters here |
|---------|---------|---------------------|
| `maxUnauthenticatedQueueSize` | 5 MiB | Caps memory an unauthenticated socket can consume |
| `maxUnauthenticatedQueueMessages` | 1000 | Same, by message count |
| `maxPendingDocuments` | 100 | Caps document-name enumeration per connection — **lower this to 1 or 2**; a legitimate client opens exactly one board |
| `websocketOptions.maxPayload` | `{}` (unset) | Set it — an unbounded payload is a trivial OOM vector on a 4 GB VM |
| `timeout` | 60000 | Idle connection reaping |

Board ids should be **UUIDv4**, not sequential, so `documentName` is unguessable even before the claim-match.

---

## Persistence

**Recommendation: Postgres.**

You need a `boards` metadata row regardless — to list a team's boards, to resolve `board_id → team_id` at mint time, and to soft-delete. Once that table exists, the doc blob is one more column or one sibling row, transactional with the metadata, and covered by the existing `xbrain-backup` service. MinIO would mean a second store, a key convention, and no transactionality — for a payload measured in tens-to-hundreds of KB.

```
-- migration 0028, down_revision = "0027"  [VERIFIED: 0027_team_invite_codes.py is current head]
boards       (id uuid pk, team_id uuid fk→teams, title text, created_by uuid,
              created_at, updated_at, deleted_at null)          -- soft delete, Phase-11 convention
board_docs   (board_id uuid pk fk→boards, state bytea not null,
              size_bytes int, updated_at)                        -- one compacted Y update per board
```

Why a sibling table rather than a column: a `bytea` in the same row as the metadata means every `SELECT * FROM boards` for a list view drags the blob through TOAST. Splitting keeps list queries cheap.

**Snapshot / compaction.** Hocuspocus hands `store()` the already-compacted full document state, and Yjs garbage-collects deleted content internally — so overwriting the single `state` row on each debounced store IS the compaction strategy. No append log, no periodic rewrite job. What you must add:

- A **size cap** (`BOARD_MAX_DOC_BYTES`, suggest 8-16 MB) enforced on the `PUT` — reject with 413 and log loudly rather than let one pasted 20 MB image OOM the board container on the next `fetch`.
- The cap matters most in 26a, where images are still base64 in the doc (see below).
- Store `size_bytes` so the Brain Monitor / admin storage view can surface runaway boards.

**Restart behaviour to verify in the gate:** two clients edit → both disconnect → `docker compose restart board` → a client reconnects and sees the edits. That single test proves `fetch`, `store`, and the debounce flush on shutdown (`Server.destroy()` flushes pending debounced stores `[CITED: hooks docs]`).

---

## Images

Excalidraw keeps images in a `BinaryFiles` map separate from `elements`; an image element references a `fileId`, and the file entry is `{ mimeType, id, dataURL, created, lastRetrieved }` where **`dataURL` is a branded `DataURL` string** `[VERIFIED: dist/types/excalidraw/types.d.ts:65-88 via jsdelivr]`. Out of the box that means base64 in the scene — and if the scene is the Y.Doc, base64 in the CRDT, replicated to every client on every load, forever (Yjs tombstones deleted content's delete-set, and the bytes were already broadcast).

**Target design (26b):**

1. Intercept the new-file event (`onChange`'s third arg / the binding's asset map write).
2. `POST /v1/media/upload` with the blob → `{item_id, key, mime, size}` `[VERIFIED: apps/memory-api/app/routes/media.py:102-215]`. Team scope comes from the `X-Team-Scope` header the endpoint already enforces via `Depends(get_team_scope)`.
3. Write only `{ fileId → { item_id, mime } }` into the Y.Doc's asset map. Tens of bytes instead of megabytes.
4. On load and on remote asset-add, each client fetches `GET /v1/media/{item_id}/img?t=<signed>`, converts the blob to a `data:` URL (`FileReader.readAsDataURL`), and calls `excalidrawAPI.addFiles([...])` `[VERIFIED: addFiles: (data: BinaryFileData[]) => void, types.d.ts:619]`.

**Cost of that:** the SPA needs a media-token mint (memory-api already has `mint_media_token`, but it is currently only called from `_enrich_event` and `_serialize_message` — a board consumer needs a small endpoint or the token returned by the upload response), a fetch-and-convert cache keyed by `fileId`, and re-entrancy guards so a remote asset-add doesn't loop back into an upload. That is **a real slice of work, not a tweak** — roughly one plan on its own.

**Honest recommendation:** ship 26a **with Excalidraw's native base64 image paste working** (the user's "send photos" ask is satisfied on day one) plus a hard `BOARD_MAX_DOC_BYTES` cap and a client-side max-image-size guard, then do the MinIO routing in 26b. Do **not** ship 26a with images disabled — that reads as a broken board.

---

## Brain Ingestion

**Extractable text.** Excalidraw's `ToolType` union includes `"text"` `[VERIFIED: types.d.ts:89]`; text elements carry a `text` field, and labels bound to shapes/arrows are themselves text elements referenced via `boundElements`. So the extraction is: take the elements array, keep `type === "text" && !isDeleted`, read `.text`, and order them by reading order (sort by `y` then `x`, or group by containing frame). Frame names (`type === "frame"`, `.name`) are worth including as section headers.

**The pipeline, mirroring Phase 24 exactly:**

```python
# Shape to mirror — Source: apps/memory-api/app/services/doc_body_ingest.py (VERIFIED in repo)
BOARD_INGEST_NS = uuid.UUID("<new fixed uuid5 namespace>")   # NEVER change it — idempotency

chunks = chunk_text(board_text,
                    chunk_size=settings.DOCBODY_CHUNK_SIZE,
                    overlap=settings.DOCBODY_CHUNK_OVERLAP,
                    max_chunks=settings.DOCBODY_MAX_CHUNKS)
for i, chunk in enumerate(chunks):
    await provider.upsert(MemoryItem(
        id=str(uuid.uuid5(BOARD_INGEST_NS, f"{board_id}:{i}")),   # deterministic → re-ingest overwrites
        team_scope=team_scope,          # INHERITED from the board's team, never derived
        project_scope=project_scope,
        content=chunk,
        metadata={"board_id": board_id, "chunk_index": i, "chunk_total": len(chunks)},
        source="board:snapshot",
        truth_level="WORKING",          # a whiteboard is working material, not canonical
        visibility="team",
        validation_status="pending",
        confidence=1.0,
        created_at=now, updated_at=now,
    ))
```

All 7 tagging fields are present. Deterministic `uuid5` ids mean a re-snapshot **overwrites** the previous chunks rather than duplicating them — essential, because a board is re-snapshotted many times over its life. Reuse `chunk_text` from `app/services/doc_extract` and the existing `DOCBODY_*` settings, or clone them as `BOARD_*` knobs if the caps should differ.

**Who extracts the text.** Two options:

- **(A) The board server extracts** and POSTs plain text + board id to memory-api, which tags/chunks/embeds. Simplest — the board server already has the live `Y.Doc` and `yjs` in-process. Recommended.
- **(B) memory-api decodes the stored blob with `pycrdt`** (MIT, cp312 manylinux wheels for aarch64 + x86_64 `[VERIFIED: pypi.org/pypi/pycrdt/json]`). Lets memory-api re-derive text from any stored board without the board server running (useful for backfill). Adds a Rust-backed dep to memory-api; `pycrdt` is Development Status 4 - Beta. Keep as a follow-up if backfill is ever needed.

**Trigger + rate limit.** Fire from `onStoreDocument` (already debounced at 2 s / 10 s max), but throttle the *ingest* far harder — a board under active editing would otherwise re-embed every 10 seconds. Suggest a per-board floor of 2-5 minutes plus a "changed since last snapshot" guard, and fire-and-forget with a bare `except Exception` so a failed ingest never breaks the save — the exact discipline `media.py:_run_body_ingest` documents `[VERIFIED: apps/memory-api/app/routes/media.py:41-81]`.

---

## Common Pitfalls

### Pitfall 1: `verify-phase16.sh` will fail the moment you touch compose

**What goes wrong:** the phase-16 gate asserts the bare core is **exactly** ten named services (empty `diff`) and that `docker compose config --profiles` equals the literal string `"integrations ops saas "` `[VERIFIED: infrastructure/scripts/verify-phase16.sh:85, 261-284]`. Adding a `board` service or a `board` profile fails both assertions, and a new `xbrain-board` container must also be added to the `OPT_IN_CONTAINERS` deny-list (verify-phase16.sh:91) used to prove opt-in services don't boot with the core.
**Why it happens:** the gate encodes the service set as literals so drift is loud — by design.
**How to avoid:** treat amending `verify-phase16.sh` (and `docs/INSTALL.md` lines 17/58/146/261-272, which enumerate the 10-service core and the three profiles) as an explicit task in the plan, not an afterthought.
**Warning signs:** a green Phase 26 gate and a red Phase 16 gate on the same commit.

### Pitfall 2: a forgotten router silently ships in both editions — and a test catches it

**What goes wrong:** every router module under `app/routes/` must appear in exactly one of `CORE_ROUTERS` / `SAAS_ONLY_ROUTERS`; `tests/test_edition_gating.py::test_every_router_module_is_classified` fails until a new one is classified `[VERIFIED: apps/memory-api/app/main.py:102-163]`.
**How to avoid:** add `boards.router` to `CORE_ROUTERS` (the board is a product feature; nothing is paywalled per locked decision Q6) in the same commit that creates the module.

### Pitfall 3: Vite strips `process`, Excalidraw reads `process.env.IS_PREACT`

**What goes wrong:** blank canvas / runtime `process is not defined`.
**How to avoid:** `[CITED: docs.excalidraw.com/.../integration]`

```ts
// vite.config.ts
define: { "process.env.IS_PREACT": JSON.stringify("true") },
```

Also `import "@excalidraw/excalidraw/index.css";` — the stylesheet is a separate export `[VERIFIED: package exports map, npm view]` — and render client-side only; Excalidraw explicitly does not support SSR.

### Pitfall 4: remote updates poison the local undo stack

**What goes wrong:** a teammate's edit lands in *your* Ctrl+Z history; undo starts deleting other people's work.
**Why it happens:** `updateScene` defaults to capturing history. `commitToHistory` was replaced by `captureUpdate`, and remote updates must pass `CaptureUpdateAction.NEVER` — "Use for updates which should never be recorded, such as remote updates or scene initialization" `[CITED: docs.excalidraw.com/.../excalidraw-api]`.
**How to avoid:** every binding-driven `updateScene` for remote changes uses `captureUpdate: CaptureUpdateAction.NEVER`. This is precisely the API `y-excalidraw@2.0.12` predates.

### Pitfall 5: arm64 dev machine → amd64 VM

**What goes wrong:** `exec format error` on the VM.
**How to avoid:** never `docker build` the board image locally. CI's bake step builds **every** `build:` service from the compose files, amd64-only, on `push: main` `[VERIFIED: .github/workflows/ci-lockstep.yml]` — so simply declaring `build:` in `docker-compose.yml` puts the board image on the correct path automatically. Confirmed locally: Docker daemon reports `linux/aarch64` `[VERIFIED: docker info]`.

### Pitfall 6: CORS blocks the SPA from calling memory-api

**What goes wrong:** the board page at `https://board.<domain>` calls `https://api.<domain>` and every request preflights to failure. `CORS_ALLOWED_ORIGIN_REGEX` defaults to `(chrome-extension://.*|http://localhost(:\d+)?)` and a `field_validator` rejects an over-broad regex `[VERIFIED: apps/memory-api/app/config.py:156, 168-196; .env.example:111]`.
**How to avoid:** extend the regex in `.env.example` + docs to include the board origin, and add the board origin to `CENTRIFUGO_ALLOWED_ORIGINS` if the board ever subscribes to Centrifugo.

### Pitfall 7: the Y.Doc grows without bound because images are base64

**What goes wrong:** a board with a dozen pasted screenshots becomes a 30 MB document that every client downloads on open, and `fetch`/`store` start timing out.
**How to avoid:** the `BOARD_MAX_DOC_BYTES` cap in 26a, a client-side image dimension/size guard, and the MinIO routing in 26b.

### Pitfall 8: adding a service to the OSS-light core blows the 4 GB budget

**What goes wrong:** OSS-light is documented at "~4 GB RAM free for the OSS-light core (10 services)" `[VERIFIED: docs/INSTALL.md:58]`.
**How to avoid:** put the board behind a new `board` profile, exactly as `integrations` / `saas` / `ops` work. Set `mem_limit: 256m` (Centrifugo's is 256m, mcp-brain's 128m `[VERIFIED: docker-compose.yml]`).

### Pitfall 9: `store()` must return the same bytes it was given

**What goes wrong:** silent data corruption or lost history.
**Why:** the Database extension docs are explicit — "Make sure to return the same Uint8Array that was saved in store(), and do not create a new Ydoc" `[CITED: tiptap.dev/docs/hocuspocus/server/extensions/database]`.
**How to avoid:** the memory-api endpoint stores/returns raw `bytea` with `Content-Type: application/octet-stream`. No JSON, no base64 round-trip, no re-encoding.

---

## Code Examples

### Fetch a fresh board token (SPA, mirrors the extension's fetch style)

```ts
// Pattern source: chrome-extension/popup.js invite/mint calls (VERIFIED in repo)
async function mintBoardToken(boardId: string): Promise<{ token: string; ws_url: string; expires_at: number }> {
  const r = await fetch(`${MEMORY_API_BASE}/v1/boards/${boardId}/token`, {
    method: "POST",
    headers: { Authorization: `Bearer ${xbtToken}`, "X-Team-Scope": teamSlug },
  });
  if (!r.ok) throw new Error(`board token ${r.status}`);
  return r.json();
}
```

### Open the board from the chat (extension)

```js
// Source: chrome-extension/background.js:1353 + popup.js:166 (VERIFIED — both already use this API)
document.getElementById("btn-board").addEventListener("click", async () => {
  const r = await fetch(`${MEMORY_API_BASE}/v1/teams/${state.activeTeamId}/boards`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ title: "Team board" }),   // idempotent: returns the existing default board
  });
  const { id, url } = await r.json();
  chrome.tabs.create({ url });
});
```

`chrome.tabs.create` needs no host permission for the target URL — proven by `nudge_open`, which opens arbitrary teammate-supplied URLs with only the `notifications` + `tabs`-free permission set in `manifest.json` `[VERIFIED: chrome-extension/manifest.json permissions list, background.js:1324-1353]`. Add `"board-*"` element ids to `chrome-extension/tests/test_popup_contract.mjs`'s required-id list — Phase 25 did exactly this for `btn-invite*` `[VERIFIED: tests/test_popup_contract.mjs:79-89]`.

### Compose service (mirroring `mcp-brain`, the closest small-service template)

```yaml
# Source shape: infrastructure/docker-compose.yml mcp-brain block (VERIFIED)
  board:
    build:
      context: ../apps/board
      dockerfile: Dockerfile
    image: xbrain/board:phase26
    container_name: xbrain-board
    profiles: ["board"]
    restart: unless-stopped
    logging: *default-logging
    environment:
      MEMORY_API_URL: http://memory-api:8000
      BRIDGE_SHARED_SECRET: ${BRIDGE_SHARED_SECRET}
      XBRAIN_BASE_DOMAIN: ${XBRAIN_BASE_DOMAIN:-localhost}
      BOARD_MAX_DOC_BYTES: ${BOARD_MAX_DOC_BYTES:-16777216}
    expose: ["8107"]
    networks: [xbrain_net]
    mem_limit: 256m
    depends_on:
      memory-api: { condition: service_healthy }
    healthcheck:
      test: ["CMD", "node", "-e", "require('http').get('http://127.0.0.1:8107/healthz',r=>process.exit(r.statusCode===200?0:1)).on('error',()=>process.exit(1))"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 20s
```

**CORRECTION (plan-check):** 8107 is NOT free — `mcp-github` already uses it internally (`FASTMCP_PORT: "8107"`, docker-compose.yml:988, profile `integrations`). This is harmless: each container has its own network namespace, neither service publishes to the host, and DNS/healthchecks are per-container-name — so `board:8107` and `mcp-github:8107` never collide. Keep the number but do NOT treat 8107 as globally reserved. (8104 mcp-brain, 8105 session-bridge, 8106 centrifugo `[VERIFIED: docker-compose.yml]`.)

### nginx vhost (mirroring the centrifugo template)

```nginx
# Source: infrastructure/nginx/templates/60-centrifugo.conf.template (VERIFIED — copy its WS block)
server {
    listen 80;
    server_name board.${XBRAIN_BASE_DOMAIN};
    location /nginx-health { return 200 "ok\n"; access_log off; }

    location /collab {
        set $board_upstream http://board:8107;
        proxy_pass $board_upstream;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;   # map already declared in 10-xbrain.conf
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
        proxy_buffering off;
    }
    location / {                       # the built SPA, served by the same container
        set $board_upstream http://board:8107;
        proxy_pass $board_upstream;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
    }
}
```

---

## Runtime State Inventory

Not a rename/refactor/migration phase — but three items behave like runtime state and must not be missed:

| Category | Items Found | Action Required |
|----------|-------------|-----------------|
| Stored data | None pre-existing. New: `boards` + `board_docs` in Postgres (migration 0028, `down_revision = "0027"`) | New schema — must upgrade clean under `EDITION=oss` AND `saas`, per the Phase-25 gate convention |
| Live service config | nginx templates are volume-mounted **read-only from the repo** (`./nginx/templates:/etc/nginx/templates:ro`) so a new vhost ships with the repo, not the VM `[VERIFIED: docker-compose.yml:47]`. `verify-phase16.sh` literals are in the repo. | Repo edits only — no out-of-band VM config |
| OS-registered state | None | None — verified: no Task Scheduler / systemd units in this project's deploy path |
| Secrets / env vars | Reuses `BRIDGE_SHARED_SECRET` (already required, boot-fatal if empty). New optional knobs: `BOARD_MAX_DOC_BYTES`, `BOARD_SNAPSHOT_MIN_INTERVAL_S`. `CORS_ALLOWED_ORIGIN_REGEX` must be widened for the board origin. | Add to `.env.example` + `docs/INSTALL.md`; `preflight-env.sh` may need the new profile coupling check |
| Build artifacts | New: `xbrain/board:phase26` image (built by the CI bake step automatically once `build:` is declared). No stale artifacts to clean. | None |

---

## Sizing / Phase Split

**Recommendation: split into 26a and 26b.**

Scope inventory (verified against the repo, not estimated from the brief):

1. New `apps/board` Node service — Hocuspocus, `onAuthenticate`, Database extension, static hosting, Dockerfile, healthz.
2. New Vite+React SPA — Excalidraw mount, provider wiring, the Yjs binding (spike + likely vendor), token supplier, English-only UI.
3. memory-api — `boards.py` router (list/create/token + internal doc GET/PUT), repo, model, migration 0028, `CORE_ROUTERS` registration.
4. Extension — a `board` header button, popup contract test extension.
5. Infra — compose service + `board` profile, nginx vhost, `.env.example`, `docs/INSTALL.md`, **`verify-phase16.sh` amendments**, `verify-phase26.sh`.
6. Images → MinIO — upload interception, asset-map indirection, fetch+`addFiles` hydration, media-token plumbing, re-entrancy guards.
7. Brain ingestion — text extraction, throttled snapshot trigger, `board_snapshot_ingest` service, real-Postgres+Qdrant gate.

Items 1-5 alone are ~6 plans. Items 6-7 are ~3. Phases 21-25 ran 3-4 plans each; a single 9-plan phase is 2-3× the established cadence and would put a stale-binding spike, a first-ever frontend build pipeline, and a new schema in the same verification gate.

| Slice | Contents | Success criterion | Est. plans |
|-------|----------|-------------------|-----------|
| **26a — Board + live collab + team-scoped auth + persistence** | Items 1-5. Images work via Excalidraw's native base64 path, bounded by `BOARD_MAX_DOC_BYTES`. | Two members of team A see each other's edits live; the board survives `docker compose restart board`; a member of team B gets `onAuthenticationFailed` on team A's board id — proven against a real Postgres. | 5-6 |
| **26b — Images to MinIO + brain ingestion** | Items 6-7. | A pasted image lands in MinIO with the doc growing by bytes not megabytes; the board's text is retrievable from `memory_search` keyless, with all 7 tagging fields — proven against real Postgres + real Qdrant, embedder not mocked (Phase-19/24 discipline). | 3 |

**Why this seam and not another:** 26a is *user-complete* — the user's literal ask ("write text and send photos, work together live") is satisfied on 26a alone. 26b is *contract-complete* — it makes the board obey the project's differentiating invariant (everything lands in the tagged common memory) and removes 26a's doc-size ceiling. Neither half is a stub of the other, and 26a's gate does not depend on any 26b code.

**The judgement call to flag for the user:** 26a ships with base64 images in the CRDT. That is a known, bounded compromise, not an oversight — Excalidraw's native behaviour, capped and documented. If the user would rather not ship that at all, the alternative is a single larger phase; do not "solve" it by disabling image paste in 26a.

---

## What NOT to Do

| Don't | Why |
|-------|-----|
| Bundle React + Excalidraw into the Chrome extension | 2.76 MB JS + 144 KB CSS `[VERIFIED: jsdelivr manifest]`, requires a bundler the repo doesn't have, in a 400 px popup |
| Add a bundler to `chrome-extension/` for any reason in this phase | It would break the hand-written-ESM convention and 13 `node *.mjs` contract tests |
| iframe the board into the popup | The popup is the wrong surface; `app-site` sets `X-Frame-Options: DENY` globally `[VERIFIED: app-site/firebase.json]` |
| Run the Yjs server inside memory-api | `UVICORN_WORKERS=2` → two divergent in-memory docs `[VERIFIED: compose:134]` |
| Use `@y/websocket-server` in production | Its own README calls it a development server and points at Hocuspocus for production `[VERIFIED: raw README]` |
| Use `excalidraw-room` | E2E-encrypted and stateless: no server-side persistence, no brain ingestion. Unmaintained since 2024-07 `[VERIFIED: GitHub API]` |
| Use Liveblocks / PartyKit Cloud / Tiptap Cloud / Excalidraw+ | Violates the OSS-only, self-hostable constraint |
| Depend on `y-excalidraw@2.0.12` from npm without a spike | 19 months stale, peers `^0.17.6`, and `commitToHistory → captureUpdate` landed in between `[VERIFIED: npm + Excalidraw docs]` |
| Caret-range `@excalidraw/excalidraw` | Unreleased `master` already has breaking `setActiveTool` / `setViewport` / toolbar-DOM changes `[VERIFIED: CHANGELOG.md]` |
| Put the board in the OSS-light core (untagged) | Breaks the 10-service assertion in `verify-phase16.sh` and the documented 4 GB budget |
| Fold the board into the `integrations` profile | That profile drags Neo4j + Langfuse + ClickHouse (~5 GB). A board user shouldn't pay for a graph DB |
| Derive `team_scope` on the board server from a query param or header | The only cross-team leak vector. It must arrive as a signed claim |
| Skip the `claims.board_id === documentName` check | A token for any board becomes a token for every board |
| Store an append-log of Yjs updates | Hocuspocus hands you compacted state; Yjs GCs internally |
| `docker build` the board image on the dev machine and deploy it | arm64 dev, amd64 prod `[VERIFIED: docker info → linux/aarch64]` |
| Write French UI strings | Product/app/code is English-only per CLAUDE.md |

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `y-websocket` ships its own server | Server extracted to `@y/websocket-server`; production users pointed at Hocuspocus/YHub | y-websocket v3 (2026-06) `[VERIFIED: npm 3.0.0 + release notes]` | Don't plan around `y-websocket/bin/server.js`; it's gone |
| Hocuspocus on `ws`, Node-only | Hocuspocus 4 on `crossws`; runs on Node 22+, Bun, Deno, Cloudflare Workers | v4 (2026) `[VERIFIED: npm deps + engines; CITED: tiptap.dev overview]` | Node 22+ floor; `websocketOptions` is now the passthrough |
| `updateScene({ commitToHistory })` | `updateScene({ captureUpdate: CaptureUpdateAction.NEVER })` | Excalidraw ~0.18 `[CITED: docs.excalidraw.com]` | Any pre-0.18 Yjs binding needs patching |
| `ExcalidrawAPI.scrollToContent()` | `ExcalidrawAPI.setViewport()` | unreleased `master` `[VERIFIED: CHANGELOG.md]` | Another reason to pin 0.18.1 exactly |
| `y-py` (Python Yjs bindings) | `pycrdt` | y-py last release 2023-10 vs pycrdt 2026-06 `[VERIFIED: PyPI]` | If Python ever needs to read a Y.Doc, it's `pycrdt`, not `y-py` |

**Deprecated / outdated:**
- `y-py` 0.6.2 — last released 2023-10-05; superseded by `pycrdt`.
- `excalidraw-room` — no push since 2024-07-12; and architecturally wrong for this use case.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | A Hocuspocus container with a handful of small boards fits in `mem_limit: 256m` | Sizing, compose example | OOM-kill loop on the VM. Node baseline RSS ~50-80 MB plus per-doc memory; **measure during the phase** and raise the limit if needed. Not verified against a running instance. |
| A2 | Board Y.Doc size stays in the tens-to-hundreds of KB without embedded images | Persistence | If wrong, Postgres `bytea` is still fine up to ~MB; the `BOARD_MAX_DOC_BYTES` cap is the real guard |
| A3 | Gzipped SPA payload lands around 800 KB - 1 MB | Summary | Only affects first-load UX. Raw JS (2.76 MB) is verified; the gzip figure is an estimate |
| A4 | `pycrdt` can decode a `Y.Doc` update produced by `yjs@13.6.31` | Brain Ingestion option (B) | Both wrap/implement the same y-crdt update format, but this was not tested. Only matters if option (B) is chosen; option (A) avoids it entirely |
| A5 | Excalidraw text elements + frame names are sufficient text for useful brain ingestion | Brain Ingestion | A diagram-heavy board may yield little text. Acceptable — a board with no text has nothing to remember |
| A6 | `truth_level = WORKING` and `source = "board:snapshot"` are the right tags | Brain Ingestion | Needs user confirmation. `WORKING` matches the existing team-chat ingest default `[VERIFIED: ROADMAP Phase 13 entry gate]`, but a board could reasonably be `EPHEMERAL` |
| A7 | One default board per team is the right initial model (rather than N named boards) | Extension trigger, code examples | Changes the endpoint shape. The user said "a board", singular — but multi-board is a small increment on the same schema |
| A8 | A new `board` profile is preferred over promoting the board into the core | Sizing, Pitfall 8 | If the user wants the board in every OSS install, the RAM budget and `verify-phase16.sh` core list both change |

---

## Open Questions (RESOLVED except Q4)

> Plan-check resolution: Q1 -> 26-01's mandatory Wave-0 spike (D-26-05); Q2 -> CONTEXT discretion + 26-02's partial-unique-index schema (allows N, defaults to 1); Q3 -> D-26-04 (opt-in profile); Q5 -> 26-04's single-image-both-editions Dockerfile; Q6 -> measured at execution time, feeds 26-06's mem_limit. **Q4 (truth_level for board snapshots) stays genuinely open but is N/A for 26a** — it only matters once 26b's brain ingestion begins.

1. **Does `y-excalidraw@2.0.12` (npm) actually work against `@excalidraw/excalidraw@0.18.1`?**
   - What we know: the peer range is `^0.17.6`; `commitToHistory` was replaced by `captureUpdate` in between; `main` on GitHub is unchanged since 2024-12-10; unmerged PR #13 targets 0.18 compat.
   - What's unclear: whether remote updates function at all, or merely pollute the undo stack.
   - Recommendation: Wave-0 timeboxed spike (two browser tabs, one shape drawn, does it appear?). Fallback: vendor the three source files (~23 KB) with the MIT notice and fix `captureUpdate` in-house. Budget for the fallback; treat "npm works as-is" as the surprise.

2. **One board per team, or many named boards?**
   - What we know: the user said "a board where the team can really work together".
   - What's unclear: whether they expect a board list.
   - Recommendation: schema supports N (`boards.team_id`), 26a ships a single auto-created default board per team, board-list UI deferred. Cheap to extend, nothing to migrate.

3. **Is the board a core service or opt-in?**
   - Recommendation: opt-in `board` profile, for RAM and for keeping `verify-phase16.sh`'s core list at 10. Confirm with the user — if the board is meant to be a headline feature of the OSS product, this flips.

4. **What `truth_level` should board snapshots carry?**
   - Recommendation: `WORKING`, matching the team-chat ingest default. Needs a one-line user confirmation (see A6).

5. **Where does the board SPA get built and hosted in the SaaS deploy?**
   - What we know: `app-site` is Firebase-hosted with `X-Frame-Options: DENY`; the OSS install has no Firebase.
   - Recommendation: the board container serves its own built assets in **both** editions — one code path, no Firebase dependency, no OSS/SaaS divergence.

6. **Real RAM footprint of the board container under 3-5 concurrent editors.**
   - Recommendation: measure during 26a's gate; adjust `mem_limit` before merge (see A1).

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Docker daemon | Building/running the board container | ✓ | 29.6.1, `linux/aarch64` | — (build on CI/VM for amd64) |
| Node (host) | Optional local SPA dev server | ✓ | v24.15.0 (≥22 required by Hocuspocus) | Docker multi-stage build needs no host Node |
| npm (host) | Optional local dep resolution | ✓ | 11.6.0 | Same |
| Python | memory-api work | ✓ | 3.13.7 host / 3.12-slim in image | — |
| Postgres 17 | `boards` + `board_docs` | ✓ (core service) | `postgres:17` | — |
| MinIO | 26b image storage | ✓ (core service since Phase 15) | Chainguard image | — |
| Qdrant | 26b embedding storage | ✓ (core service) | v1.17.1 | — |
| `node:22-alpine` base image | Board container | ✓ (public, multi-arch) | 22-alpine | `node:22-slim` if a musl issue appears (none expected — all deps are pure JS) |
| CI amd64 bake | Producing the prod image | ✓ | `.github/workflows/ci-lockstep.yml` bakes every `build:` service | Build on the VM via `make deploy` |
| Production VM | End-to-end live verification | **✗ TERMINATED** (cost pause) | — | Verify locally via `docker compose --profile board up`; the Phase-16/25 gates already run against a locally-booted stack |

**Missing dependencies with no fallback:** none.
**Missing dependencies with fallback:** the production VM is stopped `[VERIFIED: memory `project_xbrain_vm_paused_cost`; ROADMAP Phase 14 note]` — plan the gate as a local real-Postgres/real-Qdrant boot, exactly as Phases 24 and 25 did.

---

## Validation Architecture

*Skipped: `workflow.nyquist_validation` is `false` in `.planning/config.json` `[VERIFIED]`.*

Note for the planner anyway — the repo's own convention is a `SKIP=FAIL` real-dependency gate plan per phase (`24-03`, `25-03`), plus `infrastructure/scripts/verify-phase<N>.sh`. Phase 26 should ship `verify-phase26.sh` and amend `verify-phase16.sh`.

---

## Security Domain

`security_enforcement` is not set in `.planning/config.json`; absent = enabled.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | yes | Existing `xbt_token` principal via `get_current_principal`; board token is HS256 over `BRIDGE_SHARED_SECRET` with a short TTL |
| V3 Session Management | yes | Short-lived board token (5-15 min) re-minted by the provider's async supplier on every reconnect; revoked membership expires within one TTL |
| V4 Access Control | **yes — the crux** | Three gates: team membership on create/list, membership re-check at mint, `claims.board_id === documentName` on the socket |
| V5 Input Validation | yes | Pydantic on board metadata; `BOARD_MAX_DOC_BYTES` on the blob PUT; `websocketOptions.maxPayload` on the socket; UUID-only board ids |
| V6 Cryptography | yes | `authlib` HS256 (already used by `mint_media_token` and `issue_client_token`). **Never hand-roll**; do not invent a new secret — reuse `BRIDGE_SHARED_SECRET` |
| V7 Error Handling / Logging | yes | Auth failures return a generic rejection (no oracle distinguishing "board doesn't exist" from "not your team") — the Phase-25 generic-404 discipline |
| V13 API / Web Service | yes | The internal doc endpoints are `X-Internal-Secret`-gated, never routed through the public nginx vhost |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Cross-team board access with a valid token for another board | Elevation of Privilege | `claims.board_id === documentName` in `onAuthenticate` |
| Board-id enumeration over one socket | Information Disclosure | UUIDv4 ids + `maxPendingDocuments: 1` |
| Unauthenticated socket memory exhaustion | Denial of Service | `maxUnauthenticatedQueueSize` / `maxUnauthenticatedQueueMessages` (defaults exist; keep them) |
| Oversized payload OOM on a 4 GB VM | Denial of Service | `websocketOptions.maxPayload` + `BOARD_MAX_DOC_BYTES` + `mem_limit` |
| Token leakage via URL / logs | Information Disclosure | Hocuspocus sends the token in the Auth **message**, not the URL `[VERIFIED: provider d.ts]` — do not "simplify" it into a query param |
| Stored XSS via board text rendered elsewhere | Tampering | Board text reaching the brain is stored as text and rendered by existing surfaces; do not add an HTML render path for board content |
| CORS wildcard to make the SPA "just work" | Spoofing | `config.py`'s validator already rejects an over-broad regex — extend it precisely, never to `.*` |

---

## Sources

### Primary (HIGH confidence)

- **Repo (live code read this session):** `CLAUDE.md`; `infrastructure/docker-compose.yml`; `infrastructure/nginx/templates/{10-xbrain,20-api,60-centrifugo}.conf.template`; `infrastructure/centrifugo/config.json`; `infrastructure/scripts/verify-phase16.sh`; `apps/memory-api/app/main.py`; `.../app/config.py`; `.../app/routes/{team_chat,media,media_helpers}.py`; `.../app/services/{centrifugo_client,doc_body_ingest}.py`; `.../pyproject.toml`; `.../Dockerfile`; `.../alembic/versions/`; `chrome-extension/{manifest.json,popup.html,popup.js,background.js,settings.js}`; `chrome-extension/tests/`; `app-site/firebase.json`; `.github/workflows/ci-lockstep.yml`; `docs/INSTALL.md`; `Makefile`; `.env.example`; `.planning/{ROADMAP,STATE,REQUIREMENTS,BACKLOG,config.json}`
- **npm registry (`npm view`, 2026-07-24):** `@excalidraw/excalidraw` 0.18.1 MIT; `yjs` 13.6.31 MIT; `y-websocket` 3.0.0 MIT; `@y/websocket-server` 0.1.5 MIT; `y-protocols` 1.0.7 MIT; `@hocuspocus/{server,provider,common,cli,extension-database,extension-logger,extension-s3,extension-sqlite,extension-redis,extension-webhook,extension-throttle}` 4.4.0 MIT; `y-excalidraw` 2.0.12 MIT
- https://raw.githubusercontent.com/ueberdosis/hocuspocus/main/LICENSE.md — MIT, Copyright (c) 2023 Tiptap GmbH
- https://raw.githubusercontent.com/RahulBadenkal/y-excalidraw/main/{LICENSE,package.json,README.md} — MIT; peer `^0.17.6`
- https://raw.githubusercontent.com/yjs/y-websocket-server/main/README.md — "development server or starting point"; no auth hook
- https://raw.githubusercontent.com/excalidraw/excalidraw/master/packages/excalidraw/CHANGELOG.md — unreleased breaking changes
- https://cdn.jsdelivr.net/npm/@excalidraw/excalidraw@0.18.1/dist/types/excalidraw/types.d.ts — `BinaryFileData`, `addFiles`
- https://cdn.jsdelivr.net/npm/@hocuspocus/provider@4.4.0/dist/index.d.ts — `token` supplier signature
- https://data.jsdelivr.com/v1/packages/npm/@excalidraw/excalidraw@0.18.1 — dist byte sizes
- https://pypi.org/pypi/{pycrdt,pycrdt-websocket,y-py}/json — versions, licenses, wheel arches
- GitHub API: `ueberdosis/hocuspocus/contents/packages`; `RahulBadenkal/y-excalidraw` repo/issues/pulls/branches/git-trees; `excalidraw/excalidraw-room`
- https://tiptap.dev/docs/hocuspocus/server/{hooks,configuration,extensions/database} — `onAuthenticate` payload, defaults, Database callbacks
- https://docs.excalidraw.com/docs/@excalidraw/excalidraw/api/props{,/excalidraw-api} + `/integration` — props, `updateScene`/`captureUpdate`, Vite `process` define, no SSR
- Local probes: `node -v` 24.15.0, `npm -v` 11.6.0, `docker --version` 29.6.1, `docker info` → `linux/aarch64`, `python --version` 3.13.7

### Secondary (MEDIUM confidence)

- https://github.com/excalidraw/excalidraw/discussions/3879 — Excalidraw team: the npm package has no collab; consumers implement it
- https://tiptap.dev/open-source-to-platform — "Pro Extensions need a valid subscription" applies to Tiptap Editor extensions, not the Hocuspocus server
- https://news.ycombinator.com/item?id=48208834 — "Hocuspocus 4 – self-hosted Yjs collaboration backend"
- Docker Hub `openproject/hocuspocus` — evidence that no *official* Hocuspocus image exists; everyone builds their own

### Tertiary (LOW confidence — flagged, not relied on)

- Bundle-size-after-gzip estimates (A3) — no authoritative source found
- Node/Hocuspocus RAM footprint (A1) — no authoritative source; measure in-phase
- `andes90/collabmd` (249★, pushed 2026-07-19) — a possible reference implementation of Excalidraw+Yjs; not inspected, not a dependency

---

## Metadata

**Confidence breakdown:**

| Area | Level | Reason |
|------|-------|--------|
| Licensing / OSS constraint | **HIGH** | Every package's license read from the registry and, for Hocuspocus, from the LICENSE file |
| Repo constraints (no build pipeline, workers=2, verify-16 literals, auth precedents) | **HIGH** | Read from live code this session, with file:line citations |
| Standard stack + versions | **HIGH** | `npm view` against the live registry on the research date |
| Architecture (where the board lives, how auth flows, persistence) | **HIGH** | Derived from verified repo constraints + official Hocuspocus/Excalidraw docs |
| Excalidraw ↔ Yjs binding | **MEDIUM** | The only ready-made binding is 19 months stale with a known API break in the gap. Mitigation identified (vendor, ~23 KB), but not executed |
| Images-to-MinIO design | **MEDIUM** | The types and the endpoints are verified; the end-to-end flow is designed, not prototyped |
| Brain ingestion | **HIGH** | Mirrors a shipped, verified in-repo pipeline (Phase 24) |
| RAM / sizing figures | **LOW** | Estimated (A1, A2, A3); must be measured in-phase |

**Research date:** 2026-07-24
**Valid until:** 2026-08-23 (30 days) — but re-check `@excalidraw/excalidraw` before the plan executes; its `master` changelog shows breaking API churn landing roughly monthly.
