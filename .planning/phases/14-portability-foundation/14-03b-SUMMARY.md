---
phase: 14-portability-foundation
plan: 03b
subsystem: infra
tags: [docker-compose, librechat, centrifugo, makefile, envsubst, portability]

# Dependency graph
requires:
  - phase: 14-portability-foundation (14-01)
    provides: "Neutral Settings defaults + OAuth fail-fast field_validator in memory-api/mcp-brain + APP_PUBLIC_URL/CORS_ALLOWED_ORIGIN_REGEX/AGENT_MENTION_ALIASES config fields"
  - phase: 14-portability-foundation (14-02)
    provides: "apps/librechat/Dockerfile ARG MEMORY_API_BASE_URL build-time onboarding.js sed"
  - phase: 14-portability-foundation (14-03a)
    provides: "nginx ingress fully env-driven via XBRAIN_BASE_DOMAIN; docker-compose.yml nginx service block done"
provides:
  - "docker-compose.yml carries zero grooveos.app strings in ANY fallback/value/comment; both memory-api and mcp-brain OAuth fallbacks empty; WEBUI_URL wrapped; librechat build.args + 3 new env vars wired; centrifugo origins env-driven"
  - "librechat.yaml.template + entrypoint-envsubst mechanism (apps/librechat/patches/render-config-entrypoint.sh) — proven working fallback for the 2 config fields LibreChat's native ${VAR} substitution does NOT cover"
  - "centrifugo/config.json client.allowed_origins fully env-driven (CENTRIFUGO_CLIENT_ALLOWED_ORIGINS), chrome-extension://* preserved in the default"
  - "Makefile env-check + deploy pre-deploy crashloop guard (5 mandatory vars, local AND remote/VM)"
affects: [14-04, 14-05, 14-06]

# Tech tracking
tech-stack:
  added: [gettext/envsubst (apps/librechat Dockerfile build dependency)]
  patterns:
    - "Entrypoint-envsubst fallback for config fields a framework's own ${VAR} substitution does not cover — template file bind-mounted read-only, rendered to a writable in-image path by a wrapper ENTRYPOINT before handing off to the original entrypoint"
    - "USER root / USER node bracket in a Dockerfile RUN block that needs package-manager privileges on a base image with a non-root default USER — restore the base image's least-privilege default afterward"
    - "Pre-deploy Makefile guard pattern: local env-check + a remote SSH grep of the VM .env for the SAME var list, both gating the deploy target as a dependency"

key-files:
  created:
    - apps/librechat/patches/render-config-entrypoint.sh
  modified:
    - infrastructure/docker-compose.yml
    - infrastructure/librechat/librechat.yaml (renamed to librechat.yaml.template)
    - apps/librechat/Dockerfile
    - infrastructure/centrifugo/config.json
    - Makefile

key-decisions:
  - "LibreChat's native ${VAR} substitution (extractEnvVariable) is NOT a global config-tree walk — it is wired at specific field-consumption sites only (apiKey, custom-endpoint baseURL via packages/api/src/endpoints/custom/config.ts, MCP url/env/headers via packages/data-provider/src/mcp.ts). registration.allowedDomains and customUserVars.description are plain strings/arrays read directly off the parsed YAML with no transform — proven by inspecting the shipped v0.8.5 image source, not assumed."
  - "Took the plan's documented entrypoint-envsubst fallback for ALL 3 target vars (not just the 2 that don't natively resolve) for a single, uniformly-verifiable mechanism — baseURL would have worked either way; consistency was preferred over splitting the approach."
  - "envsubst is restricted to exactly the 3 target var names (envsubst '$LIBRECHAT_ALLOWED_DOMAINS $BRIDGE_BASE_URL $APP_TEAMS_URL') so every other ${VAR} field (apiKey, MCP headers) is left untouched for LibreChat's own native per-field resolution from the same process.env — no double-processing risk."
  - "Dockerfile switches to USER root only for apk add gettext + COPY/chmod of the entrypoint script, then restores USER node — matches the base image's own least-privilege default (verified via docker inspect .Config.User)."
  - "OAUTH_ISSUER_URL/OAUTH_RESOURCE_URL emptied in BOTH memory-api and mcp-brain env blocks in the same commit (T-14-11) — a partial fix would mint tokens for one resource and have the other reject them."

