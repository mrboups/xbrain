"""In-process rate limiting (D-18-06 coarse first line) for local-auth credential routes.

IMPORTANT CAVEAT — read before trusting this as a hard guarantee:
`apps/memory-api/Dockerfile` runs `uvicorn ... --workers 2`. The `MemoryStorage`
backing this limiter keeps its counters in the memory of ONE worker PROCESS. With 2
workers behind uvicorn's OS-level socket load-balancing, a burst of requests is split
roughly evenly between the two processes; each worker independently allows its own
configured threshold before tripping, so the EFFECTIVE limit an attacker experiences
is close to 2x the configured per-process limit — NOT the configured limit. This is
an accepted, documented limitation of a no-Redis single-instance default (research
Pitfall 3); a multi-replica hosted deployment would need a shared store (Redis) to
close this gap, but D-18-06 explicitly forbids adding Redis to this phase. The
DURABLE defense against brute force is the per-account DB-backed lockout
(`local_credentials.failed_attempts`/`locked_until`, Plan 01) — it lives in Postgres,
shared across all workers, and does NOT have this per-process weakness. This
in-process limiter is spray-blunting only, not a hard guarantee.

This module deliberately does NOT import `app.config` — callers (the routes, Plan
03) pass the rate-limit string explicitly (e.g. `settings.LOCAL_AUTH_RATE_LIMIT`),
keeping this module disjoint from Plan 01's config.py changes.
"""

from fastapi import HTTPException, Request
from limits import parse
from limits.storage import MemoryStorage
from limits.strategies import MovingWindowRateLimiter

# Module-level singletons — one process-wide limiter + storage for the app's lifetime.
_storage = MemoryStorage()
_limiter = MovingWindowRateLimiter(_storage)


def check_rate(limit_str: str, *identifiers: str) -> bool:
    """Return True if this hit is within budget, False if the budget is exhausted.

    `limit_str` is a `limits`-format rate string (e.g. "3/minute", "10/hour").
    `identifiers` compose the bucket key (e.g. a route name + client IP) so
    different callers/keys get independent budgets.
    """
    return _limiter.hit(parse(limit_str), *identifiers)


async def enforce_rate_limit(request: Request, limit_str: str, bucket: str) -> None:
    """FastAPI-dependency-shaped helper: raise 429 once the caller's IP exhausts its budget.

    Keys on `request.client.host` (falls back to "unknown" if the ASGI server
    didn't supply a client, e.g. some test transports) combined with `bucket` (a
    route-identifying string, e.g. "login" vs "register") so different routes
    don't share a budget.
    """
    key = request.client.host if request.client else "unknown"
    if not check_rate(limit_str, bucket, key):
        raise HTTPException(status_code=429, detail="Too many attempts — try again shortly.")
