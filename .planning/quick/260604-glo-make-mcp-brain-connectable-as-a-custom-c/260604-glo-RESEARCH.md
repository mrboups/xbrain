# Quick Task 260604-glo: Research — mcp-brain as Claude.ai Custom Connector

**Researched:** 2026-06-04
**Domain:** MCP OAuth 2.1 / Claude.ai Custom Connector / FastMCP Python auth
**Confidence:** HIGH (spec via official MCP docs + Claude auth docs) / MEDIUM (FastMCP internals, some gaps)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- Full toolset exposed (read + write), with guardrails: connector-originated writes tagged `source=claude.ai-connector`, `truth_level` capped at WORKING, scoped strictly to bound team.
- One team per connection. Team selected at consent screen. Token bound to single `team_scope`.
- memory-api is the OAuth 2.1 Authorization Server. mcp-brain is the Protected Resource.
- Reuse GitHub identity for the `/authorize` login step.

### Claude's Discretion
- Exact token format/validation (reuse `xbt_` semantics vs dedicated OAuth access-token table), PKCE/DCR storage schema, consent-screen UX.

### Deferred Ideas (OUT OF SCOPE)
- (none explicitly listed)
</user_constraints>

---

## Summary

Claude.ai's Custom Connector flow speaks the **MCP Authorization spec (OAuth 2.1)**, not a static-token paste. It is a full browser-redirect flow: Claude.ai discovers the AS via `/.well-known/oauth-protected-resource` on mcp-brain, registers a client via Dynamic Client Registration (or CIMD), and performs Authorization Code + PKCE. The redirect callback goes to `https://claude.ai/api/mcp/auth_callback`.

The good news: `mcp>=1.27.0` — already pinned in mcp-brain — ships `OAuthAuthorizationServerProvider`, `AuthSettings`, `ClientRegistrationOptions`, and the route auto-registration that produces all the required `/.well-known/` endpoints. No extra packages are needed. FastMCP's `FastMCP(auth=...)` constructor wires these routes automatically.

The implementation splits cleanly: **mcp-brain** becomes a pure Protected Resource (RS) — it advertises its `/.well-known/oauth-protected-resource`, returns `401 + WWW-Authenticate` on unauthenticated requests, and validates tokens via introspection against memory-api. **memory-api** becomes the Authorization Server (AS) — it gains `/authorize`, `/token`, `/register`, and `/.well-known/oauth-authorization-server`. No new containers needed; both services already exist.

**Primary recommendation:** Implement memory-api as an OAuth 2.1 AS using `mcp.server.auth.OAuthAuthorizationServerProvider`, store issued OAuth access tokens in a new `oauth_access_tokens` table (separate from `user_api_tokens`), and configure mcp-brain as an RS using `IntrospectionTokenVerifier` pointing at memory-api's introspection endpoint. The consent screen lets the user pick their team; the issued access token carries the `team_scope` claim.

---

## 1. Claude.ai Custom Connector — Current Requirements

### 1.1 Transport

Claude.ai Custom Connectors support **Streamable HTTP** (the `2025-03-26`/`2025-06-18` protocol). mcp-brain already runs `transport="streamable-http"` — no change needed.

The user pastes the MCP endpoint URL into the connector UI, e.g. `https://mcp.example.com/mcp`. The `/mcp` path is what FastMCP mounts on by default (confirmed by community reports; users paste `https://<host>/mcp`, not just the base URL). [VERIFIED: zenn.dev article, crumrine/fastmcp-personal-auth]

