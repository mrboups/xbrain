---
phase: 15-edition-mechanics
plan: 05
subsystem: api
tags: [neo4j, reconnect, asyncio, lifespan, boot-ordering, docker-compose-profiles, pytest]

# Dependency graph
requires:
  - phase: 15-edition-mechanics
    provides: "15-01 removed the `depends_on: neo4j: {condition: service_healthy}` edge (Compose forbids an untagged core service depending on a profile-tagged one) — which is the exact ordering guarantee this plan restores in application code"
  - phase: 15-edition-mechanics
    provides: "15-02 gated the neo4j_outbox INSERT on `get_driver() is not None` — which turns a lost cold-start race from 'undrained rows' into 'rows never enqueued', making this reconnect loop a correctness requirement rather than a nicety"
provides:
  - "app.neo4j_client.reconnect_loop(attempts=6, interval_s=20.0) — bounded, non-blocking, quiet-on-retry background reconnect"
  - "app.neo4j_client.init_driver(quiet: bool = False) — quiet=True downgrades the connectivity-failure log from ERROR to DEBUG (the retry path)"
  - "main.py lifespan starts reconnect_loop() as a background asyncio.Task and cancels AND awaits it before close_driver()"
  - "tests/test_neo4j_reconnect.py — 3 falsifiable unit tests (late-connect / bounded / quiet)"
affects: [15-04]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "When a compose `depends_on` ordering edge must be removed, the guarantee it provided has to be re-established in application code — a background, BOUNDED retry, not an unbounded one, because the dependency's config can be truthy in installs where the dependency will never exist"
    - "Retry paths log at DEBUG with a single WARNING at exhaustion; only the initial one-shot attempt logs ERROR. N ERRORs per boot in the NORMAL (OSS-light) configuration trains operators to ignore ERROR"
    - "Fix the reconnect at the single source of truth (get_driver()) rather than in each consumer: outbox_worker and graph.py read it live on every use, so they inherit a late reconnection for free"

key-files:
  created:
    - apps/memory-api/tests/test_neo4j_reconnect.py
  modified:
    - apps/memory-api/app/neo4j_client.py
    - apps/memory-api/app/main.py

key-decisions:
  - "reconnect_loop is BOUNDED (6 x 20s = 120s) because NEO4J_URI is truthy even in an OSS-light install that will never have a Neo4j (docker-compose.yml:130 is a bare literal `bolt://neo4j:7687`). An unbounded loop would retry a doomed connection forever in the default install. 120s comfortably covers neo4j's 60s healthcheck start_period (docker-compose.yml:730)."
  - "reconnect_loop runs as a background asyncio.Task and is never awaited in the startup path — /v1/healthz answers 200 while retries are still in flight (measured: 11s to 200 against a 120s retry window)."
  - "Both background tasks are cancelled AND awaited before close_driver(), so the driver is never nulled out from under an in-flight coroutine and no task is leaked."
  - "outbox_worker.py and routes/graph.py were NOT modified — verified they call get_driver() on every use (outbox_worker.py:47 is inside the `while True` tick loop, NOT captured once at startup), so a late reconnect reaches them with no further wiring."

patterns-established:
  - "A reconnect/retry loop must be proven with a CONTROL run: reproduce the race with the loop removed and show the success signal NEVER appears. A race test that passes both with and without the fix proves nothing."

requirements-completed: [EDIT-01]

# Metrics
duration: ~95min
completed: 2026-07-13
---

# Phase 15 Plan 05: Neo4j Cold-Start Reconnect Summary

**Restored the boot-ordering guarantee that removing `depends_on: neo4j` destroyed, with a bounded (6x20s), non-blocking, quiet-on-retry background reconnect loop — proven against a real container race (memory-api up first, Neo4j started late → `neo4j.connected` on attempt 3, zero restarts) and, decisively, a control run with the loop removed where `neo4j.connected` NEVER appears despite Neo4j being up and Bolt-reachable from the same process.**

## Performance

- **Duration:** ~95 min (incl. one mid-flight interruption + resume)
- **Completed:** 2026-07-13
- **Tasks:** 2 (both completed)
- **Files modified:** 3 (1 created, 2 modified)

## Task Commits

1. **Task 1: Bounded, non-blocking background reconnect + lifespan wiring** — `c44ac5a` (feat)
2. **Task 2: Prove the cold-start race is fixed (3 falsifiable unit tests)** — `0076338` (test)

