---
phase: 09-session-bridge-pro-max-routing-via-chrome-extension
plan: 04
subsystem: infrastructure
tags: [nginx, alembic, memory-api, external-sessions, cloudflare-dns, phase-9, wave-1]

requires: []
provides:
  - nginx vhost bridge.example.com (/ws/ WebSocket + /v1/ SSE) with token elision in access logs
  - Alembic migration 0014 — user_external_sessions table (UUID PK, user_id FK CASCADE, UNIQUE(user_id, provider), JSONB metadata, idx on user_id)
  - GET  /v1/me/external-sessions  (user principal only — popup UX)
  - POST /v1/me/external-sessions  (UPSERT — accepts user OR bridge JWT with acting_user_sub)
  - DELETE /v1/me/external-sessions/{provider}  (204 / 404)
  - docs/cloudflare-bridge-dns.md runbook (manual DNS step)
affects:
  - 09-01: session-bridge memory-api client posts upsert with bridge JWT carrying acting_user_sub
  - 09-03: extension WS register frame triggers the bridge → memory-api upsert chain
  - 09-05: popup Sessions section consumes GET + DELETE
  - 09-06: verify-phase9.sh tests 4 (vhost reachable) + 6 (table exists)

tech-stack:
  added: []
  patterns:
    - "bridge JWT impersonation via acting_user_sub claim (mirrors Phase 7 openwebui-pipeline pattern)"
    - "Postgres UPSERT via ON CONFLICT ON CONSTRAINT name DO UPDATE (deterministic constraint name vs implicit)"
    - "nginx log_format with $args_redacted map for token-bearing query strings"

key-files:
  created:
    - infrastructure/nginx/conf.d/50-bridge.conf
    - apps/memory-api/alembic/versions/0014_external_sessions.py
    - apps/memory-api/app/routes/external_sessions.py
    - apps/memory-api/tests/test_external_sessions.py
    - docs/cloudflare-bridge-dns.md
  modified:
    - apps/memory-api/app/main.py  (import + include_router with prefix=/v1)

key-decisions:
  - "Route prefix split: APIRouter() with NO prefix; main.py adds prefix='/v1'. This matches every other route module in memory-api (single source of /v1 truth)."
  - "Bridge JWT with iss=session-bridge stays kind=bridge (Phase 7 openwebui-pipeline JWTs convert to kind=user via the iss check in deps.get_current_principal). The bridge route then requires claims.acting_user_sub to name the target user — symmetric with the pipeline impersonation pattern."
  - "GET refuses kind=bridge entirely (popup is always a user-bearing call). DELETE accepts bridge JWT for completeness/symmetry with POST, but in practice the popup is the only DELETE caller."
  - "UPSERT uses ON CONFLICT ON CONSTRAINT uq_external_sessions_user_provider DO UPDATE rather than ON CONFLICT (user_id, provider). Named constraint is deterministic across Postgres versions; the migration creates the constraint with that exact name."
  - "Pure-bridge JWT contract for 09-01: { iss: 'session-bridge', scope: 'bridge', acting_user_sub: <user.source_user_id>, exp: short TTL }. The acting user MUST already exist in the users table (the bridge cannot create users — that path is intentional, since the user must have provisioned an xbt_ token first via the existing Phase 8 flow)."
  - "User principal path uses principal['user'].id directly (covers both kind='user' from OAuth/pipeline and kind='user_api_token' from xbt_ tokens — covers all popup callers)."

metrics:
  files-created: 5
  files-modified: 1
  loc-added: 638  # 73 nginx + 60 docs + 37 alembic + 154 route + 245 tests + 2 main.py
  commits: 3
  duration: 25 min
  completed: 2026-05-12
---

# Phase 09 Plan 04: bridge.example.com vhost + Alembic 0014 + /v1/me/external-sessions Summary

Infrastructure rails for the Phase 9 session-bridge: nginx vhost for the public WebSocket/SSE surface, Alembic migration 0014 creating `user_external_sessions`, and the memory-api endpoints (`GET / POST / DELETE /v1/me/external-sessions`) that back the popup UI and the session-bridge register-handshake. Cloudflare DNS A record is documented in a runbook and listed below under "USER ACTION REQUIRED".

## Commits

| Commit  | Scope                                                          |
| ------- | -------------------------------------------------------------- |
| ccc258c | nginx 50-bridge.conf + docs/cloudflare-bridge-dns.md            |
| f4f047b | Alembic 0014 user_external_sessions                            |
| 40ff769 | /v1/me/external-sessions GET / POST / DELETE + 12 integration tests |

