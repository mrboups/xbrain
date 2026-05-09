---
phase: 08-granola-oauth-per-user-universal-extraction-pipeline-platform-agents
plan: 05
status: complete
completed: 2026-05-09
---

# Phase 8 Plan 05: GitHub Repos Dynamic Endpoints Summary

## One-liner

Real proxy chain: memory-api /v1/github/repos forwards LibreChat JWT to LibreChat /api/xbrain/github-repos which reads github_access_token from MongoDB and calls GitHub API with visibility=all.

## Architecture

Real proxy — memory-api /v1/github/repos forwards caller's Bearer token to LibreChat /api/xbrain/github-repos via LIBRECHAT_INTERNAL_URL (Docker internal network). LibreChat validates the session JWT, reads github_access_token from MongoDB for that user, calls GitHub API, returns repos.

## Why proxy not direct

github_access_token lives in MongoDB LibreChat. memory-api must not read MongoDB directly (boundary). Proxy delegates auth + token lookup to LibreChat (source of truth).

## GitHub OAuth scope

`repo` scope required (stored in github_access_token from githubStrategy.js). Without it, private repos won't be returned.

## Privacy

- `visibility=all&affiliation=owner,collaborator,organization_member` returns all visible repos
- `Cache-Control: no-store` on LibreChat endpoint
- Per-user isolation: findOne({_id: req.user._id}) — user A can't access user B's repos

## LIBRECHAT_INTERNAL_URL

Set to `http://librechat:3080` in .env on VM. Without it, GET /v1/github/repos returns 503.

## Rebuild required

`docker compose up -d --build librechat memory-api` on VM to activate both endpoints.

## Files Created/Modified

- `apps/librechat/patches/xbrain-routes.js` — added GET /api/xbrain/github-repos endpoint (47 lines)
- `apps/memory-api/app/routes/github_repos.py` — new file, proxy router (54 lines)
- `apps/memory-api/app/main.py` — added github_repos import + include_router
- `apps/memory-api/app/config.py` — added LIBRECHAT_INTERNAL_URL: str = ""
- `.env.example` — documented LIBRECHAT_INTERNAL_URL with Phase 8 section

## Commits

- `12bb16f`: feat(librechat): add GET /api/xbrain/github-repos endpoint (plan 08-05)
- `327383e`: feat(memory-api): add GET /v1/github/repos proxy + LIBRECHAT_INTERNAL_URL (plan 08-05)

## Deviations from Plan

None - plan executed exactly as written.

## Known Stubs

None. Both endpoints are real implementations: LibreChat calls GitHub API directly, memory-api proxies with full error handling.

## Self-Check: PASSED

- apps/librechat/patches/xbrain-routes.js: FOUND
- apps/memory-api/app/routes/github_repos.py: FOUND
- apps/memory-api/app/main.py: FOUND (updated)
- apps/memory-api/app/config.py: FOUND (updated)
- .env.example: FOUND (updated)
- Commit 12bb16f: FOUND
- Commit 327383e: FOUND
- All 9 acceptance checks: PASS