patterns-established:
  - "Any future librechat.yaml field that needs to be config-driven but isn't apiKey/baseURL/MCP url|env|headers must go through the entrypoint-envsubst template mechanism, not a bare ${VAR} in the YAML — verify against the shipped LibreChat source before assuming substitution works."

requirements-completed: [PORT-01]

# Metrics
duration: 34min
completed: 2026-07-12
---

# Phase 14 Plan 03b: Infra Layer De-hardcoding + Deploy Guard Summary

**Neutralized every remaining `grooveos.app` default across docker-compose.yml/librechat.yaml/centrifugo config, discovered and fixed a real LibreChat substitution gap via an entrypoint-envsubst fallback (verified end-to-end against the exact shipped image), and shipped a Makefile pre-deploy guard that aborts on 5 now-mandatory env vars both locally and on the VM.**

## Performance

- **Duration:** 34 min
- **Started:** 2026-07-12T03:03:32Z
- **Completed:** 2026-07-12T03:38:01Z
- **Tasks:** 3
- **Files modified:** 6 (4 modified, 1 renamed, 1 new)

## Accomplishments
- `docker-compose.yml` carries zero `grooveos.app` strings anywhere — values, `${VAR:-...}` fallbacks, and comments — across memory-api, openwebui, langfuse, mcp-brain, and centrifugo blocks; the nginx service block (owned by 14-03a) was left byte-for-byte untouched (verified via `git diff 1090a24 HEAD -- infrastructure/docker-compose.yml | grep nginx:` — empty)
- `WEBUI_URL` unwrapped hardcode wrapped in `${WEBUI_URL:-http://localhost:8080}`
- `OAUTH_ISSUER_URL`/`OAUTH_RESOURCE_URL` emptied in **both** memory-api and mcp-brain (14-01's fail-fast validator now actually fires on an unconfigured `.env` instead of silently defaulting to the brand domain)
- `APP_PUBLIC_URL` + quoted `CORS_ALLOWED_ORIGIN_REGEX` wired into memory-api's environment; `docker compose config -q` proves the quoted regex scalar (containing `|` and `:`) parses correctly
- librechat service: `build.args.MEMORY_API_BASE_URL` wired from `${LIBRECHAT_MEMORY_API_BASE}` (feeds 14-02's onboarding.js sed) + 3 new environment vars for the config template
- **Real risk discharged, not assumed:** inspected the actual LibreChat v0.8.5 image source (pulled `ghcr.io/danny-avila/librechat:v0.8.5`, extracted `packages/api/src/endpoints/custom/config.ts`, `packages/data-provider/src/mcp.ts`, `api/server/middleware/checkDomainAllowed.js`) and proved `registration.allowedDomains` and `customUserVars.description` are **not** covered by LibreChat's native `${VAR}` substitution — only `apiKey`/custom-endpoint-`baseURL`/MCP `url`+`env`+`headers` get `extractEnvVariable()` applied. Had this gone unverified, `registration.allowedDomains` would have rendered as the literal string `${LIBRECHAT_ALLOWED_DOMAINS}` at runtime — the ONLY allowed signup domain would be that literal string, and nobody could have registered.
- Implemented and proved the plan's documented fallback: `librechat.yaml` renamed to `librechat.yaml.template` (bind-mounted read-only), a new entrypoint wrapper (`apps/librechat/patches/render-config-entrypoint.sh`) renders it to `/app/librechat.yaml` via a var-restricted `envsubst` before handing off to the original `docker-entrypoint.sh`
- **End-to-end proof against the exact production base image and runtime user** (not a mock): `ghcr.io/danny-avila/librechat:v0.8.5`, `--user node` (verified via `docker inspect .Config.User` == `node`), template mounted, 3 env vars set — rendered output confirmed `allowedDomains: ["acme.example"]` (not the literal placeholder), the `customUserVars.description` href resolved to the real `APP_TEAMS_URL`, the Claude Pro Max `baseURL` resolved to `BRIDGE_BASE_URL`, `apiKey: "${ANTHROPIC_API_KEY}"` was correctly left untouched for LibreChat's own resolution, zero `grooveos` remained, and the rendered YAML parsed valid
- `centrifugo/config.json`'s `client.allowed_origins` emptied to `[]`; a real `centrifugo/centrifugo:v6` container smoke test proved both the env key name (`CENTRIFUGO_CLIENT_ALLOWED_ORIGINS`) and the space-separated list format: allowed origin → WebSocket 101 upgrade, disallowed origin → 403, and (beyond the plan's literal checks) a concrete `chrome-extension://` origin → 101, confirming the wildcard default actually protects the Chrome extension's realtime channel
- `Makefile`'s `env-check` extended with the 5 now-mandatory vars (`OAUTH_ISSUER_URL`, `OAUTH_RESOURCE_URL`, `CORS_ALLOWED_ORIGIN_REGEX`, `XBRAIN_BASE_DOMAIN`, `AGENT_MENTION_ALIASES`); `deploy: env-check sync` gates every deploy; a remote SSH guard checks the SAME 5 vars against the VM's `.env` (not just the 2 OAuth ones), since `env-check` only ever reads the local `.env` and project memory confirms VM `.env` vars go missing independently

## Task Commits

Each task was committed atomically:

1. **Task 1: docker-compose.yml — neutralize every fallback, wrap WEBUI_URL, thread the new 14-01/14-02 vars** - `51a2b71` (feat)
2. **Task 2: librechat.yaml ${VAR} placeholders — with a RESOLVED-value boot assertion** - `72d0a1f` (feat)
3. **Task 3: Centrifugo origins env-driven + Makefile pre-deploy crashloop guard** - `411446f` (feat)

**Plan metadata:** SUMMARY commit follows this document.

## Files Created/Modified
- `infrastructure/docker-compose.yml` - Neutralized all grooveos.app fallbacks/comments (memory-api, openwebui, langfuse, mcp-brain, centrifugo, session-bridge comment); added APP_PUBLIC_URL/CORS_ALLOWED_ORIGIN_REGEX/librechat build.args/3 librechat env vars/CENTRIFUGO_CLIENT_ALLOWED_ORIGINS; wrapped WEBUI_URL; nginx block untouched
- `infrastructure/librechat/librechat.yaml.template` (renamed from `librechat.yaml`) - 3 brand strings converted to `${VAR}` placeholders; now a template rendered by the new entrypoint wrapper, not read directly by LibreChat
- `apps/librechat/Dockerfile` - Added Patch 9: `apk add gettext` (as root, then restored `USER node`) + COPY the entrypoint wrapper + `ENTRYPOINT` override
- `apps/librechat/patches/render-config-entrypoint.sh` - NEW — envsubst's the 3 target vars into `/app/librechat.yaml`, then execs the original `docker-entrypoint.sh`
- `infrastructure/centrifugo/config.json` - `client.allowed_origins` emptied to `[]` (key kept)
- `Makefile` - `env-check` extended to 11 vars; `deploy: env-check sync`; remote SSH guard added at the top of the `deploy` recipe

## Decisions Made
- LibreChat's `${VAR}` substitution is field-specific, not a global config-tree walk — verified from the shipped source rather than assumed from the plan's stated risk (see key-decisions in frontmatter)
- Applied the entrypoint-envsubst fallback uniformly to all 3 target vars (including `BRIDGE_BASE_URL`, which would have resolved natively) for one consistent, single-mechanism, fully-verified path rather than splitting native vs. fallback handling across fields
- `envsubst` restricted to exactly `$LIBRECHAT_ALLOWED_DOMAINS $BRIDGE_BASE_URL $APP_TEAMS_URL` so every other `${VAR}` in the file (apiKey, MCP headers) is left for LibreChat's own resolution — zero risk of double-processing or corrupting secrets that contain `$`/`{`/`}`
- Dockerfile brackets the `apk add`/`COPY`/`chmod` steps with `USER root` ... `USER node` to restore the base image's non-root runtime default (verified `/app` is node-writable, `/usr/local/bin` is root-owned)
- `make` binary is unavailable on this Windows dev host — Makefile acceptance criteria requiring literal `make -n`/`make env-check` invocations were instead verified by (a) manually simulating the exact recipe body via `bash -c '...'` against both a passing and failing `.env`, and (b) static inspection of tab-indentation and target-dependency wiring. Documented as an environment limitation, not a scope reduction — the underlying recipe logic was proven to behave correctly.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug in plan's own risk assessment] `registration.allowedDomains` and `customUserVars.description` do NOT resolve via LibreChat's native `${VAR}` mechanism — confirmed, not assumed**
- **Found during:** Task 2, RESOLVED-VALUE ASSERTION
- **Issue:** The plan flagged this as an open RISK requiring verification before assuming the mechanism worked. Inspection of the actual LibreChat v0.8.5 source (pulled the exact image, extracted `packages/api/src/endpoints/custom/config.ts`, `packages/data-provider/src/mcp.ts`, `api/server/middleware/checkDomainAllowed.js`) confirmed `extractEnvVariable()` is applied ONLY at specific field-consumption sites — `apiKey`/custom-endpoint `baseURL` (via `loadCustomEndpointsConfig`), MCP `url`/`env`/`headers` (via zod `.transform()` in `mcp.ts`). `loadCustomConfig.js` itself performs zero substitution pass over the parsed config tree. `checkDomainAllowed.js` reads `appConfig?.registration?.allowedDomains` directly, and the `customUserVars` zod schema has `description: z.string()` with no transform. Left as plain `${VAR}` literals, `registration.allowedDomains` would have rendered as the literal string `${LIBRECHAT_ALLOWED_DOMAINS}` at runtime — the ONLY allowed signup domain would be that literal string, and nobody could register (T-14-21 in the plan's own threat register).
- **Fix:** Implemented the plan's documented fallback: renamed `librechat.yaml` → `librechat.yaml.template`, added `apps/librechat/patches/render-config-entrypoint.sh` (envsubst wrapper), updated `apps/librechat/Dockerfile` (apk gettext + USER root/node bracket + ENTRYPOINT override), updated `docker-compose.yml`'s librechat volume mount to bind the template instead of the final YAML.
- **Files modified:** `infrastructure/librechat/librechat.yaml` (renamed), `apps/librechat/Dockerfile`, `apps/librechat/patches/render-config-entrypoint.sh` (new), `infrastructure/docker-compose.yml`
- **Verification:** End-to-end render proof against the exact base image (`ghcr.io/danny-avila/librechat:v0.8.5`) as the exact runtime user (`node`) — see "Accomplishments" above for the full evidence. `grep -c grooveos` = 0 on the rendered output; rendered YAML parses valid; `apiKey`/other native-mechanism fields correctly left untouched.
- **Committed in:** `72d0a1f` (Task 2 commit)

**2. [Rule 3 - Blocking] `make` binary not installed on this Windows dev host**
- **Found during:** Task 3, acceptance criteria verification
- **Issue:** The plan's acceptance criteria include literal `make -n env-check`, `make -n deploy`, and `OAUTH_ISSUER_URL= make env-check` invocations. `make`/`make.exe` is not present anywhere on `$PATH`, in Git for Windows' bundled tools, or via chocolatey on this host (confirmed via `which make`, filesystem search, and PowerShell `Get-Command`).
- **Fix:** Simulated the exact `env-check` recipe body via `bash -c '...'` against both a `.env` missing `OAUTH_ISSUER_URL` (correctly printed `MISSING: OAUTH_ISSUER_URL ...` and exited 1) and a complete `.env` (correctly printed `All required env vars present.` and exited 0). Statically verified tab-indentation on all new/modified recipe lines (`cat -A` confirms `^I` tab markers, not spaces) and confirmed `deploy: env-check sync`/`env-check:`/`sync:` targets are all correctly defined and wired.
- **Files modified:** None (verification-only workaround)
- **Verification:** See "Decisions Made" above; recipe body behavior proven correct in both branches.
- **Committed in:** N/A (no source change required)

---

**Total deviations:** 2 auto-fixed (1 plan-risk verification requiring a real fallback implementation, 1 environment-tooling verification workaround). No scope creep — the entrypoint-envsubst fallback was explicitly pre-authorized by the plan's own `<action>` text as the required response if the RESOLVED-VALUE ASSERTION failed.
**Impact on plan:** The fallback was necessary for LibreChat registration to work at all post-de-hardcoding — without it, this plan would have shipped a config that silently broke signup. No unrelated files were touched.

## Issues Encountered

- **Centrifugo v6.9.0 startup warning (non-blocking, pre-existing/environmental):** the smoke-test container logged `"level":"warn","key":"dev","message":"unknown key in configuration file"`. Confirmed this is NOT caused by this plan's edit — `infrastructure/centrifugo/config.json` contains no `dev` key anywhere (verified by reading the file post-edit). The container started successfully regardless (`"message":"serving websocket, api, prometheus, health endpoints on :8000"`), and all 3 origin checks (allowed/disallowed/chrome-extension wildcard) passed cleanly. Not fixed — out of this plan's scope, does not affect any acceptance criterion.
- **Docker on Windows path translation:** `$PWD` from git-bash produces a POSIX-style path that Docker Desktop on this host does not translate — `-v` mounts silently resolve empty/missing instead of erroring (same gotcha 14-03a documented). Used `pwd -W` throughout for all `docker run -v` invocations in this plan's verification work.

## User Setup Required

None for local development — a bare `docker compose up` boots with all-neutral defaults (except the deliberate OAuth fail-fast, which is itself the point of this plan).

**DEPLOY-PREREQ (production VM, before the next deploy):**

```
OAUTH_ISSUER_URL=https://api.grooveos.app
OAUTH_RESOURCE_URL=https://mcp.grooveos.app/mcp
CORS_ALLOWED_ORIGIN_REGEX=(chrome-extension://.*|https://chat\.grooveos\.app|https://grooveos\.app|https://grooveos\.web\.app|https://dejavu-app\.web\.app|https://claude\.ai)
XBRAIN_BASE_DOMAIN=grooveos.app
AGENT_MENTION_ALIASES=agent,grooveos,groove,gr,g
MEMORY_API_EXTERNAL_URL=https://chat.grooveos.app
APP_PUBLIC_URL=https://grooveos.app
CENTRIFUGO_WS_URL_PUBLIC=wss://centrifugo.grooveos.app/connection/websocket
CENTRIFUGO_ALLOWED_ORIGINS=chrome-extension://* https://chat.grooveos.app https://app.grooveos.app https://xbrain-495115.web.app
WEBUI_URL=https://adm.grooveos.app
LANGFUSE_HOST=https://lang.grooveos.app
LIBRECHAT_ALLOWED_DOMAINS=grooveos.app
BRIDGE_BASE_URL=https://bridge.grooveos.app/v1
APP_TEAMS_URL=https://grooveos.app/account/teams/
LIBRECHAT_MEMORY_API_BASE=https://api.grooveos.app
WAITLIST_TO=team@grooveos.app
WAITLIST_FROM=GrooveOS <waitlist@grooveos.app>
LANGFUSE_INIT_USER_EMAIL=team@grooveos.app
```

Without these, the next `make deploy` will now ABORT before touching the VM (the Makefile guard added in Task 3), rather than silently crashlooping memory-api/mcp-brain or breaking ingress/CORS/@mentions in production.

## Next Phase Readiness
- The infra layer (docker-compose.yml, librechat.yaml, centrifugo config, Makefile) is fully config-driven with neutral defaults; combined with 14-01 (server config) and 14-03a (nginx ingress), PORT-01's infra surface is complete
- The entrypoint-envsubst pattern established here (`apps/librechat/patches/render-config-entrypoint.sh`) should be reused for any future librechat.yaml field that needs to be config-driven but isn't apiKey/baseURL/MCP url|env|headers
- The Makefile deploy guard is in place but UNTESTED against the real VM (VM is currently terminated per project memory) — its remote-SSH branch will fire at the next real `make deploy`
- No blockers for 14-04/14-05/14-06

---
*Phase: 14-portability-foundation*
*Completed: 2026-07-12*

## Self-Check: PASSED

All 6 files created/modified by this plan verified present on disk (`infrastructure/docker-compose.yml`, `infrastructure/librechat/librechat.yaml.template`, `apps/librechat/Dockerfile`, `apps/librechat/patches/render-config-entrypoint.sh`, `infrastructure/centrifugo/config.json`, `Makefile`); the old `librechat.yaml` filename confirmed absent (correctly renamed, not duplicated); all 3 task commits (`51a2b71`, `72d0a1f`, `411446f`) verified present in git log. No missing items.
