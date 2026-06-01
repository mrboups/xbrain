---
quick_id: 260601-1ay
status: complete
commits:
  - f58fe4e  # tasks 1-3: mcp-brain code
  - 1515fcb  # task 4: infra wiring
  - bd6a6a0  # task 5: tests
verification: py_compile only (no local venv for mcp-brain)
---

# Summary — 260601-1ay Tokenless mcp-brain team resolution for LibreChat

## Files changed

- `apps/mcp-brain/app/config.py` — added `BRIDGE_SHARED_SECRET: str = ""` and `INTERNAL_EMAIL_PATH_ENABLED: bool = True`
- `apps/mcp-brain/app/bridge_jwt.py` (NEW) — `mint_bridge_jwt(*, secret, team_scope, sub, ttl=300) -> str` via authlib HS256, payload mirrors aggregate.py exactly
- `apps/mcp-brain/app/memory_client.py` — added `resolve_team_scope_internal(bridge_jwt, sub) -> str | None` calling GET /v1/internal/resolve-team-scope
- `apps/mcp-brain/app/main.py` — removed `_get_token`; rewrote `_resolve(ctx)` with dual-path (token + gated email)
- `apps/mcp-brain/pyproject.toml` — added `"authlib>=1.3.0"` to dependencies
- `infrastructure/docker-compose.yml` — added `BRIDGE_SHARED_SECRET: ${BRIDGE_SHARED_SECRET}` to mcp-brain `environment:` (librechat already had it — not duplicated)
- `infrastructure/librechat/librechat.yaml` — added `X-LibreChat-User-Email` + `X-Internal-Secret` headers to `xbrain-memory` MCP server; updated `XBRAIN_TOKEN` description
- `apps/mcp-brain/tests/__init__.py` (NEW) — empty package marker
- `apps/mcp-brain/tests/test_resolve.py` (NEW) — 9 unit tests covering all paths

## Exact _resolve logic

```
_resolve(ctx):
  headers = ctx.request_context.request.headers
  authorization = headers.get("authorization") or ""
  email = headers.get("x-librechat-user-email") or ""
  x_internal_secret = headers.get("x-internal-secret") or ""

  raw = authorization.removeprefix("Bearer ").strip() if authorization.startswith("Bearer ") else ""

  # TOKEN PATH
  if raw.startswith("xbt_"):
      me = await memory_client.get_me(raw)
      team_scope = me.get("api_token_team_scope") or me.get("team_scope")
      if not team_scope: raise ValueError("Token is not associated with a team...")
      log.info("mcp_brain.resolve", mode="token", team=team_scope)
      return raw, team_scope

  # EMAIL PATH (fail-closed)
  secret = settings.BRIDGE_SHARED_SECRET
  ok = (
      settings.INTERNAL_EMAIL_PATH_ENABLED
      and bool(secret)
      and bool(x_internal_secret)
      and hmac.compare_digest(x_internal_secret, secret)
      and bool(email)
  )
  if not ok: raise ValueError("Authentication required: set your xbt_ token, ...")

  sub = f"email:{email}"
  jwt0 = mint_bridge_jwt(secret=secret, team_scope="default", sub=sub)
  team_scope = await memory_client.resolve_team_scope_internal(jwt0, sub)
  if not team_scope:
      team_scope = await memory_client.resolve_team_scope_internal(jwt0, email)
  if not team_scope: raise ValueError("No team is associated with your account yet.")

  jwt1 = mint_bridge_jwt(secret=secret, team_scope=team_scope, sub=sub)
  log.info("mcp_brain.resolve", mode="email", team=team_scope)
  return jwt1, team_scope
```

## Commit hashes

| Commit  | Scope             | Content |
|---------|-------------------|---------|
| f58fe4e | mcp-brain code    | tasks 1-3: config, bridge_jwt, memory_client, main, pyproject.toml |
| 1515fcb | infra             | task 4: docker-compose + librechat.yaml |
| bd6a6a0 | tests             | task 5: 9 tests in apps/mcp-brain/tests/test_resolve.py |

## Verification status

**py_compile only** — mcp-brain has no local virtualenv on this machine. All 5 Python
files (config.py, bridge_jwt.py, memory_client.py, main.py, test_resolve.py) passed
`python -m py_compile` with no errors.

`pytest apps/mcp-brain/tests/test_resolve.py -q` was NOT run locally.
The orchestrator should run it on the VM after rebuilding the mcp-brain container
(authlib + pytest-asyncio must be available in the venv).

## Deviations from PLAN

None. All 5 tasks implemented exactly as specified:
- Task 1: config + pyproject.toml + bridge_jwt.py — done
- Task 2: resolve_team_scope_internal in memory_client.py — done
- Task 3: _resolve rewritten with dual-path, hmac.compare_digest, log.info (no secret logged) — done
- Task 4: docker-compose mcp-brain env + librechat.yaml headers (librechat already had BRIDGE_SHARED_SECRET, confirmed at line 381, not duplicated) — done
- Task 5: 9 tests (exceeds the 4+ minimum), all paths covered including killswitch + empty-secret + retry-bare-email — done
