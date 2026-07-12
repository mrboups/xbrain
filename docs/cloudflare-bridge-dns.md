# Cloudflare DNS runbook — `bridge.example.com` (Phase 9)

This runbook covers the manual Cloudflare setup required for the Phase 9
session-bridge (`apps/session-bridge`). Claude Code cannot drive the Cloudflare
dashboard without an API token, so this step is done by a human operator once
per environment.

## What this enables

- `https://bridge.example.com/v1/...` — OpenAI-compatible chat completions, called
  by LibreChat's "Claude (mon abonnement)" custom endpoint.
- `wss://bridge.example.com/ws/{user_sub}` — persistent WebSocket opened by the
  xbrain Chrome extension's service worker.

## Prerequisites

- Cloudflare zone for `example.com` already exists (set up in Phase 7 domain
  migration — see `memory/project_xbrain_domain_migration.md`).
- VM origin IP is `__VM_HOST__` (`gcloud compute instances list` to reconfirm
  if the VM was ever recreated).
- nginx vhost `infrastructure/nginx/conf.d/50-bridge.conf` is deployed on the VM
  (this happens automatically with the normal `docker compose up -d nginx` /
  reload procedure used for the other vhosts).

## Step 1 — Add the DNS A record

1. Open https://dash.cloudflare.com and select the `example.com` zone.
2. **DNS → Records → Add record**:
   - Type: **A**
   - Name: `bridge` (will become `bridge.example.com`)
   - IPv4 address: `__VM_HOST__`
   - Proxy status: **Proxied** (orange cloud — NOT DNS-only)
   - TTL: Auto
3. Save.

## Step 2 — Confirm WebSockets are enabled site-wide

Cloudflare proxies WebSockets only when the site-level toggle is on. It usually
already is for `example.com` (LibreChat needs it too), but verify:

1. In the same zone, go to **Network**.
2. Confirm **WebSockets** is **ON**.

## Step 3 — Verify DNS propagation

From any laptop (NOT from the VM — Cloudflare hides the origin):

```bash
nslookup bridge.example.com
# Expected: resolves to a Cloudflare anycast IP (e.g. 104.21.x.x or 172.67.x.x),
# NOT to __VM_HOST__. If it resolves to the VM IP, the proxy is off.
```

## Step 4 — Verify the vhost answers (after nginx reload on VM)

```bash
curl -fsS https://bridge.example.com/nginx-health
# Expected: ok
```

If this returns a Cloudflare error page (525, 526, 1016, ...), check:

- nginx logs on the VM: `docker logs xbrain-nginx-1 --tail 100`
- That the Cloudflare zone's SSL/TLS mode is **Full** (not "Full (strict)" unless
  the origin certificate is set up — Phase 1 used Full).
- That the firewall on the GCP VM allows Cloudflare IP ranges on 80/443 (this
  was configured in Phase 1 and should still be in place).

## Step 5 — Record completion

After completing steps 1–4, append a line below for traceability:

```
bridge.example.com A record created: YYYY-MM-DD by <operator name>
WebSockets toggle confirmed ON:      YYYY-MM-DD by <operator name>
```

## Recovery if the zone or VM moves

- Re-do step 1 with the new VM IP if the VM was rebuilt.
- Re-do step 2 only if the zone was re-created from scratch (rare).
- The vhost config `infrastructure/nginx/conf.d/50-bridge.conf` does not need
  changes when the IP moves — it talks to `session-bridge` via the internal
  Docker network.
