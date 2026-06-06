---
phase: quick-260604-glo
plan: 01
subsystem: auth
tags: [oauth2.1, pkce, dcr, mcp, fastmcp, introspection, claude-connector, fastapi, alembic, nginx]

# Dependency graph
requires:
  - phase: Phase 12 (GitHub App migration)
    provides: GITHUB_APP_* settings + auth_github code-exchange/profile/merge helpers reused for consent login
  - phase: Phase 4 (mcp-brain + bridge JWT)
    provides: mcp-brain FastMCP server, _resolve() dual-path auth, mint_bridge_jwt, deps.get_team_scope bridge trust
provides:
  - "memory-api OAuth 2.1 Authorization Server: AS metadata + DCR + authorize+PKCE + token + introspection on un-prefixed public paths"
  - "3 DB tables (oauth_clients, oauth_authorization_codes, oauth_access_tokens) via alembic 0022"
  - "GitHub-login consent flow with single-team binding (reuses Phase 12 auth_github helpers)"
  - "mcp-brain Protected Resource: oat_ introspection branch + audience check, protected-resource metadata, 401/WWW-Authenticate"
  - "Connector write guardrails: source=claude.ai-connector + truth_level capped at WORKING + single bound team_scope"
affects: [claude-connector, mcp-brain, oauth, deploy-runbook]

# Tech tracking
tech-stack:
  added: [uvicorn (explicit dep on mcp-brain)]
  patterns:
    - "Hand-rolled OAuth 2.1 AS as native FastAPI routers mounted WITHOUT /v1 prefix (NOT the MCP SDK auto-registration)"
    - "FastMCP @custom_route serves protected-resource metadata at app ROOT; ASGI middleware emits 401 on unauth /mcp WITHOUT seizing token validation"
    - "Resource/audience normalized (rstrip /) on every store + lookup; tokens stored as SHA-256 hashes only"
    - "In-flight authorize state carried as an HS256-signed JWT (BRIDGE_SHARED_SECRET) — no DB row for state"

key-files:
  created:
    - apps/memory-api/alembic/versions/0022_oauth_as_tables.py
    - apps/memory-api/app/auth/oauth_tokens.py
    - apps/memory-api/app/auth/oauth_store.py
    - apps/memory-api/app/routes/oauth_metadata.py
    - apps/memory-api/app/routes/oauth_register.py
    - apps/memory-api/app/routes/oauth_introspect.py
    - apps/memory-api/app/routes/oauth_authorize.py
    - apps/memory-api/app/routes/oauth_token.py
    - apps/memory-api/app/templates/oauth_consent.html
    - apps/memory-api/tests/test_oauth_as.py
    - apps/mcp-brain/app/oauth_verify.py
    - apps/mcp-brain/tests/test_oauth_resolve.py
  modified:
    - apps/memory-api/app/auth/__init__.py (renamed from app/auth.py — package conversion)
    - apps/memory-api/app/main.py (CORS regex + OAuth router includes)
    - apps/memory-api/app/config.py (OAUTH_ISSUER_URL, OAUTH_RESOURCE_URL)
    - apps/mcp-brain/app/main.py (3-tuple _resolve, oat_ branch, protected-resource route, 401 middleware)
    - apps/mcp-brain/app/memory_client.py (connector source + truth clamp)
    - apps/mcp-brain/app/config.py (OAUTH_* + introspect URL)
    - apps/mcp-brain/pyproject.toml (uvicorn)
    - apps/mcp-brain/tests/test_resolve.py (3-tuple unpacks)
    - infrastructure/nginx/conf.d/20-api.conf (/.well-known/ + /oauth/ proxy, no ACAO)
    - infrastructure/nginx/conf.d/40-mcp.conf (protected-resource fall-through note)
    - infrastructure/docker-compose.yml (OAuth env on memory-api + mcp-brain)

