---
phase: 06-marketing-docs
plan: "07"
subsystem: marketing-site/docs
tags: [documentation, deployment, configuration, ops]
dependency_graph:
  requires: [06-01]
  provides: [deployment.html, configuration.html]
  affects: [marketing-site/docs]
tech_stack:
  added: []
  patterns: [HTML docs template, docs-table, callout, code-block, sidebar 14 links]
key_files:
  created:
    - marketing-site/docs/deployment.html
    - marketing-site/docs/configuration.html
  modified: []
decisions:
  - Added Troubleshooting section to deployment.html (common OOM, OAuth, bridge secret issues — fills gap not in plan but critical for ops teams)
  - Added Upgrading section to deployment.html (git pull + docker compose build + up -d workflow)
  - Fernet key generation uses python3 not python (Ubuntu 24.04 has no python symlink)
metrics:
  duration: 8min
  completed: 2026-05-06
---

# Phase 06 Plan 07: Deployment Guide + Configuration Reference Summary

One-liner: Complete ops documentation — Phase 1-5 deployment guide with GCP gcloud commands and full environment variable reference covering 30+ vars across 8 service groups.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | deployment.html — Complete deployment guide | d968bd4 | marketing-site/docs/deployment.html (578 lines) |
| 2 | configuration.html — Environment variable reference | ac1a788 | marketing-site/docs/configuration.html (684 lines) |

## Artifacts

### deployment.html (578 lines)

Sections: Prerequisites table, VM sizing callout, Phase 1 (6 numbered steps: gcloud VM create, Docker install, clone+configure, docker compose up -d, nginx, verify-phase1.sh), Phase 2 (VM resize to e2-standard-2, Langfuse keys), Phase 3 (Neo4j, OAuth encryption key, register-mcp-tools.sh), Phase 4 (no new vars, verify-phase4.sh 8/8), Phase 5 (GITHUB_ORG/GITHUB_API_PAT/GRAPHITI_SERVICE_URL, verify-phase5.sh 8/8), HTTPS with Cloudflare (nginx config example), Backups (backup.sh/restore.sh + cron), Monitoring (docker stats, df -h, docker system prune), Upgrading, Troubleshooting.

### configuration.html (684 lines)

Variable tables by service group:
- Core Database: 4 vars (POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB, DATABASE_URL)
- memory-api: 16 vars (QDRANT_URL, QDRANT_API_KEY, BRIDGE_SHARED_SECRET, JWT_ALGORITHM, LOG_LEVEL, ADMIN_USER_SUBS, MEMORY_API_EXTERNAL_URL, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, OAUTH_CREDENTIALS_ENCRYPTION_KEY, NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, GRAPHITI_SERVICE_URL, GITHUB_ORG, GITHUB_API_PAT)
- LibreChat: 5 vars (ANTHROPIC_API_KEY, OPENAI_API_KEY, XAI_API_KEY, MEILI_MASTER_KEY, LIBRECHAT_MONGO_URI)
- Bridge & Pipeline: 6 vars (MEMORY_API_URL, BRIDGE_DEFAULT_TEAM_SCOPE, BRIDGE_BACKFILL_FROM, PIPELINE_API_KEY, PIPELINE_DEFAULT_TEAM_SCOPE, AGENT_RUNTIME_URL)
- agent-runtime: 2 vars (MCP_GATEWAY_URL, MCP_TOOL_CACHE_TTL_SECS)
- Langfuse: 2 vars (LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY)
- Graphiti: 2 vars (GRAPHITI_SERVICE_URL, SEMAPHORE_LIMIT)

Total: 37 variables documented. Secret generation commands (openssl rand -hex 32, Fernet.generate_key). Complete annotated .env.example block. Two security callouts: never commit .env, BRIDGE_SHARED_SECRET rotation procedure.

## Deviations from Plan

### Auto-added Content (Rule 2)

**1. [Rule 2 - Missing functionality] Added Troubleshooting section to deployment.html**
- Found during: Task 1 authoring
- Issue: Ops teams deploying xbrain on a fresh VM will hit predictable failures (OOM, OAuth callback mismatch, bridge secret mismatch) that are not addressed in the plan
- Fix: Added Troubleshooting section covering the 4 most common failure modes
- Files modified: marketing-site/docs/deployment.html

**2. [Rule 2 - Missing functionality] Added Upgrading section to deployment.html**
- Found during: Task 1 authoring
- Issue: No guidance for applying updates after initial deployment — critical for an ops guide
- Fix: Added Upgrading section with backup-first workflow, git pull, docker compose pull/build/up -d
- Files modified: marketing-site/docs/deployment.html

**3. [Rule 1 - Bug] python3 instead of python in Fernet command**
- Found during: Task 1
- Issue: Plan uses `python` which has no symlink on Ubuntu 24.04 LTS — command would fail
- Fix: Changed to `python3` in both deployment.html and configuration.html
- Files modified: both

## Known Stubs

None. Both pages contain complete real content with no placeholder text, mock data, or TODO items. All CHANGE_ME values are intentional placeholders in the .env.example code block.

## Threat Flags

None. All code blocks show template commands with no real secrets. The .env.example block uses CHANGE_ME placeholders throughout. Security callouts explicitly warn users not to commit .env.

## Self-Check: PASSED

- marketing-site/docs/deployment.html: EXISTS, 578 lines (>200 required), all 6 acceptance criteria PASS
- marketing-site/docs/configuration.html: EXISTS, 684 lines (>150 required), all 7 acceptance criteria PASS
- Commit d968bd4: FOUND
- Commit ac1a788: FOUND
