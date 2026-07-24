---
phase: 26-collaborative-board
plan: 03
subsystem: infra
tags: [hocuspocus, yjs, jose, jwt, websocket, node, docker, team-scope, board, collaborative-board]

# Dependency graph
requires:
  - phase: 26-collaborative-board (plan 26-02 — board schema, token, internal doc endpoints)
    provides: "board token contract (HS256 over BRIDGE_SHARED_SECRET, claim set {scope,board_id,team_scope,team_id,sub,display_name,read_only,iat,exp}) + bridge-only GET/PUT /v1/internal/boards/{id}/doc octet-stream"
provides:
  - "apps/hocuspocus — single-process @hocuspocus/server@4.4.0 Yjs service (xbrain-hocuspocus), its OWN container, NO database credentials"
  - "verifyBoardToken(token, documentName, secret) — HS256-pinned jose verifier; the claim-vs-document team-scope boundary (board_id === documentName)"
  - "@hocuspocus/extension-database persistence: byte-exact fetch/store through memory-api's bridge-authenticated internal doc endpoints with a UUIDv4 documentName guard"
  - "onAuthenticate boundary + DoS ceilings (timeout 30s, maxPendingDocuments 1, websocketOptions.maxPayload = BOARD_MAX_DOC_BYTES); boot-fatal on empty secret; SIGTERM flush"
  - "three-stage node:22-alpine Dockerfile: deps -> gate (26-07 client image) -> runtime (non-root, devDep-free, last stage)"
affects: [26-06 compose/nginx profile wiring, 26-07 non-mocked live gate (two-client convergence + real Python-token rejection matrix + testcontainers persistence)]

# Tech tracking
tech-stack:
  added:
    - "@hocuspocus/server@4.4.0 (MIT)"
    - "@hocuspocus/extension-database@4.4.0 (MIT)"
    - "@hocuspocus/provider@4.4.0 (MIT, devDependency — 26-07 client)"
    - "yjs@13.6.31 (MIT)"
    - "jose@6.2.4 (MIT) — pure-ESM JWT verify/sign, zero runtime deps"
  patterns:
    - "onAuthenticate as the load-bearing security boundary: verify HS256 signature (algorithms pinned) + exp + scope + board_id===documentName + non-empty team_scope; one identical DENY message for every failure (no oracle)"
    - "team_scope read ONLY from the verified claim, never from a query param / header / document name"
    - "Persistence routed THROUGH memory-api over a cached short-lived bridge JWT (scope=bridge) — the board container holds no DB credentials"
    - "byte-exact Y.Doc round-trip: raw octet-stream, Uint8Array in/out, no JSON/base64, store() THROWS to trigger extension retry"
    - "documentName validated as canonical lowercase UUIDv4 before URL interpolation — path-traversal / injection gate independent of the auth gate"
    - "multi-stage node:22-alpine Dockerfile with a devDep-carrying `gate` target for the live gate + a non-root devDep-free `runtime` last stage"
    - "plain `node --test tests/*.mjs` via a run_tests.mjs driver (repo convention, no TypeScript build step)"

key-files:
  created:
    - apps/hocuspocus/package.json
    - apps/hocuspocus/package-lock.json
    - apps/hocuspocus/.gitignore
    - apps/hocuspocus/Dockerfile
    - apps/hocuspocus/src/auth.mjs
    - apps/hocuspocus/src/bridge.mjs
    - apps/hocuspocus/src/persistence.mjs
    - apps/hocuspocus/src/server.mjs
    - apps/hocuspocus/tests/test_auth.mjs
    - apps/hocuspocus/tests/run_tests.mjs
  modified: []

key-decisions:
  - "verifyBoardToken mirrors board_helpers.py:verify_board_token byte-for-byte (scope, board_id===documentName, non-empty team_scope) — a module comment binds the two files to change together"
  - "bridgeAuthHeader() is async (jose signing is async) — persistence awaits it; the plan's synchronous-call snippet was adapted (Rule 1)"
  - "DENY = \"Not authorized!\" — one constant returned to the client for every failure mode; detail logged server-side only, token never logged"
  - "documentName UUIDv4 regex /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/ enforced in both fetch and store"