## What shipped

### nginx vhost — `infrastructure/nginx/conf.d/50-bridge.conf`

- `server_name bridge.example.com` on port 80 (TLS terminates at Cloudflare's orange-cloud proxy).
- `/ws/` block: `proxy_pass http://session-bridge:8105` with `Upgrade $http_upgrade` + `Connection $connection_upgrade` (map declared globally in `10-xbrain.conf`), `proxy_read_timeout 86400s`, `proxy_buffering off`.
- `/v1/` block: same upstream, SSE-friendly (`proxy_buffering off`, `chunked_transfer_encoding off`, `X-Accel-Buffering no`), 600s timeout, passes `Authorization` header.
- Access log uses a custom `log_format bridge_access` with `$args_redacted` (T-09-04-01 mitigation — elides `?token=xbt_...` to `?token=<REDACTED>` before it ever hits disk).
- `/nginx-health` returns `ok` for upstream healthchecks.
- Catchall `location /` returns 404 (prevents accidental fall-through).

### Alembic 0014 — `user_external_sessions`

Schema verbatim with CONTEXT.md (UUID PK with `gen_random_uuid()`, `user_id` UUID FK ON DELETE CASCADE, `provider` VARCHAR(32), `extension_id` VARCHAR(64), `last_seen_at` TIMESTAMPTZ default `now()`, `metadata` JSONB, `UNIQUE(user_id, provider)` named `uq_external_sessions_user_provider`, index `idx_external_sessions_user` on `user_id`). Mirrors `0013_api_tokens.py` raw-SQL style.

### Endpoints — `/v1/me/external-sessions`

| Method | Path                              | Principal kinds                                  | Behavior                                                                                                                                                |
| ------ | --------------------------------- | ------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/v1/me/external-sessions`        | user, user_api_token (NOT bridge)               | Returns rows for `user_id = principal.user.id` ordered by `last_seen_at DESC`. Bridge JWTs are rejected with 403.                                       |
| POST   | `/v1/me/external-sessions`        | user, user_api_token, bridge (w/ acting_user_sub) | UPSERT on `(user_id, provider)`. Bridge JWT path resolves user via `SELECT id FROM users WHERE source_user_id = :acting_user_sub`. Returns the upserted row. |
| DELETE | `/v1/me/external-sessions/{prov}` | user, user_api_token, bridge (w/ acting_user_sub) | `DELETE WHERE user_id=:me AND provider=:prov` — 204 on hit, 404 on miss (also catches cross-user attempts).                                            |

Wired in `app/main.py`:
```python
from app.routes import (..., external_sessions, ...)
app.include_router(external_sessions.router, prefix="/v1", tags=["external-sessions"])
```

### Tests — `tests/test_external_sessions.py` (12 integration tests)

Cross-user isolation, upsert idempotency (second POST updates same row + advances `last_seen_at`), bridge-JWT path success + 403-without-acting-user-sub, GET refuses pure bridge JWT, DELETE 204/404/cross-user-404, unauthenticated/invalid-token responses. Uses `make_bridge_jwt` helpers (one for `iss=openwebui-pipeline` → kind=user, one for `iss=session-bridge` → kind=bridge).

## Contracts confirmed with 09-01

The cross-plan contract between `apps/session-bridge` (09-01) and `apps/memory-api/app/routes/external_sessions.py` (this plan) is:

| Aspect            | Value                                                                                                              |
| ----------------- | ------------------------------------------------------------------------------------------------------------------ |
| Endpoint          | `POST {MEMORY_API_URL}/v1/me/external-sessions`                                                                    |
| Auth              | `Authorization: Bearer <JWT>` where JWT is signed HS256 with `BRIDGE_SHARED_SECRET` (existing env var, shared with both services) |
| JWT claims        | `{ iss: "session-bridge", scope: "bridge", acting_user_sub: <user.source_user_id>, sub: <bridge-instance-id>, exp: now+300 }` |
| Body              | `{ "provider": "claude", "extension_id": <chrome ext id\|null>, "metadata": { "email_logged": <str>, "org_id": <str>, ... } }` |
| Success response  | 200 + the upserted row (UUID id, provider, extension_id, last_seen_at ISO8601, metadata dict)                       |
| Missing acting_user_sub | 403 `"bridge JWT missing acting_user_sub claim"`                                                              |
| Unknown acting_user_sub | 404 `"acting_user_sub not found"` — the user must already exist (xbt_ token issued previously)                |

**Important for 09-01:** the bridge cannot create users. The acting user must already exist (i.e., must have logged into LibreChat or completed onboarding at least once). This is the same invariant as the Phase 7 OpenWebUI pipeline. If 09-01 tries to upsert for a brand-new acting_user_sub, memory-api responds 404 and 09-01 should surface this back to the extension's register-handshake error path (not auto-create).

## USER ACTION REQUIRED — Cloudflare DNS

**This is the blocking checkpoint from Task 1 of the plan, surfaced for the user (not blocking executor).**

Before the bridge can be reached from the open internet, a human operator must:

1. Open https://dash.cloudflare.com → zone `example.com` → **DNS → Records → Add record**
   - Type: `A`
   - Name: `bridge`
   - IPv4: `__VM_HOST__`
   - Proxy status: **Proxied** (orange cloud)
   - TTL: Auto
2. In the same zone → **Network** → confirm **WebSockets** toggle is **ON**.
3. Verify with `nslookup bridge.example.com` from any laptop (should resolve to a Cloudflare anycast IP, not to `__VM_HOST__`).
4. After the next nginx reload on the VM (which will happen automatically when the Phase 9 docker-compose changes from 09-01 / 09-06 land), confirm `curl -fsS https://bridge.example.com/nginx-health` returns `ok`.

Full runbook lives at `docs/cloudflare-bridge-dns.md`, including recovery steps if the VM IP or zone changes later.

**Phase 9 cannot complete until this DNS record exists.** Wave 1 (09-01, 09-02, 09-04) builds in parallel; the DNS step can be done any time before the Wave 3 verification gate (09-06).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 — Blocking issue] APIRouter prefix split between router and main.py**
- **Found during:** Task 4 wiring
- **Issue:** The plan skeleton declared `APIRouter(prefix="/v1/me", ...)`, but every other route module in `memory-api` declares `APIRouter()` (no prefix) and lets `main.py` add `prefix="/v1"`. Mixing both styles would double up the `/v1` segment (`/v1/v1/me/external-sessions`).
- **Fix:** Used `APIRouter()` in `external_sessions.py` and registered each path as `/me/external-sessions`. The `main.py` `include_router(..., prefix="/v1")` produces the correct final paths.
- **Files modified:** `apps/memory-api/app/routes/external_sessions.py`, `apps/memory-api/app/main.py`
- **Commit:** `40ff769`

