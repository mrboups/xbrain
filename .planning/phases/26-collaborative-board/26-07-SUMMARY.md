---
phase: 26-collaborative-board
plan: 07
subsystem: testing
tags: [pytest, testcontainers, yjs, hocuspocus, jose, authlib, jwt, docker-compose, board, team-scope, gate]

# Dependency graph
requires:
  - phase: 26-collaborative-board (plan 26-02)
    provides: "mint_board_token + migration 0028 (boards + board_docs bytea) + bridge-only internal doc GET/PUT octet-stream + no-oracle 404"
  - phase: 26-collaborative-board (plan 26-03)
    provides: "apps/hocuspocus onAuthenticate (verifyBoardToken, jose HS256) + the `gate` Dockerfile target with @hocuspocus/provider + Postgres persistence via the database extension"
  - phase: 26-collaborative-board (plan 26-06)
    provides: "the opt-in `board` compose profile (expose-only, mem-capped), the board.<domain> vhost, and the amended verify-phase16/17 gates"
provides:
  - "apps/memory-api/tests/test_board_gate.py — real-Postgres gate: byte-exact Y.Doc round-trip (HTTP + raw bytea), no-oracle 404, 413 cap, migration 0028 under EDITION=oss AND saas"
  - "apps/hocuspocus/tests/gate_client.mjs — live two-client convergence (toJSON equality) + cross-language rejection matrix + positive control + timeout=FAIL + GATE_MODE=verify-persisted"
  - "infrastructure/scripts/verify-phase26.sh + make verify-phase26 — boots the board profile, mints REAL Python tokens, runs the live driver in-network, proves restart-survival + no-raw-port, and re-runs verify-phase16 green"
  - "fix: server.mjs onAuthenticate uses connectionConfig.readOnly (Hocuspocus 4.4.0 has no `connection`) — the bug the live gate existed to catch"
affects: [26b (MinIO image routing + brain ingestion), any future board plan]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "The gate lesson made executable: convergence asserted on CONTENT (toJSON equality, polled to a deadline), NOT on 'no error'; a POSITIVE CONTROL must authenticate before any rejection counts; a TIMEOUT is scored FAIL not PASS; the driver mints NOTHING and exits non-zero on a zero-pass run"
    - "Cross-language auth proof: Python mint_board_token (authlib HS256) -> Node verifyBoardToken (jose HS256) against the REAL onAuthenticate; a mocked verifier or a Node-minted token cannot satisfy it"
    - "Byte-exact bytea round-trip asserted TWICE: over HTTP (response.content == PUT bytes) AND directly in the board_docs.state column, using a fixed high-byte payload (NUL + 0xFF/0xFE) so any accidental text/base64 round-trip corrupts it visibly"
    - "SKIP=FAIL structural: a boot failure records every downstream check with `ko`, never `skip`; a healthy run reports SKIP: 0"
    - "container_name: is GLOBAL — the gate refuses to boot if any xbrain-* container exists, and a single EXIT trap always tears down what it booted"

key-files:
  created:
    - apps/memory-api/tests/test_board_gate.py
    - apps/hocuspocus/tests/gate_client.mjs
    - infrastructure/scripts/verify-phase26.sh
  modified:
    - apps/hocuspocus/src/server.mjs
    - Makefile

key-decisions:
  - "The valid-token failure the live gate surfaced was a REAL shipped bug (26-03 server.mjs), not a gate defect — fixed under Rule 1 rather than weakened around"
  - "pycrdt is not installed here, so the byte-exact proof uses a fixed high-byte string (documented in-file) instead of a real pycrdt Y.Doc update — the property proven (byte-identical round-trip) is unchanged"
  - "Seeding walks the REAL HTTP route (register + my-team + create board through nginx); ONLY the four tokens are minted via `docker compose exec memory-api python` calling the production mint_board_token"

patterns-established:
  - "A live headless Node driver against the running server process is the honest proof of CRDT convergence; the provider (4.4.0) has no BroadcastChannel path, so convergence can ONLY happen through the server"
  - "Every MSYS_NO_PATHCONV=1 sits ONLY on a command carrying an in-container path (docker run --entrypoint ... tests/*.mjs, docker build <ctx>); never on a `docker compose ... --env-file/-f` host-path command"

requirements-completed: [BOARD-01]

# Metrics
duration: 50min
completed: 2026-07-24
---

# Phase 26 Plan 07: Non-Mocked Collaborative-Board Gate Summary

