---
phase: quick-260604-glo
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - apps/memory-api/alembic/versions/0022_oauth_as_tables.py
  - apps/memory-api/app/auth/oauth_store.py
  - apps/memory-api/app/auth/oauth_tokens.py
  - apps/memory-api/app/routes/oauth_metadata.py
  - apps/memory-api/app/routes/oauth_register.py
  - apps/memory-api/app/routes/oauth_authorize.py
  - apps/memory-api/app/routes/oauth_token.py
  - apps/memory-api/app/routes/oauth_introspect.py
  - apps/memory-api/app/templates/oauth_consent.html
  - apps/memory-api/app/main.py
  - apps/memory-api/tests/test_oauth_as.py
  - apps/mcp-brain/app/config.py
  - apps/mcp-brain/app/oauth_verify.py
  - apps/mcp-brain/app/memory_client.py
  - apps/mcp-brain/app/main.py
  - apps/mcp-brain/tests/test_oauth_resolve.py
  - infrastructure/nginx/conf.d/20-api.conf
  - infrastructure/nginx/conf.d/40-mcp.conf
  - infrastructure/docker-compose.yml
autonomous: false
requirements: [GLO-OAUTH-AS, GLO-OAUTH-RS, GLO-CONNECTOR-SMOKE]
user_setup: []

must_haves:
  truths:
    - "An unauthenticated MCP request to mcp.grooveos.app returns 401 + WWW-Authenticate pointing at the protected-resource metadata URL"
    - "GET https://mcp.grooveos.app/.well-known/oauth-protected-resource returns JSON whose authorization_servers lists https://api.grooveos.app and whose resource exactly matches the pasted URL (no trailing slash)"
    - "GET https://api.grooveos.app/.well-known/oauth-authorization-server returns AS metadata advertising S256 PKCE, the /oauth/* endpoints, and token_endpoint_auth_methods_supported including 'none'"
    - "Claude.ai can dynamically register a client (POST /oauth/register), complete an authorization-code+PKCE flow where the user logs in via GitHub and picks ONE team, and exchange the code for an oat_ access token bound to that single team_scope"
    - "mcp-brain validates an oat_ access token by introspecting it against memory-api, rejecting tokens whose audience does not match the pasted resource, and serves the full toolset under the bound team_scope"
    - "Connector-originated writes carry source='claude.ai-connector' and a truth_level capped at WORKING, scoped strictly to the token's bound team_scope"
    - "The existing xbt_ token path and the LibreChat email path continue to work unchanged after the OAuth layer is added"
  artifacts:
    - path: "apps/memory-api/alembic/versions/0022_oauth_as_tables.py"
      provides: "oauth_clients, oauth_authorization_codes, oauth_access_tokens tables"
      contains: "CREATE TABLE"
    - path: "apps/memory-api/app/routes/oauth_metadata.py"
      provides: "GET /.well-known/oauth-authorization-server (AS metadata)"
    - path: "apps/memory-api/app/routes/oauth_authorize.py"
      provides: "GET+POST /oauth/authorize consent flow with GitHub login + team selection + registered redirect_uri validation"
    - path: "apps/memory-api/app/routes/oauth_token.py"
      provides: "POST /oauth/token (authorization_code + refresh_token grants, PKCE S256, resource binding)"
    - path: "apps/memory-api/app/routes/oauth_introspect.py"
      provides: "POST /oauth/introspect (RFC 7662) returning active, sub, team_scope, aud, scope; gated by X-Internal-Secret"
    - path: "apps/memory-api/app/routes/oauth_register.py"
      provides: "POST /oauth/register (RFC 7591 dynamic client registration)"
    - path: "apps/mcp-brain/app/oauth_verify.py"
      provides: "introspection-based oat_ token verification returning (sub, team_scope, source)"
    - path: "apps/mcp-brain/app/main.py"
      provides: "protected-resource metadata route + 401/WWW-Authenticate + oat_ branch in _resolve + write guardrails"
      contains: "oauth-protected-resource"
  key_links:
    - from: "mcp-brain _resolve()"
      to: "memory-api POST /oauth/introspect"
      via: "httpx introspection call in oauth_verify.py carrying header X-Internal-Secret: BRIDGE_SHARED_SECRET (constant-time checked AS-side)"
      pattern: "oauth/introspect"
    - from: "Claude.ai connector"
      to: "memory-api GET /.well-known/oauth-authorization-server"
      via: "nginx /.well-known/ proxy block on api.grooveos.app (app owns CORS via CORSMiddleware allow_origin_regex incl. https://claude.ai)"
      pattern: "well-known"
    - from: "memory-api /oauth/authorize consent"
      to: "GitHub sign-in + list_teams_for_user"
      via: "reuse auth_github code-exchange + teams repo"
      pattern: "list_teams_for_user|github"
    - from: "mcp-brain memory_add (oat_ path)"
      to: "memory-api /v1/memory/upsert"
      via: "source='claude.ai-connector' + truth_level capped at WORKING"
      pattern: "claude.ai-connector"
---

<objective>
Make the existing `apps/mcp-brain` remote MCP server connectable as a **Custom Connector in the official Claude.ai app** (web + desktop) by adding the OAuth 2.1 authorization layer the MCP Authorization spec requires. Today mcp-brain only accepts a pasted `xbt_` bearer token (or the internal LibreChat email path); Claude.ai refuses static tokens and instead drives a full OAuth 2.1 browser-redirect flow (protected-resource discovery → DCR → authorize+PKCE → token → introspection).

memory-api becomes the **OAuth 2.1 Authorization Server** (hosts `/.well-known/oauth-authorization-server` + `/oauth/{register,authorize,token,introspect,revoke}`), reusing its existing GitHub identity for the consent login. mcp-brain becomes the **Protected Resource** (advertises `/.well-known/oauth-protected-resource`, returns `401 + WWW-Authenticate` on unauthenticated calls, and validates the issued `oat_` access token by introspecting it against memory-api). The full toolset (read + write) is exposed, with write guardrails: connector-originated writes are tagged `source=claude.ai-connector`, truth_level capped at `WORKING`, and strictly scoped to the single `team_scope` bound at consent time.

Purpose: let a team member use their team brain directly inside Claude.ai with proper per-team isolation, no proprietary auth service, and zero disruption to the existing LibreChat / Chrome-extension / Claude-Code callers.
Output: 3 new DB tables, 5 new memory-api OAuth routes + a consent template, an introspection-based verifier + protected-resource wiring in mcp-brain, two nginx blocks, compose env wiring, unit tests on both services, and a documented (NOT executed) deploy + Claude.ai end-to-end connect smoke test.