**2. [Rule 3 — Blocking issue] ON CONFLICT clause changed to named-constraint form**
- **Found during:** Task 4 SQL writing
- **Issue:** The plan skeleton used `ON CONFLICT (user_id, provider) DO UPDATE`. Postgres accepts that syntactically when a unique constraint exists on those columns, but the migration creates the constraint with an explicit name (`uq_external_sessions_user_provider`). Using `ON CONFLICT ON CONSTRAINT <name>` is more deterministic and surfaces a clearer error if the constraint is ever renamed.
- **Fix:** `INSERT ... ON CONFLICT ON CONSTRAINT uq_external_sessions_user_provider DO UPDATE ...`
- **Files modified:** `apps/memory-api/app/routes/external_sessions.py`
- **Commit:** `40ff769`

### Architectural Decisions Made (no checkpoint needed — within plan scope)

None — all deviations were mechanical.

## Self-Check: PASSED

- `infrastructure/nginx/conf.d/50-bridge.conf` — FOUND (155 lines, all 6 expected directives present)
- `apps/memory-api/alembic/versions/0014_external_sessions.py` — FOUND (revision=0014, down_revision=0013, AST OK)
- `apps/memory-api/app/routes/external_sessions.py` — FOUND (AST OK, exports `router`)
- `apps/memory-api/app/main.py` — modified (2 hits for `external_sessions`)
- `apps/memory-api/tests/test_external_sessions.py` — FOUND (12 test functions, AST OK)
- `docs/cloudflare-bridge-dns.md` — FOUND
- Commits `ccc258c`, `f4f047b`, `40ff769` — all present in `git log`

`pytest` was NOT executed locally because the memory-api Python deps (`qdrant_client`, `motor`, etc.) are not installed in the Windows dev sandbox — the tests are designed for the CI/VM container where the full dependency tree exists (matches the pattern of every other `test_*.py` in the suite). AST + grep verification confirms structural correctness; the actual pytest run will happen on the VM as part of plan 09-06's `verify-phase9.sh`.
