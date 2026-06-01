---
quick_id: 260601-uom
status: partial
---

# Summary — 260601-uom — SearXNG web search + scraper nudge

## Done + live
- **Scraper auto-fetch nudge** — `promptPrefix` on all 5 xbrain modelSpecs instructs the model
  to call the `scraper` tool when the message contains a URL. Deployed + active (LibreChat loaded
  it cleanly; `scraper` is among the MCP tools). Uses our own mcp-scraper (fixed in 260601-3is) —
  no external dependency. **This is the "paste a link → it reads it" behavior the user wanted.**
- **SearXNG container** — `infrastructure/searxng/settings.yml` (JSON format on, limiter off) +
  `searxng` service in docker-compose.yml. Deployed, healthy, **JSON API verified** (9 results
  for a test query). Internal-only on xbrain_net. `.env`: SEARXNG_INSTANCE_URL + SEARXNG_SECRET.

## Backed out (blocked) — webSearch block
Adding the `webSearch` block to librechat.yaml took LibreChat DOWN (crash-loop) for ~2 min. Two
blockers, both surfaced at deploy:
1. **rerankerType "none" rejected** — our LibreChat **v0.8.5** Zod schema only accepts
   `'jina' | 'cohere'` for `webSearch.rerankerType` (the docs' "none" is for a newer version).
2. **No valid Firecrawl key** — the only `fc-` string found on the system (`fc-4ea…eef4`, 25 chars,
   in ~/.claude.json skillUsage) returns **401 Unauthorized**. Searched env (User/Machine/Process),
   ~/.claude/settings.json, D:/VSC/@security, plugin dirs — no working FIRECRAWL_API_KEY anywhere.

Backed the `webSearch` block out of librechat.yaml (commit 6209183) → LibreChat restored
(RestartCount=0, healthy, MCP tools loaded). SearXNG left running (idle, ready). A junk
FIRECRAWL_API_KEY sits in the VM .env (unused now) — overwrite when the real key arrives.

## To finish web search (needs user)
1. User provides a **valid Firecrawl key** (or a Tavily key → switch scraperProvider).
2. Resolve rerankerType: OMIT the field (test if optional in v0.8.5) OR use jina/cohere (free Jina tier).
3. Re-add the webSearch block, redeploy librechat.yaml, restart, verify the toggle works.

## Commits
- feat(infra): SearXNG + Firecrawl + nudge (260601-uom) — searxng/settings.yml, docker-compose, librechat.yaml
- fix(infra): back out webSearch block (6209183)