**A real-Postgres pytest gate (byte-exact Y.Doc round-trip + migration 0028 under oss AND saas + 413/404), a live headless Node driver proving two-client convergence by `toJSON()` equality plus a cross-language (Python-mint / Node-verify) team-scope rejection matrix with a positive control and timeout=FAIL, and `verify-phase26.sh` (`make verify-phase26`) that boots the real board profile, proves restart-survival + no-raw-port, and keeps verify-phase16 green — which caught a real shipped `onAuthenticate` bug that a mocked verifier never could.**

## Performance

- **Duration:** ~50 min
- **Started:** 2026-07-24T08:33Z (~10:33 +02:00, base commit 2f4e94d)
- **Completed:** 2026-07-24T09:22Z (~11:22 +02:00, Task 3 commit + summary)
- **Tasks:** 3 (+ 1 Rule-1 fix the gate surfaced)
- **Files modified:** 5 (3 created, 2 modified)

## Accomplishments

- **Task 1 — the real-Postgres pytest gate** (`test_board_gate.py`): a comprehensive non-mocked HTTP flow (create/list membership-gated, mint no-oracle 404 proven byte-equal, the frozen token claim set, bridge-only internal doc GET/PUT, byte-exact bytea round-trip over HTTP AND in the raw column, 413 cap that does not clobber, unknown-board 404 with no implicit insert) plus a parametrized `test_migration_0028_boards_forward_only[oss,saas]` that upgrades a FRESH container per edition and probes `boards` + `board_docs.state bytea NOT NULL` + the partial-unique `ux_boards_team_default` (UNIQUE ... WHERE) identically under both editions.
- **Task 2 — the live client driver** (`gate_client.mjs`): a standalone driver (not `node:test`) that, given REAL externally-minted tokens, proves against the running server: two clients converge on identical `toJSON()` content (polled to a deadline), the team-scope boundary holds in BOTH directions, absent/malformed/expired/wrong-signature are refused, a positive control authenticates, a timeout is a FAIL, and a fresh client after the debounce window still sees the prior edits. It mints nothing and exits non-zero on a zero-pass run.
- **Task 3 — `verify-phase26.sh` + `make verify-phase26`**: boots the opt-in board profile from a zero-key oss-init env, mints the four tokens with the REAL Python `mint_board_token` via `docker compose exec`, runs the driver inside `xbrain_net`, restarts hocuspocus and proves the content survived, asserts neither container publishes a host port, and finishes by re-running `verify-phase16.sh` green.
- **The gate did its job**: it caught a real shipped `onAuthenticate` bug (26-03) that every unit test missed — see Deviations.

## Task Commits

Each task was committed atomically:

1. **Task 1: real-Postgres board pytest gate** — `9fe5825` (test)
2. **Task 2: live two-client convergence + rejection-matrix driver** — `a287877` (feat)
3. **Rule-1 fix: onAuthenticate connectionConfig.readOnly** — `a0a9673` (fix)
4. **Task 3: verify-phase26.sh + make verify-phase26** — `8fbfc90` (test)

**Plan metadata:** the SUMMARY commit is the final commit of this plan.

## Real gate output (recorded, not paraphrased)

**`verify-phase26.sh` — full summary block:**

```
=== Summary ===
PASS: 9 / 9  (SKIP: 0)
FAIL: 0
```

Per-check: (a) both images built; (b) all 12 services healthy (10 core + board + hocuspocus); (c) two teams + a board each seeded over REAL HTTP, four tokens minted via the REAL Python `mint_board_token`, distinct + bound to different `board_id` claims; (d) live driver `GATE-CLIENT SUMMARY pass=9 fail=0`; (e) post-restart read `GATE-CLIENT SUMMARY pass=1 fail=0`; (f) no host port bindings on either container; (g) verify-phase16 exit 0.

**pytest (`tests/test_board_gate.py -q`):** `3 passed, 4 warnings in 30.61s` — **0 failed, 0 skipped** (Docker up). The three: `test_board_http_gate`, `test_migration_0028_boards_forward_only[oss]`, `test_migration_0028_boards_forward_only[saas]`.

**Live driver (`GATE-CLIENT SUMMARY`):** `pass=9 fail=0` (full mode) — convergence + team-scope A/B + team-scope B/A + no-token + malformed + expired + wrong-signature + positive control + persistence handoff. Post-restart (`GATE_MODE=verify-persisted`): `pass=1 fail=0`.

**`verify-phase16.sh` summary line (re-run inside check (g)):** `PASS: 23 / 23  (SKIP: 0)` — exit 0, FAIL 0.