**Known active bug (issue #291 in anthropics/claude-ai-mcp):** After OAuth completes, Claude.ai's streamable-HTTP+OAuth code path sometimes loops on GET `/mcp` and never sends POST initialize. The SSE path (`/sse`) does not exhibit this bug. **Mitigation:** The MCP Python SDK's reference example uses streamable-HTTP and works; the bug appears to be in a specific Claude.ai client code path. The plan should include an explicit smoke-test step with MCP Inspector to verify end-to-end before marking as done. [CITED: github.com/anthropics/claude-ai-mcp/issues/291]

### 1.2 Static Bearer Token

Static pasted bearer tokens are **not supported** by Claude.ai's Custom Connector UI. The UI exclusively uses the OAuth 2.1 authorization code flow. [CITED: claude.com/docs/connectors/building/authentication]

### 1.3 OAuth Flow Performed by Claude.ai

1. User adds connector URL in Claude.ai settings.
2. Claude.ai sends an unauthenticated MCP request → expects `HTTP 401` with `WWW-Authenticate: Bearer resource_metadata="https://mcp.example.com/mcp/.well-known/oauth-protected-resource"`.
3. Claude.ai GETs the protected resource metadata document.
4. Parses `authorization_servers[0]` → constructs AS metadata URL → GETs `https://api.example.com/.well-known/oauth-authorization-server`.
5. Registers as a client via DCR (`POST /register`) unless CIMD is configured.
6. Generates PKCE `code_verifier` + `code_challenge` (S256), includes `resource=https://mcp.example.com/mcp` in the auth request.
7. Opens browser to `/authorize` — user logs in via GitHub, picks team, grants consent.
8. AS redirects to `https://claude.ai/api/mcp/auth_callback` with `code`.
9. Claude.ai POSTs to `/token` with `code_verifier` + `resource` → receives `access_token` (+ optional `refresh_token`).
10. All subsequent MCP calls include `Authorization: Bearer <access_token>`.

[CITED: modelcontextprotocol.io/specification/2025-06-18/basic/authorization] [CITED: claude.com/docs/connectors/building/authentication]

### 1.4 Redirect URI

Claude.ai web/desktop/mobile uses a single fixed redirect URI:
```
https://claude.ai/api/mcp/auth_callback
```
This URI **must be registered** in the AS (either via DCR or pre-seeded). If using DCR, Claude.ai will include it in the registration request; if DCR is disabled, it must be pre-registered. [CITED: claude.com/docs/connectors/building/authentication]

### 1.5 Client Registration: DCR vs CIMD

**Recommended: DCR (Dynamic Client Registration, RFC 7591).** Expose `registration_endpoint` in the AS metadata. Claude.ai will POST to it, register, and receive a `client_id`. Storage in memory-api's DB is fine.

Alternative: CIMD (Client ID Metadata Documents) — Claude.ai uses its own URL as `client_id`, AS fetches a JSON document from that URL. Preferred for high-traffic to avoid DB growth from many DCR registrations. For xbrain (small team), DCR is simpler and is what the MCP reference example implements. [CITED: claude.com/docs/connectors/building/authentication]

---

## 2. MCP Authorization Spec — Concrete Server-Side Contract

### 2.1 mcp-brain (Protected Resource)

Must serve at `/.well-known/oauth-protected-resource` (GET):

```json
{
  "resource": "https://mcp.example.com/mcp",
  "authorization_servers": ["https://api.example.com"],
  "scopes_supported": ["brain:read", "brain:write"],
  "bearer_methods_supported": ["header"]
}
```

**Required fields:** `resource` (must exactly match the URL the user pasted), `authorization_servers`.

On unauthenticated requests, must return:
```http
HTTP 401 Unauthorized
WWW-Authenticate: Bearer resource_metadata="https://mcp.example.com/mcp/.well-known/oauth-protected-resource"
```

Must validate that incoming access tokens were **issued for this resource** (audience binding per RFC 8707). Token is invalid if its `aud` or `resource` claim does not match `https://mcp.example.com/mcp`.

[CITED: modelcontextprotocol.io/specification/2025-06-18/basic/authorization] [CITED: RFC 9728, RFC 8707]

### 2.2 memory-api (Authorization Server)

Must serve:

**`GET /.well-known/oauth-authorization-server`** — AS metadata (RFC 8414):

```json
{
  "issuer": "https://api.example.com",
  "authorization_endpoint": "https://api.example.com/oauth/authorize",
  "token_endpoint": "https://api.example.com/oauth/token",
  "registration_endpoint": "https://api.example.com/oauth/register",
  "revocation_endpoint": "https://api.example.com/oauth/revoke",
  "introspection_endpoint": "https://api.example.com/oauth/introspect",
  "response_types_supported": ["code"],
  "grant_types_supported": ["authorization_code", "refresh_token"],
  "code_challenge_methods_supported": ["S256"],
  "token_endpoint_auth_methods_supported": ["none"],
  "scopes_supported": ["brain:read", "brain:write"]
}
```

**Critical:** `code_challenge_methods_supported: ["S256"]` is mandatory — Claude.ai rejects servers that don't advertise this. [CITED: claude.com/docs/connectors/building/authentication] [CITED: note.com/brave_quince241 — pitfalls]

**`POST /oauth/register`** (DCR, RFC 7591) — Accept `client_name`, `redirect_uris`, `grant_types`, `response_types`. Return `client_id` (+ optional `client_secret` for confidential clients, but Claude.ai registers as public). Must accept `redirect_uris: ["https://claude.ai/api/mcp/auth_callback"]`.

**`GET /oauth/authorize`** — Standard OAuth authorize redirect with PKCE `code_challenge` + `code_challenge_method=S256` + `resource=https://mcp.example.com/mcp`. Render a consent screen, authenticate user via GitHub, collect team selection.

**`POST /oauth/token`** — Exchange `authorization_code` with `code_verifier` validation. Claude.ai sends `grant_type=authorization_code` plus `resource` parameter. Must bind issued token to the `resource` value (set `aud` claim or store resource binding in the token row). Also handle `grant_type=refresh_token`.

**`POST /oauth/introspect`** — Used by mcp-brain's `IntrospectionTokenVerifier` to validate tokens. Returns `{ active: true, sub: "<user_id>", team_scope: "<slug>", scope: "brain:read brain:write", aud: "https://mcp.example.com/mcp" }`.

[CITED: modelcontextprotocol.io/specification/2025-06-18/basic/authorization, RFC 7591, RFC 8414, RFC 8707]

---

## 3. FastMCP / `mcp>=1.27.0` — What's Already Available

### 3.1 Installed version

`pyproject.toml` pins `mcp>=1.27.0`. The published 1.27.2 (2026-05-29) is the latest. [VERIFIED: pypi.org/project/mcp]

### 3.2 What `mcp.server.auth` provides (built-in, no extra install)

The `mcp.server.auth` package ships:
- `OAuthAuthorizationServerProvider` — abstract class with 9 required async methods (see §2.2 above). Instantiate, wire via `FastMCP(auth=provider, ...)`.
- `AuthSettings` — configuration dataclass (`issuer_url`, `resource_server_url`, `required_scopes`, `client_registration_options`, `revocation_options`).
- `ClientRegistrationOptions` — `enabled: bool` (default `False` → must set `True` for DCR), `valid_scopes`, `default_scopes`.
- **Route auto-registration**: When `auth=` is passed to FastMCP, the SDK auto-mounts:
  - `/.well-known/oauth-authorization-server` (RFC 8414 metadata)
  - `/.well-known/oauth-protected-resource{resource_path}` (RFC 9728)
  - `/authorize` (GET + POST)
  - `/token` (POST)
  - `/register` (POST, conditional on `ClientRegistrationOptions(enabled=True)`)
  - `/revoke` (POST, conditional)
- `IntrospectionTokenVerifier` — validates opaque tokens via introspection HTTP call. Pass `introspection_endpoint` URL + optional `client_credentials`. Used on the RS (mcp-brain) side: `FastMCP(token_verifier=IntrospectionTokenVerifier(...), auth=AuthSettings(...))`.

[CITED: raw.githubusercontent.com/modelcontextprotocol/python-sdk/main/src/mcp/server/auth/routes.py] [CITED: raw.githubusercontent.com/modelcontextprotocol/python-sdk/main/examples/servers/simple-auth/mcp_simple_auth/server.py]

### 3.3 Architecture Split Supported by SDK

The SDK's `simple-auth` example demonstrates exactly the target architecture:
- **AS** (memory-api): runs `FastMCP(auth=MyOAuthProvider(...))` on its own port. Serves all `/.well-known/` + `/authorize` + `/token` + `/register` + `/introspect` routes.
- **RS** (mcp-brain): runs `FastMCP(token_verifier=IntrospectionTokenVerifier(introspection_endpoint="https://api.example.com/oauth/introspect"), auth=AuthSettings(issuer_url="https://api.example.com", resource_server_url="https://mcp.example.com/mcp"))`. The RS only serves `/.well-known/oauth-protected-resource` and validates tokens — no user-facing login.

**Important constraint:** Do not pass both `auth_server_provider` and `token_verifier` simultaneously to FastMCP — this causes a `ValueError` at startup. [CITED: note.com/brave_quince241]

### 3.4 Token Validation in mcp-brain

After the change, `_resolve()` in `main.py` will be replaced by the SDK's built-in Bearer extraction. The `IntrospectionTokenVerifier` calls memory-api's introspection endpoint, returns an `AccessToken` object with `client_id`, `scopes`, and any extra claims (e.g., `team_scope`, `sub`). The tool handlers receive the validated principal from `ctx.request_context.auth` or equivalent SDK accessor.

The existing `xbt_` path (`if raw.startswith("xbt_"):`) can remain as a **fallback** for Claude Code and LibreChat — the SDK's verifier runs first; if it fails, `_resolve()` logic falls through to the `xbt_` branch. Or both can coexist via FastMCP's `MultiAuthProvider` (available in newer FastMCP versions). This is in Claude's Discretion.

---

## 4. Identity Mapping: Claude.ai User → xbrain Identity + team_scope

### 4.1 Recommended Flow

```
User clicks "Add connector" in Claude.ai
  → Claude.ai → GET /oauth/authorize?...&resource=https://mcp.example.com/mcp&code_challenge=...
  → memory-api renders consent page:
      1. "Sign in with GitHub" button (reuse existing GitHub OAuth App)
      2. After GitHub callback: resolve user identity (existing _resolve_or_merge_user logic)
      3. If user has multiple teams: show team selector dropdown
      4. User grants consent
  → memory-api generates authorization_code, stores (user_id, team_scope, resource, code_challenge, client_id)
  → Redirect to https://claude.ai/api/mcp/auth_callback?code=...
  → Claude.ai POSTs /oauth/token with code_verifier
  → memory-api verifies PKCE, issues OAuth access token:
      { access_token: "oat_...", team_scope: "<slug>", sub: "<user_id>", aud: "https://mcp.example.com/mcp" }
  → Token stored in new `oauth_access_tokens` table
```

### 4.2 Token Format Recommendation

**Use a NEW `oauth_access_tokens` table** (separate from `user_api_tokens`). Reasons:
- `user_api_tokens.team_scope` is `TEXT NOT NULL` — a valid team slug is required. The OAuth flow always produces a team-bound token (no empty-string sentinel).
- The OAuth token needs additional fields: `client_id`, `code_challenge`, `resource` (audience), `expires_at`, `refresh_token_hash`.
- Reusing `user_api_tokens` would require adding nullable columns and breaking the existing `api_token_team_scope` response shape.
- The `xbt_` token remains valid for all existing callers (LibreChat, Claude Code, Chrome extension) — no migration needed.

OAuth access token format: `oat_` prefix + `secrets.token_urlsafe(32)`. Stored as SHA-256 hash (same pattern as `xbt_`). [ASSUMED — design choice, not externally mandated]

### 4.3 Introspection Response Shape

mcp-brain's `IntrospectionTokenVerifier` will hit memory-api `POST /oauth/introspect`. The SDK extracts `AccessToken` claims. To carry `team_scope` to tool handlers, the introspection response should include `team_scope` as a non-standard claim. The tool handler reads it from `AccessToken.extra_claims` or similar. mcp-brain's `_resolve()` function gets removed (or kept for xbt_ only) and replaced by reading the SDK-provided `ctx.request_context.auth`. [ASSUMED — SDK exact accessor name needs verification via SDK source or docs]

### 4.4 Write Guardrails

Per CONTEXT.md, connector-originated writes must carry:
- `source=claude.ai-connector` (set by mcp-brain in all `memory_add`, `task_create`, `contact_add` calls when auth came from an OAuth token)
- `truth_level` capped at `WORKING` max (connector cannot write `VALIDATED` or `CANONICAL`)

Implementation: mcp-brain detects OAuth token (e.g., `oat_` prefix or an `auth_method` claim in introspection response) and applies these overrides before calling memory_client.

---

## 5. Pitfalls / Gotchas

### Pitfall 1: `streamable_http_path` default mismatch
**What:** FastMCP mounts the MCP endpoint at `/mcp` by default. Claude.ai sends requests to the URL the user pasted, which must match.
**Fix:** Verify the path. If user pastes `https://mcp.example.com/mcp`, FastMCP's default `/mcp` is correct. If they paste `https://mcp.example.com/`, set `streamable_http_path="/"`. Stick with `/mcp` — it's already what mcp-brain uses (current nginx proxies `/` → mcp-brain, FastMCP default path is `/mcp`, so user should paste `https://mcp.example.com/mcp`).
[CITED: note.com/brave_quince241 — bug #2]

### Pitfall 2: Trailing-slash inconsistency in `resource` parameter
**What:** Claude.ai sends `resource=https://mcp.example.com/mcp` (no trailing slash). If memory-api stores the audience as `https://mcp.example.com/mcp/`, token audience validation fails with a silent 401.
**Fix:** Normalize resource URL once on intake: `resource_url.rstrip("/")`. Apply everywhere: protected-resource metadata, authorization_code storage, token issuance, introspection `aud` field.
[CITED: note.com/brave_quince241 — bug #3]

### Pitfall 3: `ClientRegistrationOptions(enabled=False)` (DCR default-off)
**What:** `ClientRegistrationOptions.enabled` defaults to `False`. Without setting `True`, the `/register` endpoint returns 404. Claude.ai calls `/register` as the first step after AS discovery — a 404 will silently abort the flow.
**Fix:** Pass `ClientRegistrationOptions(enabled=True, valid_scopes=["brain:read", "brain:write"])` in `AuthSettings`.
[CITED: mcp python-sdk source; WebSearch result]

### Pitfall 4: nginx must pass `/.well-known/` paths through to both services
**What:** mcp.example.com nginx currently proxies all requests to mcp-brain. The `/.well-known/oauth-protected-resource` path must reach mcp-brain (good — it's already proxied). The `/.well-known/oauth-authorization-server` path must reach **memory-api at api.example.com** — it's a different vhost, so Claude.ai will fetch it at `https://api.example.com/.well-known/oauth-authorization-server`. Current api.example.com nginx (20-api.conf) only proxies `/v1/` — it does **not** proxy `/.well-known/`. This is a required nginx change.
[VERIFIED: reading infrastructure/nginx/conf.d/20-api.conf and 40-mcp.conf]

### Pitfall 5: `Authorization` header must be passed through
**What:** 40-mcp.conf already has `proxy_set_header Authorization $http_authorization;` — good. 20-api.conf also passes it. No change needed here.
[VERIFIED: reading both nginx configs]

### Pitfall 6: CORS — `/.well-known/` endpoints need CORS headers
**What:** Claude.ai fetches `/.well-known/oauth-protected-resource` and `/.well-known/oauth-authorization-server` from browser context. If CORS headers are absent, the fetch is blocked. Current 40-mcp.conf adds CORS to `/` but NOT specifically to `/.well-known/` sub-paths — verify it falls through to the root location block (it should, since there's no more-specific location).
**Fix:** Confirm `/.well-known/` requests to mcp.example.com are covered by the existing CORS block. For api.example.com, a new `location /.well-known/` block must include CORS headers.
[ASSUMED — needs confirmation in testing]

### Pitfall 7: OAuth + Streamable HTTP known Claude.ai bug
**What:** Issue #291 (anthropics/claude-ai-mcp): after OAuth completes, Claude.ai sometimes GET-loops on `/mcp` and never sends POST initialize. Affects OAuth-protected streamable-HTTP specifically. SSE path (`/sse`) works.
**Mitigation strategy:** Plan should include a verification step using MCP Inspector CLI (`npx @modelcontextprotocol/inspector`) to confirm end-to-end before user testing. If the Claude.ai bug reproduces, add SSE fallback endpoint.
[CITED: github.com/anthropics/claude-ai-mcp/issues/291]

### Pitfall 8: `token_endpoint_auth_methods_supported` must include `"none"`
**What:** Claude.ai registers as a **public client** (no client secret). The token endpoint must accept requests without a `client_secret` (auth method `"none"`). If only `client_secret_post` or `client_secret_basic` are listed in AS metadata, Claude.ai's token exchange fails.
**Fix:** Include `"none"` in `token_endpoint_auth_methods_supported` in AS metadata.
[CITED: claude.com/docs/connectors/building/authentication — DCR/CIMD note; zenn.dev article]

### Pitfall 9: `resource` parameter in token request (RFC 8707)
**What:** Claude.ai includes `resource=https://mcp.example.com/mcp` in the token request. The token endpoint must accept and record this. The issued token's `aud` must match. mcp-brain's introspection-based validator must check this (when `--oauth-strict` mode is set in the SDK reference example).
[CITED: modelcontextprotocol.io/specification/2025-06-18/basic/authorization — resource parameter section]

---

## 6. Recommended Approach + New Endpoints/Files

### Approach (1 paragraph)

Deploy memory-api as a full OAuth 2.1 AS using the MCP Python SDK's `OAuthAuthorizationServerProvider`. memory-api gains 5 new routes under `/oauth/` (authorize, token, register, revoke, introspect) and one new `/.well-known/` route — all auto-generated by the SDK when `auth=OAuthProvider(...)` is passed to FastMCP/Starlette. A new `oauth_access_tokens` table and a new `oauth_authorization_codes` table back the AS storage. The consent page reuses GitHub OAuth for authentication and adds a team selector. mcp-brain is reconfigured as a Pure Resource Server using `IntrospectionTokenVerifier` — 20-30 lines of config change to `main.py`. Nginx gets two small additions: `/.well-known/` proxy block on api.example.com (routes to memory-api:8000) and a CORS block for the same. The existing `xbt_` path in `_resolve()` can remain as a fallback for existing clients with no disruption to LibreChat/Chrome ext/Claude Code.

### New Endpoints + Files Map

| New Item | Type | Location | Based On |
|----------|------|----------|---------|
| `GET /.well-known/oauth-authorization-server` | HTTP endpoint | memory-api (auto-generated by MCP SDK `OAuthAuthorizationServerProvider`) | RFC 8414 |
| `GET /oauth/authorize` | HTTP endpoint | memory-api | RFC 6749 + PKCE |
| `POST /oauth/authorize` | HTTP endpoint | memory-api | consent form submission |
| `POST /oauth/token` | HTTP endpoint | memory-api | RFC 6749 + RFC 8707 |
| `POST /oauth/register` | HTTP endpoint | memory-api | RFC 7591 (DCR) |
| `POST /oauth/revoke` | HTTP endpoint | memory-api | RFC 7009 |
| `POST /oauth/introspect` | HTTP endpoint | memory-api | RFC 7662 |
| `GET /.well-known/oauth-protected-resource` | HTTP endpoint | mcp-brain (auto-generated by SDK `AuthSettings`) | RFC 9728 |
| `apps/memory-api/app/auth/oauth_provider.py` | New file | memory-api | `OAuthAuthorizationServerProvider` impl |
| `apps/memory-api/app/auth/consent.py` | New file | memory-api | Consent page render + GitHub login redirect |
| `apps/memory-api/alembic/versions/XXXX_oauth_tables.py` | DB migration | memory-api | `oauth_authorization_codes`, `oauth_access_tokens`, `oauth_clients` tables |
| `apps/mcp-brain/app/main.py` | Modified | mcp-brain | Add `IntrospectionTokenVerifier` + `AuthSettings`; keep `xbt_` fallback |
| `apps/mcp-brain/app/config.py` | Modified | mcp-brain | Add `MEMORY_API_OAUTH_INTROSPECT_URL` setting |
| `infrastructure/nginx/conf.d/20-api.conf` | Modified | nginx | Add `location /.well-known/` block for api.example.com → memory-api:8000 |
| `infrastructure/nginx/conf.d/20-api.conf` | Modified | nginx | Add `location /oauth/` block for api.example.com → memory-api:8000 |
| Consent HTML template | New file | memory-api | GitHub login + team selector |

### DB Schema (new tables in memory-api)

```sql
-- OAuth registered clients (DCR)
CREATE TABLE oauth_clients (
  client_id TEXT PRIMARY KEY,
  client_name TEXT,
  redirect_uris TEXT[] NOT NULL,
  grant_types TEXT[] NOT NULL DEFAULT '{authorization_code,refresh_token}',
  registered_at TIMESTAMPTZ DEFAULT now()
);

-- Short-lived authorization codes
CREATE TABLE oauth_authorization_codes (
  code TEXT PRIMARY KEY,
  client_id TEXT NOT NULL,
  user_id UUID NOT NULL REFERENCES users(id),
  team_scope TEXT NOT NULL,
  resource TEXT NOT NULL,                -- audience binding
  code_challenge TEXT NOT NULL,          -- PKCE S256 challenge
  redirect_uri TEXT NOT NULL,
  scope TEXT NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,       -- short TTL, e.g. 5 min
  used_at TIMESTAMPTZ                    -- one-time use
);

-- Issued OAuth access tokens
CREATE TABLE oauth_access_tokens (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  token_hash TEXT NOT NULL UNIQUE,       -- SHA-256 of raw token
  client_id TEXT NOT NULL,
  user_id UUID NOT NULL REFERENCES users(id),
  team_scope TEXT NOT NULL,              -- bound at consent time
  resource TEXT NOT NULL,                -- audience claim
  scope TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'claude.ai-connector',
  created_at TIMESTAMPTZ DEFAULT now(),
  expires_at TIMESTAMPTZ,
  revoked_at TIMESTAMPTZ,
  -- refresh token (separate from access token)
  refresh_token_hash TEXT UNIQUE,
  refresh_token_expires_at TIMESTAMPTZ
);
```

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | OAuth access tokens should use `oat_` prefix + new table rather than reusing `user_api_tokens` | §4.2 | Could reuse `user_api_tokens` with extra nullable columns — lower DB migration cost but messier |
| A2 | mcp-brain `ctx.request_context.auth` or similar SDK accessor provides the validated `AccessToken` to tool handlers | §4.3 | If SDK accessor differs, tool handlers need adjustment to extract `team_scope` from introspection response |
| A3 | The existing `xbt_` fallback path in `_resolve()` can coexist with the SDK's `IntrospectionTokenVerifier` without SDK conflicts | §3.4 | If FastMCP strictly validates all Bearer tokens via the verifier, the `xbt_` fallback path may need `MultiAuthProvider` or a custom verifier that handles both |
| A4 | CORS on `/.well-known/` at api.example.com is currently absent and must be added | §5, Pitfall 6 | If CORS is already present on `*` at that vhost, no change needed |
| A5 | Claude.ai streamable-HTTP + OAuth bug (#291) is still open and unfixed | §1.1, Pitfall 7 | If Anthropic fixed it, no SSE fallback needed |

---

## Open Questions

1. **Claude.ai streamable-HTTP + OAuth bug status**
   - What we know: Issue #291 documents GET-loop after OAuth with streamable-HTTP; SSE path works.
   - What's unclear: Whether this was fixed in a Claude.ai deployment after the issue was filed (no close date visible).
   - Recommendation: Plan includes a smoke-test step with MCP Inspector. If bug reproduces on first test, add SSE compatibility endpoint alongside `/mcp`.

2. **`team_scope` in introspection response — SDK support for custom claims**
   - What we know: The MCP SDK's `AccessToken` class has `client_id`, `scopes`. v1.27.2 changelog adds `subject` and `claims`.
   - What's unclear: Whether `claims` dict is passed through from introspection response to tool handlers. [CITED: github.com/modelcontextprotocol/python-sdk releases v1.27.2]
   - Recommendation: Plan includes a verification sub-task to read `AccessToken.claims` in a test tool handler before committing to this design.

3. **Consent screen UX — GitHub App vs OAuth App for the `/authorize` login**
   - What we know: memory-api uses the **GitHub App** for primary sign-in (post Phase 12). The consent flow needs to authenticate the user during `/authorize`.
   - What's unclear: Whether the GitHub App user-to-server flow can be triggered from within an OAuth authorize redirect (different redirect_uri), or whether a separate OAuth App is cleaner.
   - Recommendation: Reuse the existing GitHub App client_id + the `/v1/auth/github/signin` exchange logic, but adapt the redirect_uri to be memory-api's own callback URL (`/oauth/github-callback`), which then resumes the `/authorize` flow after identity is resolved.

---

## Environment Availability

Step 2.6: SKIPPED — no new external dependencies. All required services (memory-api, mcp-brain, nginx, PostgreSQL) are already deployed. The only new runtime dependency is the introspection endpoint (`memory-api:8000/oauth/introspect`), which is internal.

---

## Sources

### Primary (HIGH confidence)
- [MCP Authorization Spec (2025-06-18)](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization) — full OAuth 2.1 AS/RS contract, RFC 9728, RFC 8707 resource binding
- [Claude.ai connector auth docs](https://claude.com/docs/connectors/building/authentication) — redirect URI, PKCE requirements, DCR vs CIMD, static token not supported
- [MCP Python SDK — auth/routes.py](https://raw.githubusercontent.com/modelcontextprotocol/python-sdk/main/src/mcp/server/auth/routes.py) — auto-generated endpoint list
- [MCP Python SDK — simple-auth example server.py](https://raw.githubusercontent.com/modelcontextprotocol/python-sdk/main/examples/servers/simple-auth/mcp_simple_auth/server.py) — AS/RS split pattern
- [PyPI mcp package](https://pypi.org/project/mcp/) — version 1.27.2, May 29 2026

### Secondary (MEDIUM confidence)
- [note.com/brave_quince241 — 3 FastMCP OAuth pitfalls](https://note.com/brave_quince241/n/nc6e3b8e90781) — `streamable_http_path`, trailing slash, simultaneous providers bug
- [sunpeak.ai — Claude Connector OAuth (May 2026)](https://sunpeak.ai/blogs/claude-connector-oauth-authentication/) — Claude.ai OAuth flow sequence, callback URI, DCR vs CIMD detail
- [zenn.dev — custom OAuth 2.1 implementation for Claude.ai](https://zenn.dev/hideakitamai/articles/6747c9bd56bd4f) — working implementation reference, endpoint list
- [crumrine/fastmcp-personal-auth](https://github.com/crumrine/fastmcp-personal-auth) — FastMCP `auth=` constructor pattern, `/mcp` path convention

### Tertiary (LOW confidence / active bugs)
- [anthropics/claude-ai-mcp issue #291](https://github.com/anthropics/claude-ai-mcp/issues/291) — streamable-HTTP + OAuth GET-loop bug; status unclear

---

## Confidence Breakdown

| Area | Level | Reason |
|------|-------|--------|
| OAuth spec requirements (what Claude.ai needs) | HIGH | Official MCP spec + Claude.ai auth docs, both verified |
| `mcp>=1.27` capabilities | HIGH | SDK source code verified; v1.27.0 pinned in pyproject.toml |
| FastMCP `auth=` wiring pattern | MEDIUM | Confirmed via reference example; FastMCP internal exact accessor names not verified |
| Proposed DB schema | ASSUMED | Logical design, not externally mandated |
| Claude.ai streamable-HTTP+OAuth bug current status | LOW | Filed issue, fix status unknown |

**Research date:** 2026-06-04
**Valid until:** 2026-07-04 (MCP auth spec + Claude.ai connector behavior evolves; re-verify before execution if > 30 days)
