# Phase 15 — Deferred Items

Out-of-scope discoveries logged during execution. NOT fixed here (Rule: only auto-fix
issues directly caused by the current task's changes; pre-existing issues in unrelated
files are out of scope).

## nginx Docker healthcheck 302-redirects to HTTPS on a loopback probe (pre-existing, non-Phase-15)

**Found during:** 15-04 Task 2, check (f) live boot.

**What:** `xbrain-nginx`'s own Docker healthcheck is
`wget -q -O - http://127.0.0.1/nginx-health` (docker-compose.yml:58), which sends
`Host: 127.0.0.1`. That Host matches none of the named vhosts, so nginx routes it to the
catch-all `default_server` block (`infrastructure/nginx/templates/10-xbrain.conf.template`,
the `listen 80 default_server` server), which unconditionally
`return 302 https://chat.${XBRAIN_BASE_DOMAIN}$request_uri`. nginx never listens on 443 in
this compose file (TLS terminates externally — e.g. Cloudflare — which loops back to this
origin on port 80 in production), so the redirect target is unreachable from inside the
container or from any isolated environment. Result: `.State.Health.Status` for
`xbrain-nginx` never leaves `starting`/`unhealthy` in a local/CI boot, even though nginx is
serving its real vhosts correctly.

**Why it is NOT a Phase 15 regression:** No Phase 15 plan touches `infrastructure/nginx/`
(confirmed: 15-01/15-03 edit docker-compose.yml + .env.example only; nginx templates
untouched). This behavior predates the phase. It is structurally impossible to satisfy in
any isolated environment regardless of `XBRAIN_BASE_DOMAIN`'s value.

**Why NOT fixed here:** `infrastructure/nginx/` is out of 15-04's declared file scope, and
the plan's `<the_whole_point_of_this_plan>` explicitly forbids rewriting these templates
(they are correct — the resolver-based lazy DNS is *why* nginx can start with absent
upstreams). `verify-phase15.sh` check (f) works around it honestly: it probes a REAL named
vhost with its own `Host:` header (bypassing the catch-all, exactly as a real client via
Cloudflare arrives), which proves ingress resilience — SC#4's actual claim — without
depending on the broken loopback healthcheck. The Docker health status is reported as an
informational NOTE, never a FAIL.

**Suggested fix (future, out of scope):** either (a) give the Docker healthcheck an explicit
`--header="Host: chat.<domain>"`, or (b) add a dedicated `Host: 127.0.0.1` /
`Host: localhost` health server block that returns 200 without redirecting, or (c) make the
`default_server` catch-all serve `/nginx-health` locally before the 302. Any of these is a
one-line nginx template change but belongs to whoever owns the ingress templates, not this
acceptance-gate plan.