**Image sizes** (check (a)): `xbrain/board-web:phase26` = **23 MB**; `xbrain/hocuspocus:phase26` = **58 MB**.

**Measured hocuspocus RSS** (research assumption A1 / open question 6): **~20.32 MiB / 256 MiB** limit, sampled by `docker stats --no-stream` immediately after the live two-client run (document loaded). This confirms the ~27.6 MiB idle figure from 26-03 and the `mem_limit: 256m` (26-06) as comfortably safe under two concurrent clients on a single board. (Exact "during two concurrent connections" sampling is timing-sensitive; this reading is document-loaded, connections just closed.)

**Every `MSYS_NO_PATHCONV=1` occurrence in verify-phase26.sh, with justification** (the house host-path rule: it belongs ONLY on a command carrying an IN-CONTAINER path, NEVER on a `--env-file`/`-f` host-path command):

| Location | Command | Why it is correct |
|----------|---------|-------------------|
| check (d) gate image build | `docker build --target gate -t $GATE_IMAGE apps/hocuspocus` | `docker build <ctx>` — no host-to-container mount to convert; prevents Git-Bash mangling the target/context. No `-f`/`--env-file`. |
| check (d) live driver | `docker run --network xbrain_net ... --entrypoint node $GATE_IMAGE tests/gate_client.mjs` | carries an IN-CONTAINER path (`tests/gate_client.mjs`) and a `ws://` URL; no `-f`/`--env-file`. |
| check (e) post-restart read | `docker run --network xbrain_net ... --entrypoint node $GATE_IMAGE tests/gate_client.mjs` (GATE_MODE=verify-persisted) | same as (d): in-container path + `ws://` URL; no host path. |

All `docker compose ...` commands (up/build/exec/restart/down/ps) carry `-f infrastructure/docker-compose.yml --env-file $OSS_ENV` (HOST paths) and therefore run WITHOUT `MSYS_NO_PATHCONV` so MSYS rewrites `/tmp/...` -> `C:\...`. The Task-1 verify (`MSYS_NO_PATHCONV=1 python -m pytest tests/test_board_gate.py`) and the Task-2 verify (`MSYS_NO_PATHCONV=1 docker build --target gate ...` + `docker run ... --entrypoint node`) follow the same rule.

## Files Created/Modified

- `apps/memory-api/tests/test_board_gate.py` — created; the real-Postgres board gate (HTTP flow + migration under both editions), mirrors `test_join_by_code_gate.py`'s committing-session discipline.
- `apps/hocuspocus/tests/gate_client.mjs` — created; the live convergence + cross-language rejection-matrix driver.
- `infrastructure/scripts/verify-phase26.sh` — created; the phase boot gate (a)-(g).
- `apps/hocuspocus/src/server.mjs` — modified; onAuthenticate `connection` -> `connectionConfig` (Rule-1 fix).
- `Makefile` — modified; `verify-phase26` target.

## Decisions Made

- **The live-gate failure was a real bug, fixed — not designed around.** When the first full run showed a VALID Python-minted token being rejected while all negatives passed, the honest options were (a) weaken the positive control, or (b) find the real cause. Isolation proved the token contract sound (authlib-mint verifies under jose) and the two containers shared the secret, then the hocuspocus server log named it: `Cannot set properties of undefined (setting 'readOnly')`. Fixed under Rule 1.
- **Fixed high-byte payload instead of pycrdt.** pycrdt is not installed in this environment; per the plan the byte-exact proof uses `bytes([0,1,2,255,254,0,0,127]) * 64` (documented in-file), which corrupts visibly on any text/base64/UTF-8 round-trip — the property proven is identical.
- **HTTP seeding, exec minting.** Accounts + teams + boards are created over the REAL nginx HTTP route (register -> /teams/my-team -> POST /boards); only the four tokens are Python-minted via `docker compose exec` — the cross-language agreement is the property, and an expired/wrong-signature token cannot be produced over HTTP.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] onAuthenticate read `connection.readOnly` but Hocuspocus 4.4.0 has no `connection`**
- **Found during:** Task 3 (the first full `verify-phase26.sh` run — check (d))
- **Issue:** `apps/hocuspocus/src/server.mjs` destructured `onAuthenticate({ token, documentName, connection })` and executed `connection.readOnly = claims.read_only === true`. In `@hocuspocus/server@4.4.0` the onAuthenticate hook payload has NO `connection` field — the per-connection config is `connectionConfig` (verified in the server dist: the payload is `{ token, documentName, connectionConfig, request, requestHeaders, requestParameters, socketId, context, instance, providerVersion }`). So `connection` was `undefined`, and setting `.readOnly` threw a `TypeError` AFTER `verifyBoardToken` had already SUCCEEDED. The catch block logged reason `"error"` (a TypeError, not a `BoardAuthError`) and re-threw, closing the connection — so EVERY valid board token was rejected while the negative cases still (correctly) denied, perfectly masking the break. 26-03's `test_auth.mjs` only exercised `verifyBoardToken` in isolation and never the server wiring, so no unit test could catch it. This is precisely the defect class the live gate exists to surface.
- **Fix:** destructure `connectionConfig`; set `connectionConfig.readOnly = claims.read_only === true`; guard `if (connectionConfig)` so a future upstream rename fails closed rather than throwing post-verification. No change to the token contract or the DENY behaviour.
- **Files modified:** `apps/hocuspocus/src/server.mjs`
- **Verification:** after the fix, `verify-phase26.sh` -> `PASS: 9 / 9 (SKIP: 0) FAIL: 0`; live driver `pass=9 fail=0` (convergence + full rejection matrix + positive control); post-restart read `pass=1 fail=0`; no `board_auth_rejected` in the hocuspocus logs for a valid token.
- **Committed in:** `a0a9673` (dedicated fix commit)

