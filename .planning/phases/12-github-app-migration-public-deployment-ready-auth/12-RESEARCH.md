# Phase 12: GitHub App Migration — Research

**Researched:** 2026-05-17
**Domain:** GitHub App authentication migration (server-side Python/FastAPI + Chrome MV3 extension + web app)
**Confidence:** HIGH (toutes les sources GitHub officielles vérifiées via WebFetch ; codebase inspectée pour assess reuse)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **Sequencing :** Phase 12 exécute APRÈS Phase 11 (✅ Phase 11 LIVE 2026-05-17 commit `dc9a74c`). Entry gate satisfait.
- **Clean break** vers GitHub App (PAS de dual-auth) — un seul user existant (mrboups), acceptable de re-authoriser une fois.
- **App owner :** Compte personnel `mrboups` (cohérent avec les OAuth Apps `xbrain` + `xbrain LibreChat`). Transfert vers une org dédiée possible plus tard sans casser les installs.
- **Permissions :** Minimal — `read:org` + `user:email` + `read:user` (match Phase 10 scope set exactement). Aucune régression de feature, screen de consent reste familier.
- **Chrome extension stable ID :** **Manifest `key` field MAINTENANT** + Chrome Web Store DIFFÉRÉ (Phase 13+).
- **mrboups migration :** Force re-authorize sur next sign-in via new GitHub App. `users.github_id` = PK → même row, mêmes teams, brain data intact.

### Claude's Discretion

- Détails d'implémentation des helpers JWT signing / installation token cache.
- Choix de librairie pour JWT (PyJWT vs cryptography direct).
- Stratégie de cache pour installation tokens (in-memory dict vs Postgres row).
- Stratégie de lookup `installation_id` (webhook-cached vs on-demand).
- Pattern de mock pour tests (extension du pattern respx existant).

### Deferred Ideas (OUT OF SCOPE)

- LibreChat OAuth App migration (`xbrain LibreChat` Client ID `Ov23li0XHV3NL8Git7Dk`) — séparé.
- Chrome Web Store publication — phase ultérieure.
- `repo:read` permission — différée tant qu'aucune feature ne le requiert.
- GitHub App branding (logo, description install screen) — minimal v1.
- Migration `dejavudev` auto-grant vers webhook-driven sync — Phase 10 logic (poll `/user/orgs`) préservé.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| GHAPP-01 | Création GitHub App sur compte `mrboups` avec multi-callback URLs (grooveos.app + `<ext-id>.chromiumapp.org`) + permissions minimales + private key PEM stockée server-side. | §Q1 (permission mapping), §Q6 (Chrome ext ID derivation), §Q11 (multi-callback URL setup). |
| GHAPP-02 | Backend JWT signing infrastructure — mint JWT RS256 avec App private key, échange contre installation tokens, cache (1h TTL, refresh-on-401). | §Q5 (PyJWT[crypto]), §Q12 (App JWT vs installation token vs user-to-server distinction). |
| GHAPP-03 | Table `installations` + webhook handler `/v1/webhooks/github/installation` pour `installation` + `installation_repositories`. | §Q3 (installation lookup), §Q4 (webhook signature), §Q13 (`installation` vs `installation_target` events). |
| GHAPP-04 | Migration `/orgs/{org}/members/{username}` de `GITHUB_API_PAT` vers installation token + removal du PAT. | §Q1 (Members read permission), §Q14 (auth options pour cette endpoint). |
| GHAPP-05 | Refresh token flow — store `github_access_token` + `github_refresh_token` + `github_token_expires_at` (migration 0019), transparent refresh < 5min from expiry. | §Q2 (refresh token enable + payload), §Q15 (token rotation single-use). |
| GHAPP-06 | Install flow UI — banner + redirect vers `https://github.com/apps/{app_slug}/installations/new` quand l'org primary du user n'a pas l'app installée. | §Q8 (first-install UX sequence). |
| GHAPP-07 | Update frontend client_id constants + ajout `key` field au manifest Chrome. | §Q6 (key derivation), §Q11 (multi-callback). |
| GHAPP-08 | Suppression OAuth App `xbrain` du chemin actif + dispatch logic dans `auth_github.py` + documentation. | §Q9 (auth_github.py reuse 60% / rewrite 40%), §Q7 (transition window). |
</phase_requirements>

## Summary

GitHub App migration est un travail d'ingénierie d'auth modéré (~8-10 plans estimés). La part "user-to-server OAuth flow" reuse ~60 % du code Phase 10 (`auth_github.py`) — mêmes endpoints `/login/oauth/authorize` + `/login/oauth/access_token`, même CSRF state pattern, même identity resolution. **Les nouveautés sont** : (1) une infrastructure JWT-signing RS256 server-side pour authentifier l'App elle-même, (2) une table `installations` peuplée par webhooks, (3) un refresh token flow obligatoire (les user tokens expirent à 8h vs unbounded en OAuth App), (4) un install flow UX explicite (l'App doit être installée sur une org avant qu'on puisse interroger sa membership).

Le piège-clé documenté en §Q12 : **trois kinds de tokens distincts** coexistent (App JWT RS256 ; installation access token `ghs_…` server-to-server ; user-to-server token `ghu_…` avec son refresh `ghr_…`). Conflation = bug récurrent.

Le piège-clé documenté en §Q8 : pour faire le `/orgs/{org}/members/{username}` check, **l'App doit être INSTALLÉE sur l'org** — le user-to-server token seul ne suffit pas pour interroger une org où l'app n'est pas installée. Le flow `signin → check installation_id existe pour cette org → redirect install URL si pas installée` doit être implémenté en UX.

**Primary recommendation :** Implémenter dans l'ordre 6 vagues : `installations` table + alembic 0019 → JWT signing helpers (PyJWT[crypto] ajouté à pyproject) → webhook handler + signature verification → installation token cache → user-to-server refresh integration in `auth_github.py` → install flow UX in chrome-extension + app-site → migration cutover + OAuth App removal.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| GitHub App private key storage | Secrets / env | API server | Le PEM est un secret server-side ; jamais exposé au browser ou aux containers non-trusted. Loader via `settings.GITHUB_APP_PRIVATE_KEY` (multi-ligne base64-encoded ou path-to-file). |
| JWT minting (RS256) | API server (memory-api) | — | Signature requires le PEM. Pure backend ; aucune surface client. |
| Installation token cache | API server (memory-api in-process) | Postgres (optionnel) | In-process dict avec TTL = 55min suffit (1 instance memory-api, restart = re-mint). Postgres cache uniquement si multi-instance plus tard. |
| Webhook signature verification | API server (memory-api `/v1/webhooks/github/installation`) | nginx (raw body passthrough) | Memory-api fait le HMAC ; nginx doit forwarder le raw body sans modification (pas de buffering JSON). |
| `installations` table (source of truth) | Postgres | — | Sync'd via webhooks. Source canonique pour "quelle org a installé l'app, avec quels droits". |
| User-to-server token + refresh token storage | Postgres (`users` row, encrypted at rest) | — | Tokens user-bound ; persistance nécessaire pour refresh-flow cross-session. |
| OAuth user authorization flow | Browser (initiate) + memory-api (code exchange) | — | Identique au pattern Phase 10. `client_secret` reste server-side. |
| Install flow redirect | Browser (initiate window.location) | memory-api (status check) | Memory-api expose `GET /v1/auth/github/installation-status?org_login=X` qui renvoie `{installed: bool, install_url: str | null}`. |
| Chrome extension stable ID | Chrome runtime (computed from `key` field) | — | Pure client-side. La présence du `key` dans manifest déterministe l'ID. |
| Multi-callback URL routing | GitHub OAuth `/login/oauth/authorize` | — | Géré nativement par GitHub : on liste TOUS les callbacks dans App settings, le frontend passe `redirect_uri=` qui doit match prefix. |

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| PyJWT[crypto] | 2.12.1 (Mar 2026) | RS256 JWT signing pour authentification App | [VERIFIED: pypi.org/project/PyJWT 2026-05-17] — librairie OAuth/JWT la plus standard en Python ; déjà supporte RS256 nativement quand installée avec `[crypto]` extra. `cryptography>=42` est déjà dans `pyproject.toml` (Phase 3 plan 03-10), donc l'extra est de fait satisfait — il suffit d'ajouter `PyJWT[crypto]>=2.10`. |
| httpx | ≥0.28 | HTTP client async pour GitHub API + token exchange | [VERIFIED: pyproject.toml ligne 16] — déjà dans le projet ; pattern existant dans `auth_github.py` et `auth.py`. |
| cryptography | ≥42.0.0 | Backend RSA pour PyJWT (transitive) | [VERIFIED: pyproject.toml ligne 22] — déjà installé pour Fernet (drive-sync). PyJWT[crypto] le réutilise. |
| FastAPI Depends + APIRouter | (current) | Routes + DI | [VERIFIED: codebase] — pattern existant. |
| SQLAlchemy async + Alembic | ≥2.0.36 | ORM + migration 0019 | [VERIFIED: pyproject.toml] — pattern existant. Next migration number = 0019 (0018 est la tête actuelle). |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| respx | ≥0.21 | Mock httpx pour tests | [VERIFIED: pyproject.toml dev deps ligne 36] — déjà utilisé dans `test_phase10_auth.py`. Le pattern `_configure_gh_router` est directement réutilisable pour mocker `/login/oauth/access_token` + `/user/*`. |
| Alembic | ≥1.14 | Migration 0019 — `installations` table + user.github_access_token columns | [VERIFIED: pyproject.toml] |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| PyJWT[crypto] | `python-jose` | python-jose supporte plus d'algorithmes mais maintenance moins active (last release 2022-12-18 avant le ralentissement). PyJWT couvre RS256 + tous les claims Github App ; moins de surface. **Recommandation : PyJWT.** [ASSUMED on python-jose maintenance — vérifier publiquement avant lock] |
| PyJWT[crypto] | `cryptography` direct (RSA + JSON sign manual) | Fonctionne, mais reimplémente le JWT spec à la main → bugs subtils sur padding, header encoding, exp/iat sérialisation. **Anti-pattern** — utiliser une lib testée. |
| In-memory installation token cache | Postgres `installation_tokens` table | Pour 1 org (`dejavudev`) sur 1 instance memory-api : in-memory dict avec TTL 55min est plus simple, zéro round-trip DB. Migration vers Postgres si multi-instance déployé. |
| Webhook-cached installations | On-demand `GET /orgs/{org}/installation` à chaque check | Trade-off détaillé §Q3. **Recommandation : webhook + Postgres `installations` table** (CONTEXT.md le mandate déjà via GHAPP-03). |

**Installation :**
```bash
# Add to apps/memory-api/pyproject.toml under [project] dependencies:
"PyJWT[crypto]>=2.10,<3"   # GitHub App JWT signing (RS256)
```

