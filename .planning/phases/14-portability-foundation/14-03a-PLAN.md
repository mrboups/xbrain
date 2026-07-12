---
phase: 14-portability-foundation
plan: 03a
type: execute
wave: 1
depends_on: []
files_modified:
  - infrastructure/nginx/conf.d/00-health.conf
  - infrastructure/nginx/conf.d/10-xbrain.conf
  - infrastructure/nginx/conf.d/20-api.conf
  - infrastructure/nginx/conf.d/30-projects.conf
  - infrastructure/nginx/conf.d/40-mcp.conf
  - infrastructure/nginx/conf.d/50-bridge.conf
  - infrastructure/nginx/conf.d/60-centrifugo.conf
  - infrastructure/nginx/templates/
  - infrastructure/docker-compose.yml
autonomous: true
requirements: [PORT-01]
must_haves:
  truths:
    - "nginx server_name is env-driven — an operator points every vhost at their domain by setting ONE var (XBRAIN_BASE_DOMAIN), with no source edit"
    - "The rendered config actually starts nginx — proven by running the real image's entrypoint (envsubst) and `nginx -t`, not by piping one file through a bare envsubst"
    - "With XBRAIN_BASE_DOMAIN=grooveos.app the rendered server_name set is IDENTICAL to today's conf.d set (regression safety, SC#2)"
    - "The stock image's /etc/nginx/conf.d/default.conf (server_name localhost) does NOT reappear once the conf.d read-only mount is removed"
  artifacts:
    - path: "infrastructure/nginx/templates"
      provides: "envsubst-based nginx vhost templates driven by XBRAIN_BASE_DOMAIN"
    - path: "infrastructure/nginx/templates/default.conf.template"
      provides: "Neutralizes the stock nginx default.conf rogue vhost"
  key_links:
    - from: "infrastructure/docker-compose.yml (nginx service)"
      to: "infrastructure/nginx/templates/*.conf.template"
      via: "volume mount /etc/nginx/templates + XBRAIN_BASE_DOMAIN + NGINX_ENVSUBST_FILTER=^XBRAIN_"
      pattern: "/etc/nginx/templates"
---

<objective>
Make the **ingress** env-driven, atomically. Convert `infrastructure/nginx/conf.d/*.conf` (7 files,
18 hardcoded `server_name *.grooveos.app` directives) into `envsubst` templates driven by a single
`XBRAIN_BASE_DOMAIN` var, using the official `nginx:1.27-alpine` image's built-in template mechanism
(`/etc/nginx/templates/*.template` → `/etc/nginx/conf.d/*.conf` at container start), and rewire the
nginx service in `docker-compose.yml` in the SAME plan so the ingress is never left half-migrated.

Purpose: PORT-01's "point at your own domain via config alone" is violated if the operator must edit
nginx source. nginx does not read `.env` — this is the one surface with no existing config mechanism.
Output: env-driven vhosts + a proven-bootable rendered config.

WHY THIS IS ITS OWN PLAN (split from the old 14-03): nginx is the SOLE ingress. Removing the
`./nginx/conf.d:/etc/nginx/conf.d:ro` mount and adding the templates mount must land together with
the template files, or every vhost dies. The compose ENV/librechat/centrifugo work is a separate,
independently-revertable concern → 14-03b.
</objective>

<execution_context>
@D:/VSC/xbrain/.claude/get-shit-done/workflows/execute-plan.md
@D:/VSC/xbrain/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/phases/14-portability-foundation/14-CONTEXT.md
@.planning/phases/14-portability-foundation/14-RESEARCH.md

<interfaces>
<!-- The official nginx image runs /docker-entrypoint.d/20-envsubst-on-templates.sh at container start:
       for each /etc/nginx/templates/<name>.template
         envsubst "$defined_envs" < "$template" > /etc/nginx/conf.d/<name>
     NGINX_ENVSUBST_FILTER (regex) restricts WHICH env vars are substituted. Set it to ^XBRAIN_ so
     nginx's own runtime vars ($host, $remote_addr, $memory_api_upstream) are never touched.
     Note: the output name strips ONLY the trailing ".template" — so `20-api.conf.template`
     renders to `/etc/nginx/conf.d/20-api.conf`. [CITED: hub.docker.com/_/nginx] -->