key-decisions:
  - "memory-api is a PLAIN FastAPI app — OAuth routes hand-rolled as native routers (no MCP SDK OAuthAuthorizationServerProvider), mounted un-prefixed"
  - "mcp-brain keeps manual header-based _resolve() and gains an oat_ branch; FastMCP auth=/token_verifier= deliberately NOT used (would break xbt_ + email paths)"
  - "Converted app/auth.py into an app/auth/ package so oauth_store/oauth_tokens are importable as app.auth.* while preserving all existing re-exports (verified by test_auth.py)"
  - "Protected-resource metadata is served at app ROOT (confirmed against installed mcp 1.27.2: custom routes mount at root, default streamable_http_path=/mcp); WWW-Authenticate resource_metadata = https://mcp.grooveos.app/.well-known/oauth-protected-resource"
  - "401 emitted via a thin ASGI middleware on the streamable app (the SDK cannot 401 without taking over auth); build_app() + uvicorn.run wires it, forwarding lifespan"
  - "oat_ resolved to a bridge JWT for the bound team rather than teaching /v1 auth about oat_ (deps.get_team_scope already trusts bridge JWTs)"

patterns-established:
  - "Connector write contract: source=claude.ai-connector, truth_level<=WORKING, single bound team_scope — enforced client-side in mcp-brain memory_client"
  - "Constant-time X-Internal-Secret gate (hmac.compare_digest) for the internal introspection endpoint; missing/wrong secret -> 401 with NO claims and store never called"

requirements-completed: [GLO-OAUTH-AS, GLO-OAUTH-RS, GLO-CONNECTOR-SMOKE]

# Metrics
duration: ~25min
completed: 2026-06-06
---

# Phase quick-260604-glo: Claude.ai Custom Connector OAuth 2.1 layer Summary

**memory-api becomes a hand-rolled OAuth 2.1 Authorization Server (AS metadata + DCR + authorize/PKCE + token + introspection) reusing GitHub sign-in for single-team consent, and mcp-brain becomes a spec-compliant Protected Resource (protected-resource metadata + 401/WWW-Authenticate + oat_ introspection with RFC 8707 audience check + connector write guardrails) — wired but NOT yet deployed.**

> SCOPE: Tasks 1-5 (build + atomic commits) executed for real. **Task 6 server-side DEPLOYED 2026-06-06** (live on the VM, public OAuth surface verified — see "Task 6" below). Only the user-side finish remains: register the GitHub App callback URL, optionally make the App public, and add the connector in Claude.ai.

## Performance

- **Duration:** ~25 min
- **Started:** 2026-06-06T19:49:51Z
- **Completed:** 2026-06-06T20:14:33Z
- **Tasks:** 5 of 6 (Task 6 intentionally deferred — deploy runbook)
- **Files modified/created:** 22

