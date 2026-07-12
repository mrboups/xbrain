---
phase: 14-portability-foundation
plan: 03b
type: execute
wave: 2
depends_on: [14-01, 14-02, 14-03a]
files_modified:
  - infrastructure/docker-compose.yml
  - infrastructure/librechat/librechat.yaml
  - infrastructure/centrifugo/config.json
  - Makefile
autonomous: true
requirements: [PORT-01]
must_haves:
  truths:
    - "docker-compose.yml carries no grooveos.app in ANY ${VAR:-...} fallback, env value, or comment; every public URL/domain fallback is neutral and both OAuth identity fallbacks are empty in BOTH services"
    - "WEBUI_URL is env-overridable (no longer an unwrapped hardcode)"
    - "librechat.yaml's 3 brand strings resolve from ${VAR} at container startup — and the RESOLVED value is asserted on a booted container, not assumed"
    - "Centrifugo allowed_origins is env-driven via CENTRIFUGO_CLIENT_ALLOWED_ORIGINS and the default still admits chrome-extension://* (or realtime silently dies for the extension)"
    - "`make env-check` FAILS when OAUTH_ISSUER_URL / OAUTH_RESOURCE_URL / CORS_ALLOWED_ORIGIN_REGEX are missing, and `make deploy` runs env-check first — the crashloop guard for the next deploy (ROADMAP SC#2a)"
  artifacts:
    - path: "infrastructure/docker-compose.yml"
      provides: "Neutral env fallbacks + APP_PUBLIC_URL + CORS regex + librechat build arg + centrifugo origins env"
    - path: "Makefile"
      provides: "Pre-deploy env guard (env-check extended + wired into deploy)"
      contains: "OAUTH_ISSUER_URL"
  key_links:
    - from: "infrastructure/docker-compose.yml (librechat env)"
      to: "infrastructure/librechat/librechat.yaml"
      via: "${LIBRECHAT_ALLOWED_DOMAINS}/${BRIDGE_BASE_URL}/${APP_TEAMS_URL} passed through librechat.environment"
      pattern: "LIBRECHAT_ALLOWED_DOMAINS|BRIDGE_BASE_URL"
    - from: "infrastructure/docker-compose.yml (centrifugo env)"
      to: "infrastructure/centrifugo/config.json"
      via: "CENTRIFUGO_CLIENT_ALLOWED_ORIGINS overlays client.allowed_origins"
      pattern: "CENTRIFUGO_CLIENT_ALLOWED_ORIGINS"
---

<objective>
De-hardcode the remaining infrastructure config (PORT-01): neutralize every `grooveos.app`
`${VAR:-...}` fallback in `docker-compose.yml`, wrap the one unwrapped hardcode (`WEBUI_URL`), thread
the new 14-01 vars (`APP_PUBLIC_URL`, `CORS_ALLOWED_ORIGIN_REGEX`) and the 14-02 LibreChat build arg
through compose, resolve `librechat.yaml`'s 3 brand strings from `${VAR}`, move Centrifugo's
allowed-origins to an env var — and install the **pre-deploy crashloop guard** in the `Makefile`.

Purpose: after 14-01, an EMPTY `OAUTH_ISSUER_URL`/`OAUTH_RESOURCE_URL` is FATAL at boot. Today those
two vars exist ONLY as compose fallbacks (`${OAUTH_ISSUER_URL:-https://api.grooveos.app}`) and are
absent from `.env.example` — so they were never prompted into the VM `.env`. Emptying the fallback
without a guard = memory-api + mcp-brain crashloop on the next deploy (project memory
`project_xbrain_vm_env_gotchas` confirms VM `.env` vars go missing). This plan empties the fallback
AND ships the guard that stops a blind deploy.
Output: config-driven infra with neutral defaults, boot-safe bare `docker compose up`, and a deploy
that refuses to run against an under-specified `.env`.

WHY SPLIT FROM 14-03a: 14-03a owns the ingress (nginx) atomically. This plan owns everything else in
the infra layer, so a failure here cannot leave the ingress half-migrated.
</objective>

