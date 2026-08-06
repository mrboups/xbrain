---
quick_id: 260806-5zq
status: complete
date: 2026-08-06
commits:
  - 1bdd2ae  fix(scraper): return what a person would read, not the page's markup
  - 6ea4709  fix(scraper): bound mcp below 2.0, which removed mcp.server.fastmcp
  - 41e3adf  fix(scraper): put the page's content first, not the site's navigation
  - 653401a  fix(mcp): bound mcp below 2.0 in the four sidecars that had no ceiling
---

# Quick Task 260806-5zq — SUMMARY

## What was wrong

`mcp-scraper` returned `r.text[:50_000]` — the raw body. The caller keeps the
first 6000 characters, so the model was handed markup. On
`pitch.digitalaf.xyz`, those 6000 characters carried **306 characters of
readable text**, and the page's dates sat at raw offset ~18,300.

It went unnoticed because the turn that exposed it answered *correctly*: on the
Pro/Max path the request reaches `claude.ai/api/organizations/.../completion`,
which fetched the page itself. That fallback does not exist on the
team-API-key path or in the OSS edition.

Second defect: `httpx` announced `python-httpx/x.y`, so Wikipedia returned 403.

## What changed

`apps/mcp-scraper/app/main.py` — stdlib `html.parser`, no new dependency (the
sidecar runs under `mem_limit: 128m` and builds on the VM, so a native-extension
wheel is a deploy risk with nothing to buy it):

1. **Extract text.** `script`/`style`/`noscript`/`svg`/`head`/`template`/
   `iframe`/`canvas` dropped, block tags become line breaks, entities unescaped,
   `<title>` re-attached.
2. **Browser `User-Agent`** (plus `Accept`, `Accept-Language`).
3. **Two separate bounds.** Parse up to 1MB of raw body, return at most 50KB of
   text. They used to be the same number, which truncated big pages before
   their content began.
4. **Select rather than truncate.** A `<main>`/`<article>` subtree with real
   text is returned alone; `<nav>` and `<aside>` are dropped. `<footer>` is
   kept on purpose — event pages put dates and addresses there.
5. **Fail-soft everywhere.** Non-HTML bodies returned verbatim (a JSON response
   must survive), parse errors and empty extractions fall back to the raw body.

`apps/mcp-scraper/tests/test_extract_text.py` — 27 tests on properties, not
wording.

## The incident this caused, and the latent one it exposed

The first rebuild crash-looped the sidecar: `ModuleNotFoundError: No module
named 'mcp.server.fastmcp'`. `pyproject.toml` declared `mcp>=1.27.0` with no
ceiling; the image had run 12 days on a 1.x resolved at its last build, and the
rebuild pulled **mcp 2.0.0**, which removed that module. Every scrape returned
nothing until `<2.0.0` was added (6ea4709). Now on mcp 1.29.0, healthy.

**mcp-brain, mcp-calendar, mcp-deck, mcp-drive-read and mcp-gateway carried the
same unbounded specifier** — each one rebuild away from the same outage, for any
unrelated reason. Pinned in 653401a. They keep running their current images; the
pin only constrains a future build to what they already run.

## Verification

Local: `pytest apps/mcp-scraper/tests/ -q` → **27 passed**.

Live, through the production `memory-api` container calling
`_fetch_url_via_scraper` (the exact path the agent uses):

| URL | Before | After |
|---|---|---|
| `pitch.digitalaf.xyz` | 6000 chars, **306 readable**, `september` absent | 6000 chars, **0 HTML tags**, `september` at offset **1,992** |
| `en.wikipedia.org/wiki/Berlin` | **403 → nothing** | 200, 6000 chars, 0 tags |
| `example.com` | 559 chars of raw HTML | 129 chars of text |

## Known limitation (not fixed)

Wikipedia still opens with its 268-language list: that chrome lives in a plain
`<div>` inside `<main>`, so neither the `nav`/`aside` rule nor `<main>`
selection removes it. Reaching the prose would need text-density scoring
(what trafilatura does) — a real change, not a tweak, and out of scope here.
Ordinary article, product and event pages are unaffected.

## Deliberately not done

- **Raising the caller's 6000-char cap** in `team_chat_agent.py`. With
  extraction it is no longer the binding constraint.
- **Telling the model when a fetch returned almost no text.** The
  `NO_WEB_CONTENT_MARKER` covers "nothing fetched", not "fetched and it was
  boilerplate" — which is exactly how this defect stayed invisible. That is a
  `memory-api` prompt change; it belongs to its own task.
