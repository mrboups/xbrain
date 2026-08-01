---
phase: 27-pwa-and-push
plan: 08
subsystem: testing
tags: [acceptance-gate, pwa, web-push, vapid, pywebpush, centrifugo, cors, service-worker, skip-equals-fail]

# Dependency graph
requires:
  - phase: 27-pwa-and-push
    plan: 03
    provides: GET /v1/push/config, the push_subscriptions repo, and migration 0029 that check (h) and check (k) assert against
  - phase: 27-pwa-and-push
    plan: 04
    provides: app.services.web_push.send_to_user and PRUNE_STATUSES — the exact code the push probe drives unmodified
  - phase: 27-pwa-and-push
    plan: 05
    provides: app-site/app/manifest.webmanifest, sw.js and the icon set that checks (a)-(c) fetch from the deployed origin
  - phase: 27-pwa-and-push
    plan: 06
    provides: packages/chat-core/realtime.js and the vendored Centrifuge build the realtime probe drives
  - phase: 27-pwa-and-push
    plan: 07
    provides: the comment-stripping discipline in chrome-extension/tests/test_pwa_push.mjs that check (b) mirrors
provides:
  - "infrastructure/scripts/verify-phase27.sh — 11 checks, exit 0 only when FAIL == 0 AND SKIP == 0, every surface check against the deployed https origin"
  - "infrastructure/scripts/phase27_realtime_probe.mjs — content-asserted arrival at a second websocket client"
  - "infrastructure/scripts/phase27_push_probe.py — real pywebpush encryption against a real socket plus the 404/410/500 prune matrix"
  - "make verify-phase27"
affects: [27-09, any future phase touching the PWA surface, push send path or Centrifugo fan-out]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "A gate whose only failure mode is red: no branch produces a skip, and the exit condition enforces SKIP == 0"
    - "Comment-stripping before asserting on a served body, with the strip itself proved load-bearing"
    - "Probe programs that exit 2 on a missing credential so the gate can distinguish 'not configured' from 'broken'"
    - "node:vm evaluation of a vendored browser bundle, so a UMD file's module kind never has to agree with an ambient package.json"

key-files:
  created:
    - infrastructure/scripts/verify-phase27.sh
    - infrastructure/scripts/phase27_realtime_probe.mjs
    - infrastructure/scripts/phase27_push_probe.py
  modified:
    - Makefile
    - .planning/phases/27-pwa-and-push/deferred-items.md

key-decisions:
  - "The realtime probe carries no socket-scheme literal at all — the URL comes from POST /v1/me/centrifugo-token and is printed, so the log shows a URL the probe could not have invented"
  - "The push probe writes its loopback endpoint through the REPO, not the HTTP route, deliberately bypassing _is_safe_push_endpoint — the route guard has its own test and this probe needs a socket whose status it controls"
  - "Credential-bearing request headers are logged as scheme + length, never verbatim"
  - "Check (j) probes for a test-capable image and falls back to the checkout, because the runtime image ships neither tests/ nor pytest; the deployment's own dependency set is proven by check (g) instead"

patterns-established:
  - "SKIP=FAIL as an exit condition, not a slogan: `[ $FAIL -eq 0 ] && [ $SKIP -eq 0 ]`"
  - "Every gate failure message names the exact thing to export, start or install"
  - "A negative control beside every positive one where a permissive config would otherwise pass (CORS)"

requirements-completed: [PWA-01, PUSH-01]

# Metrics
duration: 95min
completed: 2026-08-01
---

# Phase 27 Plan 08: The Acceptance Gate Summary

**An 11-check executable that can only pass by touching the deployed https origin, the live API, a real Centrifugo socket and a real encrypted push — and that goes red rather than skipping when any of them is missing.**

## Performance

- **Duration:** ~95 min
- **Started:** 2026-08-01T12:20:00Z
- **Completed:** 2026-08-01T13:55:00Z
- **Tasks:** 3
- **Files modified:** 5 (3 created, 2 modified)

## Accomplishments