---

**Total deviations:** 1 auto-fixed (1 Rule-1 bug in a dependency file).
**Impact on plan:** The fix is the whole point of the plan realized — a non-mocked gate that catches what mocked tests cannot. It is a one-line contract correction on the server's readOnly wiring, no scope creep, no token-contract change; convergence needs read-write and all tokens carry `read_only=false`, so `connectionConfig.readOnly` stays false and both clients can write.

## Issues Encountered

- **Git-Bash sandbox refuses compound `docker run ... $(...)` / inline `import()` one-liners.** Diagnosing the auth failure required baking the probe token into a throwaway image layer (a temp `_probe_interop.mjs` + `_tok.txt`, both removed before committing) and running plain single commands. This is the same "split every compound line" constraint 26-03 hit.
- **Host-Python cannot `open("/tmp/...")`.** A diagnostic repro used Windows-native `python open("/tmp/r2body")`, which fails because MSYS `/tmp` is not a Windows path; the real gate reads HTTP bodies via a bash `< "$RESP"` stdin redirect (which MSYS rewrites correctly), so the gate itself was never affected — only the throwaway repro was.

## Known Stubs

None. Every assertion runs against a real Postgres, a real running Hocuspocus, and the real Python minter; nothing on the security paths is mocked. The byte-exact payload is a fixed high-byte string (pycrdt absent) — a deliberate, documented substitution for a real pycrdt Y.Doc update, not a stub of behaviour.

## Threat Flags

None. Every surface exercised (the onAuthenticate boundary, the bridge-only doc endpoints, the size cap, the no-oracle 404, the no-raw-port assertion, the cross-language token contract) is already enumerated in the plan's `<threat_model>` (T-26-43 … T-26-50) and is what this gate proves. The Rule-1 fix corrects a crash, not a trust boundary.

## Next Phase Readiness

- **26b (deferred):** MinIO image routing + brain ingestion. This gate's driver and `verify-phase26.sh` give 26b a live harness to extend (e.g., asserting a pasted image lands in MinIO rather than base64-in-doc).
- **Blocker/concern:** none. The board profile boots, converges, enforces team-scope across languages, and survives a restart; verify-phase16 stays green. Any future change to the token claim set, the algorithm, or the secret must touch `board_helpers.py` and `auth.mjs` together (both carry the mirror comment) — and now the live gate will catch a server-wiring regression too.

## Self-Check: PASSED

- All 3 created files + this SUMMARY exist on disk (verified): `test_board_gate.py`, `gate_client.mjs`, `verify-phase26.sh`, `26-07-SUMMARY.md`.
- Task commits present in `git log`: `9fe5825` (Task 1), `a287877` (Task 2), `a0a9673` (Rule-1 fix), `8fbfc90` (Task 3).
- `apps/hocuspocus/src/server.mjs` carries the `connectionConfig.readOnly` fix (verified).
- Full gate re-run to `PASS: 9 / 9 (SKIP: 0) FAIL: 0`; pytest `3 passed, 0 skipped`; verify-phase16 `PASS: 23 / 23 (SKIP: 0)`. No `xbrain-*` container left running afterward.

---
*Phase: 26-collaborative-board*
*Completed: 2026-07-24*