patterns-established:
  - "A protocol port (WebSocket upgrade) is not an HTTP health endpoint — kept EXPOSE-only here; compose TCP-connect healthcheck + nginx wiring is 26-06"
  - "onAuthenticate throw => connection closed; return value => connection context {user:{id,name,team}}; connection.readOnly from the claim"

requirements-completed: [BOARD-01]

# Metrics
duration: 10min
completed: 2026-07-24
---

# Phase 26 Plan 03: apps/hocuspocus — Yjs Server with the onAuthenticate Team-Scope Boundary Summary

**A single-process `xbrain-hocuspocus` service (@hocuspocus/server@4.4.0 on node:22-alpine, no DB credentials) whose `onAuthenticate` verifies the memory-api board token (HS256 pinned via jose) and enforces `board_id === documentName` — the phase's load-bearing cross-team boundary — with byte-exact Postgres persistence routed through memory-api's bridge-authenticated internal doc endpoints, explicit DoS ceilings, and a non-mocked 10-case rejection matrix.**

## Performance

- **Duration:** ~10 min
- **Started:** 2026-07-24T06:09:13+02:00 (base commit db85255)
- **Completed:** 2026-07-24T06:19:20+02:00 (Task 2 commit) + summary
- **Tasks:** 2
- **Files created:** 10 (all under apps/hocuspocus/)
- **Files modified:** 0

## Accomplishments

- **`verifyBoardToken` — the team-scope boundary.** HS256 with `algorithms: ["HS256"]` pinned (blocks alg:none / RS-from-HS confusion), `exp`/`nbf` validated by jose, `scope === "board"`, `board_id === documentName` (on exactly one non-comment line), and a non-empty `team_scope`. Every failure throws `BoardAuthError` with the single constant `DENY = "Not authorized!"` — no oracle.
- **Non-mocked rejection matrix** (`tests/test_auth.mjs`, 163 lines, 10 cases) driven by REAL HS256 tokens minted in-test with jose's `SignJWT`: valid, cross-team (board-A token vs board-B document), absent, malformed, expired, wrong-signature, wrong-scope, missing-team_scope, alg:none, and an identical-message invariant. `npm test` reports **10 tests, 10 pass, 0 fail**.
- **Single-process Hocuspocus server** (`src/server.mjs`) with `onAuthenticate` delegating to the real verifier, `connection.readOnly` from the claim, DoS ceilings (`timeout: 30000`, `maxPendingDocuments: 1`, `websocketOptions.maxPayload = BOARD_MAX_DOC_BYTES`), boot-fatal `process.exit(1)` on an empty `BRIDGE_SHARED_SECRET`, and a SIGTERM/SIGINT handler calling `server.destroy()` to flush debounced stores.
- **Bridge-authenticated, byte-exact persistence** (`src/persistence.mjs` + `src/bridge.mjs`): `@hocuspocus/extension-database` fetch/store against `GET/PUT /v1/internal/boards/{id}/doc` as `application/octet-stream`, 404 => new board, raw `Uint8Array` in/out with no JSON/base64, `store` THROWS to trigger the extension's retry. The bridge JWT (`scope: "bridge"`) is minted with a ~120 s TTL and cached in-process (re-minted within 30 s of expiry). The container holds NO database credentials (grep-verified across all four modules).
- **Three-stage `node:22-alpine` Dockerfile**: `deps` -> `gate` (devDeps incl. `@hocuspocus/provider` for the 26-07 two-client convergence proof) -> `runtime` (LAST stage, `npm ci --omit=dev`, `USER node`, `EXPOSE 8108`). Built and run locally as proof (see Verification).

## Task Commits

Each task was committed atomically:

