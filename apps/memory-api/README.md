# memory-api

xbrain memory-api — FastAPI service enforcing the 7-field tagging contract on every chat message, with team-scope isolation at the application layer.

## Dev local (sans Docker)

```bash
cd apps/memory-api
python -m venv .venv && source .venv/bin/activate  # ou .venv\Scripts\activate sur Windows
pip install -e ".[dev]"

# Lancer Postgres + Qdrant en local (depuis le racine du repo)
docker compose -f ../../infrastructure/docker-compose.yml --env-file ../../.env up -d postgres qdrant

cp .env.example .env  # éditer DATABASE_URL pour pointer sur localhost si tu ne tunnel pas
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

## Tests

```bash
pytest -v                    # tous les tests
pytest tests/test_tagging_contract.py -v
pytest --tb=short            # output compact
```

Les tests d'intégration utilisent `testcontainers-python` pour spinner un Postgres réel — Docker doit être dispo localement.

## Lint / format

```bash
ruff check .
ruff format .
```

## Production

L'image est build et déployée via le `docker-compose.yml` racine du repo (`infrastructure/docker-compose.yml`). Variables d'env injectées depuis `.env` racine. Voir le README de la racine pour le workflow de déploiement.

## OAuth 2.1 Authorization Server (Claude.ai connector)

memory-api also acts as the OAuth 2.1 Authorization Server behind the Claude.ai Custom
Connector. It serves these routes on the public API host, **un-prefixed** (not under `/v1`):

- `GET /.well-known/oauth-authorization-server` — AS metadata (RFC 8414): S256 PKCE,
  public-client auth method `none`, the `/oauth/*` endpoints, scopes `brain:read`/`brain:write`.
- `POST /oauth/register` — Dynamic Client Registration (RFC 7591).
- `GET|POST /oauth/authorize` — consent flow; signs the user in with GitHub (reuses the
  GitHub App) via `/oauth/github-callback` and binds the issued token to **one** team.
- `POST /oauth/token` — `authorization_code` + `refresh_token` grants, with PKCE and
  `resource` (audience) verification.
- `POST /oauth/introspect` — RFC 7662, gated by a constant-time `X-Internal-Secret`
  (`BRIDGE_SHARED_SECRET`); called by `mcp-brain` to validate `oat_` access tokens.

Storage is migration `0022_oauth_as_tables` (`oauth_clients`, `oauth_authorization_codes`,
`oauth_access_tokens`). The matching Protected Resource is `mcp-brain`, which exposes
`/.well-known/oauth-protected-resource` and the `/mcp` endpoint. Connector-originated writes
are forced to `source=claude.ai-connector`, capped at `truth_level=WORKING`, and scoped to the
bound team. Env: `OAUTH_ISSUER_URL`, `OAUTH_RESOURCE_URL`. End-user guide:
<https://grooveos.app/docs/claude-connector.html>.
