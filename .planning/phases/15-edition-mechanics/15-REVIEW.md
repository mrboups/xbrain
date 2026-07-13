---
phase: 15-edition-mechanics
reviewed: 2026-07-13T23:21:41Z
depth: standard
files_reviewed: 20
files_reviewed_list:
  - infrastructure/docker-compose.yml
  - apps/memory-api/app/main.py
  - apps/memory-api/app/config.py
  - apps/memory-api/app/neo4j_client.py
  - apps/memory-api/app/routes/memory.py
  - apps/memory-api/app/deps.py
  - apps/memory-api/app/routes/crm.py
  - apps/memory-api/app/routes/tasks.py
  - apps/memory-api/app/routes/admin_wipe.py
  - apps/mcp-deck/app/main.py
  - infrastructure/backup/backup.sh
  - infrastructure/scripts/preflight-env.sh
  - infrastructure/scripts/verify-phase15.sh
  - .env.example
  - Makefile
  - apps/memory-api/alembic/versions/0008_team_plan.py
  - apps/memory-api/tests/conftest.py
  - apps/memory-api/tests/test_edition_gating.py
  - apps/memory-api/tests/test_no_paywall.py
  - apps/memory-api/tests/test_outbox_neo4j_guard.py
  - apps/memory-api/tests/test_neo4j_reconnect.py
findings:
  critical: 0
  warning: 1
  info: 0
  total: 1
status: issues_found
---

# Phase 15: Code Review Report

**Reviewed:** 2026-07-13T23:21:41Z
**Depth:** standard (with targeted cross-file race analysis on the reconnect/outbox pair, per review brief)
**Files Reviewed:** 20
**Status:** issues_found

## Summary

This phase is unusually well executed. Every one of the seven "look hardest" areas was traced to a
concrete conclusion, not just spot-checked:

- **`create_app(edition)` router factory** (`main.py`) — verified by manual enumeration: all 35
  imported router modules are classified exactly once across `CORE_ROUTERS` (33) and
  `SAAS_ONLY_ROUTERS` (2, `waitlist` + `external_sessions`), there is no `include_router` call outside
  the two loops, and `tests/test_edition_gating.py::test_every_router_module_is_classified` makes this
  a real, non-tautological guard (it introspects `app.routes` via `pkgutil`, not the same hardcoded
  list it's checking).
- **`EDITION` validation** (`config.py`) — fails fast via `field_validator` on any value outside
  `{oss, saas}`, defaults to `oss`, and is exercised by both the reject-unknown and default-value unit
  tests.
- **`neo4j_outbox` guard** (`memory.py:342`) — gates on the runtime `get_driver()`, not on static
  `NEO4J_URI`/`NEO4J_PASSWORD` config, exactly as required (those are both truthy by default even with
  no Neo4j container — `docker-compose.yml:141`/`.env.example:248`). The memory item write and the
  outbox insert share one transaction/commit, so the item is never dropped even when the guard skips
  the outbox. One narrow race in this guard is documented below (WR-01).
- **Reconnect loop** (`neo4j_client.py`) — genuinely non-blocking (background `asyncio.create_task`),
  bounded (6 × 20s), quiet on the expected OSS-light path (`quiet=True` retries → `log.debug`, one
  final `log.warning`), and correctly cancelled-and-awaited before `close_driver()` runs at shutdown
  (`main.py:87-94`) — no leaked task, no post-shutdown use of a closed driver.
- **`require_paid_tier` removal** (`deps.py`/`crm.py`/`tasks.py`) — confirmed via diff: all 10 call
  sites were changed 1:1 from `Depends(require_paid_tier)` to `Depends(get_team_scope)`, which itself
  still enforces membership + `blocked_at`. No endpoint lost its team-scope dependency. The paired
  regression tests (`test_no_paywall.py`) assert both directions: a starter-plan team is served, and a
  non-member of the target team is still 403'd.
- **`backup.sh` Mongo-optional change** — the retry loop's `mongodump` failure is inside an `if`
  conditional, which `set -e` does not trip on; Postgres and Qdrant steps are untouched by the diff and
  still fail loudly on a real error.
- **`.env.example`** — verified with a direct scan: zero remaining `KEY=<blank> #comment` lines
  anywhere in the file. The new `EDITION=oss  # comment` line (non-blank value + trailing comment) is
  safe — it follows the same pattern already used successfully by dozens of pre-existing non-blank
  vars (`LOG_LEVEL`, `POSTGRES_USER`, etc.); the documented parser defect is specific to blank values.

One real, narrow finding survived this pass — a TOCTOU race between the reconnect loop's driver
publication and the `neo4j_outbox` write-time guard, detailed below.

## Warnings

### WR-01: `neo4j_outbox` guard has a TOCTOU window during `reconnect_loop()` retries — orphaned outbox rows possible

**File:** `apps/memory-api/app/neo4j_client.py:47-61`, `apps/memory-api/app/routes/memory.py:341-342`, `apps/memory-api/app/outbox_worker.py:47-51`

**Issue:**

`init_driver()` publishes the module-global `_driver` **before** it is verified:

```python
_driver = AsyncGraphDatabase.driver(...)      # neo4j_client.py:47 — global set, unverified
try:
    await _driver.verify_connectivity()        # neo4j_client.py:52 — await point, event loop yields here
    ...
except Exception as exc:
    ...
    await _driver.close()
    _driver = None                              # neo4j_client.py:61 — only reached on failure
```

Between line 47 and the resolution of line 52's `await`, `get_driver()` returns a non-`None` object
that has not yet been proven reachable. In the default OSS-light install, `NEO4J_URI` is a bare
literal and `NEO4J_PASSWORD` ships non-empty in `.env.example` (both confirmed truthy), so
`reconnect_loop()` runs its full 6-attempt budget on **every boot**, and each attempt reproduces this
window for roughly as long as the connection attempt takes to time out (the code's own comment at
`docker-compose.yml:235` cites ~8.5s for a DNS timeout to an absent host — so up to ~51s of cumulative
exposure per boot, across 6 attempts).

`memory.py:342`'s guard is a classic check-then-act on this same global:

```python
if entities and get_driver() is not None:
    ...
    session.add(NeoOutboxEntry(...))   # enqueued
```

Concrete failure trace:
1. Memory-api boots OSS-light default (no Neo4j container; `NEO4J_URI`/`NEO4J_PASSWORD` both truthy).
2. `reconnect_loop()` fires an attempt: `init_driver(quiet=True)` assigns `_driver` to a new,
   unverified driver object and is now awaiting `verify_connectivity()`.
3. While that await is pending, a client (bridge JWT, ChatGPT/Claude.ai connector, LibreChat) calls
   `POST /v1/memory/upsert` with `metadata.entities` non-empty.
4. `upsert_item()`'s guard reads `get_driver() is not None` → **True** (driver exists, not yet
   verified) → two `NeoOutboxEntry` rows are inserted and committed.