## The premise this plan rests on — VERIFIED

The plan's `<read_first>` demanded I confirm `outbox_worker.py` reads `get_driver()` **every tick**, not once at startup — because if it captured the driver once, a late reconnect would never reach it and this whole plan would be pointless.

**It reads it every tick.** `apps/memory-api/app/outbox_worker.py:47` (`driver = get_driver()`) sits *inside* the `while True:` loop (line 45), re-evaluated on every 2s tick, with the `None` case handled at lines 48-51. `routes/graph.py` likewise calls `get_driver()` per-request. So fixing the single source of truth reaches both consumers for free — which is exactly why the fix belongs in `neo4j_client.py` and nowhere else.

**Both files are UNTOUCHED.** Asserted against the base commit:
```
$ git diff --name-only 57dd7f1
apps/memory-api/app/main.py
apps/memory-api/app/neo4j_client.py
apps/memory-api/tests/test_neo4j_reconnect.py

$ git diff --name-only 57dd7f1 | grep -E "outbox_worker\.py|routes/graph\.py"; echo "exit=$?"
exit=1        # no match — correct
```
No changes to `deps.py`, `crm.py`, `tasks.py`, `docker-compose.yml`, `.env.example`, `STATE.md` or `ROADMAP.md` either (the parallel 15-03 / 15-06 worktrees own those).

## Unit tests — and the proof they discriminate

`apps/memory-api/tests/test_neo4j_reconnect.py`, 3/3 passing. Each was proven falsifiable by injecting the corresponding regression, confirming the FAIL, then reverting:

| Injection | Target test | Result |
|---|---|---|
| Removed the `if get_driver() is not None: return` early exits | `test_reconnect_connects_when_neo4j_appears_late` | **FAILED**: `assert 6 == 3` — "and must STOP retrying the moment it succeeds". Reverted → passed. |
| Made the loop unbounded (`while True`) | `test_reconnect_gives_up_after_a_bounded_window` | **FAILED**: hung, killed by its own `timeout=5` (log showed it spinning past `attempt=14269 of=6`). Reverted → passed. |
| Dropped `quiet=True` from the retry call | `test_reconnect_gives_up_after_a_bounded_window` | **FAILED**: `assert False is True` — "retries must be QUIET". Reverted → passed. |

## The real container race — memory-api up FIRST, Neo4j started LATE

No image was built (host is ARM64, prod is amd64). Stock `python:3.12-slim`, repo bind-mounted, real `postgres:17` + `qdrant/qdrant:v1.17.1` + `neo4j:2026.04.0-community` containers on an isolated network.

**Mount guard fired for real.** The first `docker exec ... test -f /repo/apps/memory-api/app/main.py` returned **MOUNT FAILED** — Git Bash had path-mangled the bind mount to `C:/Program Files/Git/repo`. This is exactly the trap the environment brief warned about: without the guard, every check below would have passed against an empty `/repo` and proved nothing. Re-run with `MSYS_NO_PATHCONV=1` + `cygpath -w` → **MOUNT OK**. All results below are from the guarded run.

### Run A — the FIX (reconnect_loop present)

memory-api booted at `21:16:33` with **no Neo4j container in existence**. Neo4j was started late, at `21:17:06`.

- `/v1/healthz` = **200** within seconds — **startup NOT blocked** by the 120s retry window.
- First attempt failed at ERROR (the degrade path was genuinely exercised — Neo4j was not secretly reachable).
- Neo4j appeared; the loop reconnected **on its own, with no restart**:

```json
{"error": "Failed to DNS resolve address xbrain-p1505-neo4j:7687: [Errno -2] Name or service not known", "event": "neo4j.connectivity_failed", "level": "error", "timestamp": "2026-07-13T21:16:43.190140Z"}
{"attempt": 1, "of": 6, "event": "neo4j.reconnect_attempt", "level": "debug", "timestamp": "2026-07-13T21:17:03.186405Z"}
{"error": "Failed to DNS resolve address xbrain-p1505-neo4j:7687: [Errno -2] Name or service not known", "event": "neo4j.connectivity_failed", "level": "debug", "timestamp": "2026-07-13T21:17:04.501382Z"}
{"attempt": 2, "of": 6, "event": "neo4j.reconnect_attempt", "level": "debug", "timestamp": "2026-07-13T21:17:24.502765Z"}
{"error": "Couldn't connect to xbrain-p1505-neo4j:7687 (resolved to ('172.18.0.5:7687',)):\nFailed to establish connection to ResolvedIPv4Address(('172.18.0.5', 7687)) (reason [Errno 111] Connect call failed ('172.18.0.5', 7687))", "event": "neo4j.connectivity_failed", "level": "debug", "timestamp": "2026-07-13T21:17:24.505570Z"}
{"attempt": 3, "of": 6, "event": "neo4j.reconnect_attempt", "level": "debug", "timestamp": "2026-07-13T21:17:44.503526Z"}
{"uri": "bolt://xbrain-p1505-neo4j:7687", "event": "neo4j.connected", "level": "info", "timestamp": "2026-07-13T21:17:44.849154Z"}
```

