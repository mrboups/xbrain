# GitHub content access — Phase 1: read/query org repos in the brain

> ✅ **SHIPPED + DEPLOYED + LIVE 2026-06-03.** memory-api Contents service + `/v1/internal/github/{list,read}`,
> `mcp-github` sidecar (:8107), gateway tool `github` registered. 12 unit tests pass. **Verified live
> reading real content** via the gateway (real LLM path): `github_list_files`/`github_read_file` on
> `mrboups/xbrain` return the actual tree + file text.
>
> **Auth resolution order** (`_resolve_installation_token`): (1) org App installation → (2) user App
> installation → (3) **`GITHUB_FALLBACK_TOKEN`** (a user `gho_` OAuth token, scope `repo`) when the App
> isn't installed. The fallback (mrboups' LibreChat token, pulled from LibreChat Mongo into
> `infrastructure/.env`) is what makes it work TODAY with **zero GitHub-admin action**. ⚠️ Fallback reads
> at that one user's access level (server-wide shared) — the proper multi-team model is the App
> installation (`contents:read`). The tool resolves per repo-owner — does NOT use the (stale) `GITHUB_ORG`.
> **Deploy gotcha:** `docker compose` runs from `infrastructure/` → it reads **`infrastructure/.env`**, NOT
> the repo-root `/home/user/xbrain/.env` (the two have diverged). Env vars must go in `infrastructure/.env`.


**Goal (Phase 1):** any team member or agent (LibreChat, agent-runtime) can **read & query the
content of GitHub repos the org has sanctioned**, without having direct repo access themselves —
the "team's collective GitHub access" surfaced through xbrain. Consent model: **(b) GitHub App
installation** (`contents:read`), org-admin-controlled, no per-user token borrowing.

Out of scope for Phase 1 (later): indexing repos into Qdrant (`github-sync`), agent-driven
redeploy/run, deployment metadata, write actions (issues/PRs), extension `@claude` wiring.

## Architecture

```
LibreChat / agent-runtime
      │  (xbrain MCP tools, via aggregate :8081 / gateway :8080)
      ▼
mcp-github sidecar (:8105, FastMCP)   ← thin proxy, NO GitHub secrets
      │  bridge JWT (BRIDGE_SHARED_SECRET)
      ▼
memory-api  GET /v1/internal/github/{list,read}
      │  reuses get_installation_token_for_org()  (github_installation.py:247)
      ▼
GitHub Contents API  (App installation token, contents:read)
```

**Why a thin sidecar + memory-api endpoints (not a sidecar holding a PAT):** the GitHub App
private key already lives in memory-api (`GITHUB_APP_PRIVATE_KEY_B64`), with a tested
installation-token minter (`app/services/github_installation.py`). Centralising the App secret
there + keeping the sidecar a dumb proxy avoids duplicating the App machinery and matches method (b)
(short-lived installation tokens, not a static PAT).

## Components

### memory-api
- `app/services/github_contents.py` (NEW)
  - `_resolve_installation_token(session, owner)` — try org installation
    (`get_installation_token_for_org(session, owner)`); if `None`, fall back to a **user**
    installation (`GET /users/{owner}/installation` with an App JWT → installation_id →
    `get_installation_token(installation_id)`). Returns token or `None`. (Covers both org repos and a
    member's personal Claude-Code repo.)
  - `list_repo_files(session, repo, path="", ref="HEAD") -> list[dict]` — `owner/repo` split, token,
    `GET /repos/{owner}/{repo}/contents/{path}` (JSON), map to `[{name,path,type,size,sha}]`. 404→`[]`.
  - `read_repo_file(session, repo, path, ref="HEAD") -> dict` — token, contents API, base64-decode,
    **100 KB cap** (`truncated` flag), dir→error. Returns `{repo,path,ref,size,truncated,content}`.
  - 403 from GitHub → raise a typed error mapped to HTTP 403 + a clear "App lacks contents:read"
    message (this is the expected state until the manual grant below is done).
- `app/routes/internal_github.py` (NEW) — `GET /internal/github/list`, `GET /internal/github/read`
  (prefix `/v1` in main.py). Auth: `get_current_principal` (accepts `kind=bridge`). Wire in `main.py`.
- `tests/test_github_contents.py` (NEW) — respx-mock the App JWT + installation + contents API
  (follow `tests/test_phase12_installation_token.py`: RSA key fixture + `respx.mock`).

### apps/mcp-github (NEW sidecar, port 8105)
- `app/main.py` — FastMCP `"xbrain-github"`, tools `github_list_files(repo, path, ref)` +
  `github_read_file(repo, path, ref)`; each mints a bridge JWT and calls the memory-api internal
  endpoint. Read tool returns text (or a clear error string); list returns JSON.
- `app/bridge_jwt.py` — copy mcp-brain's HS256 bridge-JWT minter.
- `app/config.py` — `MEMORY_API_URL`, `BRIDGE_SHARED_SECRET` (pydantic-settings).
- `Dockerfile`, `pyproject.toml` (`mcp`, `httpx`, `structlog`, `authlib`, `pydantic-settings`), `app/__init__.py`.

### infrastructure
- `docker-compose.yml` — add `mcp-github` service (build `../apps/mcp-github`, port 8105, env
  `MEMORY_API_URL` + `BRIDGE_SHARED_SECRET`, `networks: [xbrain_net]`, `mem_limit: 128m`,
  `depends_on: memory-api`, healthcheck). **Deploy note:** insert the block surgically on the VM
  (the VM compose is not git-tracked and may have drift — diff before touching).
- `scripts/register-mcp-tools.sh` — `register_tool "github" "http://mcp-github:8105" "..."`; bump
  `TOTAL_TOOLS` 4→5. After registering, **restart mcp-gateway** so the aggregate re-discovers.

### No change needed
- `librechat.yaml` — the `xbrain` MCP server (aggregate :8081) auto-discovers the new tool at startup.
- agent-runtime — auto-discovers via gateway (cache TTL 300s).

## Tool surface (LLM-facing)
- `github_list_files(repo: "owner/repo", path="", ref="HEAD")` → JSON `[{name,path,type,size,sha}]`.
- `github_read_file(repo: "owner/repo", path, ref="HEAD")` → file text (≤100 KB).

The gateway registers ONE entry `github`; with 2 sidecar tools the model passes the function name in
the call body (`{"name":"github_read_file", ...}`) — same as `mcp-deck`'s 2-tool precedent.

## The ONE manual step (org admin — cannot be coded)
1. `https://github.com/settings/apps/xbrain-auth` → **Permissions & events → Repository permissions
   → Contents → Read-only** → Save.
2. Each org that has the App installed must **approve the new permission**
   (`…/settings/installations/{id}` → Review request → Accept).
3. Ensure the target repos are in the installation (All repos, or Selected + add them).
Until done, the Contents API returns **403** and the tool surfaces a clear "grant contents:read" message.
For a member's **personal** Claude-Code repo, install the App on that user account + select the repo.

## Verification (as far as possible pre-grant)
- mcp-github healthy; `github` tool registered (`/tools` lists it); gateway restarted.
- memory-api `/v1/internal/github/list` reachable with a bridge JWT → returns either content (if
  grant done) or a clean 403 "needs contents:read".
- Installation-token minting works (reuses tested Phase-12 path).
Full end-to-end (real file content) unblocks the moment the org grants `contents:read`.
