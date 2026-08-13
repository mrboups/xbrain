"""Strong references for fire-and-forget background tasks.

`asyncio.create_task()` returns a Task the event loop only holds a **weak**
reference to. A bare `asyncio.create_task(coro)` in a request handler binds
that Task to nothing, so it is collectable the moment the statement returns:
CPython is free to garbage-collect it mid-await, and the symptom is the worst
kind there is — the work simply never finishes, and nothing is logged. On the
2-vCPU VM under load that reads as "the agent never answered", with a clean
log to prove it never happened.

`spawn()` keeps the task in a module-level set until it completes. The
done-callback releases it and logs whatever it raised: a fire-and-forget caller
is by definition not awaiting the result, so an exception nobody retrieves would
otherwise surface only as CPython's anonymous "Task exception was never
retrieved" at some later GC — attributed to no request, no team, no route.

Use this for every task spawned in a REQUEST path. Lifespan workers
(`app/main.py`) are a different case: a local in the lifespan coroutine already
holds them for the process's life, and they must stay cancellable at shutdown.
"""
from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

import structlog

log = structlog.get_logger()

# The strong references. Module-level on purpose — it has to outlive the request
# that spawned the task, which is the entire point.
_TASKS: set[asyncio.Task[Any]] = set()


def spawn(coro: Coroutine[Any, Any, Any], *, name: str) -> asyncio.Task[Any]:
    """Schedule *coro* fire-and-forget, keeping it alive until it finishes.

    Drop-in replacement for `asyncio.create_task(coro)` in a request path.

    *name* is mandatory: it is the only identifier a failure log line gets, and
    an unattributable background failure is the thing this module exists to
    stop. Use a dotted `area.action` string, e.g. `"brain_ingest.team_message"`.
    """
    task = asyncio.create_task(coro, name=name)
    _TASKS.add(task)
    task.add_done_callback(_release)
    return task


def _release(task: asyncio.Task[Any]) -> None:
    """Drop the strong ref and log a failure nobody else can see."""
    _TASKS.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        # Never re-raise: this runs on the event loop's callback path, where a
        # raise becomes a loop-level exception with even less context than the
        # one we are trying to give a name to.
        log.warning("background_task.failed", task=task.get_name(), err=str(exc))


def pending_count() -> int:
    """Number of tasks currently held. Diagnostics and tests only."""
    return len(_TASKS)