<!-- CURRENT server_name inventory (the exact set the render must reproduce for XBRAIN_BASE_DOMAIN=grooveos.app):
       00-health.conf:3       server_name _;                       (catch-all — LEAVE AS-IS)
       10-xbrain.conf:33      server_name chat.grooveos.app;
       10-xbrain.conf:135     server_name adm.grooveos.app;
       10-xbrain.conf:158     server_name lang.grooveos.app;
       10-xbrain.conf:181     server_name _;                       (catch-all — LEAVE AS-IS)
       20-api.conf:7          server_name api.grooveos.app;
       30-projects.conf:10    server_name projects.grooveos.app;
       40-mcp.conf:4          server_name mcp.grooveos.app;
       50-bridge.conf:17      server_name bridge.grooveos.app;
       60-centrifugo.conf:9   server_name centrifugo.grooveos.app;
     -> 8 substitutable subdomains + 2 catch-alls. There is NO bare-apex server_name today. -->

<!-- OFFLINE-VALIDATABLE: the confs already use `set $x_upstream http://svc:port; proxy_pass $x_upstream;`
     plus `resolver 127.0.0.11 valid=30s ipv6=off;` — the variable form DEFERS DNS resolution to
     request time, so `nginx -t` parses cleanly in an isolated container with no xbrain_net.
     This is what makes the Task-2 acceptance check runnable. Do NOT convert these to literal
     proxy_pass hostnames. -->