## Accomplishments
- OAuth 2.1 AS storage: alembic 0022 (3 tables) + pure token helpers + async DB store (hashes only, resource normalized, one-time codes).
- AS metadata (S256 + auth method `none` + 5 /oauth/ endpoints), RFC 7591 DCR, RFC 7662 introspection gated by a constant-time X-Internal-Secret.
- Full browser flow: GET /oauth/authorize (registered-redirect_uri + S256 validation, GitHub sign-in via memory-api's own callback), GitHub callback (reuses Phase 12 helpers, single-team auto-skip else English-only consent), POST /oauth/authorize (membership re-check + one-time PKCE-bound code), POST /oauth/token (PKCE + redirect_uri + resource checks, refresh rotation, public client `none`).
- nginx /.well-known/ + /oauth/ proxy on api.grooveos.app with NO nginx ACAO (app-owned CORS, claude.ai added to regex); 40-mcp.conf fall-through documented.
- mcp-brain Protected Resource: 3-tuple _resolve with oat_ branch first, introspection + audience check, protected-resource metadata at root, 401 middleware, and connector write guardrails.

## Task Commits

Each `type="auto"` task was committed atomically:

1. **Task 1: OAuth AS storage — migration 0022 + token/code helpers** — `05ca986` (feat) — verify: py_compile OK, pytest 4 passed / 2 skipped (DB-gated)
2. **Task 2: AS metadata + DCR + introspection endpoints** — `9498469` (feat) — verify: py_compile OK, pytest 8 passed / 2 skipped
3. **Task 3: Authorize + token browser flow with GitHub login + team selection** — `4970b6c` (feat) — verify: py_compile OK, consent template OK, pytest 12 passed / 6 deselected (full file 16 passed / 2 skipped)
4. **Task 4: nginx + compose + CORS wiring** — `37c4a7d` (feat) — verify: CORS regex matches claude.ai, exactly 2 nginx blocks, no duplicate ACAO, compose env present — all assertions passed
5. **Task 5: mcp-brain protected-resource — oat_ introspection + 401 + write guardrails** — `72e0f16` (feat) — verify: py_compile OK, pytest 11 passed (full mcp-brain suite 21 passed)

**Plan metadata commit:** pending (this SUMMARY + STATE/ROADMAP).

_Note: TDD tasks (1,2,3,5) were implemented test-first per the plan's gating convention; pure-helper + route-logic cases run unconditionally with mocks, DB-only cases are `@pytest.mark.integration` and auto-skip without Docker._

## Verify-gate results (per task)

| Task | py_compile | pytest |
|------|-----------|--------|
| 1 | OK | 4 passed, 2 skipped (DB-gated) |
| 2 | OK | 8 passed, 2 skipped |
| 3 | OK (+ consent template assertion) | 12 passed (-k filter), 6 deselected; full file 16 passed / 2 skipped |
| 4 | OK | CORS/nginx/compose shell assertions all passed |
| 5 | OK | 11 passed (full mcp-brain suite 21 passed) |

Final consolidated run: memory-api `test_oauth_as.py` 16 passed / 2 skipped; mcp-brain suite 21 passed. Auth-adjacent regression check (test_auth, internal resolve, phase10/12 auth) 10 passed / 19 skipped — no regressions from the `app.auth` package conversion.

## Confirmed FastMCP facts (executor verification)

- **Installed version:** `mcp 1.27.2` (matches pyproject `mcp>=1.27.0`).
- **Custom-route decorator:** `@mcp.custom_route("/path", methods=[...])` taking a Starlette `Request` -> `Response` (confirmed present on the FastMCP instance).
- **Streamable served path:** default `streamable_http_path = "/mcp"` -> MCP endpoint is `https://mcp.grooveos.app/mcp`.
- **Protected-resource served path:** `GET /.well-known/oauth-protected-resource` at the app **ROOT** (NOT under /mcp). Smoke-tested with Starlette TestClient (lifespan active): returns 200 with `resource: https://mcp.grooveos.app/mcp` (no trailing slash).
- **WWW-Authenticate resource_metadata URL wired:** `Bearer resource_metadata="https://mcp.grooveos.app/.well-known/oauth-protected-resource"` (root path, NOT `/mcp/.well-known/...`) — verified live via the 401 on an unauthenticated `GET /mcp`. nginx 40-mcp.conf root location already covers this path; no extra nginx block needed.

## Files Created/Modified
See frontmatter `key-files`. Highlights:
- `apps/memory-api/app/auth/` — was a single `auth.py`; converted to a package (git rename of `auth.py` -> `auth/__init__.py`, R100) so `app.auth.oauth_tokens` / `app.auth.oauth_store` import while all existing `from app.auth import ...` re-exports keep working.
- `apps/memory-api/app/routes/oauth_*.py` — five native FastAPI routers mounted un-prefixed.
- `apps/mcp-brain/app/main.py` — 3-tuple `_resolve`, oat_ branch, protected-resource custom_route, `UnauthenticatedMCP401Middleware`, `build_app()` + uvicorn run.

## Decisions Made
See frontmatter `key-decisions`. The architecturally load-bearing ones:
1. OAuth AS is hand-rolled native FastAPI (memory-api is a plain FastAPI app, not FastMCP) — honored the plan's ARCHITECTURE CORRECTION.
2. mcp-brain keeps its manual `_resolve()`; no FastMCP `auth=`/`token_verifier=` (would seize all token validation).
3. `app/auth.py` -> `app/auth/` package conversion to satisfy the plan's `app/auth/oauth_*.py` paths without breaking existing imports.
4. 401 via ASGI middleware on the streamable app (build_app + uvicorn.run) rather than a `/mcp` custom_route (which would shadow the streamable mount).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] app/auth was a module, not a package**
- **Found during:** Task 1
- **Issue:** The plan's file paths `app/auth/oauth_store.py` and `app/auth/oauth_tokens.py` assume `app.auth` is a package, but the repo had a single `app/auth.py` module (imported widely as `from app.auth import verify_bridge_jwt, ...`).
- **Fix:** `git mv app/auth.py app/auth/__init__.py` (preserves history + all re-exports), then added the two new submodules.
- **Files modified:** apps/memory-api/app/auth/__init__.py (rename), oauth_store.py, oauth_tokens.py
- **Verification:** `test_auth.py` 10 passed / 3 skipped; explicit import check of all re-exports + new submodules succeeded.
- **Committed in:** `05ca986`