- **The gate exists and has been exercised in both directions.** Every check's PASS path and FAIL path were driven — the PASS paths against a stand-in origin serving the real `app-site/app/` files, the FAIL paths against the actual (undeployed) production origin. 46-48 of 50 assertions fire green against a correct surface; the same script returns 10 failures and zero skips against today's reality.
- **Realtime is proven by arrival, never by absence-of-error.** The probe posts over plain HTTP as one client and waits for a frame carrying that run's UUID to reach a different client over a real websocket, asserting on `data.message.content`. An exit 0 is impossible unless a message crossed the fan-out.
- **Push is proven by real encryption and the exact prune matrix.** The probe drives `web_push.send_to_user` unmodified: real pywebpush, real VAPID signing, real aes128gcm, real socket. Verified against pywebpush 2.3.0 that the emitted `Authorization` scheme is `vapid`, `Content-Encoding` is `aes128gcm`, `TTL` is present and the body is non-empty — the probe asserts what the library actually sends, not what a version note claimed.
- **CORS carries a negative control.** A config that echoes any Origin passes the positive test; the gate also proves `https://attacker.example` is not echoed. Against the live API today, both controls pass.
- **No secret reaches the log.** Confirmed empirically: neither the VAPID private key (even in a deliberate-leak run), nor the `xbt_` token, nor the minted Centrifugo client token appears anywhere in the gate output.

## Task Commits

1. **Task 1: phase27_realtime_probe.mjs** — `d389e71` (test)
2. **Task 2: phase27_push_probe.py** — `a68d711` (test)
3. **Task 3: verify-phase27.sh + Makefile** — `8ea92c8` (feat)

## Files Created/Modified

- `infrastructure/scripts/verify-phase27.sh` — the gate. 861 lines, 11 checks (a)-(k), `set -uo pipefail`, PASS/FAIL/SKIP counters, `exit 0` only when both FAIL and SKIP are zero.
- `infrastructure/scripts/phase27_realtime_probe.mjs` — dependency-free node program. Loads the vendored Centrifuge bundle via `node:vm`, subscribes, waits for `subscribed`, then posts over HTTP and asserts the nonce arrives within 15 s.
- `infrastructure/scripts/phase27_push_probe.py` — fed to `docker compose exec -T memory-api python -`. Stands up a `ThreadingHTTPServer` answering 201/410/404/500 by path, generates real P-256 subscription keys, drives the real send path, asserts the prune matrix, cleans up in a `finally`.
- `Makefile` — `make verify-phase27`.
- `.planning/phases/27-pwa-and-push/deferred-items.md` — logged the memory-api image's inability to host its own test suite.

## Verification Performed

| Acceptance criterion | Result |
|---|---|
| `node --check` the realtime probe | exit 0 |
| realtime probe with no env | exit **2**, names `API_BASE, VERIFY_XBT_TOKEN, VERIFY_TEAM_ID` |
| `grep -nE 'wss?://'` on the realtime probe | **no output** — no socket-scheme literal |
| `randomUUID` / `subscribed` / `includes(nonce)` in the probe | 2 / 1 / 1 |
| `ast.parse` the push probe | exit 0 |
| `send_to_user` / `410` / `404` / `500` / `aes128gcm` / `ThreadingHTTPServer` / `finally` | 9 / 5 / 6 / 3 / 6 / 3 / 2 |
| `grep -cE '\bmock\b\|patch\('` on the push probe | **0** — nothing in the send path is stubbed |
| `bash -n verify-phase27.sh` | exit 0 |
| `SKIP=FAIL` documented / exit line requires both counters | 1 / 3 |
| `grooveos.app/app/manifest.webmanifest` / `attacker.example` | 1 / 2 |
| realtime probe named / push probe named | exactly 1 / exactly 1 |
| `grep -c 'file://'` on the gate | **0** — nothing is proven from a local file |
| `grep -c 'verify-phase27' Makefile` | 3 |
| gate run with no env and no deployment | **exit 1**, 10 FAIL lines, **0 SKIPPED lines** |

Two further verifications beyond the plan's criteria, both worth recording:

- **pywebpush behaviour was measured, not assumed.** A throwaway harness ran the real `webpush()` against the same stand-in server shape for all four statuses. 201 returns without raising; 410/404/500 raise `WebPushException` with the matching `response.status_code`; every request carried `authorization: vapid`, `content-encoding: aes128gcm`, `ttl: 86400` and a 144-byte body; an `http://127.0.0.1` endpoint signs and sends without complaint. The probe's assertions were written against that observation.
- **The gate's PASS paths were driven.** A stand-in origin served the real `app-site/app/` files with the headers `firebase.json` declares, plus the API surface the gate walks. Result: (a) 13/13, (b) 13/13 including the not-inert proof and the byte-ordering proof, (c) 4/4, (d) 3/3 with both controls, (e) 6/6, (h) 4/4, (k) 1/1. A deliberate-leak variant confirmed check (h) fires `THE PRIVATE VAPID KEY APPEARS IN THE /v1/push/config RESPONSE` when the key is present — so that check has teeth rather than being unreachable.

### Statically-verified only (Docker is down on this host)

Checks (g), (j)'s container branch and (k) talk to the daemon, which is unreachable here (`npipe:////./pipe/dockerDesktopLinuxEngine`). They were verified as follows and **not** downgraded to skips:

- **They emit `ko`, not `skip`** — proven by a real gate run: (g) and (k) both recorded FAIL with `the docker daemon is not reachable — start Docker and re-run`.
- **Their argv shapes are correct** — proven by putting a stand-in `docker` earlier on `PATH` and logging what the gate sent:
  - `docker info` (liveness)
  - `docker compose -f infrastructure/docker-compose.yml ps -q memory-api`
  - `docker compose -f infrastructure/docker-compose.yml exec -T memory-api python -` with **15260 bytes of probe body arriving on stdin**
  - `docker compose -f infrastructure/docker-compose.yml exec -T memory-api sh -c python -c "import pytest" && test -d tests` (payload intact)
  - `docker compose -f infrastructure/docker-compose.yml exec -T memory-api alembic current`
  - `docker compose -f infrastructure/docker-compose.yml exec -T memory-api printenv VAPID_PRIVATE_KEY`
- **The MSYS host rule holds** — `MSYS_NO_PATHCONV` appears only in comments; every `docker compose` invocation carries a `-f` HOST path and none suppresses conversion. No absolute in-container path appears on any of these command lines, so there is nothing for MSYS to mangle in either direction.
- **`alembic` is a runtime dependency** (`pyproject.toml`) and `alembic/` is COPYed into the runtime stage, so check (k) will genuinely run in-container. Its revision parsing was exercised end to end via the stand-in (`0029_push_subscriptions` → PASS, with a `>= 29` numeric comparison so a later head also passes).

The push probe's own DB and pywebpush behaviour inside the container remains unexercised — that is what plan 27-09 is for.

## Decisions Made

- **The realtime probe evaluates the vendored bundle with `node:vm` rather than importing it.** `chrome-extension/vendor/centrifuge.js` is a browser bundle that assigns `globalThis.Centrifuge`; evaluating it in the current context means its module kind never has to agree with whatever `package.json` sits above the checkout. Verified it yields a working constructor, `newSubscription` and `subscribe` under node 24.
- **`skip()` is defined and never called.** House-style symmetry with the other gates, and the exit condition enforces `SKIP == 0` so a future edit cannot quietly re-open the door. The header says exactly this.
- **The not-inert proof for the comment stripper is two-part:** stripped bytes must be under 75% of raw (measured 8123 → 3837, i.e. 47%), *and* `/v1/` must occur more times in the raw body than in the stripped one (measured 3 vs 1). The second half is the real proof: it demonstrates that a naive grep over the served body would have been satisfied by the worker's own documentation of a guard that could have been deleted. `Authorization` was deliberately not given the same treatment — it appears exactly once in sw.js, in code, so asserting its removal would fail on a correct file.
- **Origins must carry an http(s) scheme.** A `VERIFY_SITE_BASE` pointing at a local path is a `ko` before any check runs, and a plain-http origin prints a NOTE naming it as staging-only.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Check (j) as specified can never pass**
- **Found during:** Task 3 (verify-phase27.sh)
- **Issue:** The plan specifies `docker compose exec -T memory-api python -m pytest tests/test_push_endpoints.py ...`. The memory-api runtime stage COPYs `app/` and `alembic/` but **not** `tests/`, and `pip install --target=/build/deps -e .` installs runtime dependencies only — `pytest` is in `[project.optional-dependencies].dev`. The check would fail on every correct deployment, for a reason unrelated to the phase, which is itself a false signal.
- **Fix:** (j) now probes the container (`python -c "import pytest" && test -d tests`); when the image can host the suite it runs there, otherwise it runs the same four files against the checkout and **prints which runner it used**. Neither branch skips; when no runner exists at all it is a `ko` naming both remedies. The script documents why this is not a weakening: the "does this deployment's own dependency set work" question is answered by check (g), which drives real pywebpush inside the real container and cannot be satisfied by a checkout.
- **Files modified:** `infrastructure/scripts/verify-phase27.sh`
- **Verification:** ran green against the checkout — `98 passed, 3 skipped` across the four files. Container branch's argv shape confirmed via the stand-in docker.
- **Committed in:** `8ea92c8`