**PLAN-ONLY — DO NOT EXECUTE. This plan is for user review and approval first.**
</objective>

<execution_context>
@D:/VSC/xbrain/.claude/get-shit-done/workflows/execute-plan.md
@D:/VSC/xbrain/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@D:/VSC/xbrain/.planning/STATE.md
@D:/VSC/xbrain/CLAUDE.md
@D:/VSC/xbrain/.planning/quick/260604-glo-make-mcp-brain-connectable-as-a-custom-c/260604-glo-CONTEXT.md
@D:/VSC/xbrain/.planning/quick/260604-glo-make-mcp-brain-connectable-as-a-custom-c/260604-glo-RESEARCH.md

# Source files the executor edits / mirrors
@D:/VSC/xbrain/apps/mcp-brain/app/main.py
@D:/VSC/xbrain/apps/mcp-brain/app/memory_client.py
@D:/VSC/xbrain/apps/mcp-brain/app/config.py
@D:/VSC/xbrain/apps/mcp-brain/app/bridge_jwt.py
@D:/VSC/xbrain/apps/memory-api/app/routes/auth_github.py
@D:/VSC/xbrain/apps/memory-api/app/routes/internal.py
@D:/VSC/xbrain/apps/memory-api/app/deps.py
@D:/VSC/xbrain/apps/memory-api/app/main.py
@D:/VSC/xbrain/apps/memory-api/alembic/versions/0013_api_tokens.py
@D:/VSC/xbrain/infrastructure/nginx/conf.d/20-api.conf
@D:/VSC/xbrain/infrastructure/nginx/conf.d/40-mcp.conf

<interfaces>
<!-- Key contracts extracted from the codebase. Use these directly — no exploration needed. -->

ARCHITECTURE CORRECTION vs RESEARCH.md: memory-api is a PLAIN FastAPI app
(apps/memory-api/app/main.py: `app = FastAPI(...)`, routers via app.include_router(..., prefix="/v1")).
It is NOT a FastMCP server, so the MCP SDK's `OAuthAuthorizationServerProvider`
route auto-registration does NOT apply here. The OAuth AS endpoints MUST be
implemented as NATIVE FastAPI routes (new routers, mounted WITHOUT the /v1 prefix
so the public paths are /.well-known/... and /oauth/...). The MCP-SDK provider
classes may be imported for reference but the HTTP surface is hand-rolled FastAPI.
mcp-brain IS a FastMCP server and keeps its manual header-based `_resolve()`; we
add an `oat_` branch that introspects against memory-api (do NOT pass FastMCP
`auth=`/`token_verifier=` — that would seize ALL token validation and break the
existing xbt_ + email paths). The protected-resource metadata route + 401 are
added via a FastMCP custom route / Starlette mount on the same app.

EXISTING CORS on memory-api (apps/memory-api/app/main.py, ~line 91) — load-bearing for blocker 1:
  app.add_middleware(
      CORSMiddleware,
      allow_origin_regex=r"(chrome-extension://.*|https://chat\.grooveos\.app|https://grooveos\.app|https://grooveos\.web\.app|https://dejavu-app\.web\.app)",
      allow_credentials=True,
      allow_methods=["GET","POST","PUT","PATCH","DELETE","OPTIONS"],
      allow_headers=["Authorization","X-Team-Scope","Content-Type","Accept"],
  )
  This regex does NOT cover https://claude.ai. Claude.ai fetches /.well-known/oauth-authorization-server
  + posts /oauth/register + /oauth/token from browser context, so without claude.ai in the regex the
  preflight/ACAO is missing. The app (not nginx) must own ACAO on these paths — if nginx ALSO adds
  `Access-Control-Allow-Origin: *` the browser sees TWO ACAO headers and fails CORS.

From apps/memory-api/app/routes/auth_github.py (reuse for consent login):
  async def _exchange_code_for_token(code: str, redirect_uri: str) -> dict   # GitHub App code -> ghu_ bundle
  async def _fetch_github_profile(token: str) -> dict                        # {github_id, login, display_name, email, org_logins}
  async def _resolve_or_merge_user(session, *, github_id, login, display_name, email) -> User
  settings.GITHUB_APP_CLIENT_ID / GITHUB_APP_CLIENT_SECRET / GITHUB_APP_SLUG  # the App used for sign-in

From apps/memory-api/app/repos/teams.py (consent team selector):
  async def list_teams_for_user(session, *, user_id: UUID) -> list[Team]     # Team has .slug, .display_name
  async def get_membership(session, *, user_id, team_slug) -> TeamMember | None

From apps/memory-api/app/deps.py:
  async def get_session() -> AsyncSession                                    # DB session dependency
  # xbt_ tokens are SHA-256 hashed: hashlib.sha256(token.encode()).hexdigest()
  # user_api_tokens.team_scope is TEXT NOT NULL ('' = multi-team sentinel)

From apps/mcp-brain/app/main.py:
  async def _resolve(ctx: Context) -> tuple[str, str]                        # CURRENT: returns (token_or_jwt, team_scope); raises ValueError on failure
  # Task 5 changes this to a 3-tuple (token, team_scope, is_connector); ALL 8 call sites updated to unpack 3.
  # tools call: token, team_scope = await _resolve(ctx); then memory_client.<tool>(token, team_scope, ...)
  # memory_add(content, project_scope, truth_level="WORKING") is the write surface to guardrail

From apps/mcp-brain/app/memory_client.py:
  def _headers(token: str, team_scope: str) -> dict                          # Authorization: Bearer <token> + X-Team-Scope
  async def memory_add(token, team_scope, content, project_scope, truth_level) -> dict  # builds full MemoryItem, source="mcp-brain"
  # the item dict sets source="mcp-brain" + truth_level — both must be overridable for the connector path

From apps/mcp-brain/app/config.py (Settings, pydantic-settings):
  MEMORY_API_URL: str = "http://memory-api:8000"   # base for introspection call
  BRIDGE_SHARED_SECRET / INTERNAL_EMAIL_PATH_ENABLED (existing email path — untouched)

From apps/mcp-brain/app/bridge_jwt.py:
  def mint_bridge_jwt(*, secret, team_scope, sub, ttl=300) -> str           # mcp-brain can still mint a bridge JWT to call memory-api with the resolved team

