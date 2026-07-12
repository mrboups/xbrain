---
phase: 14-portability-foundation
reviewed: 2026-07-12T00:00:00Z
depth: standard
files_reviewed: 33
files_reviewed_list:
  - .env.example
  - Makefile
  - apps/drive-sync/app/config.py
  - apps/librechat/Dockerfile
  - apps/librechat/patches/onboarding.js
  - apps/librechat/patches/render-config-entrypoint.sh
  - apps/mcp-brain/app/config.py
  - apps/mcp-brain/tests/conftest.py
  - apps/memory-api/.env.example
  - apps/memory-api/app/config.py
  - apps/memory-api/app/deps.py
  - apps/memory-api/app/main.py
  - apps/memory-api/app/routes/waitlist.py
  - apps/memory-api/app/services/mention_detector.py
  - apps/memory-api/app/services/notifications.py
  - apps/memory-api/app/services/relevance_filter.py
  - apps/memory-api/tests/conftest.py
  - apps/memory-api/tests/test_mention_detector.py
  - infrastructure/centrifugo/config.json
  - infrastructure/docker-compose.yml
  - infrastructure/librechat/librechat.yaml.template
  - infrastructure/nginx/templates/00-health.conf.template
  - infrastructure/nginx/templates/10-xbrain.conf.template
  - infrastructure/nginx/templates/20-api.conf.template
  - infrastructure/nginx/templates/30-projects.conf.template
  - infrastructure/nginx/templates/40-mcp.conf.template
  - infrastructure/nginx/templates/50-bridge.conf.template
  - infrastructure/nginx/templates/60-centrifugo.conf.template
  - infrastructure/nginx/templates/default.conf.template
  - infrastructure/scripts/brain-index.sh
  - infrastructure/scripts/preflight-env.sh
  - infrastructure/scripts/verify-phase14.sh
  - .github/workflow-templates/deploy-cloudrun.yml
  - .github/workflow-templates/deploy-firebase.yml
findings:
  critical: 2
  warning: 4
  info: 2
  total: 8
status: issues_found
---

# Phase 14: Code Review Report

**Reviewed:** 2026-07-12T00:00:00Z
**Depth:** standard
**Files Reviewed:** 33
**Status:** issues_found

## Summary

Phase 14 de-brands the stack and wires most deployment-specific values through
`.env` + `docker-compose.yml` + envsubst templates cleanly. The OAuth
fail-fast validators, the nginx `XBRAIN_BASE_DOMAIN` templating, and the
`librechat.yaml.template` filtered-envsubst design are all sound and match
their documented intent.

However, two concrete portability defects survive that directly undercut the
phase's own PORT-01/PORT-02 acceptance bar ("operator can point the whole
stack at their own domain/config alone, zero source edit"):

1. `AGENT_MENTION_ALIASES` — the flagship var of plan 14-07 — is wired into
   the `librechat` container's environment but never into `memory-api`'s, so
   the server-side mention detector silently ignores any custom alias an
   operator configures.
2. `infrastructure/nginx/templates/30-projects.conf.template` still hardcodes
   the original maintainer's Firebase project as its fallback redirect
   target, even though its `server_name` was correctly templated.

Both slipped past `verify-phase14.sh` because that gate exercises
`Settings()` and `envsubst` directly (bypassing `docker-compose.yml`) and
scans only for the literal brand token (neither defect contains
`grooveos`/`aibrussels`). Four further warnings and two info-level items are
also reported below — all with a concrete failure scenario.

## Critical Issues

### CR-01: `AGENT_MENTION_ALIASES` never reaches the `memory-api` container