5. `verify_connectivity()` then fails (expected — no Neo4j reachable) → `init_driver` closes the
   driver and resets `_driver = None`.
6. `outbox_worker.drain_outbox()`'s next 2s tick reads `get_driver()` → `None` → `continue` (no-op,
   per `outbox_worker.py:48-51`).
7. `reconnect_loop()` eventually exhausts its 6 attempts and gives up permanently for this process
   lifetime (`neo4j_client.py:95-107`). `_driver` stays `None` from here on.
8. The two rows inserted in step 4 remain in `neo4j_outbox` with `processed = false` **forever** —
   nothing in the codebase retries or purges unprocessed outbox rows (`brain-janitor` purges
   soft-deleted `memory_items`/etc., not `neo4j_outbox`).

This is exactly the failure mode the guard's own docstring says it exists to prevent ("Without this
guard, an OSS-light install would enqueue rows that NOTHING will ever drain or delete — unbounded
growth of neo4j_outbox, silently, in the DEFAULT install" — `memory.py:330-331`). The guard closes the
common case (driver never created at all) but not this narrower race, because it reads a value that
can still change state (verified → unreachable) after the check but before the enqueued rows are ever
drained.

This is not caught by `verify-phase15.sh` check (g): that harness waits for the container to be fully
"healthy" before issuing its single upsert call, so it never races an in-flight `verify_connectivity()`
call — the exact timing this bug depends on is outside what the acceptance gate exercises.

Impact is bounded (a slow trickle of orphaned rows requiring an unlucky timing overlap on a small
number of boots, not unbounded runaway growth under steady-state operation — once `_driver` settles to
either a verified driver or a permanent `None` after the 6-attempt budget, the race window closes for
the rest of the process lifetime), and the memory item itself is never lost — only small internal
outbox metadata rows leak. Classified WARNING rather than BLOCKER on that basis.

**Fix:** don't publish the driver to the module global until it is verified — hold it in a local
variable during the connectivity check, and only assign `_driver` on success:

```python
async def init_driver(quiet: bool = False):
    global _driver
    if not (settings.NEO4J_URI and settings.NEO4J_PASSWORD):
        ...
        return None
    from neo4j import AsyncGraphDatabase

    candidate = AsyncGraphDatabase.driver(
        settings.NEO4J_URI,
        auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
    )
    try:
        await candidate.verify_connectivity()
        _driver = candidate          # only published once verified
        log.info("neo4j.connected", uri=settings.NEO4J_URI)
    except Exception as exc:
        ...
        await candidate.close()      # _driver was never set — nothing to null out
    return _driver
```

With this change, `get_driver()` correctly reports `None` for the entire duration of every connection
attempt, closing the TOCTOU window for `memory.py`'s guard.

---

_Reviewed: 2026-07-13T23:21:41Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
