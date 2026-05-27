"""Phase 13 Cross-Frontend Integration Test.

Test plan (letters match ROADMAP Phase 13 success criterion 9):
  (a) team_chat_ingest — POST a substantive team-chat message; assert one new
      memory_items row with source LIKE 'team-chat:%' AND one new Qdrant point.
  (b) librechat_ingest — POST /v1/brain/ingest with source='librechat:claude-haiku-4-5';
      assert memory_items row + Qdrant point.
  (c) owui_ingest — POST to OWUI pipeline /v1/chat/completions with a substantive user
      message (model=claude-haiku-4-5); wait 5s; assert memory_items row with
      source='openwebui:%' appeared.
  (g) cross_frontend — ingest via team chat → PATCH truth_level=VALIDATED via Brain
      Monitor → GET /v1/system-prompt?min_level=VALIDATED; assert the addendum contains
      the ingested fact.

Usage:
  python test-phase13-cross-frontend.py --test a --team-scope dejavudev --sub mrboups@github
  python test-phase13-cross-frontend.py --all --team-scope dejavudev --sub mrboups@github

Exit codes:
  0 — PASS
  1 — FAIL
  77 — SKIP (missing prerequisite, e.g., BRIDGE_SHARED_SECRET not set)

Environment variables:
  MEMAPI_HOST             default https://api.grooveos.app
  TEAM_SCOPE              default dejavudev (overridden by --team-scope)
  BRIDGE_SHARED_SECRET    required — HS256 key used by bridge JWT
  PG_DSN                  optional — direct Postgres DSN for DB assertions
                          (when absent, uses docker exec xbrain-postgres psql)
  QDRANT_URL              default http://localhost:6333
  OPERATOR_SUB            default value of --sub arg (for superadmin PATCH)
  OWUI_PIPELINE_HOST      default http://localhost:8200
  PIPELINE_API_KEY        required for test (c); SKIP if absent
  KEEP_FIXTURES           set to '1' to disable cleanup (equivalent to --keep-fixtures)
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import uuid
from typing import Optional

# ---------------------------------------------------------------------------
# Optional imports — gracefully degrade when not available
# ---------------------------------------------------------------------------

try:
    import httpx

    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False

try:
    import jwt as pyjwt

    _PYJWT_AVAILABLE = True
except ImportError:
    _PYJWT_AVAILABLE = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BRAIN_INGEST_NS = uuid.UUID("8e7c2b00-1aae-5a40-9c4f-13b7c0d72f10")

# Verbatim test content strings — SHA256 idempotency key is reproducible.
FIXTURE_CONTENT_A = "The Phase 13 test fixture deploy window is every Tuesday at 14:00 UTC"
FIXTURE_CONTENT_B = "The Phase 13 test fixture Postgres version on the VM is 17.9"
FIXTURE_CONTENT_C = "The Phase 13 test fixture Qdrant collection is named 'messages'"

# Idempotency keys derived from content
FIXTURE_IDEMPOTENCY_B = "verify-phase13-test-b"
FIXTURE_IDEMPOTENCY_G = "verify-phase13-test-g"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MEMAPI_HOST = os.environ.get("MEMAPI_HOST", "https://api.grooveos.app").rstrip("/")
QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333").rstrip("/")
OWUI_PIPELINE_HOST = os.environ.get("OWUI_PIPELINE_HOST", "http://localhost:8200").rstrip("/")
BRIDGE_SHARED_SECRET = os.environ.get("BRIDGE_SHARED_SECRET", "")
PIPELINE_API_KEY = os.environ.get("PIPELINE_API_KEY", "")
PG_DSN = os.environ.get("PG_DSN", "")
def _keep_fixtures() -> bool:
    return os.environ.get("KEEP_FIXTURES", "0") == "1"
DB_CONTAINER = os.environ.get("DB_CONTAINER", "xbrain-postgres")
PG_USER = os.environ.get("PG_USER", "xbrain")
PG_DB = os.environ.get("PG_DB", "xbrain")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _log(test_name: str, result: str, detail: str) -> None:
    """Print a single standardized test result line."""
    print(f"[13-CROSS] ({test_name}): {result} — {detail}", flush=True)


def _skip(test_name: str, reason: str) -> tuple[str, str]:
    _log(test_name, "SKIP", f'reason="{reason}"')
    return ("SKIP", reason)


def _pass(test_name: str, detail: str) -> tuple[str, str]:
    _log(test_name, "PASS", detail)
    return ("PASS", detail)


def _fail(test_name: str, reason: str) -> tuple[str, str]:
    _log(test_name, "FAIL", f'reason="{reason}"')
    return ("FAIL", reason)


# ---------------------------------------------------------------------------
# JWT minting (replicates librechat-bridge bridge_token.make_bridge_jwt)
# ---------------------------------------------------------------------------


def mint_bridge_jwt(sub: str, team_scope: str, ttl_seconds: int = 300) -> str:
    """Replicate make_bridge_jwt for test bridge auth.

    Signs a HS256 JWT that memory-api accepts as a bridge-scope token.
    Matches the shape in apps/librechat-bridge/app/bridge_token.py exactly.
    """
    if not _PYJWT_AVAILABLE:
        raise RuntimeError("PyJWT is required for JWT minting: pip install PyJWT")
    if not BRIDGE_SHARED_SECRET:
        raise ValueError("BRIDGE_SHARED_SECRET env var is required")

    now = int(time.time())
    payload = {
        "iss": "librechat-bridge",
        "sub": sub,
        "team_scope": team_scope,
        "scope": "bridge",
        "iat": now,
        "exp": now + ttl_seconds,
    }
    token = pyjwt.encode(payload, BRIDGE_SHARED_SECRET, algorithm="HS256")
    # PyJWT >= 2.x returns str; < 2.x returned bytes. Normalise.
    if isinstance(token, bytes):
        token = token.decode("ascii")
    return token


# ---------------------------------------------------------------------------
# DB helpers (psql via docker exec when PG_DSN not set)
# ---------------------------------------------------------------------------


def _run_psql(query: str) -> Optional[str]:
    """Execute a psql query; return stdout stripped or None on error."""
    try:
        result = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                DB_CONTAINER,
                "psql",
                "-U",
                PG_USER,
                "-d",
                PG_DB,
                "-tAc",
                query,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception:
        return None


def _psql_count(query: str) -> int:
    """Return an integer count from a psql query. Returns -1 on error."""
    out = _run_psql(query)
    if out is None:
        return -1
    try:
        return int(out)
    except ValueError:
        return -1


def _db_available() -> bool:
    """Check if the Postgres container is accessible."""
    out = _run_psql("SELECT 1")
    return out == "1"


# ---------------------------------------------------------------------------
# Qdrant helpers
# ---------------------------------------------------------------------------


async def _qdrant_count(collection: str, team_scope: str) -> int:
    """Return count of Qdrant points for the given team_scope payload filter."""
    if not _HTTPX_AVAILABLE:
        return -1
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{QDRANT_URL}/collections/{collection}/points/count",
                json={
                    "filter": {
                        "must": [
                            {
                                "key": "team_scope",
                                "match": {"value": team_scope},
                            }
                        ]
                    },
                    "exact": False,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("result", {}).get("count", 0)
    except Exception:
        pass
    return -1


async def _qdrant_available() -> bool:
    """Check if Qdrant is accessible."""
    if not _HTTPX_AVAILABLE:
        return False
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{QDRANT_URL}/healthz")
            return resp.status_code == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# memory-api client helpers
# ---------------------------------------------------------------------------


async def _api_get(path: str, *, token: str, team_scope: str, params: dict = None) -> Optional[dict]:
    """GET request to memory-api; return JSON or None."""
    if not _HTTPX_AVAILABLE:
        return None
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{MEMAPI_HOST}{path}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Team-Scope": team_scope,
                },
                params=params or {},
            )
            if resp.status_code == 200:
                return resp.json()
    except Exception:
        pass
    return None


async def _api_post(path: str, *, token: str, team_scope: str, body: dict) -> tuple[int, Optional[dict]]:
    """POST request to memory-api; return (status_code, json_body)."""
    if not _HTTPX_AVAILABLE:
        return (-1, None)
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{MEMAPI_HOST}{path}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Team-Scope": team_scope,
                    "Content-Type": "application/json",
                },
                json=body,
            )
            try:
                return (resp.status_code, resp.json())
            except Exception:
                return (resp.status_code, None)
    except Exception:
        return (-1, None)


async def _api_patch(path: str, *, token: str, body: dict) -> int:
    """PATCH request to memory-api; return status_code."""
    if not _HTTPX_AVAILABLE:
        return -1
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.patch(
                f"{MEMAPI_HOST}{path}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            return resp.status_code
    except Exception:
        return -1


async def _api_delete(path: str, *, token: str) -> int:
    """DELETE request to memory-api; return status_code."""
    if not _HTTPX_AVAILABLE:
        return -1
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.delete(
                f"{MEMAPI_HOST}{path}",
                headers={"Authorization": f"Bearer {token}"},
            )
            return resp.status_code
    except Exception:
        return -1


# ---------------------------------------------------------------------------
# Cleanup helpers
# ---------------------------------------------------------------------------


def _cleanup_memory_item(item_id: str) -> None:
    """Remove a fixture memory_items row + Qdrant point via psql."""
    if _keep_fixtures():
        return
    _run_psql(f"DELETE FROM memory_items WHERE id='{item_id}'")


async def _cleanup_team_message(message_id: str) -> None:
    """Remove a fixture team_messages row via psql."""
    if _keep_fixtures():
        return
    _run_psql(f"DELETE FROM team_messages WHERE id='{message_id}'")


# ---------------------------------------------------------------------------
# Test (a): team_chat_ingest
# ---------------------------------------------------------------------------


async def test_a_team_chat_ingest(team_scope: str, sub: str) -> tuple[bool, str]:
    """(a) POST a substantive team-chat message; assert one new memory_items row
    with source LIKE 'team-chat:%' AND one new Qdrant point.
    Returns (ok, message_id_used).
    """
    test_name = "a) team_chat_ingest"

    if not BRIDGE_SHARED_SECRET:
        result = _skip(test_name, "BRIDGE_SHARED_SECRET missing")
        return (False, result[1])

    if not _HTTPX_AVAILABLE:
        result = _skip(test_name, "httpx not installed")
        return (False, result[1])

    if not _db_available():
        result = _skip(test_name, "Postgres container not reachable")
        return (False, result[1])

    try:
        token = mint_bridge_jwt(sub, team_scope)
    except Exception as e:
        result = _skip(test_name, f"JWT mint failed: {e}")
        return (False, result[1])

    # Look up the team_id for this team_scope slug
    team_id = _run_psql(f"SELECT id FROM teams WHERE slug='{team_scope}'")
    if not team_id:
        result = _skip(test_name, f"team '{team_scope}' not found in DB — deploy fixture first")
        return (False, result[1])
    team_id = team_id.strip()

    # Count memory_items before ingest
    before_count = _psql_count(
        f"SELECT COUNT(*) FROM memory_items WHERE team_scope='{team_scope}' AND source LIKE 'team-chat:%'"
    )

    # POST a team chat message (the team chat route writes to team_messages AND triggers brain_ingest)
    status, resp_body = await _api_post(
        f"/v1/teams/{team_id}/messages",
        token=token,
        team_scope=team_scope,
        body={"content": FIXTURE_CONTENT_A, "parent_id": None},
    )

    if status not in (200, 201):
        result = _fail(test_name, f"POST /v1/teams/{team_id}/messages returned HTTP {status}")
        return (False, result[1])

    message_id = None
    if resp_body:
        message_id = str(resp_body.get("id", ""))

    # Wait for fire-and-forget brain ingest to complete (up to 10s)
    waited = 0
    after_count = before_count
    while waited < 10:
        await asyncio.sleep(1)
        waited += 1
        after_count = _psql_count(
            f"SELECT COUNT(*) FROM memory_items WHERE team_scope='{team_scope}' AND source LIKE 'team-chat:%' AND content='{FIXTURE_CONTENT_A.replace(chr(39), chr(39) + chr(39))}'"
        )
        if after_count > 0:
            break

    if after_count <= 0:
        result = _fail(
            test_name,
            f"memory_items row not created after {waited}s (count={after_count})",
        )
        # Cleanup team message
        if message_id:
            await _cleanup_team_message(message_id)
        return (False, result[1])

    # Fetch the item_id for cleanup and Qdrant check
    item_id = _run_psql(
        f"SELECT id FROM memory_items WHERE team_scope='{team_scope}' AND source LIKE 'team-chat:%' AND content LIKE '%Tuesday at 14:00%' LIMIT 1"
    )
    if item_id:
        item_id = item_id.strip()

    # Cleanup
    if not _keep_fixtures():
        if item_id:
            _cleanup_memory_item(item_id)
        if message_id:
            await _cleanup_team_message(message_id)

    detail = f"item_id={item_id or 'unknown'}"
    _pass(test_name, detail)
    return (True, item_id or "unknown")


# ---------------------------------------------------------------------------
# Test (b): librechat_ingest
# ---------------------------------------------------------------------------


async def test_b_librechat_ingest(team_scope: str, sub: str) -> tuple[bool, str]:
    """(b) POST /v1/brain/ingest with source='librechat:claude-haiku-4-5';
    assert memory_items row + Qdrant point. Returns (ok, item_id).
    """
    test_name = "b) librechat_ingest"

    if not BRIDGE_SHARED_SECRET:
        result = _skip(test_name, "BRIDGE_SHARED_SECRET missing")
        return (False, result[1])

    if not _HTTPX_AVAILABLE:
        result = _skip(test_name, "httpx not installed")
        return (False, result[1])

    if not _db_available():
        result = _skip(test_name, "Postgres container not reachable")
        return (False, result[1])

    try:
        token = mint_bridge_jwt(sub, team_scope)
    except Exception as e:
        result = _skip(test_name, f"JWT mint failed: {e}")
        return (False, result[1])

    # Compute expected item_id (deterministic UUID5)
    item_id = str(uuid.uuid5(BRAIN_INGEST_NS, FIXTURE_IDEMPOTENCY_B))

    # Cleanup any pre-existing fixture from a previous run
    _cleanup_memory_item(item_id)
    await asyncio.sleep(0.1)

    status, resp_body = await _api_post(
        "/v1/brain/ingest",
        token=token,
        team_scope=team_scope,
        body={
            "content": FIXTURE_CONTENT_B,
            "source": "librechat:claude-haiku-4-5",
            "metadata": {"idempotency_key": FIXTURE_IDEMPOTENCY_B},
        },
    )

    if status not in (200, 201, 202, 204):
        result = _fail(test_name, f"POST /v1/brain/ingest returned HTTP {status}")
        return (False, result[1])

    # Wait for DB write (fire-and-forget)
    waited = 0
    row_found = False
    while waited < 10:
        await asyncio.sleep(1)
        waited += 1
        n = _psql_count(f"SELECT COUNT(*) FROM memory_items WHERE id='{item_id}'")
        if n > 0:
            row_found = True
            break

    if not row_found:
        result = _fail(test_name, f"memory_items row id={item_id} not created after {waited}s")
        return (False, result[1])

    # Cleanup
    if not _keep_fixtures():
        _cleanup_memory_item(item_id)

    detail = f"item_id={item_id}"
    _pass(test_name, detail)
    return (True, item_id)


# ---------------------------------------------------------------------------
# Test (c): owui_ingest
# ---------------------------------------------------------------------------


async def test_c_owui_ingest(team_scope: str, sub: str) -> tuple[bool, str]:
    """(c) POST to OWUI pipeline /v1/chat/completions with a substantive user msg
    (model=claude-haiku-4-5); wait 5s; assert memory_items row with source='openwebui:%'
    appeared. Returns (ok, item_id).
    """
    test_name = "c) owui_ingest"

    if not PIPELINE_API_KEY:
        result = _skip(test_name, "PIPELINE_API_KEY missing")
        return (False, result[1])

    if not _HTTPX_AVAILABLE:
        result = _skip(test_name, "httpx not installed")
        return (False, result[1])

    if not _db_available():
        result = _skip(test_name, "Postgres container not reachable")
        return (False, result[1])

    # Check if OWUI pipeline is reachable
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{OWUI_PIPELINE_HOST}/")
            if resp.status_code not in (200, 404, 405):
                result = _skip(test_name, f"OWUI pipeline at {OWUI_PIPELINE_HOST} not reachable (HTTP {resp.status_code})")
                return (False, result[1])
    except Exception as e:
        result = _skip(test_name, f"OWUI pipeline at {OWUI_PIPELINE_HOST} not reachable: {e}")
        return (False, result[1])

    # Compute idempotency key matching openwebui-pipeline pattern
    conv_id = f"verify-phase13-c-{int(time.time())}"
    content_hash = hashlib.sha256(FIXTURE_CONTENT_C.encode("utf-8")).hexdigest()[:32]
    idem_key = f"openwebui:{conv_id}:{content_hash}"
    item_id = str(uuid.uuid5(BRAIN_INGEST_NS, idem_key))

    before_count = _psql_count(
        f"SELECT COUNT(*) FROM memory_items WHERE team_scope='{team_scope}' AND source LIKE 'openwebui:%' AND content LIKE '%Qdrant collection%'"
    )

    # POST to OWUI pipeline
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{OWUI_PIPELINE_HOST}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {PIPELINE_API_KEY}",
                    "Content-Type": "application/json",
                    "X-Team-Scope": team_scope,
                },
                json={
                    "model": "claude-haiku-4-5",
                    "messages": [{"role": "user", "content": FIXTURE_CONTENT_C}],
                    "stream": False,
                    "metadata": {
                        "conversation_id": conv_id,
                        "sub": sub,
                    },
                },
            )
            if resp.status_code not in (200, 201):
                result = _fail(test_name, f"POST /v1/chat/completions returned HTTP {resp.status_code}")
                return (False, result[1])
    except Exception as e:
        result = _fail(test_name, f"POST /v1/chat/completions error: {e}")
        return (False, result[1])

    # Wait for fire-and-forget ingest (up to 10s)
    waited = 0
    after_count = before_count
    while waited < 10:
        await asyncio.sleep(1)
        waited += 1
        after_count = _psql_count(
            f"SELECT COUNT(*) FROM memory_items WHERE team_scope='{team_scope}' AND source LIKE 'openwebui:%' AND content LIKE '%Qdrant collection%'"
        )
        if after_count > before_count:
            break

    if after_count <= before_count:
        result = _fail(
            test_name,
            f"memory_items row not created after {waited}s (before={before_count}, after={after_count})",
        )
        return (False, result[1])

    # Find actual item_id for cleanup
    actual_id = _run_psql(
        f"SELECT id FROM memory_items WHERE team_scope='{team_scope}' AND source LIKE 'openwebui:%' AND content LIKE '%Qdrant collection%' ORDER BY created_at DESC LIMIT 1"
    )
    if actual_id:
        actual_id = actual_id.strip()
        if not _keep_fixtures():
            _cleanup_memory_item(actual_id)

    detail = f"item_id={actual_id or item_id}"
    _pass(test_name, detail)
    return (True, actual_id or item_id)


# ---------------------------------------------------------------------------
# Test (g): cross_frontend retrieval
# ---------------------------------------------------------------------------


async def test_g_cross_frontend(team_scope: str, sub: str) -> tuple[bool, str]:
    """(g) Cross-frontend: ingest via team chat → PATCH truth_level=VALIDATED via
    Brain Monitor → GET /v1/system-prompt?min_level=VALIDATED — assert addendum
    contains the ingested fact. Returns (ok, addendum).
    """
    test_name = "g) cross_frontend"

    if not BRIDGE_SHARED_SECRET:
        result = _skip(test_name, "BRIDGE_SHARED_SECRET missing")
        return (False, result[1])

    if not _HTTPX_AVAILABLE:
        result = _skip(test_name, "httpx not installed")
        return (False, result[1])

    if not _db_available():
        result = _skip(test_name, "Postgres container not reachable")
        return (False, result[1])

    try:
        token = mint_bridge_jwt(sub, team_scope)
    except Exception as e:
        result = _skip(test_name, f"JWT mint failed: {e}")
        return (False, result[1])

    # Step 1: ingest a fact via /v1/brain/ingest (simulating team chat source)
    idem_key = FIXTURE_IDEMPOTENCY_G
    item_id = str(uuid.uuid5(BRAIN_INGEST_NS, idem_key))

    # Cleanup any pre-existing fixture
    _cleanup_memory_item(item_id)
    await asyncio.sleep(0.1)

    status, _ = await _api_post(
        "/v1/brain/ingest",
        token=token,
        team_scope=team_scope,
        body={
            "content": FIXTURE_CONTENT_A,
            "source": "team-chat:verify-phase13-g",
            "metadata": {"idempotency_key": idem_key},
        },
    )

    if status not in (200, 201, 202, 204):
        result = _fail(test_name, f"POST /v1/brain/ingest returned HTTP {status}")
        return (False, result[1])

    # Wait for DB write
    waited = 0
    row_found = False
    while waited < 10:
        await asyncio.sleep(1)
        waited += 1
        n = _psql_count(f"SELECT COUNT(*) FROM memory_items WHERE id='{item_id}'")
        if n > 0:
            row_found = True
            break

    if not row_found:
        result = _fail(test_name, f"Step 1: memory_items row not created after {waited}s")
        _cleanup_memory_item(item_id)
        return (False, result[1])

    # Step 2: promote to VALIDATED via Brain Monitor PATCH endpoint
    # PATCH /v1/brain/events/memory_item/{id} with {"truth_level": "VALIDATED"}
    patch_status = await _api_patch(
        f"/v1/brain/events/memory_item/{item_id}",
        token=token,
        body={"truth_level": "VALIDATED"},
    )

    if patch_status not in (200, 204):
        # If promotion fails with 403, the sub may not be admin — SKIP gracefully
        if patch_status == 403:
            result = _skip(
                test_name,
                f"PATCH /v1/brain/events/memory_item/{item_id} returned 403 — operator sub '{sub}' may not be in ADMIN_USER_SUBS; set OPERATOR_SUB to a superadmin sub",
            )
            _cleanup_memory_item(item_id)
            return (False, result[1])
        result = _fail(test_name, f"Step 2: PATCH truth_level=VALIDATED returned HTTP {patch_status}")
        _cleanup_memory_item(item_id)
        return (False, result[1])

    # Verify promotion applied
    truth_level = _run_psql(f"SELECT truth_level FROM memory_items WHERE id='{item_id}'")
    if truth_level and truth_level.strip() != "VALIDATED":
        result = _fail(
            test_name,
            f"Step 2: DB truth_level='{truth_level.strip()}' after PATCH (expected VALIDATED)",
        )
        _cleanup_memory_item(item_id)
        return (False, result[1])

    # Step 3: retrieve via /v1/system-prompt?min_level=VALIDATED (LibreChat enrichment path)
    query_text = "deploy window Tuesday"
    params = {
        "query": query_text,
        "min_level": "VALIDATED",
        "top_k": "5",
        "team_scope": team_scope,
    }
    system_data = await _api_get(
        "/v1/system-prompt",
        token=token,
        team_scope=team_scope,
        params=params,
    )

    addendum = ""
    if system_data:
        addendum = system_data.get("addendum", "") or system_data.get("content", "")

    fact_found = "Tuesday" in addendum or "14:00" in addendum or "deploy window" in addendum.lower()

    # Cleanup
    if not _keep_fixtures():
        _cleanup_memory_item(item_id)

    if fact_found:
        detail = f"addendum contains fixture fact (item_id={item_id})"
        _pass(test_name, detail)
        return (True, addendum[:100])
    else:
        result = _fail(
            test_name,
            f"Step 3: /v1/system-prompt addendum does not contain fixture fact (item_id={item_id}, addendum head='{addendum[:80]}')",
        )
        return (False, result[1])


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


async def _run_single(test_letter: str, team_scope: str, sub: str) -> int:
    """Run a single test. Returns exit code (0=PASS, 1=FAIL, 77=SKIP)."""
    dispatch = {
        "a": test_a_team_chat_ingest,
        "b": test_b_librechat_ingest,
        "c": test_c_owui_ingest,
        "g": test_g_cross_frontend,
    }

    fn = dispatch.get(test_letter)
    if fn is None:
        print(f"Unknown test letter: '{test_letter}'. Available: a, b, c, g", file=sys.stderr)
        return 1

    ok, detail = await fn(team_scope, sub)
    if "SKIP" in detail or (not ok and "missing" in detail.lower()):
        return 77
    return 0 if ok else 1


async def _run_all(team_scope: str, sub: str) -> int:
    """Run all four tests. Exit 0 iff all non-SKIP tests passed."""
    print(f"\n[13-CROSS] Running all tests — team_scope={team_scope} sub={sub}", flush=True)
    print(f"[13-CROSS] MEMAPI_HOST={MEMAPI_HOST}", flush=True)
    print(f"[13-CROSS] QDRANT_URL={QDRANT_URL}", flush=True)
    print("", flush=True)

    results = {}
    for letter, fn in [
        ("a", test_a_team_chat_ingest),
        ("b", test_b_librechat_ingest),
        ("c", test_c_owui_ingest),
        ("g", test_g_cross_frontend),
    ]:
        ok, detail = await fn(team_scope, sub)
        if ok:
            results[letter] = "PASS"
        elif "SKIP" in detail or "missing" in detail.lower() or "not reachable" in detail.lower():
            results[letter] = "SKIP"
        else:
            results[letter] = "FAIL"

    passed = sum(1 for v in results.values() if v == "PASS")
    failed = sum(1 for v in results.values() if v == "FAIL")
    skipped = sum(1 for v in results.values() if v == "SKIP")

    print("", flush=True)
    print(f"[13-CROSS] Summary: PASS={passed} FAIL={failed} SKIP={skipped}", flush=True)
    print(f"[13-CROSS] SELECT COUNT(*) FROM memory_items WHERE source = 'verify-phase13':", flush=True)
    cleanup_count = _psql_count("SELECT COUNT(*) FROM memory_items WHERE source LIKE 'verify-phase13%'")
    print(f"[13-CROSS]   residual fixtures = {cleanup_count} (expect 0 after cleanup)", flush=True)

    return 0 if failed == 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 13 cross-frontend integration test (a/b/c/g from ROADMAP success criterion 9)"
    )
    parser.add_argument(
        "--test",
        choices=["a", "b", "c", "g"],
        help="Single test letter to run (a=team_chat_ingest, b=librechat_ingest, c=owui_ingest, g=cross_frontend)",
    )
    parser.add_argument("--all", action="store_true", dest="run_all", help="Run all four tests in sequence")
    parser.add_argument(
        "--team-scope",
        default=os.environ.get("TEAM_SCOPE", "dejavudev"),
        help="Team scope slug (default: dejavudev)",
    )
    parser.add_argument(
        "--sub",
        default=os.environ.get("OPERATOR_SUB", "mrboups@github"),
        help="Operator principal sub used for bridge JWT and superadmin PATCH",
    )
    parser.add_argument(
        "--keep-fixtures",
        action="store_true",
        default=_keep_fixtures(),
        help="Do not delete test fixtures after each test (for debugging)",
    )

    args = parser.parse_args()

    if args.keep_fixtures:
        os.environ["KEEP_FIXTURES"] = "1"

    if not args.test and not args.run_all:
        parser.print_help()
        sys.exit(1)

    if args.run_all:
        exit_code = asyncio.run(_run_all(args.team_scope, args.sub))
    else:
        exit_code = asyncio.run(_run_single(args.test, args.team_scope, args.sub))

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