**File:** `infrastructure/docker-compose.yml:451` (only occurrence; missing from the `memory-api` service block at lines 93-213)
**Issue:**
`apps/memory-api/app/services/mention_detector.py:43` builds its match regex
once at import time from `settings.AGENT_MENTION_ALIASES`
(`apps/memory-api/app/config.py:181`, pydantic default `"agent"`). There is
no `env_file:` directive anywhere in `docker-compose.yml`, and the
`memory-api` Docker image does not bundle a `.env` file
(`apps/memory-api/Dockerfile` copies only `app/`, `alembic/`, no dotenv) — so
inside the container, `Settings()` can only see variables explicitly listed
under that service's own `environment:` block. Grepping the whole compose
file confirms `AGENT_MENTION_ALIASES` is set **exactly once**, under the
`librechat` service (for `render-config-entrypoint.sh`'s
`AGENT_MENTION_PRIMARY` derivation) — never under `memory-api`.

Concrete failure: an operator follows `.env.example`'s own documented
example (`AGENT_MENTION_ALIASES=agent,ai,assistant`, or rebrands entirely to
`AGENT_MENTION_ALIASES=ai`) and runs `docker compose up`.
`render-config-entrypoint.sh` correctly renders LibreChat's system prompt to
say "mention @ai". But `memory-api`'s container never receives
`AGENT_MENTION_ALIASES` at all, so `mention_detector.py` keeps using the
hardcoded pydantic default `"agent"`. A user who types `@ai hello` in team
chat — exactly what the rendered prompt told them to do — gets silently
ignored: `detect()` returns `None`, no agent task fires, no error anywhere.
This is precisely the "silently degrades to a default instead of erroring"
failure class, and it defeats the explicit purpose of plan 14-07. It is not
caught by `verify-phase14.sh` (which calls `Settings()` directly, bypassing
`docker-compose.yml` entirely) nor by the Makefile's `env-check`/`preflight`
guards (which only confirm the var is non-empty in the `.env` file, not that
compose forwards it to the right container).

**Fix:**
```yaml
# infrastructure/docker-compose.yml — memory-api service, alongside the other
# Phase 14 vars (near CORS_ALLOWED_ORIGIN_REGEX / OAUTH_ISSUER_URL):
      AGENT_MENTION_ALIASES: ${AGENT_MENTION_ALIASES:-agent}
```
Also worth adding an assertion to `verify-phase14.sh` (or a new test) that
actually renders/reads the compose file's `memory-api` environment block for
this key, since the current gate cannot detect this class of gap by
construction.

### CR-02: `30-projects.conf.template` still hardcodes the maintainer's own Firebase project