Note attempt 2's error shape changes from *DNS does not resolve* → *connect refused*: the container had appeared but Bolt was not yet listening. Attempt 3 connected. Corroborating assertions:

- `RestartCount=0` — **the same process throughout**. It self-healed; it was not restarted.
- `neo4j.reconnect_attempt` count = **3** — the loop **stopped the moment it succeeded** (no attempts 4-6).
- `neo4j.reconnect_exhausted` count = **0** — it connected, so it never gave up.
- `neo4j.connected` count = **1**.

### Run B — the CONTROL (reconnect_loop removed = pre-15-05 behaviour)

Same container, same source, same network, same Postgres/Qdrant. Only the lifespan was reverted to pre-15-05 (`init_driver()` once, no retry) by deleting the `_reconnect_task = asyncio.create_task(reconnect_loop())` line from the container's copy of `main.py` (the repo mount is read-only and was never modified). Neo4j stopped first to restore the identical cold state.

- Control uvicorn started `21:20:45`, Neo4j started late at `21:21:12`, **Bolt confirmed accepting at `21:21:39`** — comfortably inside the window in which Run A reconnected.
- Waited until `21:25:24` — **~4 minutes**, more than double the 120s budget.

**The entire neo4j log output of the control run:**
```json
{"error": "Failed to DNS resolve address xbrain-p1505-neo4j:7687: [Errno -2] Name or service not known", "event": "neo4j.connectivity_failed", "level": "error", "timestamp": "2026-07-13T21:20:50.067663Z"}
```
That is all of it. One line.

| Assertion | Control | Fix (Run A) |
|---|---|---|
| `neo4j.connected` occurrences | **0** | **1** |
| Neo4j up & Bolt reachable from memory-api at check time | **True** | True |
| memory-api RestartCount | 0 | 0 |
| `/v1/healthz` | 200 | 200 |

**That is the bug, reproduced.** Neo4j was right there, healthy, and Bolt-reachable *from inside that very container* — and pre-15-05 memory-api stayed permanently disconnected for the whole life of the process. With 15-02's `get_driver()` outbox gate, that means entity rows would never be enqueued at all. The test discriminates: it passes only with the loop.

## OSS-light (no Neo4j, ever) — bounded, and genuinely quiet

The normal, expected state of a default install: `NEO4J_URI` is truthy (compose passes the bare literal) but no Neo4j container exists or ever will. Booted with the Neo4j container **removed entirely**:

- `/v1/healthz` = **200 after 11s** against a 120s retry window → **startup is not blocked**.
- The loop ran its 6 attempts and stopped. Exactly one WARNING, which explains itself and says this is expected:

```json
{"attempts": 6, "window_s": 120.0, "reason": "Neo4j is configured (NEO4J_URI/NEO4J_PASSWORD are set) but was not reachable within the reconnect window. Graph sync is DISABLED for this process: graph routes return 503 and entity rows are not enqueued. This is EXPECTED in an OSS-light install (the `integrations` profile is off and NEO4J_URI simply has a default value). If you DID enable `integrations`, restart memory-api once Neo4j is healthy.", "event": "neo4j.reconnect_exhausted", "level": "warning", "timestamp": "2026-07-13T21:29:03.113692Z"}
```

**Log-level census for the whole OSS-light boot** (the anti-spam contract, T-15-05-04):

| Level | neo4j lines | Meaning |
|---|---|---|
| ERROR | **1** | only the initial one-shot `init_driver()` — pre-existing behaviour, unchanged, and explicitly accepted by D-15-03 ("exactly one `neo4j.connectivity_failed` ERROR log line") |
| DEBUG | 12 | the 6 retries + their 6 failures — quiet by design |
| WARNING | **1** | exhaustion, once, ever |

