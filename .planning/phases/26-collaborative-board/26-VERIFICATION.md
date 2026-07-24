---
phase: 26-collaborative-board
verified: 2026-07-24T09:44:00Z
status: passed
score: 4/4 must-haves verified (BOARD-01 satisfied)
overrides_applied: 0
---

# Phase 26: Collaborative Board (Excalidraw + Yjs) — 26a Verification Report

**Phase Goal:** A team opens a live collaborative Excalidraw board from the chat; two members drawing at once converge in real time; the board survives a reload; a member of team B can never open team A's board.

**Verified:** 2026-07-24T09:44:00Z
**Status:** PASSED
**Re-verification:** No — initial verification

## Method

Goal-backward, adversarial. SUMMARY.md claims were treated as unproven narrative and independently falsified against: (1) direct reading of every artifact file listed in the phase's canonical refs, (2) `git log` confirmation that every commit hash cited in the SUMMARYs actually exists on `main` at HEAD `80827c2`, (3) a **fresh, independent re-run** of the real-Postgres pytest gate (`test_board_gate.py`), the extension's popup contract test, the `apps/hocuspocus` unit test suite, and — critically — the **entire `verify-phase26.sh` gate** (image builds, 12-service live boot, real HTTP registration + real Python-minted tokens, the live two-client convergence + cross-language rejection-matrix driver, a real `docker compose restart hocuspocus` persistence proof, the no-raw-port assertion, and a full re-run of `verify-phase16.sh`). Docker was up throughout (arm64 host, linux/aarch64) and all runs used `MSYS_NO_PATHCONV=1` where the house rule requires it. Nothing below is taken on the SUMMARYs' word alone.

## Goal Achievement