**2. [Rule 2 - Missing Critical] Credential-bearing headers redacted in the push probe log**
- **Found during:** Task 2 (phase27_push_probe.py)
- **Issue:** The plan says to print the observed `/ok` request headers "minus the body". Verbatim printing would put the freshly signed VAPID JWT into a gate log that gets pasted into issues.
- **Fix:** `Authorization`, `Crypto-Key` and `Encryption` print as scheme + character count. Every other header prints in full, and the body length is printed — which is all the evidence the assertion needs. Consistent with T-27-08-02.
- **Files modified:** `infrastructure/scripts/phase27_push_probe.py`
- **Verification:** the redaction path was exercised by the pywebpush harness; the gate logs were grepped for the secret and returned 0 hits.
- **Committed in:** `a68d711`

**3. [Rule 2 - Missing Critical] Distinguish a dead daemon from a stopped container**
- **Found during:** Task 3, after the first gate run
- **Issue:** With Docker down, checks (g)/(j)/(k) all reported "the memory-api container is not running" — an operator would go looking for a container on a host with no daemon.
- **Fix:** `api_blocker()` separates three cases (no docker CLI / daemon unreachable / service down) and each check reports the precise one with the exact command to run.
- **Files modified:** `infrastructure/scripts/verify-phase27.sh`
- **Verification:** re-ran the gate; the three checks now report `the docker daemon is not reachable — start Docker and re-run`.
- **Committed in:** `8ea92c8`

**4. [Rule 3 - Blocking] Missing-probe guards**
- **Found during:** Task 3
- **Issue:** If either probe file were absent, (f) would fail with a node module-resolution stack trace and (g) with a shell redirect error — neither naming the real cause.
- **Fix:** explicit `[ ! -f "$PROBE" ]` guards ahead of both, phrased so the reader knows the check has no substitute.
- **Files modified:** `infrastructure/scripts/verify-phase27.sh`
- **Committed in:** `8ea92c8`

**5. [Rule 1 - Bug] PWA-01 and PUSH-01 rolled back to unchecked in REQUIREMENTS.md**
- **Found during:** state updates, after `requirements.mark-complete PWA-01 PUSH-01`
- **Issue:** The plan's frontmatter lists both requirements, so the standard state step ticked them. But PWA-01 reads "The team chat **is available** as an installable PWA" and `https://grooveos.app/app/` returns 404 — the gate this plan just built proves the claim false. Ticking them here is precisely the false-green the phase exists to refuse, in the tracking artifact rather than the code. Plan 27-09 carries the same `requirements: [PWA-01, PUSH-01]` and is the plan that deploys the PWA and runs the gate.
- **Fix:** both checkboxes reverted to `[ ]`. `git diff .planning/REQUIREMENTS.md` is now empty — the file is byte-identical to its pre-run state, so 27-09 marks them for the first time when they are actually true.
- **Files modified:** `.planning/REQUIREMENTS.md` (net zero change)
- **Verification:** `git diff .planning/REQUIREMENTS.md` returns nothing.

---

