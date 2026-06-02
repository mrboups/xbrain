---
quick_id: 260603-1vr
status: complete
---

# Summary — 260603-1vr — Gateway unwraps aggregate kwargs envelope

**Commit:** (fix) apps/mcp-gateway/app/main.py — unwrap `{"kwargs": {...}}` one level in call_tool.

## Bug (user screenshot)
LibreChat model called the `scraper` tool (web-search nudge) and got repeated
`scrapeArguments: url Field required [input_value={"kwargs": {"url": "..."}}]`. Args were
double-wrapped under `kwargs`. Root cause: the mcp-gateway rebuild for 260601-3is pulled a newer
FastMCP that exposes the aggregate proxy tools (signature `async def _proxy(**kwargs)`) with a
single generic `kwargs` object param, so the model is forced to send `{"kwargs": {<real args>}}`.
The gateway forwarded it verbatim → sidecar `scrape(url)` never saw `url`.

## Fix
In `call_tool`, after extracting `mcp_arguments`, strip exactly one `kwargs` level when the dict
is `{"kwargs": {...}}`. Safe: direct callers (agent-runtime) never wrap in kwargs; no sidecar
tool has a sole `kwargs` param. Verified live: POST /tools/scraper/call with
`arguments={kwargs:{url:example.com}}` → isError=False + real HTML.

## Follow-up (deeper, optional)
Proper fix = have the aggregate expose each sidecar tool with its REAL inputSchema (so the model
sends `url` directly, no kwargs envelope). Bigger aggregate.py change; the unwrap covers it for now.
