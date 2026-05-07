# Team Onboarding Modal — Design Spec
**Date:** 2026-05-07  
**Status:** Approved  
**Phase:** 8

---

## Overview

First-login (and team-less existing users) see a blocking modal inside LibreChat that guides them through joining or creating a team. The modal is injected via a minimal patch to LibreChat's `Root.tsx` and a custom Docker image that also permanently bakes in the `socialLogin.js` OAuth linking patch.

**Goal:** Every user that reaches LibreChat has a team assigned before they can chat. No orphan users.

---

## Trigger Logic

- On app mount, `useOnboarding` hook calls `GET /v1/teams/my-team`
- `204 No Content` → modal appears, **non-closable** (blocks the UI)
- `200 OK` → modal never mounts, normal app flow
- Applies to **all** users — new logins AND existing users without a team (they see it on their next visit)

---

## Wizard Flow

### Step 1 — GitHub or team name

**If user has GitHub linked:**
- Fetch their GitHub orgs via `GET /v1/teams/github-matches`
- Show dropdown of matched xbrain teams + "Create new team" option

**If user has no GitHub:**
- Text input: "Enter your team name" → searches `GET /v1/teams/search?name=xxx`
- Secondary option: "Connect GitHub to list your teams" (OAuth redirect, returns to modal)

---

### Step 2 — Join or Create

**Join existing team:**
- Open team → "Join [Team X]" button → immediate membership
- Closed team → "Request access" button → pending state, modal closes with "Request sent" message

**Create new team:**
- Fields: Name (display), Slug (auto-generated, editable), Description (optional), GitHub org to link (optional), Visibility (`open` | `closed`, default `closed`)
- User becomes founder with `admin` role

---

### Step 3 — API Keys (team defaults, optional)

Dynamic list: one row = one model + one key. Rows can be added with `+` and removed with `✕`.

```
[Anthropic Claude  ▾]  [sk-ant-••••••••]  ✕
[OpenAI GPT        ▾]  [sk-••••••••••••]  ✕
[+ Ajouter une clé]
```

- Provider dropdown: Anthropic, OpenAI, xAI/Grok, Google Gemini (matches LibreChat-supported providers)
- Keys encrypted at rest with Fernet (same key as `OAUTH_CREDENTIALS_ENCRYPTION_KEY`)
- Stored in new table `team_api_keys(team_id, provider, key_enc)`
- Members can override with their own keys in settings (future)
- Entire step is skippable via "Passer →"

---

### Step 4 — Confirmation

"Bienvenue dans [Team X]" + team avatar/icon if available.  
CTA: "Commencer →" — dismisses modal, normal app flow resumes.

---

## Backend — New Endpoints (memory-api)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/v1/teams/my-team` | Returns current user's team or `204` |
| `GET` | `/v1/teams/search?name=xxx` | Search teams by slug or display_name |
| `GET` | `/v1/teams/github-matches` | Match user's GitHub orgs to xbrain teams |
| `POST` | `/v1/teams` | Create team — caller becomes admin founder |
| `POST` | `/v1/teams/{id}/join` | Join open team directly |
| `POST` | `/v1/teams/{id}/join-request` | Request access to closed team |
| `GET` | `/v1/teams/{id}/api-keys` | List team API key providers (no secrets returned) |
| `PUT` | `/v1/teams/{id}/api-keys` | Upsert team API keys (encrypted) |

Auth: all endpoints require valid JWT (same as existing memory-api pattern).

---

## Database Changes

### `teams` table — add 2 columns

```sql
ALTER TABLE teams ADD COLUMN visibility TEXT NOT NULL DEFAULT 'closed'
  CHECK (visibility IN ('open', 'closed'));
ALTER TABLE teams ADD COLUMN github_org TEXT;
```

### New table `team_api_keys`

```sql
CREATE TABLE team_api_keys (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  team_id     UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  provider    TEXT NOT NULL,           -- 'anthropic' | 'openai' | 'xai' | 'google'
  key_enc     TEXT NOT NULL,           -- Fernet-encrypted
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (team_id, provider)
);
```

### New table `team_join_requests`

```sql
CREATE TABLE team_join_requests (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  team_id     UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'approved', 'rejected')),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (team_id, user_id)
);
```

---

## Frontend — Files to Create/Patch

| File | Type | Description |
|------|------|-------------|
| `apps/librechat/patches/useOnboarding.ts` | New | Hook — calls `/v1/teams/my-team`, returns `{ needsOnboarding, team }` |
| `apps/librechat/patches/OnboardingModal.tsx` | New | 4-step modal, Radix UI Dialog (same design system as LibreChat) |
| `apps/librechat/patches/OnboardingModal.css` | New | Scoped styles — minimal, follows LibreChat CSS variables |
| `apps/librechat/patches/Root.patch` | New | Minimal unified diff on `client/src/routes/Root.tsx` to mount `<OnboardingGate>` |
| `apps/librechat/Dockerfile` | New | Custom image — bakes Root.patch + socialLogin.js patch at build time |

### Root.tsx patch (conceptual)

```tsx
// After existing auth check, before rendering children:
import { OnboardingGate } from '../patches/OnboardingGate';
// ...
return (
  <>
    <OnboardingGate />
    {children}
  </>
);
```

`OnboardingGate` renders nothing when team exists, renders `<OnboardingModal>` (non-closable Dialog) otherwise.

---

## Docker Image Strategy

`apps/librechat/Dockerfile` extends `librechat/librechat:v0.8.2-rc2`:

```dockerfile
FROM librechat/librechat:v0.8.2-rc2

# Bake socialLogin.js patch (permanent fix for cross-provider OAuth linking)
COPY patches/socialLogin.js /app/api/strategies/socialLogin.js

# Bake onboarding frontend patches
COPY patches/useOnboarding.ts /app/client/src/patches/useOnboarding.ts
COPY patches/OnboardingModal.tsx /app/client/src/patches/OnboardingModal.tsx
COPY patches/OnboardingModal.css /app/client/src/patches/OnboardingModal.css

# Apply Root.tsx patch and rebuild client
COPY patches/Root.patch /tmp/Root.patch
RUN cd /app && patch -p1 < /tmp/Root.patch && npm run build:client
```

`docker-compose.yml` switches LibreChat image from `librechat/librechat:v0.8.2-rc2` to `build: ./apps/librechat`.

---

## Error States

| Situation | UX |
|-----------|-----|
| GitHub API unreachable | Skip org fetch, fall back to name input |
| Team search returns nothing | Show "Create this team" CTA |
| Join request already pending | Show "Request already sent" (idempotent) |
| API key invalid (detected on save) | Inline error on the key row |
| Network error during wizard | Toast + retry button, wizard state preserved |

---

## Out of Scope (Phase 8)

- Admin UI to approve/reject join requests (Phase 9)
- Per-user API key override in settings (Phase 9)
- Team settings page (Phase 9)
- Invite links / invite by email (Phase 9)