**And then silence.** 14 neo4j log lines at exhaustion (`21:29:03`); **14 lines 90s later** — count unchanged, `/v1/healthz` still 200, `RestartCount=0`. The loop terminates and never speaks again. An OSS-light install does not log-spam and does not pay for a graph it does not want.

## Verification

| Check | Result |
|---|---|
| `grep -n "async def reconnect_loop" app/neo4j_client.py` | match (line 65) |
| `grep -n "reconnect_loop" app/main.py` | match (import line 12 + `create_task` line 79) |
| `outbox_worker.py` / `routes/graph.py` in the diff | **no** (grep exit=1) |
| `get_driver()` contract unchanged (zero-arg, module default `None`) | PASS |
| `pytest tests/test_neo4j_reconnect.py` | **3 passed** |
| Each unit test FAILS when its behaviour is broken | **3/3 confirmed** (table above) |
| `pytest tests/test_outbox_neo4j_guard.py tests/test_edition_gating.py tests/test_neo4j_reconnect.py` | **18 passed** — 15-02's real-Postgres outbox guard tests are unaffected (they monkeypatch `_driver` directly) |
| Full suite `pytest -q` | 339 passed / 56 failed — **byte-for-byte the pre-existing baseline** documented in 15-02-SUMMARY (same 14 files, none touched by this plan). No regression introduced. |
| Any `docker build` | **none** — stock images only (host is ARM64) |

## Deviations from Plan

None. The plan was executed as written; its literal code was correct and its acceptance criteria were internally consistent.

Two environment traps were hit and handled (neither is a plan defect):
1. **The Git-Bash mount trap fired.** The first harness run silently mounted nothing (`/repo` → `C:/Program Files/Git/repo`). The plan's mandated hard mount guard caught it and I re-ran with `MSYS_NO_PATHCONV=1` + `cygpath -w`. Had the guard been advisory, the whole race would have "passed" against an empty directory.
2. **The no-build harness needed the Dockerfile's staging order.** `pip install .` on the full source tree fails (`Multiple top-level packages discovered in a flat-layout: ['app', 'alembic']`); the real Dockerfile installs from `pyproject.toml` alone *first*, then copies `app/` + `alembic/` in. Reproduced that order. Same technique as 15-04, no image built.

## Note for 15-04

15-04's `<read_first>` already anticipates this: its check (g) boots memory-api with `NEO4J_URI`/`NEO4J_PASSWORD` set and **no Neo4j container**, so `reconnect_loop` runs there too. It will retry for its bounded 120s window and then emit exactly one `neo4j.reconnect_exhausted` WARNING. This does **not** affect 15-04's assertions — `get_driver()` stays `None` throughout, so the outbox guard still writes zero rows — but the WARNING line will be in that log and is expected. Startup is not delayed (healthz 200 in 11s, measured above), so 15-04's 30s healthz budget is safe.

## Known Stubs

None.

## Threat Flags

None. No new network endpoint, auth path, or schema change. The plan's threat register (T-15-05-01 through T-15-05-04) is fully addressed:

| Threat | Disposition | Evidence |
|---|---|---|
| T-15-05-01 graph sync silently dead for the process's lifetime after losing the cold-start race | mitigated | Run A reconnects on attempt 3, no restart; Run B (control) proves it does NOT without the loop |
| T-15-05-02 a reconnect that blocks startup | mitigated | healthz 200 in 11s against a 120s window; loop is a background `asyncio.Task`, never awaited at startup |
| T-15-05-03 unbounded loop retrying a doomed connection | mitigated | bounded at 6 attempts (unit test asserts exactly 6); OSS-light run exhausts and stops |
| T-15-05-04 retry noise training operators to ignore ERROR | mitigated | OSS-light census: 1 ERROR (pre-existing one-shot), 12 DEBUG, 1 WARNING, then permanent silence |

## Cleanup

All race containers/network/volume (`xbrain-p1505-*`) removed. `docker ps -a --filter name=xbrain-p1505` returns nothing.

---
*Phase: 15-edition-mechanics*
*Completed: 2026-07-13*

## Self-Check: PASSED

All 3 created/modified source files plus this SUMMARY confirmed present on disk. Both task commits
(`c44ac5a`, `0076338`) confirmed present in `git log --oneline --all`. `outbox_worker.py` and
`routes/graph.py` re-confirmed absent from `git diff --name-only 57dd7f1` (exit=1).