<execution_context>
@D:/VSC/xbrain/.claude/get-shit-done/workflows/execute-plan.md
@D:/VSC/xbrain/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/phases/14-portability-foundation/14-CONTEXT.md
@.planning/phases/14-portability-foundation/14-RESEARCH.md
@.planning/phases/14-portability-foundation/14-01-PLAN.md

<interfaces>
<!-- FROM 14-01 (already merged): memory-api Settings now defines
       APP_PUBLIC_URL: str = "http://localhost:8000"
       CORS_ALLOWED_ORIGIN_REGEX: str = r"(chrome-extension://.*|http://localhost(:\d+)?)"
       OAUTH_ISSUER_URL: str = ""   OAUTH_RESOURCE_URL: str = ""   <- EMPTY IS NOW FATAL (field_validator)
     mcp-brain Settings: same empty+fatal OAuth pair. -->
<!-- FROM 14-02 (already merged): apps/librechat/Dockerfile now accepts `ARG MEMORY_API_BASE_URL=""`
     and seds it into onboarding.js. Compose must pass it via the librechat service's build.args. -->
<!-- FROM 14-03a (already merged): the nginx service block is DONE. Do not touch it again. -->

<!-- docker-compose.yml grooveos.app sites (line refs, 2026-07-11 — re-grep, 14-03a shifted them):
       122  MEMORY_API_EXTERNAL_URL: ${MEMORY_API_EXTERNAL_URL:-https://chat.grooveos.app}
       146  # comment: "Waitlist email proxy (grooveos.app landing page)"
       146  WAITLIST_TO: ${WAITLIST_TO:-team@grooveos.app}
       147  WAITLIST_FROM: ${WAITLIST_FROM:-GrooveOS <waitlist@grooveos.app>}
       167  CENTRIFUGO_WS_URL_PUBLIC: ${CENTRIFUGO_WS_URL_PUBLIC:-wss://centrifugo.grooveos.app/...}
       185  OAUTH_ISSUER_URL: ${OAUTH_ISSUER_URL:-https://api.grooveos.app}       (memory-api)
       186  OAUTH_RESOURCE_URL: ${OAUTH_RESOURCE_URL:-https://mcp.grooveos.app/mcp}  (memory-api)
       464  WEBUI_URL: https://adm.grooveos.app        <- UNWRAPPED. No ${:-}. True hardcode.
       612  NEXTAUTH_URL: ${LANGFUSE_HOST:-https://lang.grooveos.app}
       625  LANGFUSE_INIT_USER_EMAIL: ${LANGFUSE_INIT_USER_EMAIL:-team@grooveos.app}
       845  OAUTH_ISSUER_URL: ${OAUTH_ISSUER_URL:-https://api.grooveos.app}       (mcp-brain)
       846  OAUTH_RESOURCE_URL: ${OAUTH_RESOURCE_URL:-https://mcp.grooveos.app/mcp}  (mcp-brain)
       926  # comment: "Fronted by nginx vhost centrifugo.grooveos.app"
     librechat service ~387: has a `build:` block (context ../apps/librechat) + an `environment:` block. -->

<!-- librechat.yaml brand strings:
       27-28  registration.allowedDomains: - "grooveos.app"   (+ "gmail.com" on the next line)
       84     baseURL: "https://bridge.grooveos.app/v1"
       211    customUserVars.description ... <a href='https://grooveos.app/account/teams/'>grooveos.app/account/teams</a>
     LibreChat resolves ${VAR} in string fields at startup (already PROVEN in this file for apiKey /
     X-Team-Scope / X-Internal-Secret). It has NO ${VAR:-default} fallback syntax — the default must
     live in compose's ${VAR:-...} layer, then plain ${VAR} in the YAML.
     NOTE: line 164's `mcpSettings.allowedDomains` (http://mcp-gateway:8081 etc.) is INTERNAL DOCKER
     HOSTS — brand-free, DO NOT TOUCH.
     NOTE: the `promptPrefix` at ~line 155 contains the PRODUCT NAME "GrooveOS" (title-case), not a
     domain. Product naming is a Phase-16 rebrand concern, NOT a Phase-14 domain hardcode. Leave it.
     A case-sensitive grep for `grooveos` does not match `GrooveOS`, so 14-06's gate stays green. -->

<!-- centrifugo: image centrifugo/centrifugo:v6, `command: centrifugo -c /centrifugo/config.json`.
     Compose ALREADY overlays config keys via path-derived env names:
       client.token.hmac_secret_key -> CENTRIFUGO_CLIENT_TOKEN_HMAC_SECRET_KEY
       http_api.key                 -> CENTRIFUGO_HTTP_API_KEY
     => client.allowed_origins      -> CENTRIFUGO_CLIENT_ALLOWED_ORIGINS   [key PINNED by this convention]
     config.json client.allowed_origins TODAY:
       ["chrome-extension://*", "https://chat.grooveos.app", "https://app.grooveos.app", "https://xbrain-495115.web.app"]
     `chrome-extension://*` MUST survive into the default or the Chrome extension's WebSocket is
     origin-rejected and realtime dies silently. -->

<!-- Makefile: `env-check` (line ~96) loops a hardcoded var list and exits 1 on the first empty one.
     `deploy: sync` (line ~65) does NOT depend on env-check today. -->
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: docker-compose.yml — neutralize every fallback, wrap WEBUI_URL, thread the new 14-01/14-02 vars</name>
  <files>infrastructure/docker-compose.yml</files>
  <read_first>
    - infrastructure/docker-compose.yml — re-grep the current line numbers first (`grep -n 'grooveos' infrastructure/docker-compose.yml`); 14-03a already edited the nginx block, so the refs in <interfaces> may have shifted.
    - Blocks to touch: memory-api environment (~110-190), openwebui-pipeline WEBUI_URL (~464), langfuse (~612/625), mcp-brain environment (~845-846), librechat service (build + environment, ~387), centrifugo service (~927-940).
    - DO NOT touch the nginx service block (owned by 14-03a, already done).
  </read_first>
  <action>
    In infrastructure/docker-compose.yml:
    1. memory-api `environment:` — neutralize fallbacks and ADD the two new 14-01 vars:
       - `MEMORY_API_EXTERNAL_URL: ${MEMORY_API_EXTERNAL_URL:-http://localhost:8000}`
       - `CENTRIFUGO_WS_URL_PUBLIC: ${CENTRIFUGO_WS_URL_PUBLIC:-ws://localhost:8000/connection/websocket}`
       - `WAITLIST_TO: ${WAITLIST_TO:-}` and `WAITLIST_FROM: ${WAITLIST_FROM:-Example <waitlist@example.com>}`
       - `OAUTH_ISSUER_URL: ${OAUTH_ISSUER_URL:-}` and `OAUTH_RESOURCE_URL: ${OAUTH_RESOURCE_URL:-}`
         (EMPTY → fail-fast per 14-01; the Task-3 Makefile guard is what stops a blind deploy)
       - ADD `APP_PUBLIC_URL: ${APP_PUBLIC_URL:-http://localhost:8000}` next to MEMORY_API_EXTERNAL_URL.
       - ADD `CORS_ALLOWED_ORIGIN_REGEX: ${CORS_ALLOWED_ORIGIN_REGEX:-(chrome-extension://.*|http://localhost(:\d+)?)}`
         — QUOTE the whole YAML scalar (it contains `|` and `:`), e.g.
         `CORS_ALLOWED_ORIGIN_REGEX: "${CORS_ALLOWED_ORIGIN_REGEX:-(chrome-extension://.*|http://localhost(:\\d+)?)}"`.
         Do NOT put the prod grooveos regex here — that would re-introduce the brand into compose and
         fail the 14-06 gate. The prod value belongs in the VM `.env`; it is recorded as a
         DEPLOY-PREREQ in this plan's SUMMARY.
       - Fix the comment `# Waitlist email proxy (grooveos.app landing page)` → `# Waitlist email proxy (landing page)`.
    2. openwebui-pipeline: wrap the unwrapped hardcode → `WEBUI_URL: ${WEBUI_URL:-http://localhost:8080}`.
    3. langfuse: `NEXTAUTH_URL: ${LANGFUSE_HOST:-http://localhost:3000}` and
       `LANGFUSE_INIT_USER_EMAIL: ${LANGFUSE_INIT_USER_EMAIL:-admin@example.com}`.
    4. mcp-brain `environment:`: `OAUTH_ISSUER_URL: ${OAUTH_ISSUER_URL:-}` and
       `OAUTH_RESOURCE_URL: ${OAUTH_RESOURCE_URL:-}` (both empty — the SAME .env var feeds both
       services; a partial fix mints tokens for one resource and rejects them at the other,
       14-RESEARCH.md Pitfall 3).
    5. librechat service:
       - under `build:` add `args:` → `MEMORY_API_BASE_URL: ${LIBRECHAT_MEMORY_API_BASE:-}`
         (feeds 14-02's onboarding.js sed; empty = same-origin fallback).
       - under `environment:` add the three vars librechat.yaml resolves in Task 2:
         `LIBRECHAT_ALLOWED_DOMAINS: ${LIBRECHAT_ALLOWED_DOMAINS:-localhost}`
         `BRIDGE_BASE_URL: ${BRIDGE_BASE_URL:-http://session-bridge:8105/v1}`
         `APP_TEAMS_URL: ${APP_TEAMS_URL:-http://localhost:8000/account/teams/}`
    6. centrifugo service: add to `environment:`
       `CENTRIFUGO_CLIENT_ALLOWED_ORIGINS: ${CENTRIFUGO_ALLOWED_ORIGINS:-chrome-extension://* http://localhost:8080}`
       (space-separated list — Centrifugo v6 slice env format; the smoke check in Task 3 PROVES the
       key+format). `chrome-extension://*` MUST be in the default or extension realtime dies.
       Also scrub the comment `# Fronted by nginx vhost centrifugo.grooveos.app` → `# Fronted by the nginx centrifugo vhost.`
    Do NOT change any `default` team_scope fallback (`LIBRECHAT_DEFAULT_TEAM_SCOPE` etc.) — KEPT per D-04
    and the amended ROADMAP (explicitly out of scope).
  </action>
  <acceptance_criteria>
    - `grep -c 'grooveos' infrastructure/docker-compose.yml` returns 0 (catches values AND comments).
    - `grep -c 'APP_PUBLIC_URL' infrastructure/docker-compose.yml` returns >= 1.
    - `grep -c 'CORS_ALLOWED_ORIGIN_REGEX' infrastructure/docker-compose.yml` returns >= 1.
    - Both services' OAuth fallbacks are empty: `grep -c 'OAUTH_ISSUER_URL: ${OAUTH_ISSUER_URL:-}' infrastructure/docker-compose.yml` returns 2 and `grep -c 'OAUTH_RESOURCE_URL: ${OAUTH_RESOURCE_URL:-}' infrastructure/docker-compose.yml` returns 2.
    - No unwrapped hardcode left: `grep -Ec 'WEBUI_URL: *https?://' infrastructure/docker-compose.yml` returns 0.
    - `grep -c 'MEMORY_API_BASE_URL' infrastructure/docker-compose.yml` returns 1 (librechat build arg).
    - `grep -c 'CENTRIFUGO_CLIENT_ALLOWED_ORIGINS' infrastructure/docker-compose.yml` returns 1, and that line contains `chrome-extension://*`.
    - `docker compose -f infrastructure/docker-compose.yml config -q` exits 0 (compose interpolates + validates; also proves the quoted CORS regex scalar parses).
    - `default` team_scope untouched: `grep -c 'LIBRECHAT_DEFAULT_TEAM_SCOPE:-default' infrastructure/docker-compose.yml` returns 1.
  </acceptance_criteria>
  <verify>
    <automated>test "$(grep -c 'grooveos' infrastructure/docker-compose.yml)" = "0" && docker compose -f infrastructure/docker-compose.yml config -q</automated>
  </verify>
  <done>compose has zero brand strings, neutral fallbacks, empty OAuth in BOTH services, WEBUI_URL wrapped, and the new APP_PUBLIC_URL / CORS regex / librechat build arg / centrifugo origins wired.</done>
</task>

<task type="auto">
  <name>Task 2: librechat.yaml ${VAR} placeholders — with a RESOLVED-value boot assertion (registration must not break)</name>
  <files>infrastructure/librechat/librechat.yaml</files>
  <read_first>
    - infrastructure/librechat/librechat.yaml lines 25-30 (registration.allowedDomains), 80-90 (bridge baseURL), 205-215 (customUserVars.description), and lines 160-168 (mcpSettings.allowedDomains — INTERNAL DOCKER HOSTS, DO NOT TOUCH)
    - Confirm the existing `${VAR}` usage for `apiKey` / `X-Team-Scope` / `X-Internal-Secret` — that is the proof the mechanism works in this file.
  </read_first>
  <action>
    LibreChat has NO `${VAR:-default}` fallback syntax — the defaults live in compose's `${VAR:-...}`
    layer (added in Task 1). Here use plain `${VAR}`.
    1. `registration.allowedDomains:` — replace the `- "grooveos.app"` entry with
       `- "${LIBRECHAT_ALLOWED_DOMAINS}"`. KEEP the `- "gmail.com"` entry below it as-is.
    2. `baseURL: "https://bridge.grooveos.app/v1"` → `baseURL: "${BRIDGE_BASE_URL}"`.
    3. In `customUserVars.description`, replace
       `<a href='https://grooveos.app/account/teams/' target='_blank'>grooveos.app/account/teams</a>`
       with `<a href='${APP_TEAMS_URL}' target='_blank'>your team settings</a>` (drop the brand link text).
    Do NOT touch `mcpSettings.allowedDomains` (internal Docker hosts) or the `promptPrefix`
    (contains the product NAME "GrooveOS", not a domain — Phase-16 rebrand concern).

    RISK (must be discharged by the acceptance check below, not assumed):
    `${VAR}` substitution is only PROVEN in this file for `apiKey`/`headers`. If LibreChat does NOT
    substitute inside `registration.allowedDomains` (a list of strings), the literal string
    `"${LIBRECHAT_ALLOWED_DOMAINS}"` becomes the ONLY allowed signup domain → **nobody can register**.
    FALLBACK if the assertion below shows the value did NOT resolve: revert this file to literals and
    instead add an entrypoint `envsubst` pass over `librechat.yaml` in `apps/librechat/Dockerfile`
    (write the rendered file to the path LibreChat reads at startup, e.g. render
    `/app/librechat.yaml.template` → `/app/librechat.yaml` in the container entrypoint, substituting
    only `$LIBRECHAT_ALLOWED_DOMAINS $BRIDGE_BASE_URL $APP_TEAMS_URL`). Record which path you took in
    the SUMMARY.
  </action>
  <acceptance_criteria>
    - `grep -c 'grooveos' infrastructure/librechat/librechat.yaml` returns 0 (case-sensitive; the title-case `GrooveOS` product name in promptPrefix is intentionally retained and does not match).
    - `grep -Ec '\$\{(LIBRECHAT_ALLOWED_DOMAINS|BRIDGE_BASE_URL|APP_TEAMS_URL)\}' infrastructure/librechat/librechat.yaml` returns 3.
    - `grep -c 'gmail.com' infrastructure/librechat/librechat.yaml` returns >= 1 (second allowed domain preserved).
    - `grep -c 'mcp-gateway:8081' infrastructure/librechat/librechat.yaml` returns >= 1 (internal mcpSettings.allowedDomains untouched).
    - `python -c "import yaml; yaml.safe_load(open('infrastructure/librechat/librechat.yaml'))"` exits 0.
    - **RESOLVED-VALUE ASSERTION (the W1 discharge — do NOT skip):** boot the librechat container with
      `LIBRECHAT_ALLOWED_DOMAINS=acme.example BRIDGE_BASE_URL=http://session-bridge:8105/v1 APP_TEAMS_URL=https://acme.example/account/teams/`
      (`docker compose -f infrastructure/docker-compose.yml up -d librechat`, or `docker compose run --rm librechat` with those env values), then assert the value LibreChat actually loaded:
      `docker compose -f infrastructure/docker-compose.yml logs librechat 2>&1 | grep -iE 'allowedDomains|Custom config|librechat.yaml'`
      shows the config loaded WITHOUT error, AND `curl -s http://localhost:3080/api/config` (or the container's `/api/config`) returns a body in which the registration domain is `acme.example` and NOT the literal string `${LIBRECHAT_ALLOWED_DOMAINS}`. Concretely: `curl -s .../api/config | grep -c '\${LIBRECHAT_ALLOWED_DOMAINS}'` must return 0.
      If the literal placeholder IS present → the mechanism does not cover this field → take the
      entrypoint-envsubst fallback described in <action> and re-run this assertion.
    - Paste the resolved `/api/config` evidence (or the envsubst fallback decision) into the SUMMARY.
  </acceptance_criteria>
  <verify>
    <automated>test "$(grep -c 'grooveos' infrastructure/librechat/librechat.yaml)" = "0" && test "$(grep -Ec '\$\{(LIBRECHAT_ALLOWED_DOMAINS|BRIDGE_BASE_URL|APP_TEAMS_URL)\}' infrastructure/librechat/librechat.yaml)" = "3" && python -c "import yaml; yaml.safe_load(open('infrastructure/librechat/librechat.yaml'))"</automated>
  </verify>
  <done>librechat.yaml resolves the 3 brand strings from env, and the RESOLVED value is proven on a booted container — registration still works.</done>
</task>

<task type="auto">
  <name>Task 3: Centrifugo origins env-driven (with WS origin smoke) + Makefile pre-deploy crashloop guard</name>
  <files>infrastructure/centrifugo/config.json, Makefile</files>
  <read_first>
    - infrastructure/centrifugo/config.json (the `client.allowed_origins` array; note `chrome-extension://*` is the FIRST entry — it must survive)
    - infrastructure/docker-compose.yml centrifugo service (~927-945) — confirm the `CENTRIFUGO_CLIENT_ALLOWED_ORIGINS` env added in Task 1, and the pinned image `centrifugo/centrifugo:v6` + `command: centrifugo -c /centrifugo/config.json`
    - Makefile lines 60-70 (`deploy: sync`) and 94-99 (`env-check` — the hardcoded var list it loops over)
  </read_first>
  <action>
    In infrastructure/centrifugo/config.json:
    1. Set `client.allowed_origins` to `[]` (empty array — keep the KEY so the shape stays obvious and
       the file remains valid). The value is supplied by `CENTRIFUGO_CLIENT_ALLOWED_ORIGINS` (compose,
       Task 1), whose default carries `chrome-extension://*`. Do not delete any other key.
    In Makefile:
    2. Extend `env-check`'s var list with the three vars that are now REQUIRED-or-broken:
       `OAUTH_ISSUER_URL OAUTH_RESOURCE_URL CORS_ALLOWED_ORIGIN_REGEX`
       (appended to the existing `POSTGRES_PASSWORD GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET
       BRIDGE_SHARED_SECRET MEILI_MASTER_KEY OPENWEBUI_SECRET_KEY` loop). Why each:
         - OAUTH_ISSUER_URL / OAUTH_RESOURCE_URL: empty is now FATAL at boot (14-01 field_validator).
           They were previously served by a compose fallback that Task 1 removed, and they have NEVER
           been in `.env.example` — so the VM `.env` almost certainly lacks them. Missing → crashloop.
         - CORS_ALLOWED_ORIGIN_REGEX: missing → the neutral default applies → the Chrome extension and
           the web app are CORS-blocked in prod (silent functional regression, not a crash).
    3. Make `deploy` depend on the guard: change `deploy: sync` → `deploy: env-check sync`.
       Add a `@echo` in `env-check`'s failure path pointing at the phase-14 DEPLOY-PREREQ
       (e.g. `echo "MISSING: $$v — see .planning/phases/14-portability-foundation/14-06-SUMMARY.md (DEPLOY-PREREQ)"`).
    4. Add a REMOTE guard line at the top of the `deploy` recipe (the VM `.env` is the one that
       actually matters — `env-check` only reads the LOCAL `.env`, and project memory
       `project_xbrain_vm_env_gotchas` records that VM `.env` vars go missing):
       `$(SSH) 'cd /home/$(VM_USER)/xbrain && grep -q "^OAUTH_ISSUER_URL=" .env && grep -q "^OAUTH_RESOURCE_URL=" .env' || (echo "ABORT: VM .env is missing OAUTH_ISSUER_URL / OAUTH_RESOURCE_URL — memory-api + mcp-brain will crashloop. See 14-06-SUMMARY.md DEPLOY-PREREQ."; exit 1)`
       (match the existing `$(SSH)` / `$(VM_USER)` macro style already used by the `backup` target).
       NOTE: the production VM is currently TERMINATED, so this line cannot be EXECUTED now — its
       acceptance is static (`make -n deploy` shows it; the Makefile parses). It fires at the next
       real deploy, which is exactly the gate ROADMAP SC#2(a) asks for.
  </action>
  <acceptance_criteria>
    - `grep -c 'grooveos' infrastructure/centrifugo/config.json` returns 0.
    - `python -c "import json; d=json.load(open('infrastructure/centrifugo/config.json')); assert d['client']['allowed_origins'] == []"` exits 0 (key kept, value emptied, JSON valid).
    - `grep -c 'OAUTH_ISSUER_URL' Makefile` returns >= 2 (env-check list + the remote deploy guard).
    - `grep -c 'CORS_ALLOWED_ORIGIN_REGEX' Makefile` returns >= 1.
    - `grep -Ec '^deploy: .*env-check' Makefile` returns 1 (deploy depends on the guard).
    - Guard actually fails: `OAUTH_ISSUER_URL= make env-check` (or running env-check against a `.env` lacking the var) exits NON-zero and prints `MISSING: OAUTH_ISSUER_URL`.
    - Makefile parses: `make -n env-check` exits 0 and `make -n deploy` prints the guard + sync lines without executing them.
    - **CENTRIFUGO ORIGIN SMOKE (proves both the env KEY and the space-separated list FORMAT):**
      ```
      docker run --rm -d --name cent-smoke -p 8999:8000 \
        -e CENTRIFUGO_CLIENT_ALLOWED_ORIGINS="http://localhost:9999 chrome-extension://*" \
        -e CENTRIFUGO_CLIENT_TOKEN_HMAC_SECRET_KEY=smoke \
        -v "$PWD/infrastructure/centrifugo/config.json:/centrifugo/config.json:ro" \
        centrifugo/centrifugo:v6 centrifugo -c /centrifugo/config.json
      # allowed origin -> NOT 403 (101 upgrade, or 400 on protocol details — either proves it passed the origin check)
      curl -s -o /dev/null -w '%{http_code}\n' -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
        -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
        -H 'Origin: http://localhost:9999' http://localhost:8999/connection/websocket
      # disallowed origin -> 403
      curl -s -o /dev/null -w '%{http_code}\n' -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
        -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
        -H 'Origin: http://evil.example' http://localhost:8999/connection/websocket
      docker rm -f cent-smoke
      ```
      First curl must NOT be 403; second curl MUST be 403. If the allowed origin is ALSO 403, the env
      key or list format is wrong → try a JSON-array value (`'["http://localhost:9999","chrome-extension://*"]'`),
      fix BOTH compose and this check to match, and record the working format in the SUMMARY.
  </acceptance_criteria>
  <verify>
    <automated>python -c "import json; d=json.load(open('infrastructure/centrifugo/config.json')); assert d['client']['allowed_origins'] == []" && make -n deploy >/dev/null && grep -Ec '^deploy: .*env-check' Makefile</automated>
  </verify>
  <done>Centrifugo origins are env-driven (key + format PROVEN by a WS origin smoke, chrome-extension://* preserved); `make deploy` refuses to run against an .env missing the now-mandatory OAuth vars, locally AND on the VM.</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| browser → Centrifugo WebSocket | `client.allowed_origins` is the origin allow-list gating realtime connections |
| browser → LibreChat registration | `registration.allowedDomains` gates which email domains may sign up |
| operator `.env` → memory-api + mcp-brain boot | the two OAuth identity vars are now boot-fatal when empty |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-14-11 | Tampering | OAuth env must be empty in BOTH services or tokens minted for one resource are rejected by the other | mitigate | Task 1 empties `${OAUTH_ISSUER_URL:-}`/`${OAUTH_RESOURCE_URL:-}` in both env blocks together; acceptance greps for exactly 2 occurrences of each (14-RESEARCH.md Pitfall 3). |
| T-14-18 | Denial of Service | Emptying the compose OAuth fallback crashloops memory-api + mcp-brain on the next deploy (VM `.env` never had these vars) | mitigate | `make env-check` extended + `deploy: env-check sync` + a remote SSH grep of the VM `.env` in the deploy recipe. DEPLOY-PREREQ recorded in the SUMMARY. |
| T-14-21 | Denial of Service | LibreChat `registration.allowedDomains` receiving the LITERAL `${LIBRECHAT_ALLOWED_DOMAINS}` string → nobody can register | mitigate | Task 2 asserts the RESOLVED value on a booted container (`/api/config` must not contain the literal placeholder); documented entrypoint-envsubst fallback if the mechanism does not cover list fields. |
| T-14-22 | Denial of Service | Centrifugo default origins omitting `chrome-extension://*` → extension WebSocket origin-rejected, realtime dies silently | mitigate | The compose default explicitly carries `chrome-extension://*`; the WS origin smoke asserts allowed→not-403 and disallowed→403, proving key + format. |
| T-14-12 | Information Disclosure | LibreChat allowedDomains / Centrifugo origins defaulting to a foreign domain, widening the allow surface | accept | Neutral `localhost` / `chrome-extension://*` defaults NARROW, not widen. Bearer-token auth + RFC 8707 audience binding unchanged. |
</threat_model>

<verification>
- Zero `grooveos` in docker-compose.yml, librechat.yaml, centrifugo/config.json.
- `docker compose config -q` + librechat.yaml YAML + centrifugo JSON all parse.
- Both OAuth fallbacks empty in BOTH services; WEBUI_URL wrapped.
- LibreChat's RESOLVED registration domain is `acme.example`, not the literal placeholder.
- Centrifugo WS origin smoke: allowed origin not-403, disallowed origin 403.
- `make env-check` fails on missing OAuth vars; `make deploy` depends on it and SSH-checks the VM `.env`.
</verification>

<success_criteria>
- The infra layer points at a new domain via config alone — no source edit (PORT-01).
- Prod values reproduce prod; a bare `docker compose up` boots (except the deliberate OAuth fail-fast).
- The next real deploy CANNOT crashloop on the missing OAuth vars — it aborts with an actionable message (ROADMAP SC#2a).
</success_criteria>

<output>
After completion, create `.planning/phases/14-portability-foundation/14-03b-SUMMARY.md`.
MUST contain a `## DEPLOY-PREREQ` section listing the exact `.env` lines to add to the VM before the
next deploy, with the CURRENT PROD VALUES (this SUMMARY lives inside the phase-14 dir, which is an
explicit keep-as-is / grep-excluded surface, so recording the real domain here is correct):

```
OAUTH_ISSUER_URL=https://api.grooveos.app
OAUTH_RESOURCE_URL=https://mcp.grooveos.app/mcp
CORS_ALLOWED_ORIGIN_REGEX=(chrome-extension://.*|https://chat\.grooveos\.app|https://grooveos\.app|https://grooveos\.web\.app|https://dejavu-app\.web\.app|https://claude\.ai)
XBRAIN_BASE_DOMAIN=grooveos.app
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
</output>