**Total deviations:** 5 auto-fixed (2 bug, 2 missing-critical, 1 blocking)
**Impact on plan:** No scope creep. Deviation 1 is the only one that changes a check's shape, and it makes the check runnable without loosening what it asserts. Deviations 1-4 are inside the plan's declared `files_modified` set; deviation 5 is a net-zero correction to a state artifact.

## Issues Encountered

- **`grep -c 'phase27_realtime_probe.mjs' == 1` is an exact count, not a minimum.** The probe filenames therefore appear exactly once each — at the invocation. Every other reference goes through `$REALTIME_PROBE` / `$PUSH_PROBE`, including the error messages.
- **The exit-line grep pattern constrains the shell style.** `FAIL -eq 0 \] && \[ .*SKIP -eq 0` does not match `[[ "$FAIL" -eq 0 ]]` — the quote sits between `FAIL` and the space. The gate uses `[ $FAIL -eq 0 ] && [ $SKIP -eq 0 ]`, unquoted, which is safe for integer counters that are initialised at the top.
- **The word "mock" in prose failed the push probe's own acceptance check.** The docstring's sentence declaring that nothing is stubbed contained the very token the criterion greps for. Reworded to "no test double, no import interception, no fake transport", which says the same thing without tripping the guard.
- **This worktree carried a stale CRLF copy of `chrome-extension/vendor/centrifuge.js`.** The worktree was created before the base commit's `.gitattributes` pin (`**/vendor/centrifuge.js text eol=lf`), so `git reset --hard` left the old working-tree bytes in place and `test_pwa_chat.mjs` reported the two surfaces' builds as differing (54043 vs 54035 bytes). Fixed by re-materialising that single file (`rm` + `git checkout -- <file>`); the extension suite then ran **19/19 green**, matching the base-commit baseline. Nothing was committed — the index was already LF.
- **The extension suite cannot run from inside `.claude/`.** `.claude/package.json` is `{"type":"commonjs"}`, which makes the `.js` modules the `.mjs` tests import resolve as CommonJS. This is an artifact of executing in a worktree under `.claude/worktrees/`, not a repo defect, and no workaround was added to the gate. The suite was verified green by copying `chrome-extension app-site packages scripts` outside `.claude` and running there.

## User Setup Required

None for this plan. To *run* the gate against production, plan 27-09 will need:

```
export VERIFY_XBT_TOKEN=xbt_...                     # a real token for a member of the team below
export VERIFY_TEAM_ID=<team uuid>
export VERIFY_XBT_TOKEN_2=xbt_...                   # OPTIONAL: a second member, for cross-account fan-out
```

and a running memory-api container on the host it runs from. Optionally `VERIFY_SITE_BASE` / `VERIFY_API_BASE` to point at a staging origin.

## Next Phase Readiness

- **The gate is red today, on purpose.** `https://grooveos.app/app/` and its manifest and worker all return 404 — the PWA is not deployed. Checks (a)(b)(c) are the ones that go green the moment 27-09 ships it. Nothing was weakened to make today's run pass.
- **What already passes against production:** (d) CORS, both controls — the live API at `api.grooveos.app` answers the browser preflight from `https://grooveos.app` with the correct origin and `allow-credentials: true`, and does not echo a foreign origin. (j) the server suite, 98 passed. (i) the chat-core drift check.
- **What 27-09 must supply:** the deploy, the two credentials, and a host with Docker up. Checks (g)/(j)-container/(k) have not executed against a real daemon — their command shapes are verified, their behaviour in-container is not.
- **One residual worth a decision at the 27-09 gate:** adding a `test` stage to `apps/memory-api/Dockerfile` (mirroring `apps/hocuspocus`'s `--target gate`) would move check (j) back inside the container with no edit to the gate. Logged in `deferred-items.md`.

## Self-Check: PASSED

All three created files exist on disk (`verify-phase27.sh` 37754 B, `phase27_realtime_probe.mjs` 11130 B, `phase27_push_probe.py` 15260 B), `Makefile` carries the `verify-phase27` target, and all three task commits are present in `git log`: `d389e71`, `a68d711`, `8ea92c8` — each a child of the base commit `2a0458e`.

---
*Phase: 27-pwa-and-push*
*Completed: 2026-08-01*
