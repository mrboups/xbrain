---
quick_id: 260601-uom
slug: searxng-web-search-scraper-auto-fetch-nu
mode: quick
status: planned
---

# Quick Task 260601-uom — SearXNG web search + scraper auto-fetch nudge

## Goal
Make LibreChat behave more like claude.ai for the web: (1) auto-read pasted URLs via our
existing `scraper` MCP tool (always-on, no toggle), and (2) keyword web search via SearXNG
(self-hosted, OSS) + Firecrawl (scraper for full page content). Decided 2026-06-01; Firecrawl
key sourced from the user's Firecrawl plugin config.

## Parts

### A — SearXNG container (self-hosted search)
- `infrastructure/searxng/settings.yml` (new) — `use_default_settings: true`, `search.formats: [html, json]` (JSON required by LibreChat), `limiter: false` (internal-only), secret via $SEARXNG_SECRET.
- `infrastructure/docker-compose.yml` — new `searxng` service (image `searxng/searxng:latest`, mount `./searxng:/etc/searxng`, env SEARXNG_BASE_URL + SEARXNG_SECRET, internal-only `expose: 8080`, mem_limit 384m, healthcheck).

### B — LibreChat webSearch + scraper nudge
- `infrastructure/librechat/librechat.yaml`:
  - top-level `webSearch` block: searchProvider=searxng (`${SEARXNG_INSTANCE_URL}`), scraperProvider=firecrawl (`${FIRECRAWL_API_KEY}`), rerankerType=none, scraperTimeout 10000, safeSearch 1.
  - scraper auto-fetch nudge: add `promptPrefix` to each xbrain modelSpec preset instructing the model to call the `scraper` tool whenever the message contains a URL.

### C — VM .env + deploy
- VM `.env`: `SEARXNG_INSTANCE_URL=http://searxng:8080`, `FIRECRAWL_API_KEY=<from plugin>`, `SEARXNG_SECRET=<random>`.
- Deploy: scp settings.yml + docker-compose.yml + librechat.yaml; `docker compose up -d searxng`; restart librechat.
- Verify: SearXNG JSON API (`/search?q=test&format=json` returns JSON), LibreChat web search toggle returns results, scraper nudge fires on a pasted URL.

## Constraints
- Web search is AUXILIARY (not the critical memory path) → a Firecrawl cloud key is acceptable per the OSS-in-critical-path rule. SearXNG keeps search self-hosted.
- Do NOT commit secrets (SEARXNG_SECRET + FIRECRAWL_API_KEY live in VM .env only).
- RAM-conscious VM — SearXNG ~150-200MB, cap at 384m, limiter off (no redis needed).