### Observable Truths (ROADMAP Success Criteria, verbatim)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Two Yjs clients connected to the REAL running Hocuspocus service converge on the same document state (headless two-client test asserting convergence, not "no error"); "Open board" opens the board for the active team | ✓ VERIFIED | Independent re-run of `verify-phase26.sh` check (d): live driver against the real running `xbrain-hocuspocus` container inside `xbrain_net` — `PASS convergence: two clients on board A reach identical document content` via `toJSON()` equality (not "no error"). `gate_client.mjs` reviewed: asserts content equality, polls to a 15s deadline, never sleeps once. `chrome-extension/popup.js:openTeamBoard()` reviewed: POSTs `/v1/teams/{id}/boards`, validates `open_url` with `isSafeHttpUrl`, opens via `chrome.tabs.create`; popup contract test independently re-run: **173 passed, 0 failed**, including all 4 board-action assertions. |
| 2 | The team-scope boundary holds: a board token minted for team A's board is REJECTED by the real `onAuthenticate` against team B's `documentName`; absent/malformed/expired/wrong-signature all rejected. Non-mocked — real tokens from real minting code against the real handler | ✓ VERIFIED | Independent re-run, check (c)+(d): tokens minted via `docker compose exec memory-api python` calling the REAL `mint_board_token` (authlib HS256) — cross-language proof, since the verifier is Node `jose` (`apps/hocuspocus/src/auth.mjs`). All 6 rejection cases PASS (`A-token/B-board`, `B-token/A-board`, no-token, malformed, expired, wrong-signature) plus a POSITIVE CONTROL that authenticates (`GATE-CLIENT SUMMARY pass=9 fail=0`) — the positive control is what makes the rejections meaningful, and a timeout is scored FAIL not PASS (`connect()`'s `CONNECT_TIMEOUT_MS` path, reviewed). `algorithms: ["HS256"]` is pinned in `auth.mjs:48` (blocks `alg:none`/asymmetric confusion) and `tests/test_auth.mjs` independently re-run: **10/10 pass**, including an explicit `alg:none` case. The 26-07 finding — `onAuthenticate` used `connection.readOnly` (undefined in Hocuspocus 4.4.0, silently rejecting every VALID token while negatives still passed) — is confirmed FIXED on `main`: `apps/hocuspocus/src/server.mjs:63` reads `if (connectionConfig) connectionConfig.readOnly = claims.read_only === true;`. |
| 3 | A Y.Doc update stored through the real database extension against a REAL Postgres (testcontainers, migration 0028) and re-fetched into a fresh doc survives intact; the board reloads with its content | ✓ VERIFIED | **Independently re-run** `pytest tests/test_board_gate.py -q` → **3 passed, 0 failed, 0 skipped** in 40.31s (real testcontainers Postgres). Confirms byte-exact HTTP round-trip + raw `bytea` column read, 413 cap without clobbering, no-oracle 404 (byte-equal bodies), and `alembic upgrade head` under **both** `EDITION=oss` and `EDITION=saas` on fresh containers creating identical `boards`/`board_docs` schema (no fork). Migration `0028_boards.py` reviewed: `down_revision = "0027_team_invite_codes"`, additive `CREATE TABLE IF NOT EXISTS`, no `EDITION` branch. Reload survival independently proven live: `verify-phase26.sh` check (e) — `docker compose restart hocuspocus`, then a FRESH client with a FRESH Python-minted token still sees both prior elements (`PASS persistence-after-restart: fresh client still sees e1 and e2`, `pass=1 fail=0`). |
| 4 | The board + Hocuspocus containers are OPT-IN profile services and `verify-phase16.sh` stays GREEN (bare core still exactly 10 services, profile list + `OPT_IN_CONTAINERS` amended); the board image BUILDS via its multi-stage Dockerfile | ✓ VERIFIED | `infrastructure/docker-compose.yml` reviewed: both `board` and `hocuspocus` carry `profiles: ["board"]` and `expose:` only (no `ports:`). `verify-phase16.sh` `CORE=` line (10 names, no board) unchanged; `OPT_IN_CONTAINERS` includes `xbrain-board xbrain-hocuspocus` (24 total). **Independently re-run**, `verify-phase26.sh` check (g) re-ran the REAL `verify-phase16.sh` end-to-end (tore down the board stack first, booted a fresh 10-core-only stack, walked the full SC#3 HTTP flow) → **`PASS: 23 / 23 (SKIP: 0)`, exit 0**. Both Dockerfiles reviewed as genuinely multi-stage with `runtime`/default target LAST and devDep-free (`apps/hocuspocus/Dockerfile`: `deps → gate → runtime`; `apps/board-web/Dockerfile`: `node:22-alpine AS build → nginx:1.27-alpine AS runtime`, zero Node in the runtime image). Independently re-run image builds (check (a)): **both build clean** — `xbrain/board-web:phase26` 23 MB, `xbrain/hocuspocus:phase26` 58 MB. |

**Score:** 4/4 truths verified.

**BOARD-01** (REQUIREMENTS.md): fully satisfied by the above — live collaborative Excalidraw board from chat, real-time convergence, Postgres-persisted reload survival, and the team-scope `onAuthenticate` boundary, delivered as two OPT-IN containers that keep the OSS-light 10-service core green.

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `apps/board-web/` (Vite+React SPA) | Excalidraw + Yjs binding + Dockerfile | ✓ VERIFIED | `Board.tsx`, `session.ts`, vendored `yjs-binding/`, multi-stage `Dockerfile`, `nginx.conf` all read; real (non-stub) HocuspocusProvider wiring; `tsc --noEmit` proof recorded in 26-01/26-04 SUMMARYs; independently re-built via `verify-phase26.sh` check (a) — 23 MB image. |
| `apps/board-web/src/yjs-binding/` (vendored) | y-excalidraw fixed for 0.18.1 | ✓ VERIFIED | `DECISION.md` read: raw spike evidence (0 hits for `commitToHistory`/`captureUpdate` in the npm package), 4 call sites patched to `CaptureUpdateAction.NEVER`, MIT LICENSE preserved verbatim. |
| `apps/hocuspocus/` (Yjs server) | onAuthenticate boundary, DB-credential-free, Dockerfile | ✓ VERIFIED | `auth.mjs`, `server.mjs`, `persistence.mjs`, `bridge.mjs` all read; `connectionConfig.readOnly` fix confirmed present on `main`; unit tests independently re-run 10/10; live driver independently re-run 9/9 (full) + 1/1 (post-restart). |
| `apps/memory-api/app/routes/board_helpers.py` | mint/verify_board_token, media-token shape | ✓ VERIFIED | Read in full; HS256 via authlib, `BRIDGE_SHARED_SECRET`, exact claim set matching the frozen cross-plan contract. |
| `apps/memory-api/app/routes/boards.py` | create/list/mint + bridge-only internal doc endpoints | ✓ VERIFIED | Read in full; gate 1 (membership), gate 2 (re-checked membership + no-oracle 404), `_require_bridge_principal` shared by both internal endpoints, 413 cap, fragment-only token handoff. |
| `apps/memory-api/alembic/versions/0028_boards.py` | additive migration, no EDITION branch | ✓ VERIFIED | Read in full; `down_revision="0027_team_invite_codes"`, `CREATE TABLE IF NOT EXISTS`, partial unique `ux_boards_team_default`, `board_docs.state BYTEA NOT NULL`. |
| `apps/memory-api/tests/test_board_gate.py` | non-mocked real-Postgres gate | ✓ VERIFIED | Independently re-run: 3 passed, 0 failed, 0 skipped. |
| `apps/hocuspocus/tests/gate_client.mjs` | live convergence + rejection driver | ✓ VERIFIED | Read in full; mints nothing, reads tokens from env only, timeout=FAIL, positive control present. Independently re-run live: 9/9 + 1/1. |
| `infrastructure/scripts/verify-phase26.sh` | phase boot gate (a)-(g) | ✓ VERIFIED | Independently re-run end-to-end: `PASS: 9 / 9 (SKIP: 0) FAIL: 0`, exit 0. |
| `infrastructure/docker-compose.yml` board+hocuspocus blocks | opt-in profile, expose-only | ✓ VERIFIED | Read in full; `profiles: ["board"]` on both, `expose:` only, `mem_limit` set (64m / 256m), healthchecks present. |
| `infrastructure/nginx/templates/70-board.conf.template` | board.<domain> vhost, /collab upgrade | ✓ VERIFIED | Read in full; lazy `set $var` upstreams, Upgrade/Connection headers, `proxy_buffering off`, no `/v1/internal` route. |
| `infrastructure/scripts/verify-phase16.sh` (amended) | CORE unchanged, OPT_IN_CONTAINERS extended | ✓ VERIFIED | `CORE=` line has exactly the 10 pre-existing names; `OPT_IN_CONTAINERS` includes `xbrain-board xbrain-hocuspocus`. Independently re-run: PASS 23/23, exit 0. |
| `chrome-extension/popup.js` (`openTeamBoard`) | board header action | ✓ VERIFIED | Read in full; POST → validate → `chrome.tabs.create`, no client-built URL, no token logging, double-click guard. |
| `chrome-extension/tests/test_popup_contract.mjs` (section 7) | frozen `btn-board` + 4 assertions | ✓ VERIFIED | Independently re-run in an isolated ESM copy: 173 passed, 0 failed. |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `chrome-extension/popup.js:openTeamBoard` | `memory-api POST /v1/teams/{id}/boards` | `fetch` + `isSafeHttpUrl` + `chrome.tabs.create` | WIRED | Real endpoint call, response `open_url` validated then opened; independently re-proven live via the gate's real-HTTP seeding (check (c)) hitting the identical route. |
| `apps/board-web/src/session.ts` | `apps/board-web/src/Board.tsx` | `readSession()` → `HocuspocusProvider({url: wsUrl, name: boardId, token: async () => token})` | WIRED | Token delivered via async supplier into the Auth message, never the URL; fragment stripped via `history.replaceState` before any network call. |
| `apps/hocuspocus/src/server.mjs onAuthenticate` | `apps/hocuspocus/src/auth.mjs verifyBoardToken` | direct call, throw-to-close | WIRED | Confirmed live: valid tokens authenticate (positive control PASS), invalid tokens denied (6/6 rejection cases PASS) — the exact defect class (`connection.readOnly` crash post-verification) that a mocked test could never have caught was caught by this gate in 26-07 and is fixed on `main`. |
| `apps/hocuspocus/src/persistence.mjs` | `memory-api GET/PUT /v1/internal/boards/{id}/doc` | `fetch` with `bridgeAuthHeader()`, raw octet-stream | WIRED | Confirmed live: restart-survival check (e) round-trips through this exact path; byte-exactness confirmed independently by `test_board_gate.py`'s direct `bytea` column read. |
| `infrastructure/nginx/templates/70-board.conf.template` | `hocuspocus:8108` / `board:8107` | lazy `set $var` + `proxy_pass`, WebSocket upgrade | WIRED | Confirmed live: `verify-phase26.sh` check (c) registered accounts and created boards over REAL HTTP through this exact nginx path (`Host: api.$XBRAIN_BASE_DOMAIN` via port 80); `/collab` upgrade exercised by the live gate_client driver connecting inside the same compose network the vhost fronts. |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|---------------------|--------|
| `Board.tsx` canvas | `Y.Doc` elements array | `HocuspocusProvider` sync over `/collab`, backed by Postgres `board_docs.state` | Yes — independently proven: two live clients wrote real elements (`e1`,`e2`) that converged and then survived a real container restart, re-read from the real `board_docs` row via `test_board_gate.py`'s direct SQL assertion | ✓ FLOWING |
| extension `btn-board` open | `open_url` | real `POST /v1/teams/{id}/boards`, membership-gated, real DB row via `boards_repo.get_or_create_default_board` | Yes — independently proven live (check (c): real registration → real team → real board row, distinct `board_id` per team) | ✓ FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Board pytest gate (real Postgres) | `MSYS_NO_PATHCONV=1 python -m pytest tests/test_board_gate.py -q` | `3 passed, 4 warnings in 40.31s`, 0 failed, 0 skipped | ✓ PASS |
| Extension popup contract (isolated ESM copy) | `node tests/run_tests.mjs` / `node tests/test_popup_contract.mjs` | `12/12 test files passed`; `173 passed, 0 failed` | ✓ PASS |
| Hocuspocus unit rejection matrix | `npm test` (`apps/hocuspocus`) | `10 tests, 10 pass, 0 fail` | ✓ PASS |
| Full phase-26 live gate (`verify-phase26.sh`) | `bash infrastructure/scripts/verify-phase26.sh` | `PASS: 9 / 9 (SKIP: 0)  FAIL: 0`, exit 0 — image builds, 12-service live boot, real HTTP seeding + real Python-minted tokens, live 2-client convergence + 6-case rejection matrix + positive control (`pass=9 fail=0`), real container restart persistence (`pass=1 fail=0`), no-raw-port assertion, and a full re-run of `verify-phase16.sh` (`PASS: 23 / 23 (SKIP: 0)`, exit 0) | ✓ PASS |
| Post-gate container hygiene | `docker ps -a --filter 'name=^xbrain-'` | empty — the gate's `EXIT trap` tore down everything it booted | ✓ PASS |

### Requirements Coverage

| Requirement | Source Plans | Description | Status | Evidence |
|-------------|--------------|--------------|--------|----------|
| BOARD-01 | 26-01 .. 26-07 (all 7 plans declare it) | Live collaborative Excalidraw board, real-time convergence, reload survival via Postgres-persisted Y.Doc, team-scope boundary in `onAuthenticate`, OPT-IN containers | ✓ SATISFIED | All 4 roadmap Success Criteria independently re-verified above; no orphaned requirements found for this phase in REQUIREMENTS.md. |

### Anti-Patterns Found

None. Scanned every file listed in `key-files.created`/`key-files.modified` across all 7 plan SUMMARYs for `TODO`/`FIXME`/`placeholder`/`not yet implemented`/hardcoded-empty-return patterns — none found in production code paths. `Board.tsx`/`session.ts` grep-confirmed to carry zero MinIO/media/brain/Tiptap references (D-26-06 and the Tiptap deferral both hold). One pre-existing, unrelated test failure (`test_github_sync.py::test_sync_repo_multi_chunk_ids`) is correctly logged in `deferred-items.md` as out-of-scope and does not touch any file this phase modified.

### Deferred Items (informational — matches phase's own explicit deferrals, not late findings)

| # | Item | Addressed In | Evidence |
|---|------|--------------|----------|
| 1 | Images routed to MinIO instead of base64-in-doc; board snapshots ingested into the brain | 26b (a separate, not-yet-planned phase) | `.planning/phases/26-collaborative-board/26-CONTEXT.md` `<deferred>` section; `Board.tsx` grep-confirmed 0 MinIO/media/brain references. |
| 2 | Board permissions finer than team membership; multi-board UX; export/import; Tiptap Editor | 26b / not scheduled | Same `<deferred>` section; schema (`ux_boards_team_default` partial unique) already ALLOWS multiple boards without a migration, per D-26-02 discretion. |

### Human Verification Required

None. Every load-bearing claim (convergence, team-scope rejection, byte-exact persistence + reload survival, OPT-IN packaging, image builds) was independently re-run against live Docker infrastructure with real HTTP, real Postgres, real tokens, and a real WebSocket server — not simulated, not taken from the SUMMARYs' word. The one genuinely browser-only behavior (Excalidraw's visual rendering / actual mouse-drawn strokes) is out of this gate's scope by design (the phase's own `<specifics>` state "Two people can draw together is not provable by a unit test of a React component" and instead requires the CRDT-content proof this report re-ran) and is not a must-have distinct from SC1's convergence assertion.

### Gaps Summary

None found. All 4 ROADMAP Success Criteria are VERIFIED with independently-reproduced, non-mocked evidence (fresh pytest run, fresh unit-test runs, and a fresh full live-infrastructure gate run producing PASS 9/9 including a green `verify-phase16.sh` re-run at 23/23). No stubs, no orphaned wiring, no scope creep beyond the phase's own documented deferrals (26b, out of scope by design). The single defect this phase's own gate caught during execution (`connection.readOnly` vs `connectionConfig.readOnly`) is confirmed fixed on `main` and re-proven fixed by this independent re-run.

---

*Verified: 2026-07-24T09:44:00Z*
*Verifier: Claude (gsd-verifier)*