Alembic chain head: 0021_brain_events_media (down_revision for the new 0022 migration).
Alembic revision style: see 0013_api_tokens.py — string revision id, op.execute() raw DDL, CREATE TABLE IF NOT EXISTS + indexes.

nginx facts:
  - api.grooveos.app (20-api.conf) currently ONLY proxies /v1/ and /v1/drive-webhook -> memory-api:8000. /.well-known/ and /oauth/ are NOT proxied -> MUST ADD.
  - mcp.grooveos.app (40-mcp.conf) proxies / (everything) -> mcp-brain:8104 with CORS on the root location. /.well-known/oauth-protected-resource already falls through to mcp-brain (good); confirm CORS covers it.
  - Both configs already pass `proxy_set_header Authorization $http_authorization;`.
  - CRITICAL (blocker 1): the memory-api app already emits its own ACAO via CORSMiddleware. nginx MUST NOT add Access-Control-Allow-Origin on /.well-known/ or /oauth/ on api.grooveos.app, or the browser sees duplicate ACAO headers and CORS fails.
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: OAuth AS storage — migration 0022 + token/code helpers</name>
  <files>
    apps/memory-api/alembic/versions/0022_oauth_as_tables.py,
    apps/memory-api/app/auth/oauth_store.py,
    apps/memory-api/app/auth/oauth_tokens.py,
    apps/memory-api/tests/test_oauth_as.py
  </files>
  <behavior>
    - mint_access_token() returns a raw "oat_"+token_urlsafe(32) string and stores ONLY its SHA-256 hash (mirrors the xbt_ pattern in deps.py).
    - introspect_token(raw) returns {active:True, sub, team_scope, resource (aud), scope, source} for a live, non-revoked, non-expired token; {active:False} otherwise (unknown / revoked / expired) — never raises for a bad token.
    - resource/audience values are normalized via rstrip("/") on EVERY store + lookup (Gotcha: trailing-slash mismatch -> silent 401).
    - create_auth_code() stores (client_id, user_id, team_scope, resource, code_challenge S256, redirect_uri, scope, 5-min expiry); consume_auth_code() is one-time (sets used_at; second use returns None).
    - register_client() persists redirect_uris and returns a client_id; rejects a registration whose redirect_uris does not include https://claude.ai/api/mcp/auth_callback only if a strict flag is set (default: accept any, public client).
  </behavior>
  <action>
Create alembic migration `0022_oauth_as_tables.py` (revision="0022", down_revision="0021_brain_events_media", op.execute raw DDL in the 0013 style). Create three tables EXACTLY per RESEARCH.md §6 "DB Schema": `oauth_clients` (client_id PK, client_name, redirect_uris TEXT[], grant_types TEXT[] default '{authorization_code,refresh_token}', registered_at), `oauth_authorization_codes` (code PK, client_id, user_id UUID FK users(id), team_scope NOT NULL, resource NOT NULL, code_challenge NOT NULL, redirect_uri NOT NULL, scope NOT NULL, expires_at NOT NULL, used_at), `oauth_access_tokens` (id UUID PK gen_random_uuid(), token_hash TEXT NOT NULL UNIQUE, client_id, user_id UUID FK users(id), team_scope NOT NULL, resource NOT NULL, scope NOT NULL, source TEXT NOT NULL DEFAULT 'claude.ai-connector', created_at, expires_at, revoked_at, refresh_token_hash TEXT UNIQUE, refresh_token_expires_at). Add indexes on token_hash (WHERE revoked_at IS NULL) and refresh_token_hash. All CREATE TABLE IF NOT EXISTS; downgrade drops all three. Per CONTEXT.md these implement GLO-OAUTH-AS storage.

Create `app/auth/oauth_tokens.py`: pure helpers — `hash_token(raw)->sha256 hex`, `mint_access_token()->raw oat_...`, `mint_refresh_token()->raw ort_...`, `normalize_resource(url)->url.rstrip("/")`. No DB.

Create `app/auth/oauth_store.py`: async DB functions taking an AsyncSession — `register_client`, `create_auth_code`, `consume_auth_code`, `mint_and_store_access_token(...)` (computes hashes, inserts row with the resolved team_scope + normalized resource + source='claude.ai-connector'), `introspect_token(session, raw)->dict` (single indexed lookup by token_hash; checks revoked_at IS NULL and expires_at > now()). Use `sa.text(...)` parameterized SQL consistent with deps.py. Never log raw tokens.

Write `tests/test_oauth_as.py` covering the five behaviors above against an in-memory/sqlite-or-pg test session (mirror the existing memory-api test harness; if integration DB is unavailable, mark those cases gated and unit-test the pure helpers in oauth_tokens.py deterministically — normalize_resource, prefix/length of minted tokens, hash stability).
  </action>
  <verify>
    <automated>cd apps/memory-api && python -m py_compile alembic/versions/0022_oauth_as_tables.py app/auth/oauth_store.py app/auth/oauth_tokens.py && python -m pytest tests/test_oauth_as.py -x -q</automated>
  </verify>
  <done>Migration compiles and defines the 3 tables; oauth_tokens helpers + oauth_store functions exist; test_oauth_as.py passes (pure-helper cases unconditionally; DB cases pass or are explicitly gated when no test DB).</done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: AS metadata + DCR + introspection endpoints (no browser flow yet)</name>
  <files>
    apps/memory-api/app/routes/oauth_metadata.py,
    apps/memory-api/app/routes/oauth_register.py,
    apps/memory-api/app/routes/oauth_introspect.py,
    apps/memory-api/app/main.py,
    apps/memory-api/tests/test_oauth_as.py
  </files>
  <behavior>
    - GET /.well-known/oauth-authorization-server -> 200 JSON with issuer=https://api.grooveos.app, authorization_endpoint/token_endpoint/registration_endpoint/introspection_endpoint/revocation_endpoint under /oauth/, response_types_supported=["code"], grant_types_supported=["authorization_code","refresh_token"], code_challenge_methods_supported=["S256"], token_endpoint_auth_methods_supported INCLUDES "none", scopes_supported=["brain:read","brain:write"]. (Gotchas: S256 mandatory; "none" mandatory for public client.)
    - POST /oauth/register with {client_name, redirect_uris:["https://claude.ai/api/mcp/auth_callback"], grant_types, response_types} -> 201 with {client_id, redirect_uris, ...}. Accepts public clients (no client_secret).
    - POST /oauth/introspect with the correct X-Internal-Secret header and {token} -> 200 {active:true, sub, team_scope, aud, scope, source} for a live oat_; {active:false} otherwise (RFC 7662).
    - POST /oauth/introspect with a MISSING or WRONG X-Internal-Secret header -> 401 (or 403); it never reveals token claims to an uncredentialed caller.
  </behavior>
  <action>