**Version verification :**
```bash
pip index versions PyJWT
# Expected: 2.12.1 (released 2026-03-13)
```

## Architecture Patterns

### System Architecture Diagram

```
                              ┌──────────────────────────────────────────────────────┐
                              │              GitHub Cloud (auth source)              │
                              │  ┌──────────────────┐    ┌──────────────────────┐    │
                              │  │ /login/oauth/    │    │ /app/installations/  │    │
                              │  │   authorize      │    │   {id}/access_tokens │    │
                              │  └──────────────────┘    └──────────────────────┘    │
                              │  ┌──────────────────┐    ┌──────────────────────┐    │
                              │  │ /orgs/{org}/     │    │ Webhook fan-out      │    │
                              │  │   installation   │    │ (installation event) │    │
                              │  └──────────────────┘    └──────────────────────┘    │
                              └────────┬─────────────────────────────┬───────────────┘
                                       │  HTTPS                       │ HTTPS POST
                                       │                              ▼
                                       │            ┌─────────────────────────────────┐
              ┌────────────────────────┼────────────│ /v1/webhooks/github/installation│
              │                        │            │  - HMAC-SHA256 verify           │
              │                        │            │  - upsert installations row     │
              │                        │            └─────────────────────────────────┘
              │                        │
              │                        ▼
┌─────────────┴───────────┐  ┌───────────────────────────────────────────────┐
│  Browser (web-app or    │  │            memory-api (FastAPI)               │
│  chrome ext popup)      │  │ ┌─────────────────────────────────────────┐   │
│  - signin button        │  │ │ /v1/auth/github/signin                  │   │
│  - launchWebAuthFlow OR │◄─┼─┤  - exchange code → user token            │   │
│    location.href        │  │ │  - get user/emails/orgs via user token  │   │
│  - store xbt_token in   │  │ │  - identity resolve / merge             │   │
│    chrome.storage or    │  │ │  - persist gho_/ghr_/expires_at         │   │
│    localStorage         │  │ │  - mint xbt_                             │   │
└──────────┬──────────────┘  │ └────────────────┬────────────────────────┘   │
           │                  │                  │                            │
           │                  │                  ▼                            │
           │                  │ ┌─────────────────────────────────────────┐   │
           │                  │ │ assert_org_member_via_install_token()   │   │
           │                  │ │  1. SELECT installation_id FROM         │   │
           │                  │ │     installations WHERE github_org_login│   │
           │                  │ │  2. if missing → return                 │   │
           │                  │ │     "INSTALL_REQUIRED" → frontend       │   │
           │                  │ │     redirects to install URL.           │   │
           │                  │ │  3. mint installation_token (cached)    │   │
           │                  │ │     via App JWT → GET .../access_tokens │   │
           │                  │ │  4. GET /orgs/{org}/members/{user}      │   │
           │                  │ │     with installation_token             │   │
           │                  │ │     → 204 = member, 404 = not.          │   │
           │                  │ └─────────────────────────────────────────┘   │
           │                  │                                                │
           │                  │ ┌─────────────────────────────────────────┐   │
           │                  │ │ JWT signing helper (app/auth_github_app │   │
           │                  │ │ .py)                                     │   │
           │                  │ │  - load PEM from settings                │   │
           │                  │ │  - mint App JWT (iss=client_id, exp=10m)│   │
           │                  │ │  - PyJWT.encode(payload, key, RS256)    │   │
           │                  │ └─────────────────────────────────────────┘   │
           │                  │                                                │
           │                  │ ┌─────────────────────────────────────────┐   │
           │                  │ │ refresh_user_token(user) — called from  │   │
           │                  │ │ a request-level dep when                 │   │
           │                  │ │ user.github_token_expires_at < now+5m   │   │
           │                  │ │  - POST /login/oauth/access_token       │   │
           │                  │ │    grant_type=refresh_token              │   │
           │                  │ │  - rotate stored access + refresh tokens│   │
           │                  │ └─────────────────────────────────────────┘   │
           │                  └───────────────────────────────────────────────┘
           │                                       │
           │                                       ▼
           │                  ┌──────────────────────────────────────────┐
           │                  │           Postgres (xbrain)              │
           │                  │  ┌────────────────────────────────────┐  │
           │                  │  │ installations (NEW — migration 0019│  │
           │                  │  │  - installation_id PK              │  │
           │                  │  │  - github_org_login                │  │
           │                  │  │  - installed_at, installed_by_gh_id│  │
           │                  │  │  - permissions JSONB               │  │
           │                  │  │  - revoked_at NULL                 │  │
           │                  │  └────────────────────────────────────┘  │
           │                  │  ┌────────────────────────────────────┐  │
           │                  │  │ users (ALTERED — migration 0019)   │  │
           │                  │  │  + github_access_token TEXT        │  │
           │                  │  │  + github_refresh_token TEXT       │  │
           │                  │  │  + github_token_expires_at TIMESTPZ│  │
           │                  │  │  + github_refresh_expires_at       │  │
           │                  │  └────────────────────────────────────┘  │
           │                  └──────────────────────────────────────────┘
           │
           ▼
  (Chrome extension only — install URL flow)
  chrome.identity.launchWebAuthFlow(
    https://github.com/apps/{app_slug}/installations/new?state=...
  )
  → user clicks "Install xbrain on dejavudev" → GitHub fires installation webhook
  → memory-api populates installations row → user can retry signin and pass org check
```

### Recommended Project Structure

```
apps/memory-api/
├── app/
│   ├── routes/
│   │   ├── auth_github.py            # MODIFIED — exchange code → store ghu_+ghr_ instead of gho_
│   │   ├── auth_github_app.py        # NEW — /v1/auth/github/installation-status + install URL helper
│   │   └── webhooks_github.py        # NEW — /v1/webhooks/github/installation
│   ├── services/
│   │   ├── github_app_jwt.py         # NEW — mint App JWT (RS256)
│   │   ├── github_installation.py    # NEW — mint installation token + cache + refresh-on-401
│   │   └── github_user_token.py      # NEW — refresh user token + rotate storage
│   ├── repos/
│   │   └── installations.py          # NEW — CRUD on installations table
│   ├── models/
│   │   └── installation.py           # NEW — Installation ORM model
│   ├── auth.py                       # MODIFIED — check_github_org_membership() uses installation_token, not PAT
│   ├── config.py                     # MODIFIED — add GITHUB_APP_ID, GITHUB_APP_SLUG, GITHUB_APP_CLIENT_ID,
│   │                                 #            GITHUB_APP_PRIVATE_KEY (multi-line PEM),
│   │                                 #            GITHUB_APP_WEBHOOK_SECRET. REMOVE GITHUB_API_PAT.
│   └── deps.py                       # MODIFIED — GitHub gho_ branch: also accept ghu_ token prefix;
│                                     #            on 401, transparent refresh.
└── alembic/versions/
    └── 0019_github_app_install.py    # NEW — installations table + users.github_access_token, etc.

chrome-extension/
├── manifest.json                     # MODIFIED — add "key" field (base64 pub key)
└── background.js                     # MODIFIED — GITHUB_CLIENT_ID = new GitHub App client_id
                                       #            (Iv23li... or Ov23li... prefix)

app-site/account/teams/
└── teams.js                          # MODIFIED — GITHUB_CLIENT_ID = new GitHub App client_id;
                                       #            install-flow banner UI
```

### Pattern 1: Three Distinct Tokens

**What:** GitHub Apps use three completely separate token types for three different purposes.
**When to use:** Always be explicit about which one any function takes/returns.

| Token | Prefix | TTL | Auth Header | Use |
|-------|--------|-----|-------------|-----|
| App JWT | (none — base64 with dots) | 10 min max | `Authorization: Bearer <jwt>` | App-to-GitHub identity. Authenticates the App itself (not a user, not an installation). Used to mint installation tokens. |
| Installation access token | `ghs_` | 1 hour | `Authorization: Bearer ghs_...` | Server-to-server actions on behalf of an installation (e.g. read org members, post commit statuses). |
| User-to-server token | `ghu_` (with refresh `ghr_`) | 8 hours (refresh: 6 months) | `Authorization: Bearer ghu_...` | Act as the user (read user profile, list user orgs). Single-use refresh: each refresh rotates BOTH tokens. |

[CITED: https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/refreshing-user-access-tokens]

**Why this matters:** A bug pattern is calling `/user` (which needs user token) with `ghs_` (server-to-server) or vice-versa. Each helper should accept ONLY one token type and name it explicitly.

```python
# Source: https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-json-web-token-jwt-for-a-github-app
# Mint App JWT (server-to-GitHub)
import jwt as pyjwt
import time

def mint_app_jwt(client_id: str, private_key_pem: str) -> str:
    """Returns a 10-minute App JWT signed RS256."""
    now = int(time.time())
    payload = {
        "iat": now - 60,        # 60s clock-drift cushion per GitHub docs
        "exp": now + 600,       # 10-minute max
        "iss": client_id,       # GitHub App client_id (or numeric App ID — both accepted)
    }
    return pyjwt.encode(payload, private_key_pem, algorithm="RS256")
```

### Pattern 2: Installation Token Cache (In-Memory, TTL-based)

**What:** Cache installation access tokens server-side. Each is valid 1h ; cache them ~55 min and refresh on 401.

**When to use:** Any call to `/orgs/{org}/members/...`, `/repos/{owner}/{repo}/...`, etc. that needs server-to-server auth.

```python
# Source: pattern after auth.py:_github_membership_cache (line ~120)
import time
import httpx

_INSTALLATION_TOKEN_CACHE: dict[int, tuple[float, str]] = {}
# Key: installation_id. Value: (expires_at_unix_ts, token_string).
_TOKEN_TTL_S = 55 * 60   # Cache 55 min (GitHub returns 1h, give 5min safety).

async def get_installation_token(installation_id: int, client_id: str, pem: str) -> str:
    cached = _INSTALLATION_TOKEN_CACHE.get(installation_id)
    now = time.time()
    if cached and cached[0] > now:
        return cached[1]

    app_jwt = mint_app_jwt(client_id, pem)
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(
            f"https://api.github.com/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {app_jwt}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
    r.raise_for_status()
    body = r.json()
    token = body["token"]  # ghs_...
    # body["expires_at"] is ISO 8601 — parse if you want exact, but TTL works.
    _INSTALLATION_TOKEN_CACHE[installation_id] = (now + _TOKEN_TTL_S, token)
    return token
```

### Pattern 3: Webhook Signature Verification

**What:** Every webhook from GitHub carries `X-Hub-Signature-256: sha256=<hex>` computed as HMAC-SHA256 of the raw body using the webhook secret.

**When to use:** EVERY webhook route. No exceptions. Reject 401 on mismatch.

