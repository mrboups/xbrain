# xbrain

> Mémoire collective persistante pour humains + agents, organisée par équipe et par niveau de vérité. **Pas un workspace de chatbot** — le différenciateur est la couche mémoire + truth-level + team-scope, pas l'interface.

## État

- **Phase 1** : Socle infra + frontends (LibreChat + Open WebUI) + memory-api Python avec contrat de tagging 7 champs.
- Architecture cible : voir [`CLAUDE.md`](./CLAUDE.md) et [`.planning/PROJECT.md`](./.planning/PROJECT.md).

## Quickstart Phase 1

### Pré-requis

- Une VM Ubuntu 24.04 avec Docker + Docker Compose installés (cf. memory `project_xbrain_phase1_infra.md`)
- Compte Google Cloud avec OAuth client configuré (cf. [Configuration Google OAuth](#configuration-google-oauth))
- Clé SSH pour la VM accessible localement

### Setup local

```bash
git clone https://github.com/mrboups/xbrain.git
cd xbrain
cp .env.example .env
# Éditer .env : remplacer tous les __FILL__ par les vraies valeurs (voir section secrets ci-dessous)
make env-check    # vérifie qu'aucun secret critique ne manque
```

### Génération des secrets

```bash
# Pour chaque __FILL_RANDOM_32_CHARS__ :
openssl rand -base64 32

# Pour chaque __FILL_RANDOM_64_CHARS__ :
openssl rand -base64 48

# Pour chaque __FILL_RANDOM_64_HEX__ :
openssl rand -hex 32

# Pour __FILL_RANDOM_32_HEX__ :
openssl rand -hex 16
```

### Configuration Google OAuth

1. https://console.cloud.google.com → projet `xbrain-495115` → APIs & Services → Credentials
2. Create Credentials → OAuth client ID → Web application
3. Authorized redirect URIs (les deux) :
   - `http://__VM_HOST__/oauth/google/callback`
   - `http://__VM_HOST__/openwebui/oauth/google/callback`
4. Copier `Client ID` → `GOOGLE_CLIENT_ID` et `OAUTH_GOOGLE_CLIENT_ID` dans `.env`
5. Copier `Client secret` → `GOOGLE_CLIENT_SECRET` et `OAUTH_GOOGLE_CLIENT_SECRET`

### Déploiement sur la VM

```bash
make sync       # rsync code vers la VM (sans build)
make deploy     # build + up -d sur la VM
make vm-ps      # vérifier que tous les containers sont healthy
make vm-logs    # tail logs si quelque chose ne démarre pas
```

Une fois déployé : http://__VM_HOST__

- LibreChat à la racine : http://__VM_HOST__/
- Open WebUI : http://__VM_HOST__/openwebui/
- memory-api : http://__VM_HOST__/api/v1/healthz

### Tests

```bash
make test       # tests memory-api (pytest + testcontainers)
make lint       # ruff sur les 3 services Python
```

### Backup / Restore (success criterion 5)

```bash
make backup           # backup manuel vers GCS bucket xbrain-backups-prod
make restore-test     # test E2E restore sur env clean (mandatory gate Phase 1 done)
```

## Structure du repo

```
.
├── apps/                       # Services applicatifs Python
│   ├── memory-api/             # FastAPI core (plan 01-02)
│   ├── librechat-bridge/       # Sidecar MongoDB → memory-api (plan 01-03)
│   └── openwebui-pipeline/     # Pipeline Open WebUI → memory-api (plan 01-04)
├── services/                   # Réservé Phase 3 (scraper, calendar, drive-sync)
├── packages/                   # Schémas partagés (Phase 2+)
│   └── schemas/
├── infrastructure/             # Docker Compose, nginx, scripts deploy
│   ├── docker-compose.yml
│   ├── nginx/conf.d/
│   └── scripts/
├── .planning/                  # GSD planning artifacts (PROJECT.md, ROADMAP.md, phases/)
└── .claude/                    # GSD toolchain
```

## Commandes utiles

```bash
make help         # liste tous les targets
make ssh          # SSH interactive sur la VM
make vm-down      # arrêter le stack (volumes préservés)
```

## Documentation

- Architecture & contraintes : [`CLAUDE.md`](./CLAUDE.md)
- Plan complet : [`.planning/ROADMAP.md`](./.planning/ROADMAP.md)
- Recherche Phase 1 : [`.planning/phases/01-socle-infra-frontends-memory-api/01-RESEARCH.md`](./.planning/phases/01-socle-infra-frontends-memory-api/01-RESEARCH.md)

## Licence

À définir (probablement MIT ou Apache 2.0).