1. **Task 1: scaffold + pinned deps + gated Dockerfile + verifyBoardToken + rejection matrix** — `908204f` (feat)
2. **Task 2: server onAuthenticate wiring + bridge-authenticated persistence + DoS ceilings** — `9d2603f` (feat)

**Plan metadata:** the SUMMARY commit is the final commit of this plan.

## Files Created/Modified

- `apps/hocuspocus/.gitignore` — created; `node_modules/`.
- `apps/hocuspocus/package.json` — created; `"type":"module"`, `"private":true`, `engines node>=22`, caret-free exact pins, start/test scripts.
- `apps/hocuspocus/package-lock.json` — created; committed so `npm ci` is reproducible in the Dockerfile.
- `apps/hocuspocus/Dockerfile` — created; three-stage node:22-alpine, `runtime` last, `gate` for 26-07.
- `apps/hocuspocus/src/auth.mjs` — created; `verifyBoardToken` + `BoardAuthError` + `DENY` — the security core, mirrors `board_helpers.py:verify_board_token`.
- `apps/hocuspocus/src/bridge.mjs` — created; cached short-lived bridge JWT minter (`getBridgeToken`, `bridgeAuthHeader`).
- `apps/hocuspocus/src/persistence.mjs` — created; `makeDatabaseExtension` with the UUIDv4 documentName guard.
- `apps/hocuspocus/src/server.mjs` — created; the Server, onAuthenticate, DoS ceilings, boot guard, SIGTERM flush.
- `apps/hocuspocus/tests/test_auth.mjs` — created; the non-mocked 10-case rejection matrix.
- `apps/hocuspocus/tests/run_tests.mjs` — created; the plain-node test driver.

## Output artefacts requested by the plan

- **`jose` version + license:** `6.2.4`, **MIT** (`npm view jose version license`). Pinned exactly (caret-free) in `package.json`.
- **`documentName` UUID regex** (in `persistence.mjs`, both callbacks): `/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/` — canonical lowercase UUIDv4 (version nibble 4, variant `[89ab]`). Verified to accept a real board id and reject `../../etc/passwd`, `abc`, `<uuid>/../x`, a non-v4 uuid, empty, and an invalid-variant uuid.
- **Exact `DENY` string:** `"Not authorized!"`.
- **Observed container RAM:** **~27.6 MiB idle** (`docker stats` on the locally-built `runtime` image, arm64 Docker Desktop, no connections/docs loaded — `27.64MiB / 15.3GiB`). Comfortably under a `mem_limit: 256m`; this is the number 26-06 needs to size the compose block. (Research assumption A1 had flagged `mem_limit: 256m` as UNVERIFIED — now measured, though under load it will rise with the number of open Y.Docs.)
- **`npm test` count:** **10 tests, 10 pass, 0 fail** (1/1 test file).

## Decisions Made