**2. [Rule 1 - Bug] FastAPI rejected the union return annotation on authorize routes**
- **Found during:** Task 3
- **Issue:** `-> HTMLResponse | RedirectResponse` made FastAPI try to build a response model -> `FastAPIError: Invalid args for response field`.
- **Fix:** Added `response_model=None` to the three authorize routes (GET/POST /oauth/authorize, GET /oauth/github-callback).
- **Files modified:** apps/memory-api/app/routes/oauth_authorize.py
- **Verification:** Task 3 pytest 12 passed.
- **Committed in:** `4970b6c`

**3. [Rule 1 - Bug] WWW-Authenticate resource_metadata URL was malformed**
- **Found during:** Task 5
- **Issue:** First implementation used `OAUTH_RESOURCE_URL.split("/mcp")[0]`, which also split at `//mcp.grooveos...` producing `https:/...`.
- **Fix:** Derive the resource host root via `urllib.parse.urlparse` (scheme + netloc) so only the trailing `/mcp` path segment is dropped.
- **Files modified:** apps/mcp-brain/app/main.py
- **Verification:** Live 401 smoke test shows `resource_metadata="https://mcp.grooveos.app/.well-known/oauth-protected-resource"`.
- **Committed in:** `72e0f16`

**4. [Rule 1 - Bug] Existing test_resolve.py unpacked the old 2-tuple**
- **Found during:** Task 5
- **Issue:** Changing `_resolve()` to a 3-tuple broke the 4 existing 2-value unpacks in `tests/test_resolve.py`.
- **Fix:** Updated the 4 unpacks to 3-tuple and asserted `is_connector is False` on the xbt_ + email paths.
- **Files modified:** apps/mcp-brain/tests/test_resolve.py
- **Verification:** Full mcp-brain suite 21 passed.
- **Committed in:** `72e0f16`

---

**Total deviations:** 4 auto-fixed (1 blocking, 3 bugs). All necessary for the plan's own correctness; no scope creep.

## Deferred Items

**`/v1/tasks` source enum — CLOSED (migration 0023, commit `412d1a8`).**
- The memory-api `TaskCreateBody.source` field was `pattern=^(granola|agent|chat|manual)$` with `extra="forbid"`, and the `tasks.source` column was `VARCHAR(16)` with a matching CHECK — so the 20-char connector tag was rejected on two counts. Migration `0023_tasks_source_connector` widens the column to `VARCHAR(32)` and extends the CHECK + Pydantic pattern to include `claude.ai-connector`. All three connector write tools (`memory_add`, `task_create`, `contact_add`) now tag provenance with no hedge. Applied live (alembic head = `0023`).
- Separately, the current `task_create`/`contact_add` client bodies were already missing required route fields (`source`; tasks also rejects `assignee_email` under `extra="forbid"`) BEFORE this plan — a pre-existing condition left untouched per the scope boundary. Logged for awareness.

## Known Stubs
None introduced by this plan. The consent template renders real teams; no hardcoded empty/placeholder data paths.

## Threat Flags
None beyond the plan's `<threat_model>`. New surface (OAuth endpoints, protected-resource 401) is exactly the threat register's `mitigate` items (T-glo-01..08), all implemented and asserted by tests.

## Issues Encountered
- `mcp` was not installed in the local Windows env. Installed `mcp==1.27.2` locally to (a) confirm the exact FastMCP custom_route + streamable_http_path API the plan asked the executor to verify, and (b) run the mcp-brain pytest gate + a live 401/well-known smoke test. No production change resulted from this; it only enabled real verification.

## User Setup Required
None for the build. The **deploy + Claude.ai connect** is Task 6 (see below) and requires the operator.