```python
# Source: https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries
import hmac
import hashlib

def verify_github_webhook(payload_body: bytes, signature_header: str | None, secret: str) -> None:
    """Raise HTTPException(401) on mismatch or missing header."""
    if not signature_header:
        raise HTTPException(status_code=401, detail="Missing signature header")
    expected = "sha256=" + hmac.new(
        key=secret.encode("utf-8"),
        msg=payload_body,
        digestmod=hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature_header):
        raise HTTPException(status_code=401, detail="Signature mismatch")
```

**Critical FastAPI pitfall:** The signature is over the RAW BYTES, not the parsed JSON. You MUST use `Request.body()` (bytes) before any Pydantic deserialization, or recompute over the re-serialized JSON which can differ in whitespace/order and break.

```python
from fastapi import Request

@router.post("/v1/webhooks/github/installation")
async def github_webhook(request: Request):
    raw = await request.body()  # raw bytes — verify against this
    sig = request.headers.get("X-Hub-Signature-256")
    verify_github_webhook(raw, sig, settings.GITHUB_APP_WEBHOOK_SECRET)
    # NOW parse:
    payload = json.loads(raw)
    event = request.headers.get("X-GitHub-Event")
    # ... dispatch on event + payload["action"]
```

### Pattern 4: User Token Refresh, Transparent

**What:** Before any call to `/user/*` with a user token, check if `expires_at < now + 5min` and refresh transparently.

**When to use:** In a FastAPI dependency that wraps `get_current_principal` for GitHub-user-authenticated routes. Phase 10 didn't need this (OAuth App = unbounded tokens) ; Phase 12 makes it mandatory.

```python
# Source: https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/refreshing-user-access-tokens
async def refresh_user_token_if_needed(session, user) -> str:
    """Return a valid (possibly newly-refreshed) user token for the user."""
    now = datetime.now(timezone.utc)
    if user.github_token_expires_at and user.github_token_expires_at > now + timedelta(minutes=5):
        return user.github_access_token  # still fresh

    if not user.github_refresh_token:
        raise HTTPException(401, "User has no refresh token — must re-authorize")

    if user.github_refresh_expires_at and user.github_refresh_expires_at < now:
        raise HTTPException(401, "Refresh token expired — must re-authorize")

    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": settings.GITHUB_APP_CLIENT_ID,
                "client_secret": settings.GITHUB_APP_CLIENT_SECRET,
                "grant_type": "refresh_token",
                "refresh_token": user.github_refresh_token,
            },
        )
    if r.status_code != 200:
        raise HTTPException(401, "Refresh failed — must re-authorize")
    body = r.json()
    if "error" in body:
        raise HTTPException(401, f"Refresh failed: {body.get('error_description', body.get('error'))}")

    # SINGLE-USE: the old refresh_token is now invalid. ROTATE both.
    user.github_access_token = body["access_token"]                        # ghu_...
    user.github_refresh_token = body["refresh_token"]                      # ghr_...
    user.github_token_expires_at = now + timedelta(seconds=int(body["expires_in"]))
    user.github_refresh_expires_at = now + timedelta(seconds=int(body["refresh_token_expires_in"]))
    await session.commit()
    return user.github_access_token
```

### Anti-Patterns to Avoid

- **Caching App JWT.** It expires in 10 min and is cheap to mint (one local RS256 sign, no I/O). Caching adds complexity for zero gain.
- **Re-using installation token across orgs.** Each installation has its own token; one per `installation_id`.
- **Calling `/user/orgs` with the installation token.** That endpoint requires a user-to-server token. Use the right type.
- **Parsing JSON before verifying webhook signature.** See Pattern 3 — re-serializing JSON breaks HMAC.
- **Storing the App private key in the codebase or unencrypted git.** PEM lives in `.env` (gitignored) or in a secret store. Phase 1 SOPS pattern fits.
- **Confusing OAuth App `Client ID = Ov23li...` (legacy) with GitHub App `client_id = Iv23li...` (new).** Both are valid prefixes ; the new App will have its own freshly-issued one.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| JWT signing (RS256) | Custom RSA + manual JSON serialization | `PyJWT[crypto]` | Padding, header encoding, exp/iat semantics — already-solved problem with subtle bugs. |
| HMAC-SHA256 webhook verification | Re-implement in raw `hashlib` per route | Shared helper using `hmac.compare_digest` | Timing-attack resistance only via `compare_digest` ; copy-pasted naive `==` is a known CVE pattern. |
| Token lifetime tracking / refresh scheduling | Per-call ad-hoc clock checks | Single `refresh_user_token_if_needed(session, user)` helper | Three different places will need it ; spread logic = drift = bugs. |
| GitHub OAuth code exchange | Re-implement when Phase 10 already has it | Reuse `_exchange_code_for_token` from `auth_github.py` (modify to pull `expires_in` + `refresh_token` from response body) | 60% of `auth_github.py` is reusable as-is. |
| Chrome extension ID derivation | Trying to publish to Web Store first | Generate keypair locally, add `key` to manifest | See §Q6 — `key` field makes ID deterministic without Web Store. |

**Key insight :** GitHub's OAuth + JWT specs are stable but full of subtle requirements (single-use refresh tokens, 10-min JWT cap, signature timing-attacks, JSON re-serialization breaking HMAC). Each of these has burned a senior engineer. Use battle-tested libraries.

## Runtime State Inventory

> Phase 12 = rename + refactor + cutover. Inventory required.

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| **Stored data** | (1) `users.github_id` rows — preserved as-is (PK is `github_id` numeric, OAuth App identity == GitHub App identity for the same user). mrboups's `github_id` = same int → same user row → same teams/brain data. **No data migration needed.** (2) Live `gho_` token in `user_api_tokens` is NOT stored (xbt_ tokens only). The `gho_` token Phase 10 stored only in-process for the single sign-in HTTP request, never persisted. | None. |
| **Live service config** | (1) GitHub OAuth App `xbrain` (Client ID `Ov23liy7tZekl0uEztoj`) registered at github.com/settings/applications. (2) GitHub OAuth App `xbrain LibreChat` (Client ID `Ov23li0XHV3NL8Git7Dk`) — **out of scope per CONTEXT.md**. (3) Existing `xbrain` callback URLs registered: `https://grooveos.app/account/teams/` (per teams.js line 24 comment). | Create new GitHub App registration on `mrboups` account ; transfer/revoke OAuth App `xbrain` **AFTER** GitHub App is live (per §Q7). |
| **OS-registered state** | None. memory-api runs in Docker container `xbrain-memory-api` ; no OS-level service registration carries OAuth App identifiers. | None. |
| **Secrets and env vars** | (1) `GITHUB_CLIENT_ID` + `GITHUB_CLIENT_SECRET` in `.env` on VM — currently the OAuth App `xbrain`. (2) `GITHUB_API_PAT` (declared as `GITHUB_ORG_PAT` in `.env.example` line 178) — long-lived PAT used for `/orgs/{org}/members/{username}` checks. (3) `GITHUB_ORG_NAME=your-github-org` (line 179) — confusing : code uses `GITHUB_ORG` (settings.py line 41) defaulting to `your-github-org`, but the real prod org for Phase 10 is `dejavudev` per CONTEXT.md mention. Verify which value is actually set in `.env` on the VM. | **NEW envs** to add: `GITHUB_APP_ID`, `GITHUB_APP_SLUG`, `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET`, `GITHUB_APP_PRIVATE_KEY` (multi-line PEM), `GITHUB_APP_WEBHOOK_SECRET`. **REMOVE**: `GITHUB_API_PAT` / `GITHUB_ORG_PAT` (both spellings). **DECISION POINT for planner**: rename `GITHUB_CLIENT_ID` → `GITHUB_OAUTH_APP_CLIENT_ID` (Phase 5 OAuth App = LibreChat-only, still in use) ; reserve `GITHUB_APP_CLIENT_ID` for the new GitHub App. Documenting this naming in `.env.example` prevents 3-month-later confusion. |
| **Build artifacts / installed packages** | (1) `apps/memory-api/pyproject.toml` — needs `PyJWT[crypto]>=2.10,<3` added. (2) Chrome extension built artifact — current `manifest.json` lacks `key` field. After adding `key`, every developer + the deployed extension will have a stable, NEW extension ID. Old extension ID (currently `chrome.runtime.id` is randomized per-install) is dropped. Users who had clipped notes via the old extension keep working (memory-api never tied data to the extension ID — only to the user_sub). | Add PyJWT to deps. Generate key + add to manifest in plan covering GHAPP-01/07. Communicate the new ext ID to mrboups (he reloads the unpacked extension once). |

**Critical clarification on `.env` naming mess:** The codebase has TWO different env names for the same concept: `config.py` declares `GITHUB_API_PAT` (line 43), but `.env.example` declares `GITHUB_ORG_PAT` (line 178). The actual `.env` on the VM probably has one or the other ; the plan must verify which. Phase 12 is the right moment to unify naming.

**Nothing found in category:** All five categories produced at least one item — none can be marked "none".

## Common Pitfalls

### Pitfall 1: Conflating the three token types

**What goes wrong:** Code calls `/user/orgs` with an installation token (returns 401), or calls `/orgs/{org}/members/{user}` with the App JWT directly (returns 401), or refreshes a user token by sending it as the `refresh_token` param (the refresh flow needs the SEPARATE `ghr_…` refresh token, not the access token).

**Why it happens:** All three look like "GitHub auth", and the naming overlaps ("token" everywhere).

**How to avoid:** Type-name every helper explicitly. `mint_app_jwt() -> AppJWT`, `mint_installation_token() -> InstallationToken`, `refresh_user_token() -> UserToken`. Use `NewType` or just naming discipline. Document the function with WHICH endpoint the returned token works against.

**Warning signs:** 401 from GitHub API with `{"message": "Resource not accessible by integration"}` (you used wrong token type) or `{"message": "Bad credentials"}` (token format mismatch).

### Pitfall 2: Webhook delivery is best-effort — no exactly-once

**What goes wrong:** Webhook for `installation.created` is dropped (network hiccup, memory-api restarting, signature secret mismatch causing 401 → GitHub retries 5x then gives up). The `installations` table never gets the row. User signs in, app tries to check org membership, finds no `installation_id`, redirects to install URL — but the app IS already installed. User loops.

**Why it happens:** Webhooks are at-least-once-when-they-deliver, but they can fail entirely. GitHub retries 5 times with exponential backoff then stops. The "Recent Deliveries" tab in App settings shows the failure but no app code polls it.

**How to avoid:** Implement a **reconciliation fallback** — on user sign-in, if their primary org has no row in `installations`, do a one-shot `GET /orgs/{org}/installation` with App JWT. If GitHub returns 200, the App IS installed (webhook was missed) ; backfill the row. If 404, the App is genuinely not installed ; redirect to install URL.

**Warning signs:** User reports "I installed the app but xbrain says I haven't"; GitHub App "Advanced → Recent Deliveries" shows failed deliveries.

### Pitfall 3: `installation` vs `installation_target` events

