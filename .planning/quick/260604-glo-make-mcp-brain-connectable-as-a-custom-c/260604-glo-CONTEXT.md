# Quick Task 260604-glo: Make mcp-brain connectable as a Custom Connector in the official Claude.ai app — Context

**Gathered:** 2026-06-04
**Status:** Ready for planning

<domain>
## Task Boundary

Make the existing remote MCP server (`apps/mcp-brain`) connectable as a **Custom Connector** in the **official Claude.ai app** (web + desktop), so a team member can use their team brain directly from Claude.ai.

Existing state:
- `apps/mcp-brain` — FastMCP, `transport="streamable-http"`, exposed at `https://mcp.example.com` (nginx `infrastructure/nginx/conf.d/40-mcp.conf`).
- Current auth: `Authorization: Bearer xbt_…` (validated via memory-api `GET /v1/me`) + an internal `X-Internal-Secret` path.
- Tools: memory_search, memory_add, tasks_list/create/update, contacts_search/add, agent_invoke, team_context.

The GAP: Claude.ai's official custom-connector flow uses the MCP **Authorization** spec (OAuth 2.1 — `/.well-known/oauth-protected-resource` + authorization-server metadata, dynamic client registration, `401 + WWW-Authenticate` discovery, PKCE). It does **not** accept a pasted static bearer token.

**This is a PLAN-ONLY task.** Produce a reviewable plan; the user approves before any execution.
</domain>

<decisions>
## Implementation Decisions

### Connector scope (v1)
- **Read + write (full toolset).** Expose the full mcp-brain toolset (memory_search, memory_add, tasks_*, contacts_*, agent_invoke, team_context) to the Claude.ai connector — NOT read-only.
- Implication: external write surface. Plan MUST include guardrails (e.g. writes tagged with provenance `source=claude.ai-connector`, truth_level capped at WORKING/EPHEMERAL for connector-originated writes, scoped strictly to the bound team). Surface these guardrails explicitly.

### Team selection (multi-team users)
- **One team per connection.** The user selects their team on the OAuth consent screen; the issued access token is bound to that single `team_scope`. To use another team, the user adds a second connector connection. No cross-team switching inside one token — strict isolation.

### OAuth Authorization Server placement
- **memory-api is the OAuth 2.1 Authorization Server.** memory-api hosts `/authorize`, `/token`, dynamic client registration, and the AS metadata; it already owns identity (GitHub-primary) + `xbt_` token issuance. `mcp-brain` becomes the **Protected Resource**: it advertises `/.well-known/oauth-protected-resource`, returns `401 + WWW-Authenticate` pointing at memory-api's AS metadata, and validates the OAuth access token on each MCP request.
- Reuse the existing **GitHub** identity for end-user login during the `/authorize` flow.

### Claude's Discretion
- Exact token format/validation (reuse `xbt_` semantics vs a dedicated OAuth access-token table), PKCE/DCR storage schema, and consent-screen UX are left to research + planner, consistent with the decisions above and the OSS/self-hostable constraint.
</decisions>

<specifics>
## Specific Ideas

- Keep everything OSS + self-hostable — no proprietary auth service (no Auth0/Clerk/etc. in the critical path).
- Honor the project tagging contract on any connector-originated write.
- The connect target the user pastes into Claude.ai is `https://mcp.example.com/mcp` (confirm exact path during research).
</specifics>

<canonical_refs>
## Canonical References

- MCP Authorization specification (OAuth 2.1 / protected-resource metadata / DCR / PKCE).
- Anthropic / Claude.ai "Custom Connectors" (remote MCP) documentation — current requirements.
- Existing code: `apps/mcp-brain/app/main.py` (auth + tools), `apps/memory-api` (identity, `GET /v1/me`, `xbt_` issuance, GitHub OAuth), `infrastructure/nginx/conf.d/40-mcp.conf`.
</canonical_refs>