## Task 6 (DEPLOYED 2026-06-06 — server side complete)

Deployed to the VM via surgical `git archive HEAD` of the 5 changed paths -> tar extract -> rebuild memory-api + mcp-brain -> boot-time `alembic upgrade head` -> nginx reload. alembic head = `0023`; all 33 containers healthy.

**Incident (resolved): migration 0023 crash-loop.** First boot, `0023`'s `ALTER TABLE tasks ALTER COLUMN source TYPE` failed — the `v_brain_events` view (Brain Monitor, Phase 11) depends on `tasks.source` (`cannot alter type of a column used by a view or rule`), and memory-api's boot command runs `alembic upgrade head`, so the container crash-looped (whole batch rolled back, head stayed `0021`). Fixed by rewriting `0023` to capture the live `pg_get_viewdef`, drop the view, widen the column, then recreate the view verbatim (robust across envs); rebuilt + restarted. Repo (commit `412d1a8`) matches deployed. Downtime was limited to memory-api/mcp-brain during the loop; no other service affected, no data touched.

**Live verification (public path, as Claude.ai will hit it):**
- `GET https://api.grooveos.app/.well-known/oauth-authorization-server` -> 200, S256 + `none` + all 5 `/oauth/` endpoints.
- CORS: `Origin: https://claude.ai` -> exactly **one** `Access-Control-Allow-Origin: https://claude.ai` (blocker-1 clean).
- `GET https://mcp.grooveos.app/.well-known/oauth-protected-resource` -> `resource=https://mcp.grooveos.app/mcp` (no trailing slash), `authorization_servers=[https://api.grooveos.app]`.
- Unauthenticated `GET https://mcp.grooveos.app/mcp` -> 401 + `WWW-Authenticate: Bearer resource_metadata="https://mcp.grooveos.app/.well-known/oauth-protected-resource"`.
- DCR `POST /oauth/register` -> `client_id` (public client, `auth_method: none`).
- `GET /oauth/authorize` (registered redirect_uri) -> 302 to GitHub authorize, redirect_uri=`https://api.grooveos.app/oauth/github-callback`, signed `state` JWT (`stage: pre_github`). Unregistered redirect_uri -> 400, no redirect (open-redirect defense).

**Remaining USER-only steps (cannot be done server-side):**
1. **REQUIRED — GitHub App `xbrain` callback URL.** The consent flow sends users to GitHub with `redirect_uri=https://api.grooveos.app/oauth/github-callback`. GitHub requires the host+port to match a registered callback URL. Add `https://api.grooveos.app/oauth/github-callback` to the GitHub App's Callback URLs (it allows multiple; existing sign-in callback stays). Without this, the GitHub login step fails with a redirect_uri mismatch.
2. **For team members other than the App owner — make the GitHub App public** (Settings -> Advanced). A private App can only be authorized by its owner; this is the same blocker already tracked for 2nd-member sign-in/install. The owner's own first connect works while private.
3. **Connect in Claude.ai:** Settings -> Connectors -> Add custom connector -> URL `https://mcp.grooveos.app/mcp` -> complete the browser OAuth (GitHub authorize -> pick ONE team -> Authorize). Then confirm tools list + a `memory_search`, and that a `memory_add` lands as `source='claude.ai-connector'`, `truth_level<=WORKING`, in the bound team only (Brain Monitor).
4. **If Claude.ai GET-loops after auth** (known upstream bug #291, streamable-HTTP+OAuth): we add an `/sse` fallback. Optionally pre-check with `npx @modelcontextprotocol/inspector` against `https://mcp.grooveos.app/mcp`.

## Next Phase Readiness
- Build is complete and green on both services; ready for the operator-run deploy (Task 6).
- One memory-api integration follow-up before connector task-writes land: widen the `/v1/tasks` source enum to accept `claude.ai-connector` (or map it).

## Self-Check: PASSED
All 13 created files verified present; all 5 task commits (05ca986, 9498469, 4970b6c, 37c4a7d, 72e0f16) verified in git history.

---
*Phase: quick-260604-glo*
*Completed (build, Tasks 1-5): 2026-06-06 — Task 6 deploy PENDING*
