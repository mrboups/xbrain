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
