---
phase: 06-marketing-docs
plan: "05"
subsystem: marketing-site
tags: [docs, drive-sync, chrome-extension, github-auth, html]
dependency_graph:
  requires: ["06-01"]
  provides: ["drive-sync.html", "chrome-ext.html", "github-auth.html"]
  affects: ["marketing-site/docs/"]
tech_stack:
  added: []
  patterns: ["docs-layout with 14-link sidebar", "breadcrumb", "callout--info/warning", "docs-table", "code-block"]
key_files:
  created:
    - marketing-site/docs/drive-sync.html
    - marketing-site/docs/chrome-ext.html
    - marketing-site/docs/github-auth.html
  modified: []
decisions:
  - "Real manifest.json content used in chrome-ext.html (manifest_version 3, permissions identity/activeTab/storage, oauth2 client_id, host_permissions)"
  - "chrome-ext.html uses &lt;all_urls&gt; HTML entity for content_scripts matches to avoid browser parsing issues in pre block"
  - "github-auth.html documents three-state github_is_org_member (None vs False) as a security-critical distinction"
metrics:
  duration: "12 minutes"
  completed: "2026-05-06"
  tasks_completed: 3
  files_created: 3
---

# Phase 06 Plan 05: Drive Sync + Chrome Extension + GitHub Auth Docs — Summary

Three external-integration documentation pages covering Google Drive sync (push webhooks + polling fallback), Chrome Extension MV3 web clipper, and GitHub OAuth with Org membership verification.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Drive Sync docs | be18c34 | marketing-site/docs/drive-sync.html |
| 2 | Chrome Extension docs | be18c34 | marketing-site/docs/chrome-ext.html |
| 3 | GitHub Auth docs | be18c34 | marketing-site/docs/github-auth.html |

## What Was Built

**drive-sync.html** (350 lines): Full documentation of the drive-sync service including push webhook flow (`POST /v1/drive/webhook`), polling fallback (5-minute incremental), 3-step setup (OAuth → map folder via `POST /v1/admin/drive/mappings` → auto webhook registration), multi-folder mapping with distinct `project_scope` per folder, Fernet encryption callout for credentials, and a supported file types table (Google Docs/Sheets/PDF/Markdown/txt — images skipped).

**chrome-ext.html** (367 lines): Complete MV3 web clipper documentation including developer mode installation steps, real `manifest.json` content (manifest_version 3, permissions: identity/activeTab/storage, background service worker, oauth2 with Google scopes, host_permissions), 4-step usage flow, architecture diagram showing content.js → popup.html → background.js → `launchWebAuthFlow` → `POST memory-api`, CORS `chrome-extension://*` regex config in memory-api, and configuration table for custom deployments.

**github-auth.html** (365 lines): GitHub OAuth documentation covering Google vs GitHub auth comparison table, 3-step OAuth App setup, org membership verification flow (gho_ prefix detection → GitHub API PAT check → allow/403), migration 0007 schema (github_username + github_id columns), three-state `github_is_org_member` logic (None vs False), `source_user_id = "github:{login}"` identity format, account linking limitation, and security notes.

## Deviations from Plan

None — plan executed exactly as written.

The only minor implementation choice: `<all_urls>` in the manifest JSON code block was written as `&lt;all_urls&gt;` to prevent browser parsing issues inside a `<pre>` element. This is correct HTML and renders identically in-browser.

## Threat Flags

None — documentation pages only. No new network endpoints or auth paths introduced. The Fernet encryption callout (T-06-05-03) and github_is_org_member=None distinction (T-06-05-02) from the threat model are both documented explicitly in the respective pages.

## Self-Check: PASSED

- `marketing-site/docs/drive-sync.html` exists: FOUND (350 lines)
- `marketing-site/docs/chrome-ext.html` exists: FOUND (367 lines)
- `marketing-site/docs/github-auth.html` exists: FOUND (365 lines)
- Commit `be18c34` exists: FOUND
- All acceptance criteria grep checks: PASSED (12/12)