**What goes wrong:** Plan subscribes to `installation_target` thinking it's "install on a target" — but that event fires only on account RENAMES (not installs). New installs come via `installation.created`. The `installations` table never populates.

**Why it happens:** The names sound similar.

**How to avoid:** Subscribe to `installation` (and `installation_repositories` per CONTEXT.md). The action types to handle:
- `installation.created` — first install on an account → INSERT row
- `installation.deleted` — uninstall → set `revoked_at = now()`
- `installation.suspend` / `installation.unsuspend` — temporary disable → set `suspended_at` (optional column)
- `installation.new_permissions_accepted` — admin granted updated perms → update `permissions JSONB`
- `installation_repositories.added` / `.removed` — repos selectively added (only relevant if we add repo permissions later)

[CITED: https://docs.github.com/en/webhooks/webhook-events-and-payloads]

### Pitfall 4: The 302 response on `/orgs/{org}/members/{username}`

**What goes wrong:** Phase 10 code treats `status_code == 204` as "is member" and "everything else" as "not member". With an installation token, GitHub may return **302** ("Response if requester is not an organization member") instead of 404 in edge cases — and the current `auth.py` line 178 `is_member = org_r.status_code == 204` correctly handles this by returning False. But if the installation token belongs to an installation that includes the org, the request IS an org-member-equivalent → 204 or 404 only.

**Why it happens:** GitHub's response semantics for this endpoint depend on BOTH the target user's membership AND the requester's relation to the org.

**How to avoid:** Continue treating `204 = member, anything else = not`. Verify with httpx mock both 204 and 404 in tests. The installation token + Members:Read permission lets us see private members so 302 should not occur — but defensive code is cheap.

[CITED: https://docs.github.com/en/rest/orgs/members]

### Pitfall 5: Raw body must be read BEFORE Pydantic parsing for webhook signature

**What goes wrong:** Route signature is `def handler(payload: InstallationWebhookBody)`. FastAPI/Starlette consumes the body to parse Pydantic. Then you try `await request.body()` → empty. Or you re-serialize from the Pydantic model — JSON whitespace differs from GitHub's payload → HMAC mismatch → 401.

**Why it happens:** Body streams can only be read once. Pydantic parsing consumes the stream.

**How to avoid:** Use `Request: Request` (raw) as the dependency, do `await request.body()` FIRST, verify HMAC, THEN `json.loads(raw)` manually. NEVER use a Pydantic model param for webhook endpoints. (See Pattern 3 example.)

### Pitfall 6: Single-use refresh tokens silently invalidating each other on race

**What goes wrong:** Two concurrent requests both hit `refresh_user_token_if_needed`. Both see the same expiring token, both call the refresh endpoint with the same `ghr_…`. The first succeeds + rotates ; the second uses an already-spent refresh token → 401 → user forced to re-auth.

**Why it happens:** GitHub's refresh tokens are single-use ([CITED docs](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/refreshing-user-access-tokens) — "Once you use a refresh token, that refresh token and the old user access token will no longer work.").

**How to avoid:** Wrap the refresh in a per-user lock. Cheapest pattern in asyncio: a dict of `asyncio.Lock` keyed by `user.id`. Acquire before the refresh ; re-check `user.github_token_expires_at` inside the lock (it may have been refreshed by the concurrent request).

```python
_refresh_locks: dict[UUID, asyncio.Lock] = {}

async def refresh_user_token_if_needed(session, user):
    lock = _refresh_locks.setdefault(user.id, asyncio.Lock())
    async with lock:
        await session.refresh(user)  # re-read from DB inside lock
        if user.github_token_expires_at > datetime.now(tz=UTC) + timedelta(minutes=5):
            return user.github_access_token
        # ... do the refresh ...
```

For multi-instance deployment (Phase 13+) the lock pattern is insufficient — use a Postgres advisory lock. v1 = single instance, in-process lock is enough.

### Pitfall 7: Chrome extension `key` field changes the extension ID — old test users break

**What goes wrong:** Adding `key` to `manifest.json` changes `chrome.runtime.id` to a new deterministic value. Anyone who had the unpacked extension loaded BEFORE the `key` was added now has a different ID. The chromiumapp.org callback URL changes. Their old sign-in flow breaks until they reload the extension.

**Why it happens:** That's by design — the whole POINT of `key` is to fix the ID.

**How to avoid:** Communicate to mrboups (the single existing user) : "reload the unpacked extension once after the v12 update — your extension ID will change to a stable value." After Web Store publish (future phase), the Store-assigned key takes over and the manifest `key` is ignored.

### Pitfall 8: Webhook IP source allow-listing breaks behind Cloudflare/proxies

**What goes wrong:** Some teams "secure" webhooks by allow-listing GitHub's IP ranges. memory-api sees nginx's IP, not GitHub's. All webhooks rejected.

**Why it happens:** HMAC signature IS the authentication. IP filtering is unnecessary AND breaks under any reverse proxy.

**How to avoid:** Rely on HMAC ONLY. Do not add IP filtering. (Documented for defense — there's no indication the plan would do this, but it's a frequent over-engineering trap.)

## Code Examples

Verified patterns from official sources:

### Mint App JWT (App authentication to GitHub)

```python
# Source: https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-json-web-token-jwt-for-a-github-app
import time
import jwt as pyjwt  # PyJWT[crypto]

def mint_app_jwt(client_id: str, private_key_pem: str) -> str:
    """Returns a 10-min App JWT (RS256). Caller passes this to GitHub
    endpoints like POST /app/installations/{id}/access_tokens."""
    now = int(time.time())
    payload = {
        "iat": now - 60,    # GitHub recommends 60s in the past for clock drift
        "exp": now + 600,   # 10-minute max (GitHub rejects longer)
        "iss": client_id,   # GitHub App client_id ('Iv23li...') OR numeric App ID
    }
    return pyjwt.encode(payload, private_key_pem, algorithm="RS256")
```

### Exchange OAuth code → user token (modified Phase 10 helper)

```python
# Source: extension of apps/memory-api/app/routes/auth_github.py:_exchange_code_for_token
# Difference: response body now includes expires_in + refresh_token + refresh_token_expires_in.
async def _exchange_code_for_user_token(code: str, redirect_uri: str) -> dict:
    """Returns dict with access_token (ghu_), refresh_token (ghr_),
    expires_in (28800 = 8h), refresh_token_expires_in (15897600 = 6mo).
    Raises HTTPException(400/502) on failure."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": settings.GITHUB_APP_CLIENT_ID,
                "client_secret": settings.GITHUB_APP_CLIENT_SECRET,
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
    if r.status_code != 200:
        raise HTTPException(400, "GitHub token exchange failed")
    body = r.json()
    if "error" in body:
        raise HTTPException(400, f"GitHub error: {body['error']}")
    return body  # {access_token, refresh_token, expires_in, refresh_token_expires_in, token_type, scope}
```

### Lookup installation_id for an org (webhook-fallback)

```python
# Source: https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/authenticating-as-a-github-app-installation
async def find_installation_for_org(org_login: str, client_id: str, pem: str) -> int | None:
    """Lookup installation_id by org login. Returns None if not installed.
    Falls back to GitHub API when webhook-cached row is missing (Pitfall 2)."""
    app_jwt = mint_app_jwt(client_id, pem)
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(
            f"https://api.github.com/orgs/{org_login}/installation",
            headers={
                "Authorization": f"Bearer {app_jwt}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return int(r.json()["id"])
```

### Verify webhook signature (FastAPI raw-body pattern)

```python
# Source: https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries
import hmac
import hashlib
import json
from fastapi import APIRouter, Request, HTTPException

router = APIRouter()

@router.post("/v1/webhooks/github/installation")
async def github_installation_webhook(request: Request):
    raw = await request.body()
    sig = request.headers.get("X-Hub-Signature-256")
    if not sig:
        raise HTTPException(401, "Missing signature")
    expected = "sha256=" + hmac.new(
        settings.GITHUB_APP_WEBHOOK_SECRET.encode(),
        raw,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(401, "Signature mismatch")

    event = request.headers.get("X-GitHub-Event")
    payload = json.loads(raw)
    if event == "installation":
        await handle_installation_event(payload)
    elif event == "installation_repositories":
        await handle_installation_repos_event(payload)
    # else: ignore (we don't subscribe to other events)
    return {"ok": True}
```

### Chrome extension `key` derivation (one-off, bash)

```bash
# Source: https://www.plasmo.com/blog/posts/how-to-create-a-consistent-id-for-your-chrome-extension
# (Verified against Chromium source comments via WebSearch)

# Step 1: generate 2048-bit RSA private key (KEEP THIS PRIVATE, never commit)
openssl genrsa -out chrome_ext_private.pem 2048

# Step 2: extract base64-encoded DER public key — this is the value for manifest "key"
openssl rsa -in chrome_ext_private.pem -pubout -outform DER 2>/dev/null | base64 -w 0
# → paste this string into manifest.json under "key": "<paste here>"

# Step 3: compute the resulting chrome.runtime.id (deterministic)
openssl rsa -in chrome_ext_private.pem -pubout -outform DER 2>/dev/null \
  | sha256sum \
  | cut -c1-32 \
  | tr '0-9a-f' 'a-p'
# → 32-char string in a-p alphabet (e.g. "abcdefghijklmnopabcdefghijklmnop")
# Use this in: chrome-extension/manifest callback URL, GitHub App callback URL list,
#              and verify with chrome.runtime.id at runtime.
```

**Operator workflow:** Generate the keypair once locally, base64 the pub key into `manifest.json` (committed), keep the private key OUT of git (in `.gitignore`) — it's only needed for Web Store publishing later. Until then, the `key` field alone is enough to make the ID deterministic across all unpacked installs.

### Test fixture pattern (extending Phase 10 respx mocks)

```python
# Source: extension of apps/memory-api/tests/test_phase10_auth.py:_configure_gh_router
import httpx
import respx
import time

def _configure_github_app_router(
    mock: respx.MockRouter,
    *,
    login: str,
    github_id: int,
    email: str,
    orgs: list[str],
    installation_id: int = 12345,
    refresh_token: str = "ghr_test_refresh",
) -> None:
    # OAuth user code exchange — now returns refresh_token + expires_in
    mock.post("https://github.com/login/oauth/access_token").mock(
        return_value=httpx.Response(200, json={
            "access_token": "ghu_test_user",
            "refresh_token": refresh_token,
            "expires_in": 28800,
            "refresh_token_expires_in": 15897600,
            "token_type": "bearer",
            "scope": "",
        })
    )
    # Profile endpoints (same as Phase 10)
    mock.get("https://api.github.com/user").mock(
        return_value=httpx.Response(200, json={
            "id": github_id, "login": login,
            "name": f"Display {login}", "email": None,
        })
    )
    mock.get("https://api.github.com/user/emails").mock(
        return_value=httpx.Response(200, json=[
            {"email": email, "primary": True, "verified": True}
        ])
    )
    mock.get(url__regex=r"https://api\.github\.com/user/orgs.*").mock(
        return_value=httpx.Response(200, json=[
            {"login": o, "id": i + 1} for i, o in enumerate(orgs)
        ])
    )
    # App-level: get installation for an org (webhook-fallback path)
    for org in orgs:
        mock.get(f"https://api.github.com/orgs/{org}/installation").mock(
            return_value=httpx.Response(200, json={"id": installation_id})
        )
    # Mint installation token (server-to-server)
    mock.post(
        f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    ).mock(
        return_value=httpx.Response(201, json={
            "token": "ghs_test_installation",
            "expires_at": "2099-01-01T00:00:00Z",
        })
    )
    # Org membership check using installation token
    for org in orgs:
        mock.get(
            f"https://api.github.com/orgs/{org}/members/{login}"
        ).mock(return_value=httpx.Response(204))


def make_app_jwt_pem_fixture() -> str:
    """Generate a fresh 2048-bit RSA key for tests so we don't ship one."""
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()

@pytest.fixture(autouse=True)
def _github_app_env(monkeypatch):
    pem = make_app_jwt_pem_fixture()
    monkeypatch.setenv("GITHUB_APP_CLIENT_ID", "Iv23li_test")
    monkeypatch.setenv("GITHUB_APP_CLIENT_SECRET", "test_secret")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", pem)
    monkeypatch.setenv("GITHUB_APP_WEBHOOK_SECRET", "test_webhook_secret")
    from app.config import settings
    monkeypatch.setattr(settings, "GITHUB_APP_CLIENT_ID", "Iv23li_test")
    monkeypatch.setattr(settings, "GITHUB_APP_CLIENT_SECRET", "test_secret")
    monkeypatch.setattr(settings, "GITHUB_APP_PRIVATE_KEY", pem)
    monkeypatch.setattr(settings, "GITHUB_APP_WEBHOOK_SECRET", "test_webhook_secret")
    yield
```

## Detailed Q&A — addressing CONTEXT.md open questions

### Q1 — Permission scopes mapping (OAuth → GitHub App fine-grained)

| OAuth Scope (Phase 10) | GitHub App Permission | Level | Group | Notes |
|------------------------|----------------------|-------|-------|-------|
| `user:email` | **Email addresses** | Read | Account permissions | Grants `GET /user/emails`. |
| `read:user` | **Profile** | Read | Account permissions | Grants `GET /user` (login, name, avatar). Note: GitHub Apps have implicit access to "public resources" so basic `/user` works without explicit perm, but **Profile:Read** is needed for full profile data per official docs. |
| `read:org` | **Members** | Read | Organization permissions | Grants `GET /orgs/{org}/members/{username}` and list org members. The App must be INSTALLED on the org for these to work (Q14). |
| (implicit in OAuth `user`) | `GET /user/orgs` | (no perm needed) | — | Works with any user-to-server token — returns ONLY orgs the user has consented to share with this App (this is a behavior change vs OAuth Apps — see Q1.1 below). |

[CITED: https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/choosing-permissions-for-a-github-app]
[CITED: https://docs.github.com/en/rest/authentication/permissions-required-for-github-apps]

**Q1.1 — Critical behavior change to flag for planner:** With OAuth App + `read:org` scope, `GET /user/orgs` returned ALL orgs the user belongs to. With GitHub App, `GET /user/orgs` returns ONLY orgs that have INSTALLED the App AND the user is a member of. **This breaks Phase 10 auto-grant if the org hasn't installed the App.** Mitigation: the install-flow UX (GHAPP-06) handles this — when user has no orgs in result but is signing in for the first time, prompt to install. After install, the org appears in the list. [ASSUMED — verify with a test call from a fresh test installation before locking].

**No Repository permissions** in v1. If we later add brain-from-repo sync, we'll add `Contents:Read` + `Metadata:Read`.

### Q2 — Refresh token enablement

**Toggle name in the UI :** "User-to-server token expiration" under "Optional Features" in App settings. Click "Opt-in" to enable expiration + refresh tokens. Without this opt-in, user tokens are unbounded (legacy mode, equivalent to OAuth App) and no refresh token is issued.

[CITED: https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/refreshing-user-access-tokens]

**Why opt in:** Required for production security posture. Unbounded user tokens stored server-side = larger blast radius if leaked. Phase 12 mandates the opt-in (per GHAPP-05).

**Refresh flow:**

```
POST https://github.com/login/oauth/access_token
Headers:
  Accept: application/json
Body (form-encoded):
  client_id      = <GitHub App client_id>
  client_secret  = <GitHub App client_secret>
  grant_type     = refresh_token
  refresh_token  = <stored ghr_...>
```

**Response (200):**

```json
{
  "access_token": "ghu_NEW_TOKEN_HERE",
  "expires_in": 28800,
  "refresh_token": "ghr_NEW_REFRESH_TOKEN_HERE",
  "refresh_token_expires_in": 15897600,
  "scope": "",
  "token_type": "bearer"
}
```

**TTLs (verified):**
- User access token (`ghu_…`) : 28800 sec = **8 hours**
- Refresh token (`ghr_…`) : 15897600 sec = **184 days** (~6 months). [CITED docs say "6 months"; the exact integer differs between docs pages — 15811200 in older doc, 15897600 in current refreshing-user-access-tokens. Use the integer GitHub returns in response, don't hardcode.]

**Error response:** When refresh token is expired or already-used, response is HTTP 200 with body `{"error": "...", "error_description": "..."}` (NOT a non-2xx status). Code MUST check `body["error"]` even on 200. Likely error values: `bad_refresh_token`, `expired_token`. On either, the user MUST re-authorize via full OAuth flow.

**Rotation:** Each refresh single-uses the old refresh token. The NEW refresh token must replace the old one atomically (DB write inside the same transaction as the new access_token). See Pitfall 6 for the race condition.

### Q3 — Installation lookup strategy

**Two approaches, both valid:**

| Approach | How | Pros | Cons |
|----------|-----|------|------|
| **(A) Webhook-cached** (CONTEXT.md mandate) | Subscribe to `installation` event ; INSERT/UPDATE `installations` row on receipt. At lookup time, `SELECT installation_id FROM installations WHERE github_org_login = X`. | Fast (1 PG query, ~1 ms). No GitHub API call. Stateful = source of truth. | Webhook can be missed (Pitfall 2). Cold-start problem: app installed BEFORE webhook subscription set up. |
| **(B) On-demand** | At lookup time, mint App JWT + `GET /orgs/{org}/installation`. 200 → installation_id ; 404 → not installed. | Always correct (no cache drift). | One extra HTTPS round-trip per check (~50-200ms). Counts against App-level rate limit. |

**Recommendation for xbrain:** **Hybrid.**

1. Use **(A)** as the primary fast path.
2. On cache miss (no row in `installations` for the org_login), fall back to **(B)** as a one-shot reconciliation. If (B) returns 200, INSERT the row (the webhook was missed). If (B) returns 404, the App is genuinely not installed → return the install URL to the user.
3. Webhook continues to update the table as the source of truth going forward.

This costs zero extra calls in the happy path and self-heals on missed webhooks. Rationale documented in Pitfall 2.

**Rate limits to consider:** Per-installation, GitHub allows 5000 req/h base + scales with installation size. For xbrain at 1 installation = 5000/h ; we'll never approach it. Per-App JWT, lower — but JWT calls only happen at install-token mint time (cached 55 min) and rare reconciliation, so also non-issue.

### Q4 — Webhook signature verification

**Header :** `X-Hub-Signature-256: sha256=<64 hex chars>`

**Algorithm :** HMAC-SHA256 over the raw request body, keyed by the webhook secret configured in App settings.

**Python (verified, from docs) :**

```python
import hmac, hashlib
def verify(payload_body: bytes, signature_header: str, secret: str) -> bool:
    expected = "sha256=" + hmac.new(
        secret.encode(),
        payload_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)
```

[CITED: https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries]

**Critical:** `hmac.compare_digest`, NOT `==`. Avoids timing side-channel.

**Secret scope:** Per-webhook (each webhook URL configured in the App has its own secret). For xbrain v1, one webhook (`/v1/webhooks/github/installation`) — one secret in `.env` (`GITHUB_APP_WEBHOOK_SECRET`).

**FastAPI pitfall:** see Pattern 3 + Pitfall 5 — must read raw bytes before Pydantic.

**No equivalent in codebase :** verified by grep — no existing webhook receiver in memory-api (Centrifugo webhooks are different mechanism). This is greenfield, follow the pattern in Pattern 3.

### Q5 — JWT signing library

**Recommendation : `PyJWT[crypto]>=2.10,<3`** ([VERIFIED: pypi.org — 2.12.1 is current as of 2026-03-13]).

**Why:**
- Industry standard for Python JWT (PyJWT has 1500+ GitHub stars, used by django-rest-framework-simplejwt, FastAPI tutorials, etc.).
- `[crypto]` extra pulls `cryptography` — but `cryptography>=42` is ALREADY in `pyproject.toml` (line 22, added Phase 3 for Fernet). So the extra is satisfied transitively. Adding `PyJWT[crypto]` is effectively just `pip install PyJWT`.
- Direct support for RS256 with `algorithm="RS256"` parameter — verified in official GitHub docs JWT example.
- Existing `authlib` is already a dep (line 15) and supports JWT too, but `authlib.jose` API for RS256 signing is more verbose ; PyJWT is closer to the GitHub docs example.

**Anti-pattern :** Don't write JWT manually with `cryptography` alone (header encoding bugs, base64url padding traps). Don't use `python-jose` (less active maintenance). Don't reuse `authlib.jose` unless you want the codebase to have two JWT libraries.

**Add to `pyproject.toml`:**
```toml
"PyJWT[crypto]>=2.10,<3",   # GitHub App JWT signing (RS256)
```

### Q6 — Chrome extension stable ID derivation

**The math (verified against Chromium source comments via WebSearch):**

1. Take the SHA-256 of the DER-encoded RSA public key (binary, NOT the PEM text, NOT the base64 string).
2. Take the first 32 HEX characters of that hash (16 bytes).
3. Translate digit `0` → `a`, `1` → `b`, ..., `f` → `p`. (Chromium does this to avoid the extension-id-looking-like-an-IPv4 confusion.)

[CITED: https://developer.chrome.com/docs/extensions/reference/manifest/key]
[CITED: https://www.plasmo.com/blog/posts/how-to-create-a-consistent-id-for-your-chrome-extension]

**Bash one-liner (verified):**

```bash
openssl genrsa -out chrome_ext_private.pem 2048

# Step A — get base64 single-line pub key for manifest:
openssl rsa -in chrome_ext_private.pem -pubout -outform DER 2>/dev/null | base64 -w 0

# Step B — derive the extension ID:
openssl rsa -in chrome_ext_private.pem -pubout -outform DER 2>/dev/null \
  | sha256sum \
  | cut -c1-32 \
  | tr '0-9a-f' 'a-p'
```

The output of Step B is the `chrome.runtime.id` at runtime. The output of Step A goes into `manifest.json` under `"key": "..."`.

**Verification step in the plan:** load the unpacked extension and assert `chrome.runtime.id === <computed-id>`. If they don't match, the base64 in manifest is wrong (whitespace, line-break, padding issue).

**Reference implementation note :** The Plasmo tool ([itero.plasmo.com/tools/generate-keypairs](https://itero.plasmo.com/tools/generate-keypairs)) does the same math via web UI ; useful as a cross-check.

**Filename for private key :** add to `.gitignore` ; needed ONLY when publishing to Chrome Web Store later (you submit it as the "upload key" to bind your Store listing to your dev key).

### Q7 — Migration sequencing on live VM

**Per CONTEXT.md decision : clean break. NO dual-auth code path.** But sequencing still matters so mrboups doesn't get locked out mid-deploy.

**Recommended order:**

1. **Pre-deploy (no service downtime):**
   - Register the new GitHub App on `mrboups` account (web UI, manual).
   - Generate App keypair (private key PEM), webhook secret. Add to `.env` on VM.
   - Install the GitHub App on `dejavudev` org via web UI (one click for mrboups).
   - Verify the webhook URL points to `https://api.grooveos.app/v1/webhooks/github/installation` — but the endpoint doesn't exist yet. The first webhooks will fail until we deploy.

2. **Deploy memory-api code with NEW GitHub App support:**
   - Migration 0019 (`installations` + `users.github_*` columns) applies at container boot.
   - Webhook endpoint live, but no rows yet because the original `installation.created` webhook was missed (Pitfall 2). The fallback in §Q3 will reconcile on first sign-in.
   - At this point, the OAuth App `xbrain` is still the active code path in `auth_github.py` — mrboups can still sign in with the OLD client_id. (We haven't switched the frontend yet.)

3. **Deploy frontends with NEW GitHub App client_id:**
   - `app-site/account/teams/teams.js` → new `GITHUB_CLIENT_ID`.
   - `chrome-extension/background.js` + `manifest.json` → new `GITHUB_CLIENT_ID` + new `key` field.
   - Now mrboups signing in via either frontend goes through the new GitHub App flow.

4. **mrboups re-authorizes (one-click):**
   - Signs in on app-site or extension → goes through new OAuth flow → identity resolution finds his existing `users` row by `github_id` (PK unchanged) → mints xbt_ → he's in.
   - First sign-in triggers the install fallback in §Q3 if needed (but since we manually installed the App in step 1, no install redirect should fire).

5. **Verify end-to-end :** run `infrastructure/scripts/verify-phase12.sh` (to be authored in the verify plan). All 8 success criteria from ROADMAP pass.

6. **Revoke the OLD OAuth App `xbrain`:**
   - Delete OAuth App `Ov23liy7tZekl0uEztoj` from github.com/settings/applications.
   - Remove the now-dead env vars from `.env` on VM (after confirming the new flow works for at least 24h).

**No dual-auth needed.** The transition window is the duration of one sign-in (mrboups clicks the button once). The OAuth App stays registered (but unused by xbrain) until step 6 — this is purely housekeeping ; nothing depends on it being live during the window.

**Rollback path (defensive):** if the new GitHub App flow breaks at step 4, revert step 3 (re-deploy old frontend) — mrboups can sign in with the OAuth App flow that's still live in step 2. The cost is ~1 hour to revert and ship a fix.

### Q8 — First-install UX for mrboups

**The flow that MUST be implemented (the planner needs this sequence verbatim):**

```
┌───────────────┐                ┌─────────────────┐               ┌────────┐
│  User clicks  │                │   memory-api    │               │ GitHub │
│ "Sign in with │                │                 │               │        │
│   GitHub"     │                │                 │               │        │
└───────┬───────┘                └────────┬────────┘               └────┬───┘
        │                                 │                              │
        │ 1. window.location.href = github/login/oauth/authorize?        │
        │    client_id=Iv23li...&redirect_uri=...&state=...              │
        │ ──────────────────────────────────────────────────────────────►│
        │                                                                 │
        │            (user grants user-to-server access — minimal       │
        │             screen, asks "share your profile, email, orgs")   │
        │                                                                 │
        │ 2. redirect back with ?code=...&state=...                       │
        │ ◄──────────────────────────────────────────────────────────────│
        │                                                                 │
        │ 3. POST /v1/auth/github/signin {code, redirect_uri, state}      │
        │ ──────────────────────────────► │                              │
        │                                  │ 4. POST /login/oauth/        │
        │                                  │    access_token              │
        │                                  │ ────────────────────────────►│
        │                                  │ ◄────────────────────────────│
        │                                  │   {access_token: ghu_...,    │
        │                                  │    refresh_token: ghr_...,   │
        │                                  │    expires_in: 28800, ...}   │
        │                                  │                              │
        │                                  │ 5. GET /user, /user/emails,  │
        │                                  │    /user/orgs                │
        │                                  │    (using ghu_ token)        │
        │                                  │ ────────────────────────────►│
        │                                  │ ◄────────────────────────────│
        │                                  │   {id, login, ...,           │
        │                                  │    orgs: [dejavudev?]}       │
        │                                  │                              │
        │                                  │ 6. Identity resolve / merge  │
        │                                  │   (Phase 10 logic preserved) │
        │                                  │                              │
        │                                  │ 7. For each user.org_login:  │
        │                                  │      lookup installation_id  │
        │                                  │      in installations table  │
        │                                  │      → if missing, try /orgs/│
        │                                  │        {org}/installation    │
        │                                  │        with App JWT (Pitfall │
        │                                  │        2 reconciliation)     │
        │                                  │                              │
        │                                  │ 8a. If dejavudev installation│
        │                                  │     exists → mint installa-  │
        │                                  │     tion token → check       │
        │                                  │     /orgs/dejavudev/members/ │
        │                                  │     {login} → 204 = OK →     │
        │                                  │     auto_grant_via_org_match │
        │                                  │     → mint xbt_ → return     │
        │                                  │                              │
        │ 9a. {xbt_token, user: {...}}     │                              │
        │ ◄────────────────────────────────│                              │
        │                                                                 │
        │ ─OR─                                                            │
        │                                                                 │
        │                                  │ 8b. If dejavudev installation│
        │                                  │     missing AND user expects │
        │                                  │     to join (org_login is in │
        │                                  │     user.orgs) → mint a      │
        │                                  │     "pending" xbt_ (multi-   │
        │                                  │     team but no team_member- │
        │                                  │     ship rows yet) → return  │
        │                                  │     install_required = true  │
        │                                  │     + install_url            │
        │                                                                 │
        │ 9b. {xbt_token, user: {...},                                    │
        │      install_required: true,                                    │
        │      install_url: https://github.com/apps/xbrain/installations/ │
        │                  new?state=<return-to-teams-page>}              │
        │ ◄────────────────────────────────│                              │
        │                                                                 │
        │ 10. UI shows banner: "Install xbrain on dejavudev to access     │
        │     your team. [Install]"                                       │
        │                                                                 │
        │ 11. User clicks Install → window.location = install_url         │
        │ ──────────────────────────────────────────────────────────────►│
        │                                                                 │
        │      (user grants org-level install — only org admins can do   │
        │       this. If user is NOT org admin, GitHub will say so and   │
        │       the user must contact their admin.)                      │
        │                                                                 │
        │ 12a. webhook installation.created → memory-api populates       │
        │      installations row.                                         │
        │ ◄───────────────────────────────────────────────────────────── │
        │                                                                 │
        │ 12b. user redirected back to grooveos.app/account/teams/        │
        │      with ?installation_id=...&setup_action=install             │
        │ ◄──────────────────────────────────────────────────────────────│
        │                                                                 │
        │ 13. UI auto-retries the sign-in (or just calls /v1/me/teams     │
        │     with the still-valid xbt_ from step 9b) → now team_         │
        │     membership rows exist → user lands in Brain monitor.        │
        │                                                                 │
```

**Critical clarifications:**
- The user-to-server token (step 5) is enough for `/user`, `/user/emails`, `/user/orgs`. **It is NOT enough for `/orgs/{org}/members/{username}`** — that one needs the installation token (or works with the user token IF the user is a PUBLIC member of the org — but xbrain users will typically be private members, so don't rely on it).
- For the membership check, we use the installation token + the Members:Read org permission. This requires the App to be installed.
- The "install_required" flow is the ONE differentiator vs Phase 10 UX — and it's the killer UX difference: before, users could sign in but not see anything ; now we tell them exactly why (the org admin needs to install xbrain) and how (link).

**Edge case to flag for the planner:** mrboups IS the admin of `dejavudev` (per CONTEXT.md). So when he hits the install URL, he can self-install. For future users joining teams whose org admin hasn't installed: they'll see "Ask your org admin to install xbrain" — this is a feature, not a bug (org-level consent is the GitHub App security model).

### Q9 — auth_github.py reuse vs rewrite

Inspected `apps/memory-api/app/routes/auth_github.py` (361 lines).

**Reusable as-is (~60%):**
- `SigninGithubBody` / `SigninGithubOut` Pydantic models — same shape.
- `_resolve_or_merge_user` (130 lines) — identity merge logic unchanged. Same `github_id` PK.
- `_mint_xbt_for_user` — same xbt_ minting pattern.
- The route's overall structure (`signin_github` orchestration: code → profile → resolve → autograntq → mint).
- `_fetch_github_profile` — same `/user` + `/user/emails` + `/user/orgs` calls. **Behavior change Q1.1:** with GitHub App, `/user/orgs` returns only orgs that installed the App — but the call shape itself is identical.

**Must rewrite (~40%):**
- `_exchange_code_for_token` (lines 77-101) — change `settings.GITHUB_CLIENT_ID` → `settings.GITHUB_APP_CLIENT_ID` AND change the return type to a dict (include `refresh_token`, `expires_in`, `refresh_token_expires_in`). See "Exchange OAuth code → user token (modified Phase 10 helper)" example above.
- Add post-exchange persistence: store `access_token`, `refresh_token`, `expires_at`, `refresh_expires_at` on the user row (new columns in migration 0019).
- After identity resolve, add `check_installation_status_for_user(orgs)` step that returns `install_required: bool, install_url: Optional[str]`. Add these fields to `SigninGithubOut`.
- After auto-grant, conditionally do the installation-token-backed membership check (vs Phase 10's PAT-backed). Refactor `check_github_org_membership` in `auth.py` to take an installation token (or to look up the installation_id from org_login internally).

**Estimate :** ~150 lines of new code in `auth_github.py` + ~80 lines for new helpers in `app/services/github_app_jwt.py` and `app/services/github_installation.py`. The lift is moderate, not massive — most of Phase 10's logic transfers cleanly.

### Q10 — Test fixtures

The Phase 10 pattern (`tests/test_phase10_auth.py` `_configure_gh_router`) IS reusable. The new test fixtures only need to add:

1. A respx mock for the new `expires_in` + `refresh_token` keys in the token response.
2. A respx mock for the App-level `GET /orgs/{org}/installation` endpoint (returns `{"id": ...}` or 404).
3. A respx mock for `POST /app/installations/{id}/access_tokens` (returns `{"token": "ghs_...", "expires_at": "..."}`).
4. A respx mock for `GET /orgs/{org}/members/{username}` (returns 204 or 404).
5. A test PEM fixture (generated fresh per test session using `cryptography.hazmat.primitives.asymmetric.rsa.generate_private_key`) so we don't ship a committed test key.
6. A test for webhook signature verification (POST raw body with valid + invalid `X-Hub-Signature-256`).
7. A test for refresh token rotation (mock the refresh endpoint, assert old token is replaced).
8. A test for the install-required UX branch (orgs returned but no installation row → install_url in response).

See "Test fixture pattern" code example above for the verified scaffolding.

**Existing pattern files to extend:**
- `apps/memory-api/tests/test_phase10_auth.py` — copy the `_configure_gh_router` helper, rename to `_configure_github_app_router`, add the new mocks.
- `apps/memory-api/tests/conftest.py` — add the PEM fixture + monkeypatched env vars for App config.

**New test files (recommended):**
- `tests/test_phase12_jwt.py` — App JWT mint + verify the iss/iat/exp claims.
- `tests/test_phase12_installation_token.py` — cache hit, cache miss, 401 refresh.
- `tests/test_phase12_refresh_token.py` — rotation, expired token error, race-condition (lock).
- `tests/test_phase12_webhook.py` — signature valid/invalid, installation.created + .deleted dispatch.
- `tests/test_phase12_install_flow.py` — sign-in returns install_required + correct install_url.
- `tests/test_phase12_org_membership.py` — installation-token-backed check works, falls back when org not installed.

### Q11 — Multi-callback URL configuration (additional detail)

**GitHub App settings UI :** "Callback URL" field accepts MULTIPLE URLs (one per line). Add both:
- `https://grooveos.app/account/teams/`
- `https://<computed-ext-id>.chromiumapp.org/`

When the OAuth `authorize` request includes `&redirect_uri=...`, GitHub validates it matches one of the registered URLs (exact match OR prefix match — exact match is safer for production).

If `redirect_uri` is omitted, GitHub uses the FIRST registered URL. Don't rely on this — always send `redirect_uri` explicitly.

[CITED: https://github.com/orgs/community/discussions/54273 — "How to use the redirect_uri parameter in github apps with multiple callback urls"]

**Why this is the killer feature vs OAuth Apps :** OAuth Apps support only ONE callback URL. To serve both web + extension, Phase 5/8/10 had to choose ONE — which is exactly the "Chrome extension flow is broken" pain documented in CONTEXT.md entry gate. GitHub App fixes this nativement.

### Q12 — The three-token taxonomy (additional emphasis)

Documented exhaustively in Pattern 1 above. Repeated here because it's THE pitfall vector :

```
| Token             | Generation              | Auth use            | TTL    | Sample prefix      |
|-------------------|-------------------------|---------------------|--------|--------------------|
| App JWT           | mint_app_jwt(client_id, | Mint installation   | 10 min | (no prefix; JWT)   |
|                   |   pem)  [server-side]   | tokens, list app    |        |                    |
|                   |                         | installations       |        |                    |
| Installation token| POST /app/installations | Act on installation | 1 hour | ghs_...            |
|                   |   /{id}/access_tokens   | (org members read,  |        |                    |
|                   |   with App JWT          | repo writes, etc.)  |        |                    |
| User-to-server    | POST /login/oauth/      | Act as user         | 8 hours| ghu_... (+ ghr_... |
|                   |   access_token with     | (/user, /user/orgs) |        | for refresh)       |
|                   |   code or refresh_token |                     |        |                    |
```

The planner should require explicit type names in every helper. Linting could enforce via a `TokenType` `NewType` alias.

### Q13 — `installation` vs `installation_target` (additional detail)

| Event | When it fires | Action types | Subscribe? |
|-------|---------------|--------------|------------|
| `installation` | App installed, uninstalled, or permissions changed on an account | `created`, `deleted`, `suspend`, `unsuspend`, `new_permissions_accepted` | **YES** — primary signal for the installations table |
| `installation_repositories` | When repos are added/removed from an existing installation (only relevant if the App has repo permissions) | `added`, `removed` | YES per GHAPP-03 (even though we have no repo perms in v1 — subscribing is cheap and future-proofs) |
| `installation_target` | When the account hosting the install is renamed | (no action subtypes — the event itself signals) | OPTIONAL — only matters if we display org_login in the UI and want auto-rename. Defer to a later phase. |

[CITED: https://docs.github.com/en/webhooks/webhook-events-and-payloads]

**Plan instruction :** subscribe to `installation` + `installation_repositories` in the GitHub App settings UI. Do NOT subscribe to `installation_target` in v1.

### Q14 — Auth options for `/orgs/{org}/members/{username}`

Three valid ways to call this endpoint:

| Auth | Sees private members? | Requires installation? |
|------|----------------------|------------------------|
| User-to-server token, user is PUBLIC org member | yes (own membership) | no |
| User-to-server token, user is PRIVATE org member | only their own (the username being checked must == their own login, otherwise depends on requester's relation) | no |
| Installation token with **Members:Read** organization permission | yes — all members | **yes** (App must be installed on the org) |
| Classic PAT with `read:org` | yes — all members | no (current Phase 10 approach via `GITHUB_API_PAT`) |

**Phase 12 picks option 3 :** installation token + Members:Read. Reasons:
- Eliminates the long-lived PAT (security goal of Phase 12).
- Tied to org-level consent (admins control whether xbrain can see members).
- Rate limits are per-installation, scoped, much more generous than per-PAT.
- Cleanly handles the "org didn't install xbrain" case (Q8 install flow UX).

[CITED: https://docs.github.com/en/rest/orgs/members]

### Q15 — Refresh token rotation single-use (additional detail)

Verified from official docs : "Once you use a refresh token, that refresh token and the old user access token will no longer work."

[CITED: https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/refreshing-user-access-tokens]

Implications for plan:
1. **DB write atomicity** : update `github_access_token` + `github_refresh_token` + `github_token_expires_at` + `github_refresh_expires_at` in a SINGLE transaction. If only the access token is persisted and the refresh token is lost mid-write, the user is locked out until manual re-auth.
2. **Per-user lock** to prevent concurrent refresh attempts racing. See Pitfall 6.
3. **Failure handling** : when refresh returns `{"error": "bad_refresh_token"}` (HTTP 200 still), the user MUST be flagged as needing re-auth. Clear `github_access_token` + `github_refresh_token` on the user row to force the next call to redirect to OAuth.

### Q16 — Webhook URL gotcha for Cloudflare passthrough

The webhook endpoint `https://api.grooveos.app/v1/webhooks/github/installation` will go through Cloudflare (DNS) → VM nginx → memory-api Docker container. Three things to verify in the deploy plan:

1. nginx must NOT buffer or modify the POST body (default OK, but worth confirming `client_max_body_size` is adequate — webhooks are typically <100KB but `installation_repositories` with hundreds of repos can be larger).
2. Cloudflare passes through POST bodies by default — no special config needed unless rate limiting or WAF rules interfere.
3. The webhook secret + signature pattern means we DON'T need to allow-list GitHub's source IPs (see Pitfall 8).

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `app_id` (numeric) as JWT `iss` claim | `client_id` (string `Iv23li...`) as JWT `iss` claim | 2024-05-01 GitHub Changelog | [CITED: github.blog/changelog/2024-05-01] — both still work, but new GitHub Apps should use client_id (more stable identifier). |
| OAuth Apps | GitHub Apps | Industry trend ; OAuth Apps are not deprecated but GitHub explicitly recommends GitHub Apps for new integrations | [CITED: docs.github.com/en/apps/oauth-apps/building-oauth-apps/differences-between-github-apps-and-oauth-apps] |
| Long-lived user OAuth tokens | User-to-server tokens with 8h expiration + 6mo refresh | Built-in to GitHub Apps from launch | More secure ; aligns with OAuth2.1 / refresh-token best practices. |
| `GITHUB_API_PAT` (org-wide PAT for server-side checks) | Installation tokens (server-to-server, per-org, 1h TTL) | GitHub Apps' core model | No long-lived secret in env ; per-org admin consent. |

**Deprecated/outdated:**
- `expires_in: "28800"` returned as a STRING in some older GitHub doc examples ; current API returns it as a number. Code should `int(body["expires_in"])` defensively.
- Older docs reference `refresh_token_expires_in: 15811200` (=183 days). Current docs show `15897600` (=184 days). Use the value GitHub returns — don't hardcode the integer.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `python-jose` maintenance is less active than PyJWT | Standard Stack → Alternatives | Low — even if false, PyJWT is still the GitHub-docs-recommended choice. |
| A2 | With GitHub App, `GET /user/orgs` returns only orgs that installed the App | §Q1.1 | **Medium** — if false, the install-flow UX changes (we wouldn't need to prompt install for orgs that show up in the list). Verify with a real test call against a fresh installation before locking GHAPP-06 UX. |
| A3 | `GITHUB_ORG` env on VM = `dejavudev` (despite default `your-github-org` in config.py) | Runtime State Inventory | **Medium** — if the VM env is set to something else, the org-membership check will fail. The plan must include a step to verify the actual VM `.env` value before deploy. |
| A4 | mrboups is org admin of `dejavudev` and can self-install the App | §Q8 | Low — CONTEXT.md implies he's admin, but planner should confirm before deploy day. |
| A5 | Webhook delivery during normal operation has <0.1% loss | §Q3 reconciliation rationale | Low — even if loss is higher, the fallback in §Q3 self-heals on user sign-in. |
| A6 | The `cryptography` package in `pyproject.toml` (Phase 3) satisfies PyJWT[crypto]'s implicit dep | Standard Stack | Low — easy to verify with `pip install PyJWT[crypto]` in a fresh venv. |
| A7 | nginx on VM forwards POST bodies for webhook URL without buffering modifications | §Q16 | Low — standard nginx config does this. The verify-phase12.sh script should include a webhook signature roundtrip test. |

**Net assessment :** A2 and A3 are the two assumptions that warrant explicit verification in the planning phase (a "discovery task" early in the plan). The rest are low-risk.

## Open Questions

1. **Should we add a `github_install_state` column on `users` to remember which orgs the user has been prompted to install for?**
   - What we know : the install URL flow doesn't natively remember state across page loads.
   - What's unclear : whether the planner wants to silently retry the install-required check on every sign-in vs explicitly prompt once.
   - Recommendation : defer — the `installations` table tells us if an org is installed ; we don't need per-user install state. The sign-in flow checks `installations` every time, transparently.

2. **Should the `GITHUB_APP_PRIVATE_KEY` env be the PEM text inline, OR a path to a mounted file?**
   - What we know : `.env` files can contain multi-line values with care (quoting, escaping `\n`).
   - What's unclear : whether SOPS supports multi-line values cleanly. The Phase 1 secret handling pattern used `.env` directly without SOPS for some fields.
   - Recommendation : store as `GITHUB_APP_PRIVATE_KEY_B64` (base64-encoded single-line, decoded at load time). Avoids all multi-line headaches.

3. **For Pitfall 6 (concurrent refresh race), is the in-process lock sufficient given the deployment is single-instance memory-api?**
   - What we know : Phase 1 deployment is one memory-api container ; single asyncio event loop ; `dict[UUID, asyncio.Lock]` is correct.
   - What's unclear : whether xbrain ever spins up a second memory-api instance for failover.
   - Recommendation : in-process lock for v1 + add a TODO comment pointing to "switch to Postgres advisory lock if scaling to >1 instance".

4. **Should the new App's webhook events list include `installation_target` for org renames?**
   - What we know : `installation_target` fires on org rename ; xbrain stores `github_org_login` as text, so a rename would orphan the row.
   - What's unclear : how often `dejavudev` is likely to be renamed.
   - Recommendation : defer in v1. Add a TODO. Manual fix-up if it happens.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.12 | memory-api | ✓ | 3.12.x (per pyproject `requires-python`) | — |
| PostgreSQL 17 | `installations` table + new user columns | ✓ | 17.x in compose | — |
| FastAPI + httpx + SQLAlchemy async | All routes/services | ✓ | per pyproject pins | — |
| `cryptography` (RSA backend for PyJWT) | App JWT signing | ✓ | ≥42.0.0 (line 22 of pyproject) | — |
| `PyJWT[crypto]` | App JWT signing | ✗ | — | **Must add** to pyproject — see Standard Stack |
| `respx` | Test mocks | ✓ | ≥0.21 dev dep | — |
| openssl CLI | Generate Chrome ext keypair + manifest key | ✓ (assumed — universal on dev machines) | any recent | If unavailable on dev workstation, use Plasmo's web tool |
| `gh` CLI | Verify GitHub App settings during deploy | optional | — | Manual via web UI |
| nginx (on VM) | Reverse-proxy webhook endpoint | ✓ | (Phase 1 stack) | — |
| Cloudflare (DNS) | api.grooveos.app routing | ✓ | (existing) | — |
| Alembic 1.14+ | Migration 0019 | ✓ | per pyproject | — |

**Missing dependencies with no fallback :** None.

**Missing dependencies with fallback :** `PyJWT[crypto]` is missing but trivial to add ; not a blocker.

## Project Constraints (from CLAUDE.md)

- **Open-source + self-hostable only.** ✓ PyJWT is MIT; all deps OSS. GitHub Apps themselves are a free GitHub feature (no proprietary service).
- **App/code in English.** Plan must enforce: variable names, log messages, error strings, comments — all English. French only in user-facing UI strings on app-site and chrome extension (per existing Phase 10 banner pattern — see teams.js line 24+). The planner must keep new install-flow banners in English to match.
- **Multi-frontend invariant.** ✓ The GitHub App's multi-callback feature is the killer enabler for this constraint.
- **Tagging contract on every data point.** ✓ The `installations` table doesn't store brain content — it's auth metadata, exempt from the tagging contract. Same for `users.github_*` token columns.
- **GSD workflow enforcement.** Plans must use `/gsd:plan-phase 12` to generate sub-plans ; no direct edits outside the GSD flow.
- **Pre-implementation status.** WRONG per CLAUDE.md — actually 11 phases are LIVE per ROADMAP. CLAUDE.md is stale, ignore that line.

## Security Domain

ASVS categories applicable to Phase 12:

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | yes | GitHub App OAuth flow (well-defined spec). CSRF state token (already in Phase 10). |
| V3 Session Management | yes | xbt_ token TTL + revocation already in Phase 10. New: user GitHub token expiry handling. |
| V4 Access Control | yes | `team_members` + org-install-driven membership (existing model, extended). |
| V5 Input Validation | yes | Pydantic models for SigninGithubBody, webhook payloads. Validate `redirect_uri` is in the allowlist before exchange. |
| V6 Cryptography | yes | RS256 JWT signing — use PyJWT[crypto] (never hand-roll). HMAC-SHA256 webhook verify with `hmac.compare_digest`. |
| V8 Data Protection | yes | Encrypt `github_access_token` + `github_refresh_token` at rest if possible (Fernet — same key as drive-sync). Strong recommendation, not a hard blocker for v1 (DB-at-rest encryption at infra level is the alternative). |
| V9 Communication | yes | TLS everywhere ; HTTPS on grooveos.app + api.grooveos.app already enforced. |
| V10 Malicious Code | partial | Use only well-known PyJWT ; pin to `>=2.10,<3` ; lockfile via pip-tools if not already. |
| V14 Configuration | yes | Secrets in `.env`/SOPS, never committed. App private key (PEM) is the critical new secret — leak = anyone can mint App JWTs and impersonate xbrain to any org that installed it. |

### Known Threat Patterns for FastAPI + GitHub App

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Webhook spoofing | Spoofing | HMAC-SHA256 signature verification with `hmac.compare_digest` |
| Timing attack on HMAC compare | Information Disclosure | `hmac.compare_digest` (constant-time) — NEVER `==` |
| App private key leak | Elevation of Privilege (full takeover) | PEM in `.env` (gitignored) or SOPS-encrypted ; rotate via "Generate a new private key" in App settings |
| Refresh token theft → infinite access | Spoofing + Elevation | Refresh tokens single-use; rotation invalidates the stolen one once user logs in once (the "stolen but unused" window is ≤8h until access token expires) |
| Open redirect via `redirect_uri` | Spoofing | Server validates `redirect_uri` against allowlist OR relies on GitHub's allowlist (App settings — preferable). Phase 10's existing approach: trust GitHub's allowlist. |
| CSRF on OAuth callback | Tampering | `state` parameter validated client-side before POST to memory-api (Phase 10 pattern preserved). |
| Webhook replay attack | Tampering | GitHub webhooks include `X-GitHub-Delivery: <uuid>` and a timestamp ; optionally dedupe on delivery ID. v1: ignore (HMAC is enough — attacker can't forge signature). |
| Installation token over-privileged | Elevation of Privilege | Use the `permissions` field in installation token mint request to scope DOWN. v1: don't scope down (Members:Read is the only perm we need ; nothing to subset). |
| User-to-server token stolen from DB | Spoofing | Encrypt at rest (recommended) ; rotate on suspicious activity ; 8h TTL limits blast radius. |

## Sources

### Primary (HIGH confidence — official GitHub docs verified via WebFetch this session)

- [GitHub App JWT generation (RS256, 10-min max, iss=client_id)](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-json-web-token-jwt-for-a-github-app) — algorithm, claims, Python PyJWT example
- [GitHub App installation token endpoint (POST /app/installations/{id}/access_tokens, 1h TTL)](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/authenticating-as-a-github-app-installation) — endpoint shape, lookup endpoints
- [Refreshing GitHub App user access tokens (8h access + 6mo refresh, single-use rotation, "User-to-server token expiration" opt-in)](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/refreshing-user-access-tokens) — refresh flow + checkbox name
- [Validating GitHub webhook deliveries (X-Hub-Signature-256, hmac.compare_digest)](https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries) — Python example
- [Webhook events and payloads — installation vs installation_target](https://docs.github.com/en/webhooks/webhook-events-and-payloads) — action types
- [About the user authorization callback URL — multi-callback support](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/about-the-user-authorization-callback-url) — multi-URL behavior
- [Choosing permissions for a GitHub App](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/choosing-permissions-for-a-github-app) — permission categories
- [REST orgs/members endpoint reference](https://docs.github.com/en/rest/orgs/members) — 204/302/404 response semantics
- [PyJWT 2.12.1 on PyPI (released 2026-03-13)](https://pypi.org/project/PyJWT/) — version + `[crypto]` extra
- [Chrome manifest "key" field docs](https://developer.chrome.com/docs/extensions/reference/manifest/key) — purpose, format

### Secondary (MEDIUM confidence — community docs verified against Chromium source comments)

- [Plasmo guide to deterministic Chrome ext ID](https://www.plasmo.com/blog/posts/how-to-create-a-consistent-id-for-your-chrome-extension) — openssl one-liner, a-p alphabet rationale
- [GitHub Changelog — client_id usable in App JWT (2024-05-01)](https://github.blog/changelog/2024-05-01-github-apps-can-now-use-the-client-id-to-fetch-installation-tokens/) — iss claim accepts client_id
- [GitHub Community discussion #54273 — multi-callback URL behavior](https://github.com/orgs/community/discussions/54273) — redirect_uri matching behavior

### Tertiary (codebase inspection)

- `apps/memory-api/app/routes/auth_github.py` — Phase 10 reuse assessment
- `apps/memory-api/app/auth.py` — `check_github_org_membership` pattern (lines 130-187)
- `apps/memory-api/app/config.py` — settings structure
- `apps/memory-api/app/deps.py` — principal kinds + token branches
- `apps/memory-api/pyproject.toml` — current deps
- `apps/memory-api/alembic/versions/0018_brain_events_view.py` — latest migration (next = 0019)
- `apps/memory-api/tests/test_phase10_auth.py` — respx mock pattern
- `chrome-extension/manifest.json` — current MV3 manifest
- `chrome-extension/background.js` — current sign-in flow (line 63 GITHUB_CLIENT_ID)
- `app-site/account/teams/teams.js` — current web sign-in (line 34 GITHUB_CLIENT_ID)
- `.env.example` lines 167-285 — current Phase 5/10 env layout

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — PyJWT[crypto] is verified current on PyPI 2026-03-13.
- Architecture (3-token model + installations table + webhook + refresh): HIGH — all from official GitHub docs verified this session.
- Pitfalls (1-8): HIGH — sourced from official GitHub docs, codebase inspection, or well-documented community knowledge.
- Q1.1 behavior (GitHub App `/user/orgs` returning only installed orgs): MEDIUM — inferred from GitHub App permission model but not directly tested. **Flagged as Assumption A2 — verify before locking the install-flow UX in plan 06.**
- Q9 reuse estimate (60/40): MEDIUM — based on line-by-line read of auth_github.py ; depends on whether the planner extracts the full helper or rewrites in place.
- Chrome ext ID derivation algorithm: HIGH — Chromium source comments cross-referenced.

**Research date:** 2026-05-17
**Valid until:** 2026-06-16 (30 days — GitHub Apps spec is stable, no imminent breaking changes signalled in changelog as of research date).