**File:** `infrastructure/nginx/templates/30-projects.conf.template:23`
**Issue:**
The vhost's `server_name` was correctly templated
(`projects.${XBRAIN_BASE_DOMAIN}`), but the fallback redirect target was not:
```
location / {
    return 301 https://xbrain-495115.web.app$request_uri;
}
```
`xbrain-495115` is the original maintainer's own GCP/Firebase project ID
(confirmed elsewhere in the repo, e.g. `.github/workflow-templates/deploy-firebase.yml`).
`projects.<domain>` is one of the eight subdomains `.env.example` explicitly
documents as driven by the single `XBRAIN_BASE_DOMAIN` knob ("one var repoints
every nginx vhost (chat./api./mcp./adm./lang./bridge./centrifugo./projects.<domain>)").
No env var can change this redirect target — it is a plain string literal in
a file that Phase 14 otherwise fully templated.

Concrete failure: a self-hoster deploys with `XBRAIN_BASE_DOMAIN=acme.example`
and — per the documented domain contract — expects `projects.acme.example`
to be their own vhost. Any request that reaches this container (before or
without Firebase Hosting DNS in front of it, which the file's own comment
says is a real, expected scenario: "Ce vhost est un fallback redirect si le
DNS est temporairement sur la VM") gets 301-redirected to
`xbrain-495115.web.app` — the original maintainer's live site, not the
operator's own project. This is not caught by `verify-phase14.sh` test (e)
(which only exercises `20-api.conf.template`) nor by the brand-token scan
(the string `xbrain-495115` contains neither `grooveos` nor `aibrussels`).

**Fix:**
```nginx
# Either parameterize it like every other vhost:
location / {
    return 301 https://${PROJECTS_DASHBOARD_FALLBACK_URL}$request_uri;
}
# ...with PROJECTS_DASHBOARD_FALLBACK_URL added to the nginx service's
# NGINX_ENVSUBST_FILTER-matched env (rename to start with XBRAIN_, or widen
# the filter), and documented/defaulted in .env.example.
#
# Or, simpler and safer as a default: 404 instead of redirecting anywhere,
# since there is no neutral default third-party site to fall back to:
location / { return 404; }
```

## Warnings

### WR-01: `_build_mention_regex` has no guard for an empty alias list

**File:** `apps/memory-api/app/services/mention_detector.py:23-37`
**Issue:** If `AGENT_MENTION_ALIASES` ever resolves to an empty string
(reachable today via CR-01's local/non-Docker `.env` path, or any future
deployment mechanism that passes the var through), `aliases` becomes `[]`,
`escaped` becomes `""`, and the compiled pattern degenerates to
`@()(?=$|[\s.,!?;:()\[\]{}'"])` — which matches a **bare `@`** followed by
whitespace/punctuation/end-of-string, with `trigger` = `""`. Verified by
direct execution:
```
pattern.search('hi @ there')  -> matches '@' at offset 3
```
Every ordinary "@ " in a chat message would then spuriously fire an agent
task. This is exactly the failure mode `render-config-entrypoint.sh`
explicitly guards against on the shell side
(`[ -z "$AGENT_MENTION_PRIMARY" ] && AGENT_MENTION_PRIMARY=agent`) — the
Python side has no equivalent fallback.
**Fix:**
```python
def _build_mention_regex(aliases_csv: str) -> re.Pattern[str]:
    aliases = [a.strip() for a in aliases_csv.split(",") if a.strip()]
    if not aliases:
        aliases = ["agent"]
    ...
```

### WR-02: `AGENT_MENTION_PRIMARY` derivation doesn't strip YAML-breaking characters

**File:** `apps/librechat/patches/render-config-entrypoint.sh:34-36`
**Issue:**
```sh
AGENT_MENTION_PRIMARY=$(printf '%s' "${AGENT_MENTION_ALIASES:-agent}" \
  | cut -d, -f1 | tr -d '[:space:]@')
```
`tr -d '[:space:]@'` strips whitespace and `@`, but not a literal double
quote. The result is envsubst'd into
`infrastructure/librechat/librechat.yaml.template` inside a double-quoted
YAML scalar: `promptPrefix: "... (mention @${AGENT_MENTION_PRIMARY}), ..."`.
An operator who sets, e.g., `AGENT_MENTION_ALIASES=my"agent` (or any alias
containing `"`) produces a rendered `/app/librechat.yaml` where the
`promptPrefix` string is truncated mid-sentence by the injected quote,
corrupting the YAML document from that point on — LibreChat then fails to
parse its own generated config (or silently misinterprets everything after
the break) purely from an operator-supplied env value that nothing in the
stack validates or rejects.
**Fix:** Either reject/strip non-`[A-Za-z0-9_-]` characters when deriving
`AGENT_MENTION_PRIMARY`, e.g. `tr -dc 'A-Za-z0-9_-'`, or document/validate
the allowed character set for `AGENT_MENTION_ALIASES` in `.env.example` and
enforce it (a pydantic `field_validator` on the memory-api side would also
close this for the server-side list).

### WR-03: No guardrail against a dangerous `CORS_ALLOWED_ORIGIN_REGEX` value

**File:** `apps/memory-api/app/config.py:154-164`, `apps/memory-api/app/main.py:95-101`
**Issue:** `.env.example:96-98` explicitly warns "Do NOT set this to `.*`",
but nothing enforces it — the `field_validator` only rejects an *empty*
value (`.env.example:98`, `config.py:156-164`), and `Makefile`'s
`env-check`/`preflight-env.sh` only check `has_var` (non-empty), never the
regex's shape. `CORSMiddleware` is configured with `allow_credentials=True`
(`main.py:98`). An operator who hits CORS errors while wiring up a new
frontend and, under time pressure, sets
`CORS_ALLOWED_ORIGIN_REGEX=.*` passes every existing check (`env-check`,
`preflight`, `verify-phase14.sh`) and silently removes the CORS origin
boundary for every credentialed browser request to this API.
**Fix:** Add a lightweight sanity check in the `field_validator` (e.g. reject
a compiled pattern that matches an arbitrary/empty string such as `""` or
`"evil.example"` against a canary, or simply reject the literal `.*`/`.+`
patterns) so the documented "don't do this" becomes enforced, not just
commented.

### WR-04: Public waitlist endpoint injects unescaped user input into an HTML email body

**File:** `apps/memory-api/app/routes/waitlist.py:29-39`
**Issue:** `POST /v1/waitlist` requires no authentication
(`router = APIRouter()`, docstring: "no auth required") and interpolates
`body.name` / `body.email` directly into an HTML string sent to Resend
without any escaping:
```python
"html": (
    f"<p><strong>Name:</strong> {body.name}</p>"
    f"<p><strong>Email:</strong> {body.email}</p>"
    ...
)
```
An anonymous caller can submit `name` containing arbitrary HTML/markup
(e.g. `<a href="http://evil.example">Click here, urgent</a>`), which lands,
rendered, in the inbox configured via `WAITLIST_TO` — a basic HTML/phishing
injection vector against whoever reads the team's waitlist inbox. This file
was touched by Phase 14 (brand-only default changes on lines 15/33), so it
is in the reviewed surface; the vulnerable interpolation itself predates
this phase but remains unaddressed.
**Fix:** HTML-escape user-controlled fields before interpolation, e.g.
`import html; html.escape(body.name)` / `html.escape(body.email)`.

## Info

### IN-01: Stale "3 vars" comment in `render-config-entrypoint.sh`

**File:** `apps/librechat/patches/render-config-entrypoint.sh:1-17`
**Issue:** The header comment says the entrypoint resolves "librechat.yaml's
3 `${VAR}` brand strings" / "ALL THREE of librechat.yaml's brand strings",
but the actual `envsubst` allow-list on line 43 substitutes **four**
variables (`LIBRECHAT_ALLOWED_DOMAINS`, `BRIDGE_BASE_URL`, `APP_TEAMS_URL`,
`AGENT_MENTION_PRIMARY`) — the fourth was added by plan 14-07 without
updating this comment. Not functionally wrong (the allow-list itself is
correct), but a future maintainer auditing "the 3 sites" per this comment
would not think to check the `AGENT_MENTION_PRIMARY` site.
**Fix:** Update the comment to say "4" and list all four vars explicitly.

### IN-02: Entrypoint comment cites a LibreChat version that doesn't match the pinned image

**File:** `apps/librechat/patches/render-config-entrypoint.sh:9`, `apps/librechat/Dockerfile:16`
**Issue:** The comment justifying the entrypoint's design says the analysis
was "Proven by inspecting the shipped LibreChat **v0.8.2-rc2** image source,"
but `apps/librechat/Dockerfile:16` pins `FROM ghcr.io/danny-avila/librechat:v0.8.5`.
If the native `${VAR}` substitution behavior the comment describes differs
between v0.8.2-rc2 and v0.8.5, the documented rationale for which fields
need the entrypoint fallback (vs. which LibreChat resolves natively) may no
longer be accurate for the image actually being built.
**Fix:** Re-verify the claim against the actually-pinned v0.8.5 source, or
correct the version cited in the comment.

---

_Reviewed: 2026-07-12T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