Issuer base URL comes from a new setting `OAUTH_ISSUER_URL` (default "https://api.grooveos.app") and resource default `OAUTH_RESOURCE_URL` (default "https://mcp.grooveos.app/mcp") added to memory-api `app/config.py` Settings.

Create `oauth_metadata.py`: a router with `GET /.well-known/oauth-authorization-server` returning the static metadata dict above (built from OAUTH_ISSUER_URL). Create `oauth_register.py`: `POST /oauth/register` validating the body (pydantic), calling oauth_store.register_client, returning the RFC 7591 response. Create `oauth_introspect.py`: `POST /oauth/introspect` (accepts form OR json `token`), calling oauth_store.introspect_token, returning the RFC 7662 body; this endpoint is internal (called by mcp-brain) — gate it with the existing BRIDGE_SHARED_SECRET via an X-Internal-Secret header check using `hmac.compare_digest` (constant-time). If the header is missing or does not match, return 401 (do NOT call introspect_token, do NOT leak claims) — it must NOT require the bearer it is validating.

Wire all three routers in `app/main.py` via `app.include_router(...)` WITH NO prefix (public paths must be exactly /.well-known/oauth-authorization-server and /oauth/register, /oauth/introspect). Place them after the existing includes.

Extend `tests/test_oauth_as.py`: use FastAPI TestClient to assert the metadata JSON contains S256 + "none" + the five /oauth/ endpoints; assert /oauth/register returns a client_id; assert /oauth/introspect with the correct X-Internal-Secret returns active:false for a bogus token and active:true (with team_scope) for a token minted via Task 1's mint_and_store_access_token; AND assert /oauth/introspect with a missing X-Internal-Secret returns 401/403 and with a wrong secret returns 401/403 (token claims never returned). Mock or gate the DB-backed cases as in Task 1.
  </action>
  <verify>
    <automated>cd apps/memory-api && python -m py_compile app/routes/oauth_metadata.py app/routes/oauth_register.py app/routes/oauth_introspect.py app/main.py && python -m pytest tests/test_oauth_as.py -x -q</automated>
  </verify>
  <done>The three routers compile and are mounted at the un-prefixed public paths; metadata advertises S256 + "none" + all /oauth/ endpoints; register behaves per RFC 7591; /oauth/introspect returns RFC 7662 bodies ONLY with a valid X-Internal-Secret and returns 401/403 (no claims) when the X-Internal-Secret is missing or wrong — asserted via TestClient; tests pass (DB cases gated when no test DB).</done>
</task>

<task type="auto" tdd="true">
  <name>Task 3: Authorize + token browser flow with GitHub login + team selection</name>
  <files>
    apps/memory-api/app/routes/oauth_authorize.py,
    apps/memory-api/app/routes/oauth_token.py,
    apps/memory-api/app/templates/oauth_consent.html,
    apps/memory-api/app/main.py,
    apps/memory-api/tests/test_oauth_as.py
  </files>
  <behavior>
    - PKCE failure: POST /oauth/token with a code_verifier whose SHA256(verifier) base64url != the stored code_challenge -> error (400 invalid_grant), no token issued.
    - Code replay: a code that was already exchanged once (used_at set) -> second POST /oauth/token returns error (400 invalid_grant), no token issued.
    - Unregistered / mismatched redirect_uri: GET /oauth/authorize with a redirect_uri NOT in the client's registered oauth_clients.redirect_uris -> 400 (do NOT redirect to it). POST /oauth/token whose redirect_uri != the code's stored redirect_uri -> 400.
    - Resource mismatch: POST /oauth/token whose normalized `resource` != the code's stored resource -> error (400 invalid_target), no token issued.
    - Happy path: a correct code_verifier + matching redirect_uri + matching resource yields an oat_ access token bound to the single chosen team_scope, plus an ort_ refresh token.
  </behavior>
  <action>
Create the consent flow. `oauth_authorize.py`:
  - `GET /oauth/authorize` receives `response_type=code`, `client_id`, `redirect_uri`, `code_challenge`, `code_challenge_method=S256`, `state`, `resource`, `scope`. Validate the client_id exists (DCR) AND that `redirect_uri` is one of that client's registered `oauth_clients.redirect_uris` — if the client is unknown or the redirect_uri is not registered, return a 400 error page and DO NOT redirect anywhere (defends open-redirect / code exfiltration). Validate code_challenge_method == S256; normalize `resource` via normalize_resource(). Persist the in-flight authorize params in a short-lived signed cookie/state record, then redirect the browser into GitHub sign-in. REUSE the GitHub App: build the GitHub authorize URL with `settings.GITHUB_APP_CLIENT_ID` and a memory-api callback `redirect_uri=https://api.grooveos.app/oauth/github-callback` (per RESEARCH.md Open Question 3 recommendation — memory-api owns its own callback that resumes the flow). Do NOT reuse Claude.ai's redirect for the GitHub leg.
  - `GET /oauth/github-callback` receives GitHub's `code`+`state`: call the existing `_exchange_code_for_token` + `_fetch_github_profile` + `_resolve_or_merge_user` from auth_github.py to resolve the xbrain User. Then load `list_teams_for_user(user_id)`. If the user has exactly one team, skip selection; otherwise render `oauth_consent.html` listing the teams as radio options + an "Authorize Claude.ai for team X" submit. Carry the original authorize params through the state.
  - `POST /oauth/authorize` (consent submit) receives the chosen `team_scope` + the in-flight authorize state. Re-verify the redirect_uri is still the one registered for the client (defense in depth). Verify the user IS a member of the chosen team via get_membership (reject otherwise — strict isolation, one team per connection per CONTEXT.md). Call oauth_store.create_auth_code(client_id, user_id, team_scope, resource, code_challenge, redirect_uri, scope), then 302 redirect to the ORIGINAL (registered) Claude.ai redirect_uri with `?code=...&state=...`.

