# Phase 8: Granola OAuth Per-User + Universal Extraction Pipeline + Platform Agents - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-05-08
**Phase:** 8-granola-oauth-per-user-universal-extraction-pipeline-platform-agents
**Areas discussed:** Granola per-user credentials, granola-sync per-user polling, pipeline extraction trigger, platform agents invocation

---

## Zone 1 — Granola per-user credentials

| Option | Description | Selected |
|--------|-------------|----------|
| OAuth PKCE popup | memory-api callback endpoint, token Fernet stocké, redirect frontend | |
| API key manuelle | Champ dans le profil user, clé Fernet chiffrée dans granola_user_connections | ✓ |

**User's choice:** API key manuelle — "en fait on va plutot mettre un field ou le user peut mettre son api key pour commencer"
**Notes:** OAuth PKCE différé à une phase future. Clé manuelle suffit pour Phase 8. Pattern identique aux team API keys existantes.

---

## Zone 2 — granola-sync per-user polling

| Option | Description | Selected |
|--------|-------------|----------|
| granola-sync étendu (2 boucles) | Même service, boucle team + nouvelle boucle per-user | ✓ |
| Nouveau worker dans memory-api | Background asyncio task au startup memory-api | |
| Table unifiée | Refactor de la table granola_integrations pour couvrir team + user | |

**User's choice:** Option 1 — granola-sync étendu
**Notes:** Moins de containers, moins de surface. La boucle per-user suit le même pattern que la boucle team.

---

## Zone 3 — Pipeline extraction universel : point de déclenchement

| Option | Description | Selected |
|--------|-------------|----------|
| Tout dans librechat-bridge | Bridge gère LibreChat + Chrome ext via queue/webhook | |
| Tout dans memory-api | Extraction dans les handlers POST (fire-and-forget) | |
| Split par source | LibreChat → bridge, Chrome ext → memory-api | ✓ |

**User's choice:** Option 3 (sur recommandation Claude)
**Notes:** User a demandé "qu'est ce qui est le mieux ?" — Claude a recommandé option 3 (split par source, chaque source gérée au plus près de son point d'entrée). User a validé.

---

## Zone 4 — Platform agents : invocation

| Option | Description | Selected |
|--------|-------------|----------|
| Claude direct (memory-api) | Appel Anthropic API direct, synchrone, simple | ✓ |
| LangGraph (agent-runtime) | Job async, orchestration, tool calls MCP réels | |
| Split selon tools_json | Claude direct si pas de tools, LangGraph sinon | |

**User's choice:** Option 1 (sur recommandation Claude)
**Notes:** User a demandé "lequel tu choisirais pourquoi ?" — Claude a recommandé option 1 (YAGNI, meeting-recap est single-shot, LangGraph overkill pour Phase 8). User a demandé si "ça sera fait automatiquement quand même" → confirmé que l'automatisme est orthogonal au mécanisme d'invocation.

**Auto-trigger** : `auto_trigger: true` par défaut sur agent_definitions. "Set as an option, by default automatic."

---

## Claude's Discretion

Aucune zone laissée à la discrétion de Claude — toutes les décisions ont été confirmées par le user (parfois après recommandation explicite demandée par le user).

## Deferred Ideas

- OAuth PKCE Granola (connexion self-service popup) — Phase 9+
- Tool calls MCP réels depuis agents (`tools_json` exécuté) — Phase 9+ avec agent-runtime LangGraph
- Scoring automatique contacts (lead scoring) — reporté depuis Phase 7
- Sync tâches vers Linear/Notion/Jira — reporté depuis Phase 7