- **`bridgeAuthHeader()` is async** (jose's `SignJWT.sign` is async), so `persistence.mjs` uses `await bridgeAuthHeader()` rather than the plan's synchronous `bridgeAuthHeader()` snippet. Behaviour is identical; the acceptance grep (`grep -q 'bridgeAuthHeader'`) still matches. Logged as a Rule-1 adaptation below.
- **Bridge token caching** with a 30 s re-mint skew rather than one-per-request, per the plan — keeps CPU and clock-skew risk low.
- **Unauthenticated queue caps left at Hocuspocus defaults** (5 MiB / 1000 messages) with a comment saying so, to avoid drift against upstream — the plan explicitly asked for this.
- **Test-runner choice:** `run_tests.mjs` invokes `node --test <file>` per test file (prints the pass count and exits non-zero on failure), matching the repo's plain-node convention while satisfying "the driver must print a pass count; a zero-test run is a FAILURE".

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `bridgeAuthHeader()` made async and awaited in persistence**
- **Found during:** Task 2 (bridge + persistence)
- **Issue:** The plan's `persistence.mjs` snippet calls `bridgeAuthHeader()` synchronously, but minting an HS256 JWT with jose (`SignJWT.sign`) is inherently async. A synchronous header helper could only return a stale/undefined token, silently sending an unauthenticated (or wrong) request to memory-api.
- **Fix:** `bridge.mjs` exports an async `bridgeAuthHeader()`; both `fetch` and `store` in `persistence.mjs` `await` it (spread into the headers object). No behavioural change to the contract — still `Authorization: Bearer <bridge-jwt>`.
- **Files modified:** `apps/hocuspocus/src/bridge.mjs`, `apps/hocuspocus/src/persistence.mjs`
- **Verification:** `node --check` passes; `makeDatabaseExtension` imports and constructs a real `Database`; the Server constructs with the extension wired in.
- **Committed in:** `9d2603f` (Task 2 commit)

**2. [Rule 1 - Bug] Rejection-matrix "identical message" test rebuilt to await each case in turn**
- **Found during:** Task 1 (test authoring — first `npm test` run)
- **Issue:** The initial "every rejection produces the identical DENY message" test built an array of already-invoked (pending-rejecting) promises, which node's test runner flagged as "asynchronous activity after the test ended" (`PromiseRejectionHandledWarning`), failing the test even though the assertion logic was correct.
- **Fix:** Restructured the case list as thunks and awaited each inside a `try/catch` one at a time, so no rejection escapes its `await`. Assertion (all messages collapse to the single `DENY`) unchanged.
- **Files modified:** `apps/hocuspocus/tests/test_auth.mjs`
- **Verification:** `npm test` -> 10/10 pass.
- **Committed in:** `908204f` (Task 1 commit)

---

**Total deviations:** 2 auto-fixed (2 Rule-1 bugs — one API-shape correctness, one test-harness correctness).
**Impact on plan:** Both are correctness fixes on the executor's own code, not scope changes. The public contract (claim shape, endpoint paths, octet-stream, DENY message, DoS ceilings) is exactly as specified.

## Issues Encountered

- **Sandbox refuses compound bash lines** (subshells, `unset`, `&`/`sleep`/`kill` chains) inside the worktree. Split every such check into plain single commands (empty-secret boot guard run as `BRIDGE_SHARED_SECRET= node src/server.mjs`; container smoke run via `docker run -d` + separate `docker logs`/`docker stop`).
- **LF->CRLF warnings** on `git add` (Windows). Cosmetic; no `.gitattributes` change made (out of scope, and no other repo file carries one for these paths).

## Verification (real output)

- `cd apps/hocuspocus && npm ci && npm test` -> **10 tests, 10 pass, 0 fail** (1/1 test file). Non-mocked, against the real `verifyBoardToken`, with 3 `SignJWT` mint sites and 0 mock/stub/fake references.
- `node --check` passes for `server.mjs`, `persistence.mjs`, `bridge.mjs`; `makeDatabaseExtension` is importable and returns a real `Database`; `new Server({...})` constructs with the exact option shape (proves no typo'd option names in Hocuspocus 4.4.0).
- **Empty-secret boot guard:** `BRIDGE_SHARED_SECRET= node src/server.mjs` prints the English FATAL line and exits **1**.
- **UUIDv4 guard:** accepts a canonical board id; rejects `../../etc/passwd`, `abc`, `<uuid>/../x`, a non-v4 uuid, empty, and an invalid-variant uuid.
- **Acceptance greps (all pass):** four caret-free exact pins + `engines ">=22"`; Dockerfile `AS deps`/`AS gate`/`AS runtime` with `runtime` LAST, `npm ci --omit=dev` + `USER node`; `auth.mjs` has 0 `node:crypto`/`createHmac`, `jwtVerify` present, `algorithms: ["HS256"]` pinned, exactly one non-comment `payload.board_id !== documentName`; `server.mjs` has `onAuthenticate`/`verifyBoardToken`/`connection.readOnly`, `maxPendingDocuments: 1`, `maxPayload`, `timeout: 30000`, `process.exit(1)`, `server.destroy`, `SIGTERM`; 0 DB-credential references across `src/*.mjs`; `persistence.mjs` has `internal/boards`, `application/octet-stream`, `bridgeAuthHeader`, `404`, `Uint8Array`, 0 JSON/base64; `bridge.mjs` has `scope: "bridge"`; 0 token-logging paths.
- **Docker (local proof, arm64):** `docker build` of the default `runtime` stage succeeds; the container boots, logs `board_server_listening` on 8108, uses **~27.6 MiB** idle, and on `docker stop` (SIGTERM) logs `board_server_shutdown` (graceful `server.destroy()` flush). `docker build --target gate` succeeds and `docker run` of the gate image runs the test suite **10/10 pass** inside the container. All proof images/containers removed afterward.
- **Deferred to 26-07 (the non-mocked live gate):** two-client Yjs convergence against the real running server; the rejection matrix driven by tokens minted by the REAL Python `mint_board_token`; the real bridge round-trip persistence against a testcontainers Postgres; the compose/nginx profile wiring + `verify-phase16.sh` staying green.

## Known Stubs

None. `persistence.mjs` calls the real memory-api internal doc endpoints (26-02, already shipped); `auth.mjs`/`server.mjs` verify real tokens; there are no hardcoded empty responses or placeholder data. The board SPA (26-04) and the extension "Open board" action (26-05) are the client consumers of this service and are out of scope for this plan.

## Threat Flags

None. All surface introduced here (the WebSocket auth boundary, the bridge-authenticated persistence path, the DoS ceilings, the UUIDv4 injection guard, the no-oracle DENY) is already enumerated in the plan's `<threat_model>` (T-26-13 … T-26-22) and mitigated as specified — cross-team replay (T-26-13, `board_id === documentName`), algorithm confusion (T-26-14, pinned HS256 + boot guard), the oracle (T-26-16, single DENY), enumeration (T-26-17, `maxPendingDocuments: 1`), OOM (T-26-18, bounded `maxPayload`), document-name injection (T-26-19, UUIDv4 guard), no DB credentials (T-26-20), byte-exact round-trip + retry + shutdown flush (T-26-21), and no token in logs (T-26-22).

## Next Phase Readiness

- **26-06 (compose / nginx / verify-phase16):** the service is `xbrain-hocuspocus`, `EXPOSE 8108`, built from `apps/hocuspocus/Dockerfile` (default `runtime` stage, amd64 via CI — do NOT ship a locally-built arm64 image). Env it needs: `MEMORY_API_URL` (default `http://memory-api:8000`), `BRIDGE_SHARED_SECRET` (boot-fatal if empty), `HOCUSPOCUS_PORT` (default 8108), `BOARD_MAX_DOC_BYTES` (default 16777216). Measured idle RAM ~27.6 MiB -> `mem_limit: 256m` is safe. Use a TCP-connect healthcheck (a WebSocket upgrade endpoint is not an HTTP 200), mirroring the mcp-brain block. Both `xbrain-hocuspocus` and `xbrain-board` must join `OPT_IN_CONTAINERS` so the 10-service core assertion stays green.
- **26-07 (live gate):** build `--target gate` for the two-client convergence client image (`@hocuspocus/provider@4.4.0` is present). Drive the rejection matrix with tokens minted by the real Python `mint_board_token`, and exercise the real bridge round-trip persistence against a testcontainers Postgres.
- **Concern:** none blocking. The auth contract is frozen against 26-02; any future change to the claim keys, the algorithm, or the secret must touch `auth.mjs` and `board_helpers.py` together (both files carry the mirror comment).

## Self-Check: PASSED

- All 10 code/test files under `apps/hocuspocus/` + this SUMMARY exist on disk (verified via Glob).
- Task commits `908204f` (Task 1) and `9d2603f` (Task 2) present in `git log` (verified).
- `npm ci && npm test` -> 10/10; runtime + gate Docker images built and run clean locally.

---
*Phase: 26-collaborative-board*
*Completed: 2026-07-24*