`oauth_token.py`:
  - `POST /oauth/token` accepts form-encoded `grant_type`. For `authorization_code`: look up + one-time-consume the auth code (consume_auth_code sets used_at; a second exchange of the same code MUST fail with invalid_grant). Verify PKCE (`base64url(SHA256(code_verifier)) == stored code_challenge`) — mismatch -> 400 invalid_grant. Verify redirect_uri + client_id match the code's stored values -> 400 on mismatch. Verify `resource` (normalize + must equal the code's resource) -> 400 invalid_target on mismatch. Only after all checks pass, mint via oauth_store.mint_and_store_access_token(...) binding team_scope + resource(aud) + source='claude.ai-connector'; also mint a refresh token. Return `{access_token: "oat_...", token_type:"Bearer", expires_in, refresh_token:"ort_...", scope}`. For `grant_type=refresh_token`: validate the refresh hash, rotate (invalidate old refresh, issue new access+refresh), re-issue. Accept public-client requests with auth method "none" (no client_secret).

`oauth_consent.html`: minimal English-only template (NO French) — team radio list + submit, plus the connecting-app name and scopes shown for informed consent. Use the existing template/render mechanism if memory-api has one; otherwise return an HTMLResponse with an inline string (keep it dependency-free — Jinja optional).

Mount both routers un-prefixed in `app/main.py`.

Guardrails surfaced here: the issued token's `source` is fixed to 'claude.ai-connector' and its `team_scope` is the single bound team — the write cap is enforced on the mcp-brain side (Task 5).

Extend `tests/test_oauth_as.py` with FastAPI TestClient behavioral tests covering the highest-risk paths (this is the riskiest code in the plan, so py_compile alone is insufficient): (a) wrong code_verifier -> invalid_grant, no token; (b) code replay (second exchange of the same code) -> invalid_grant, no token; (c) GET /oauth/authorize with an unregistered redirect_uri -> 400 with NO redirect, and POST /oauth/token with a redirect_uri != stored -> 400; (d) mismatched `resource` in the token request -> invalid_target, no token; (e) happy path -> oat_ + ort_ returned bound to the chosen team_scope. Seed a client + auth code via Task 1's oauth_store helpers (or direct inserts). DB-backed cases follow the same gating convention as Tasks 1-2 when no test DB is available, but the PKCE/replay/redirect/resource assertions MUST run against the route logic (use a mocked/in-memory store if the integration DB is absent so these never silently skip).
  </action>
  <verify>
    <automated>cd apps/memory-api && python -m py_compile app/routes/oauth_authorize.py app/routes/oauth_token.py app/main.py && python -c "import pathlib; s=pathlib.Path('app/templates/oauth_consent.html').read_text(encoding='utf-8'); assert 'team' in s.lower() and 'authorize' in s.lower(); print('consent ok')" && python -m pytest tests/test_oauth_as.py -x -q -k "pkce or replay or redirect or resource or authorize or token"</automated>
  </verify>
  <done>GET /oauth/authorize rejects unregistered/mismatched redirect_uri with 400 (no redirect) and otherwise redirects into GitHub login; github-callback resolves the user, lists teams, renders consent (English only); POST /oauth/authorize issues a one-time PKCE-bound code; POST /oauth/token verifies PKCE, redirect_uri, and resource and issues an oat_ access token (+ refresh) bound to the single chosen team_scope; behavioral tests for wrong code_verifier, code replay, unregistered/mismatched redirect_uri, and resource mismatch all assert no-token-issued and pass; all files compile.</done>
</task>

<task type="auto">
  <name>Task 4: nginx + compose + CORS wiring for the well-known + oauth surface</name>
  <files>
    apps/memory-api/app/main.py,
    infrastructure/nginx/conf.d/20-api.conf,
    infrastructure/nginx/conf.d/40-mcp.conf,
    infrastructure/docker-compose.yml
  </files>
  <action>
CORS (blocker 1) — `apps/memory-api/app/main.py`: extend the existing CORSMiddleware `allow_origin_regex` to ALSO match `https://claude.ai` so the FastAPI app emits a correct single `Access-Control-Allow-Origin` for Claude.ai's browser calls to /.well-known/oauth-authorization-server, /oauth/register, and /oauth/token. Change the regex from
  `r"(chrome-extension://.*|https://chat\.grooveos\.app|https://grooveos\.app|https://grooveos\.web\.app|https://dejavu-app\.web\.app)"`
to additionally include `https://claude\.ai` (e.g. add `|https://claude\.ai` before the closing paren). Do NOT touch allow_credentials/allow_methods/allow_headers. The app — not nginx — owns ACAO on these paths.