<!-- CURRENT compose nginx service (lines 37-52):
       image: nginx:1.27-alpine
       ports: ["80:80"]
       volumes: [ ./nginx/conf.d:/etc/nginx/conf.d:ro ]     <- this RO mount currently MASKS the
                                                               stock image's conf.d/default.conf
       healthcheck: wget http://127.0.0.1/nginx-health | grep -q ok   (served by 00-health.conf) -->
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Convert the 7 nginx confs to XBRAIN_BASE_DOMAIN templates (+ neutralize the stock default.conf)</name>
  <files>infrastructure/nginx/templates/*.conf.template (new), infrastructure/nginx/conf.d/*.conf (removed)</files>
  <read_first>
    - infrastructure/nginx/conf.d/00-health.conf, 10-xbrain.conf, 20-api.conf, 30-projects.conf, 40-mcp.conf, 50-bridge.conf, 60-centrifugo.conf (all 7 — note the `set $x_upstream` + `resolver` pattern and the two `server_name _;` catch-alls)
  </read_first>
  <action>
    1. `mkdir -p infrastructure/nginx/templates`. Move ALL 7 files from `infrastructure/nginx/conf.d/`
       to `infrastructure/nginx/templates/`, appending `.template` to each name (keep the `.conf`):
         `00-health.conf` → `00-health.conf.template`
         `10-xbrain.conf` → `10-xbrain.conf.template`
         `20-api.conf` → `20-api.conf.template`
         `30-projects.conf` → `30-projects.conf.template`
         `40-mcp.conf` → `40-mcp.conf.template`
         `50-bridge.conf` → `50-bridge.conf.template`
         `60-centrifugo.conf` → `60-centrifugo.conf.template`
       Use `git mv` so history is preserved. `infrastructure/nginx/conf.d/` must end up with NO `.conf`
       files (delete the directory if it is then empty — the compose mount for it is removed in Task 2).
    2. In the moved templates, replace ONLY the 8 branded `server_name` values:
         `server_name chat.grooveos.app;`       → `server_name chat.${XBRAIN_BASE_DOMAIN};`
         `server_name adm.grooveos.app;`        → `server_name adm.${XBRAIN_BASE_DOMAIN};`
         `server_name lang.grooveos.app;`       → `server_name lang.${XBRAIN_BASE_DOMAIN};`
         `server_name api.grooveos.app;`        → `server_name api.${XBRAIN_BASE_DOMAIN};`
         `server_name projects.grooveos.app;`   → `server_name projects.${XBRAIN_BASE_DOMAIN};`
         `server_name mcp.grooveos.app;`        → `server_name mcp.${XBRAIN_BASE_DOMAIN};`
         `server_name bridge.grooveos.app;`     → `server_name bridge.${XBRAIN_BASE_DOMAIN};`
         `server_name centrifugo.grooveos.app;` → `server_name centrifugo.${XBRAIN_BASE_DOMAIN};`
       LEAVE both `server_name _;` catch-alls EXACTLY as they are.
       LEAVE every `$`-prefixed nginx runtime variable untouched (`$host`, `$remote_addr`,
       `$memory_api_upstream`, ...). They are safe because Task 2 sets NGINX_ENVSUBST_FILTER=^XBRAIN_,
       which makes envsubst substitute ONLY vars whose name starts with XBRAIN_.
       Also scrub any grooveos.app left in comments inside these files.
    3. CREATE `infrastructure/nginx/templates/default.conf.template` containing ONLY comment lines:
       ```
       # Intentionally (almost) empty.
       # The stock nginx image ships /etc/nginx/conf.d/default.conf (server_name localhost, root
       # /usr/share/nginx/html). Until Phase 14 that file was MASKED by the read-only ./nginx/conf.d
       # mount. That mount is gone (we now mount /etc/nginx/templates instead), so the stock file
       # would REAPPEAR and add a rogue vhost on :80.
       # This template renders over it: /etc/nginx/templates/default.conf.template
       #   -> /etc/nginx/conf.d/default.conf  (this comment-only file).
       # xbrain's real vhosts live in the numbered templates. Do not add directives here.
       ```
       A comment-only nginx conf file is valid and contributes no server block.
  </action>
  <acceptance_criteria>
    - `ls infrastructure/nginx/templates/*.conf.template | wc -l` returns 8 (7 moved + default.conf.template).
    - `test -f infrastructure/nginx/templates/default.conf.template` and it declares NO server block: `grep -c 'server' infrastructure/nginx/templates/default.conf.template` returns 0 (comment text must not contain the word `server` — reword the comment if needed to keep this grep clean).
    - `ls infrastructure/nginx/conf.d/*.conf 2>/dev/null | wc -l` returns 0 (no vhost left behind).
    - `grep -rc 'grooveos' infrastructure/nginx/` returns 0 across every file.
    - `grep -rho '\${XBRAIN_BASE_DOMAIN}' infrastructure/nginx/templates/ | wc -l` returns 8 (one per branded subdomain).
    - Catch-alls preserved: `grep -rc 'server_name _;' infrastructure/nginx/templates/ | awk -F: '{s+=$2} END {print s}'` returns 2.
  </acceptance_criteria>
  <verify>
    <automated>test "$(grep -rc 'grooveos' infrastructure/nginx/ | awk -F: '{s+=$2} END {print s+0}')" = "0" && test "$(grep -rho '\${XBRAIN_BASE_DOMAIN}' infrastructure/nginx/templates/ | wc -l)" = "8" && test "$(ls infrastructure/nginx/templates/*.conf.template | wc -l)" = "8"</automated>
  </verify>
  <done>All 7 vhosts are XBRAIN_BASE_DOMAIN-driven templates; the stock default.conf is neutralized; zero brand strings under infrastructure/nginx/.</done>
</task>

<task type="auto">
  <name>Task 2: Wire the templates into the nginx service + PROVE the rendered config boots and reproduces prod</name>
  <files>infrastructure/docker-compose.yml</files>
  <read_first>
    - infrastructure/docker-compose.yml lines 37-52 (the ENTIRE nginx service block — image, ports, the `./nginx/conf.d:/etc/nginx/conf.d:ro` volume, healthcheck). Do NOT touch any other service in this plan — 14-03b owns the rest of this file.
  </read_first>
  <action>
    In `infrastructure/docker-compose.yml`, nginx service ONLY:
    1. Replace the volume `- ./nginx/conf.d:/etc/nginx/conf.d:ro` with
       `- ./nginx/templates:/etc/nginx/templates:ro`.
    2. Add an `environment:` block to the nginx service:
       ```yaml
       environment:
         XBRAIN_BASE_DOMAIN: ${XBRAIN_BASE_DOMAIN:-localhost}
         NGINX_ENVSUBST_FILTER: "^XBRAIN_"
       ```
       The `${XBRAIN_BASE_DOMAIN:-localhost}` compose default is the correct guard — an operator with
       no `.env` still gets a bootable nginx (vhosts answer on chat.localhost, api.localhost, ...).
       KEEP it. `NGINX_ENVSUBST_FILTER=^XBRAIN_` is what protects nginx's own `$host` / `$*_upstream`
       runtime variables from being blanked by envsubst — it is NOT optional.
    3. Leave `image: nginx:1.27-alpine`, `ports`, `mem_limit`, and the healthcheck unchanged. The
       healthcheck hits `/nginx-health` served by 00-health.conf's `server_name _;` catch-all, which
       is domain-independent and still renders.
    Change NOTHING else in docker-compose.yml.
  </action>
  <acceptance_criteria>
    - `grep -c '/etc/nginx/templates' infrastructure/docker-compose.yml` returns 1 and `grep -c '/etc/nginx/conf.d' infrastructure/docker-compose.yml` returns 0 (old mount gone).
    - `grep -c 'NGINX_ENVSUBST_FILTER' infrastructure/docker-compose.yml` returns 1.
    - `grep -c 'XBRAIN_BASE_DOMAIN:-localhost' infrastructure/docker-compose.yml` returns 1 (boot-safe default kept).
    - Compose still parses: `python -c "import yaml; yaml.safe_load(open('infrastructure/docker-compose.yml'))"` exits 0.
    - **RENDER PROOF (the real image code path, not a bare envsubst):**
      `docker run --rm -e XBRAIN_BASE_DOMAIN=acme.example -e NGINX_ENVSUBST_FILTER='^XBRAIN_' -v "$PWD/infrastructure/nginx/templates:/etc/nginx/templates:ro" nginx:1.27-alpine nginx -t`
      exits 0 (`configuration file /etc/nginx/nginx.conf test is successful`). This runs
      /docker-entrypoint.d/20-envsubst-on-templates.sh, so it validates BOTH the substitution and the
      resulting nginx syntax. It works offline because proxy_pass uses `$var` upstreams + `resolver`.
    - **REGRESSION PROOF (SC#2):** render with the prod value and diff the server_name set against
      today's. Run:
      `docker run --rm -e XBRAIN_BASE_DOMAIN=grooveos.app -e NGINX_ENVSUBST_FILTER='^XBRAIN_' -v "$PWD/infrastructure/nginx/templates:/etc/nginx/templates:ro" --entrypoint sh nginx:1.27-alpine -c '/docker-entrypoint.sh nginx -t >/dev/null 2>&1; grep -rho "server_name .*;" /etc/nginx/conf.d/ | sort'`
      The output must equal EXACTLY (sorted) the set from `git show HEAD:infrastructure/nginx/conf.d` —
      i.e. these 10 lines: `server_name _;` (x2), `server_name adm.grooveos.app;`,
      `server_name api.grooveos.app;`, `server_name bridge.grooveos.app;`,
      `server_name centrifugo.grooveos.app;`, `server_name chat.grooveos.app;`,
      `server_name lang.grooveos.app;`, `server_name mcp.grooveos.app;`,
      `server_name projects.grooveos.app;`. Zero additions (no `server_name localhost;` from a stock
      default.conf), zero omissions.
    - Record BOTH command outputs verbatim in the SUMMARY (they are the SC#2 evidence for the ingress).
  </acceptance_criteria>
  <verify>
    <automated>docker run --rm -e XBRAIN_BASE_DOMAIN=acme.example -e NGINX_ENVSUBST_FILTER='^XBRAIN_' -v "$PWD/infrastructure/nginx/templates:/etc/nginx/templates:ro" nginx:1.27-alpine nginx -t</automated>
  </verify>
  <done>nginx boots from templates; `nginx -t` passes on the rendered output; the prod-value render reproduces today's server_name set exactly, with no rogue default.conf vhost.</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| internet → nginx vhosts | `server_name` routing decides which upstream a request reaches; a rogue/duplicate vhost can shadow a real one |
| operator env → nginx config text | `XBRAIN_BASE_DOMAIN` is substituted into the config BEFORE nginx parses it |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-14-10 | Tampering | envsubst blanking nginx's own runtime vars (`$host`, `$memory_api_upstream`) if unfiltered → broken proxying | mitigate | `NGINX_ENVSUBST_FILTER=^XBRAIN_` restricts substitution to XBRAIN_* only. Proven by the Task-2 `nginx -t` render check (a blanked `proxy_pass ;` fails the syntax test). |
| T-14-19 | Spoofing | The stock image's `default.conf` (`server_name localhost`) reappearing on :80 once the conf.d mask is removed, shadowing the intended catch-all | mitigate | `default.conf.template` renders over it with a comment-only file; the regression-proof check asserts NO `server_name localhost;` appears in the rendered set. |
| T-14-20 | Denial of Service | Ingress left half-migrated (templates exist but compose still mounts the deleted conf.d, or vice-versa) → every vhost 404s | mitigate | Template move + compose rewire are in the SAME plan, gated by a render proof that runs the real image entrypoint. |
</threat_model>

<verification>
- Zero `grooveos` under `infrastructure/nginx/`.
- `nginx -t` exits 0 against the real image's envsubst-rendered output with `XBRAIN_BASE_DOMAIN=acme.example`.
- With `XBRAIN_BASE_DOMAIN=grooveos.app`, the rendered `server_name` set matches today's `conf.d` set exactly (SC#2).
- No `server_name localhost;` in the rendered output (stock default.conf neutralized).
</verification>

<success_criteria>
- One var (`XBRAIN_BASE_DOMAIN`) repoints every vhost — no source edit (PORT-01).
- The rendered config is proven bootable, not merely proven substituted.
- Prod values reproduce prod's exact vhost set (SC#2).
</success_criteria>

<output>
After completion, create `.planning/phases/14-portability-foundation/14-03a-SUMMARY.md`
(paste the two acceptance command outputs — they are the ingress SC#2 evidence).
</output>