20-api.conf (api.grooveos.app): add `location /.well-known/ { proxy_pass http://memory-api:8000; ... }` and `location /oauth/ { proxy_pass http://memory-api:8000; ... }` — same proxy_set_header block as the existing /v1/ location (Host, X-Real-IP, X-Forwarded-For, X-Forwarded-Proto, Authorization). CRITICAL (blocker 1): do NOT add any `Access-Control-Allow-Origin` / CORS `add_header` in these two location blocks — the memory-api app already emits ACAO via CORSMiddleware, so an nginx-added ACAO would produce DUPLICATE ACAO headers and the browser would reject the response. Just proxy through and let the app set CORS (its OPTIONS preflight is handled by Starlette's CORSMiddleware). Add a short comment in each block: `# CORS owned by memory-api CORSMiddleware — do NOT add_header ACAO here (duplicate-header CORS failure).`

40-mcp.conf (mcp.grooveos.app): confirm `/.well-known/oauth-protected-resource` falls through to the existing `location /` (it does — no more-specific block). The existing root-location CORS on mcp.grooveos.app is unchanged; only add a comment noting the well-known path is covered by the root location. If FastMCP serves protected-resource at `/mcp/.well-known/oauth-protected-resource` rather than root, add an explicit note — the executor confirms the actual served path in Task 5 and adjusts the resource_metadata URL accordingly.

docker-compose.yml: add `OAUTH_ISSUER_URL: ${OAUTH_ISSUER_URL:-https://api.grooveos.app}` and `OAUTH_RESOURCE_URL: ${OAUTH_RESOURCE_URL:-https://mcp.grooveos.app/mcp}` to the `memory-api` service env. Add `MEMORY_API_OAUTH_INTROSPECT_URL: ${MEMORY_API_OAUTH_INTROSPECT_URL:-http://memory-api:8000/oauth/introspect}` and reconfirm `BRIDGE_SHARED_SECRET` is present on the `mcp-brain` service env (it is) — mcp-brain uses it as the X-Internal-Secret on introspection calls.
  </action>
  <verify>
    <automated>cd apps/memory-api && python -m py_compile app/main.py && python -c "import re,pathlib; s=pathlib.Path('app/main.py').read_text(encoding='utf-8'); m=re.search(r'allow_origin_regex\s*=\s*r?[\"\x27](.*?)[\"\x27]', s); assert m and 'claude' in m.group(1), 'claude.ai missing from CORS regex'; print('cors ok')" && cd ../.. && grep -v '^[[:space:]]*#' infrastructure/nginx/conf.d/20-api.conf | grep -c 'location /\(\.well-known\|oauth\)/' | grep -qx 2 && echo "nginx blocks ok" && (grep -v '^[[:space:]]*#' infrastructure/nginx/conf.d/20-api.conf | grep -A6 'location /\(\.well-known\|oauth\)/' | grep -qi 'Access-Control-Allow-Origin' && echo "FAIL: nginx adds duplicate ACAO" && exit 1 || echo "no duplicate ACAO in oauth/well-known blocks") && grep -qE 'OAUTH_ISSUER_URL|OAUTH_RESOURCE_URL' infrastructure/docker-compose.yml && grep -qE 'MEMORY_API_OAUTH_INTROSPECT_URL' infrastructure/docker-compose.yml && echo "compose env ok"</automated>
  </verify>
  <done>memory-api CORSMiddleware allow_origin_regex now matches https://claude.ai (verified by the regex assertion); 20-api.conf proxies both /.well-known/ and /oauth/ to memory-api:8000 WITHOUT any nginx-added Access-Control-Allow-Origin on those blocks (so ACAO is single-sourced from the app — no duplicate-header CORS failure); 40-mcp.conf documents protected-resource fall-through; compose sets OAUTH_ISSUER_URL, OAUTH_RESOURCE_URL on memory-api and MEMORY_API_OAUTH_INTROSPECT_URL on mcp-brain.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 5: mcp-brain protected-resource — oat_ introspection branch + 401 + write guardrails</name>
  <files>
    apps/mcp-brain/app/config.py,
    apps/mcp-brain/app/oauth_verify.py,
    apps/mcp-brain/app/memory_client.py,
    apps/mcp-brain/app/main.py,
    apps/mcp-brain/tests/test_oauth_resolve.py
  </files>
  <behavior>
    - oauth_verify.introspect(raw) calls memory-api /oauth/introspect with X-Internal-Secret=BRIDGE_SHARED_SECRET; returns (sub, team_scope, source) for active tokens; raises ValueError for inactive.
    - _resolve() returns a 3-tuple (token, team_scope, is_connector). It recognizes a Bearer token starting with "oat_" and routes it through introspection (is_connector=True); the xbt_ path and the email path are untouched, take precedence by prefix, and return is_connector=False.
    - An xbt_-prefixed Bearer NEVER calls introspect() — it stays on the existing /v1/me validation path.
    - The internal LibreChat email path bypasses the oat_ branch entirely (no introspect call) and returns is_connector=False.
    - An unauthenticated MCP request yields a 401 + WWW-Authenticate: Bearer resource_metadata="<protected-resource URL>".
    - GET /.well-known/oauth-protected-resource returns {resource: OAUTH_RESOURCE_URL (rstrip /), authorization_servers:[OAUTH_ISSUER_URL], scopes_supported:["brain:read","brain:write"], bearer_methods_supported:["header"]}.
    - When the resolved auth is_connector (oat_ token), memory_add, task_create, and contact_add all force source="claude.ai-connector"; memory_add additionally caps truth_level at WORKING (EPHEMERAL/WORKING pass through; VALIDATED/CANONICAL/PUBLIC are downgraded to WORKING); all connector writes use the bound team_scope only.
  </behavior>
  <action>
config.py: add `OAUTH_ISSUER_URL: str = "https://api.grooveos.app"`, `OAUTH_RESOURCE_URL: str = "https://mcp.grooveos.app/mcp"`, `MEMORY_API_OAUTH_INTROSPECT_URL: str = "http://memory-api:8000/oauth/introspect"`.

oauth_verify.py (new): `async def introspect(raw_token) -> dict` — httpx POST to MEMORY_API_OAUTH_INTROSPECT_URL with `{"token": raw_token}` and header `X-Internal-Secret: settings.BRIDGE_SHARED_SECRET`; if response `active` is true, return `{"sub":..., "team_scope":..., "source":..., "resource":...}`; else raise ValueError("OAuth token inactive"). Also validate the returned `resource` equals settings.OAUTH_RESOURCE_URL.rstrip("/") (audience check — RFC 8707; mismatch -> ValueError).

main.py:
  - Change `_resolve()` to return a 3-tuple `(token, team_scope, is_connector)`. Add an `oat_` branch FIRST so prefix routing is unambiguous: if `raw.startswith("oat_")`, call oauth_verify.introspect, then mint a bridge JWT (mint_bridge_jwt with the resolved team_scope + sub) so downstream memory_client calls authenticate to memory-api with the bound team; return `(bridge_jwt, team_scope, True)`. The existing `xbt_` branch returns `(xbt_token, team_scope, False)` and MUST NOT call introspect. The existing internal email path returns `(jwt_or_token, team_scope, False)` and MUST NOT enter the oat_ branch. (Choice rationale: mint a bridge JWT rather than forward oat_ to /v1 — memory-api already trusts bridge JWTs scoped to a team via deps.get_team_scope, so we avoid teaching /v1 auth about oat_.)
  - UPDATE ALL 8 `_resolve()` call sites (every tool: memory_search, memory_add, tasks_list, task_create, task_update, contacts_search, contact_add, agent_invoke, team_context — confirm the exact set in main.py; there are 8 unpack sites) to unpack THREE values: `token, team_scope, is_connector = await _resolve(ctx)`. Read-only / non-tagging tools ignore is_connector; the write tools (memory_add, task_create, contact_add) thread is_connector into memory_client so the connector source tag (and, for memory_add, the truth_level cap) is applied. There must be NO remaining 2-value unpack of _resolve() after this task.
  - Add a FastMCP custom route (`@mcp.custom_route` / Starlette mount, per the installed mcp>=1.27 API — executor confirms the exact decorator) for `GET /.well-known/oauth-protected-resource` returning the JSON above, and ensure unauthenticated tool calls surface `401` with the `WWW-Authenticate` header. If the SDK's FastMCP cannot emit the 401 without taking over auth, add a thin Starlette middleware on the FastMCP app that returns 401+WWW-Authenticate when no recognized Authorization is present on the /mcp path. Keep the existing xbt_/email behavior intact.

memory_client.py: change `memory_add` to accept an optional `source: str | None = None` and an `is_connector: bool = False`; when is_connector, set item["source"]="claude.ai-connector" and clamp truth_level to WORKING if it is VALIDATED/CANONICAL/PUBLIC. Also add an optional `source`/`is_connector` parameter to `task_create` and `contact_add` so that, when is_connector, their written rows carry source="claude.ai-connector". Default behavior unchanged for existing (non-connector) callers.

main.py write tools: pass `is_connector` from `_resolve()` through to memory_client for memory_add, task_create, and contact_add so EVERY connector write carries source='claude.ai-connector' (per the locked CONTEXT.md decision — no "where supported" hedge). memory_add additionally enforces the WORKING truth_level cap. If a given target endpoint genuinely has no source/origin column, note it inline AND still pass the flag so the tag is applied at whatever layer accepts it; at minimum memory_add, task_create, and contact_add MUST tag source.

tests/test_oauth_resolve.py: unit-test (a) the truth_level clamp helper (VALIDATED->WORKING, CANONICAL->WORKING, PUBLIC->WORKING, EPHEMERAL->EPHEMERAL, WORKING->WORKING); (b) the resource audience-mismatch ValueError in oauth_verify; (c) that an "oat_"-prefixed token routes to introspect (httpx mocked) and yields is_connector=True; (d) that an "xbt_"-prefixed Bearer does NOT call introspect() (assert the mocked introspect is never invoked) and stays on the existing path with is_connector=False; (e) that the internal email path bypasses the oat_ branch (no introspect call) and returns is_connector=False; (f) that _resolve returns a 3-tuple and the connector path sets is_connector=True. py_compile-level coverage is acceptable where async DB/httpx mocking is heavy, consistent with the repo's mcp-brain test convention, but cases (a)-(e) MUST run as real assertions (mock httpx so they never silently skip).
  </action>
  <verify>
    <automated>cd apps/mcp-brain && python -m py_compile app/config.py app/oauth_verify.py app/memory_client.py app/main.py && python -m pytest tests/test_oauth_resolve.py -x -q</automated>
  </verify>
  <done>_resolve returns a 3-tuple (token, team_scope, is_connector) with all 8 call sites updated to unpack 3 values (no remaining 2-value unpack); oat_ tokens route through introspection and are rejected on audience mismatch, xbt_ tokens never call introspect, and the email path bypasses the oat_ branch; protected-resource metadata route exists; unauthenticated calls return 401+WWW-Authenticate; memory_add, task_create, and contact_add each tag source='claude.ai-connector' for connector origin and memory_add caps truth_level at WORKING; the existing xbt_ + email paths are unchanged (asserted by tests d & e); tests for the clamp, audience mismatch, oat_ routing, xbt_ no-introspect, and email bypass all pass.</done>
</task>

<task type="checkpoint:human-action" gate="blocking">
  <name>Task 6: Deploy + Claude.ai end-to-end connect smoke test (DOCUMENTED — NOT executed in this run)</name>
  <what-built>
    A full OAuth 2.1 layer: memory-api as Authorization Server (3 tables + 5 /oauth/ routes + AS metadata + GitHub-login consent with team selection), mcp-brain as Protected Resource (protected-resource metadata + 401/WWW-Authenticate + oat_ introspection + write guardrails), and nginx/compose/CORS wiring. NOTHING is deployed yet — this task is the runbook the user approves before execution.
  </what-built>
  <how-to-verify>
    PRECONDITION: this plan is PLAN-ONLY. Do these steps ONLY after the user approves and a follow-up execution run lands Tasks 1-5.

    1. Surgical deploy (per CLAUDE.md): from repo root,
       `git archive HEAD apps/memory-api apps/mcp-brain infrastructure/nginx/conf.d/20-api.conf infrastructure/nginx/conf.d/40-mcp.conf infrastructure/docker-compose.yml | gzip | ssh user@130.211.55.142 'cd /home/user/xbrain && tar xzf -'`
       then on the VM: apply alembic (memory-api migrate to 0022), `docker compose build memory-api mcp-brain && docker compose up -d memory-api mcp-brain && docker compose exec nginx nginx -s reload`.
    2. Metadata reachability (no auth) + CORS check:
       - `curl -s https://api.grooveos.app/.well-known/oauth-authorization-server | jq .` → S256 + "none" + /oauth/ endpoints present.
       - `curl -si -H 'Origin: https://claude.ai' https://api.grooveos.app/.well-known/oauth-authorization-server` → EXACTLY ONE `Access-Control-Allow-Origin: https://claude.ai` header (NOT two — blocker 1 regression check).
       - `curl -s https://mcp.grooveos.app/.well-known/oauth-protected-resource | jq .` → resource == https://mcp.grooveos.app/mcp (no trailing slash), authorization_servers == ["https://api.grooveos.app"]. (If FastMCP serves it under /mcp/.well-known/..., adjust the WWW-Authenticate resource_metadata URL accordingly and re-test.)
       - `curl -si https://mcp.grooveos.app/mcp` (no Authorization) → 401 with `WWW-Authenticate: Bearer resource_metadata=...`.
    3. MCP Inspector dry-run (catches Gotcha 7, the streamable-HTTP+OAuth GET-loop): `npx @modelcontextprotocol/inspector` → connect to `https://mcp.grooveos.app/mcp` → complete the OAuth browser flow → confirm tools list + a memory_search call succeeds. If Inspector GET-loops, add an `/sse` fallback before user testing.
    4. Official Claude.ai connect: Claude.ai → Settings → Connectors → Add custom connector → URL `https://mcp.grooveos.app/mcp` → complete browser OAuth (GitHub login → pick ONE team → authorize) → confirm the connector shows connected and the brain tools are callable. Verify a memory_add from Claude.ai lands as source='claude.ai-connector', truth_level<=WORKING, in the bound team only (check Brain Monitor / `memory_items`).
    5. Isolation check: with a second team, confirm a token bound to team A cannot read/write team B (audience + team_scope binding holds).
  </how-to-verify>
  <resume-signal>This is PLAN-ONLY — do not run the runbook now. Type "approved" to accept the plan; execution + this smoke test happen in a separate run.</resume-signal>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| Claude.ai client → mcp.grooveos.app (mcp-brain) | Untrusted public client; bearer token must be validated every request; unauth → 401 |
| Claude.ai browser → api.grooveos.app /oauth/authorize | Untrusted browser; PKCE + one-time code + state + registered-redirect_uri check defend the auth-code exchange |
| mcp-brain → memory-api /oauth/introspect | Internal; gated by X-Internal-Secret == BRIDGE_SHARED_SECRET (constant-time via hmac.compare_digest) |
| oat_ access token → team data | Token is bound to ONE team_scope + ONE resource (aud); cross-team / cross-resource use must be rejected |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-glo-01 | Spoofing | /oauth/token code exchange | mitigate | PKCE S256 verification (SHA256(verifier)==stored challenge) + one-time auth code (used_at) + client_id/redirect_uri match (Task 1/3); behavioral tests assert wrong-verifier + replay both fail (Task 3) |
| T-glo-02 | Tampering | resource/audience binding | mitigate | normalize_resource(rstrip "/") on store+lookup; /oauth/token rejects resource mismatch (Task 3); mcp-brain rejects tokens whose introspected resource != OAUTH_RESOURCE_URL (Task 1/5) |
| T-glo-03 | Information disclosure | cross-team read via connector token | mitigate | team_scope bound at consent, membership re-checked on POST /oauth/authorize, enforced downstream by memory-api get_team_scope (Task 3/5) |
| T-glo-04 | Elevation of privilege | connector writes at high truth_level | mitigate | mcp-brain caps connector writes to WORKING + source='claude.ai-connector' across memory_add/task_create/contact_add (Task 5) |
| T-glo-05 | Information disclosure | open introspection endpoint leaking token claims | mitigate | /oauth/introspect gated by X-Internal-Secret (BRIDGE_SHARED_SECRET), constant-time compare; missing/wrong secret -> 401, no claims; TestClient asserts this (Task 2) |
| T-glo-06 | Spoofing | unauthenticated MCP calls | mitigate | 401 + WWW-Authenticate; no anonymous tool access on the public token path (Task 5) |
| T-glo-07 | Tampering | open-redirect / auth-code exfiltration via unregistered redirect_uri | mitigate | GET /oauth/authorize rejects any redirect_uri not in oauth_clients.redirect_uris with 400 (no redirect); POST /oauth/token re-checks redirect_uri match; behavioral test (Task 3) |
| T-glo-08 | Information disclosure | duplicate ACAO / CORS misconfig exposing or breaking /oauth + well-known | mitigate | app owns ACAO via CORSMiddleware (claude.ai added to regex); nginx forbidden from adding ACAO on /.well-known/ + /oauth/ blocks (Task 4); deploy-time single-ACAO curl check (Task 6) |
| T-glo-09 | Repudiation | token theft / replay | accept | oat_ stored as SHA-256 hash, short access-token TTL + revoked_at column; full rotation/audit deferred — low value target, internal team, revocation column present for manual kill |
| T-glo-10 | Denial of service | DCR registration spam growing oauth_clients | accept | small team, nginx per-IP rate limiting already fronts api.grooveos.app; CIMD migration noted in RESEARCH.md if growth becomes an issue |
</threat_model>

<verification>
- `python -m py_compile` passes for every new/edited .py file in apps/memory-api and apps/mcp-brain.
- `pytest apps/memory-api/tests/test_oauth_as.py` and `pytest apps/mcp-brain/tests/test_oauth_resolve.py` pass, including the behavioral PKCE/replay/redirect_uri/resource-mismatch tests (Task 3), the introspect X-Internal-Secret gate test (Task 2), and the xbt_-no-introspect + email-bypass tests (Task 5).
- AS metadata advertises S256 + token_endpoint_auth_methods_supported includes "none" + all five /oauth/ endpoints (asserted in test_oauth_as.py via TestClient).
- memory-api CORSMiddleware allow_origin_regex matches https://claude.ai; nginx adds NO Access-Control-Allow-Origin on the /.well-known/ + /oauth/ blocks (single-sourced ACAO from the app).
- nginx 20-api.conf contains both `location /.well-known/` and `location /oauth/` proxying to memory-api:8000; 40-mcp.conf documents the protected-resource fall-through.
- docker-compose.yml sets OAUTH_ISSUER_URL + OAUTH_RESOURCE_URL (memory-api) and MEMORY_API_OAUTH_INTROSPECT_URL (mcp-brain).
- Existing xbt_ + email auth paths in mcp-brain remain prefix-routed and unaffected (asserted by tests).
- Deploy + Claude.ai end-to-end smoke test is documented as a blocking human-action checkpoint and is NOT executed in this planning run.
</verification>

<success_criteria>
- memory-api serves a spec-compliant OAuth 2.1 AS (metadata + DCR + authorize+PKCE + token + introspect) on un-prefixed public paths, reusing GitHub identity for consent and binding the issued token to ONE user-selected team_scope (CONTEXT.md locked decisions honored).
- mcp-brain is a spec-compliant Protected Resource: protected-resource metadata, 401+WWW-Authenticate, and oat_ tokens validated by introspection with audience checks, while the full read+write toolset stays exposed.
- Connector writes are guarded across memory_add, task_create, and contact_add: source='claude.ai-connector', truth_level capped at WORKING (memory_add), single bound team only — no "where supported" hedge.
- Browser CORS works: Claude.ai's browser calls to /.well-known/ + /oauth/ receive exactly one ACAO header (app-sourced); no duplicate-header failure.
- All 5 RESEARCH.md gotchas are addressed: DCR enabled (register returns a client_id), resource trailing-slash normalized, auth_methods include "none", nginx proxies /.well-known/ + /oauth/, and a Claude.ai GET-loop smoke test (MCP Inspector) is in the runbook.
- The existing LibreChat / Chrome-extension / Claude-Code callers continue to work unchanged.
- Nothing is deployed or connected during this planning run — the deploy + Claude.ai connect test is a documented, user-gated follow-up.
</success_criteria>

<output>
After completion, create `.planning/quick/260604-glo-make-mcp-brain-connectable-as-a-custom-c/260604-glo-SUMMARY.md`
</output>
</content>
</invoke>
