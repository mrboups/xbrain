#!/usr/bin/env bash
# brain-index.sh — Indexer le contenu d'un repo xbrain dans la mémoire collective.
#
# Usage (depuis la racine du repo, après un deploy) :
#   bash infrastructure/scripts/brain-index.sh
#
# Variables d'environnement requises :
#   XBRAIN_BRIDGE_JWT    — JWT de service signé avec BRIDGE_SHARED_SECRET
#
# Variables optionnelles :
#   MEMORY_API_URL       — URL de l'API xbrain (défaut: https://api.dejavu.cat)
#   GITHUB_REPOSITORY    — Owner/repo (ex: your-github-org/my-project, auto-détecté en CI)
#
# Dépendances : bash, curl, python3 (avec pyyaml — disponible dans ubuntu-latest)
#
# FAIL-SOFT : ce script retourne TOUJOURS exit 0.
# Le brain indexing est optionnel — ne doit jamais bloquer un deploy.
# Toute erreur est loggée et ignorée (T-05-03-04 accepted).

set -uo pipefail

MEMORY_API_URL="${MEMORY_API_URL:-https://api.dejavu.cat}"
XBRAIN_BRIDGE_JWT="${XBRAIN_BRIDGE_JWT:-}"
GITHUB_REPOSITORY="${GITHUB_REPOSITORY:-}"

BRAIN_YAML="${BRAIN_YAML_PATH:-brain.yaml}"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

log() {
  echo "[brain-index] $*"
}

log_error() {
  echo "[brain-index] ERROR: $*" >&2
}

# Toutes les sorties d'erreur fatales passent par ici → exit 0 (fail-soft)
fail_soft() {
  log_error "$*"
  log "Brain indexing skipped (fail-soft — deploy not affected)."
  exit 0
}

# ---------------------------------------------------------------------------
# Vérifications préalables
# ---------------------------------------------------------------------------

if [ ! -f "${BRAIN_YAML}" ]; then
  log "No brain.yaml found at '${BRAIN_YAML}' — nothing to index."
  exit 0
fi

if [ -z "${XBRAIN_BRIDGE_JWT}" ]; then
  fail_soft "XBRAIN_BRIDGE_JWT not set — cannot authenticate to xbrain API. Add XBRAIN_BRIDGE_JWT to GitHub Secrets."
fi

# Vérifier que python3 et pyyaml sont disponibles
python3 -c "import yaml" 2>/dev/null || fail_soft "python3 with pyyaml not found. Install: pip install pyyaml"

# ---------------------------------------------------------------------------
# Lire brain.yaml
# ---------------------------------------------------------------------------

log "Reading ${BRAIN_YAML}..."

# Extraire les champs via python3 (robuste avec les strings YAML)
read_yaml() {
  python3 -c "
import yaml, sys
try:
    with open('${BRAIN_YAML}') as f:
        d = yaml.safe_load(f)
    keys = '$1'.split('.')
    val = d
    for k in keys:
        if val is None:
            break
        val = val.get(k) if isinstance(val, dict) else None
    if val is None:
        print('')
    elif isinstance(val, list):
        # Liste → une valeur par ligne
        for item in val:
            print(str(item))
    else:
        print(str(val))
except Exception as e:
    print('', file=sys.stderr)
" 2>/dev/null
}

BRAIN_NAME=$(read_yaml "name")
BRAIN_TYPE=$(read_yaml "type")
BRAIN_SLUG=$(read_yaml "slug")
BRAIN_TEAM=$(read_yaml "team_scope")
BRAIN_PROJECT=$(read_yaml "project_scope")
BRAIN_ENRICH=$(read_yaml "brain.enrich")
BRAIN_TRUTH_LEVEL=$(read_yaml "brain.truth_level")
DEPLOY_TARGET=$(read_yaml "deploy.target")

# Appliquer les valeurs par défaut
BRAIN_PROJECT="${BRAIN_PROJECT:-$BRAIN_SLUG}"
BRAIN_TRUTH_LEVEL="${BRAIN_TRUTH_LEVEL:-EPHEMERAL}"
BRAIN_ENRICH="${BRAIN_ENRICH:-false}"

# Valider les champs requis
if [ -z "${BRAIN_NAME}" ] || [ -z "${BRAIN_TYPE}" ] || [ -z "${BRAIN_SLUG}" ] || [ -z "${BRAIN_TEAM}" ]; then
  fail_soft "brain.yaml missing required fields: name='${BRAIN_NAME}' type='${BRAIN_TYPE}' slug='${BRAIN_SLUG}' team_scope='${BRAIN_TEAM}'"
fi

if [ -z "${DEPLOY_TARGET}" ]; then
  fail_soft "brain.yaml missing required field: deploy.target"
fi

log "Project: ${BRAIN_NAME} (${BRAIN_TYPE}) — team: ${BRAIN_TEAM}, slug: ${BRAIN_SLUG}"

# ---------------------------------------------------------------------------
# Source GitHub repo pour le tagging
# ---------------------------------------------------------------------------

if [ -z "${GITHUB_REPOSITORY}" ]; then
  # Tenter de détecter depuis git remote
  GITHUB_REPOSITORY=$(git remote get-url origin 2>/dev/null | sed 's|.*github\.com[:/]||;s|\.git$||' || echo "unknown/unknown")
fi
BRAIN_SOURCE="github:${GITHUB_REPOSITORY}"

log "Source: ${BRAIN_SOURCE}"

# ---------------------------------------------------------------------------
# Si brain.enrich != true → sortir proprement
# ---------------------------------------------------------------------------

if [ "${BRAIN_ENRICH}" != "true" ] && [ "${BRAIN_ENRICH}" != "True" ]; then
  log "brain.enrich is not true — skipping content indexing."
  # Continuer quand même pour enregistrer le projet (si mcp_tool)
  # via la section suivante. Pour les autres types, on s'arrête ici.
  if [ "${BRAIN_TYPE}" != "mcp_tool" ]; then
    exit 0
  fi
fi

# ---------------------------------------------------------------------------
# POST /v1/admin/projects — enregistrer le projet dans la registry xbrain
# ---------------------------------------------------------------------------

log "Registering project in xbrain registry..."

PROJECTS_PAYLOAD=$(python3 -c "
import json
print(json.dumps({
    'slug': '${BRAIN_SLUG}',
    'name': '${BRAIN_NAME}',
    'url': 'https://${BRAIN_SLUG}.dejavu.cat',
    'team_scope': '${BRAIN_TEAM}',
    'project_scope': '${BRAIN_PROJECT}',
    'deploy_target': '${DEPLOY_TARGET}',
    'type': '${BRAIN_TYPE}',
}))
" 2>/dev/null)

if [ -n "${PROJECTS_PAYLOAD}" ]; then
  PROJECTS_HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST "${MEMORY_API_URL}/v1/admin/projects" \
    -H "Authorization: Bearer ${XBRAIN_BRIDGE_JWT}" \
    -H "Content-Type: application/json" \
    -d "${PROJECTS_PAYLOAD}" \
    --max-time 10 2>/dev/null) || PROJECTS_HTTP_CODE="000"

  if echo "${PROJECTS_HTTP_CODE}" | grep -qE "^(200|201|409)$"; then
    log "Project registered (HTTP ${PROJECTS_HTTP_CODE})"
  else
    log_error "Project registration failed (HTTP ${PROJECTS_HTTP_CODE}) — continuing anyway"
  fi
fi

# ---------------------------------------------------------------------------
# Indexer les fichiers de contenu si brain.enrich: true
# ---------------------------------------------------------------------------

if [ "${BRAIN_ENRICH}" = "true" ] || [ "${BRAIN_ENRICH}" = "True" ]; then
  # Lire la liste des fichiers à indexer
  CONTENT_FILES=$(python3 -c "
import yaml, sys
with open('${BRAIN_YAML}') as f:
    d = yaml.safe_load(f)
brain = d.get('brain', {}) or {}
content = brain.get('content', 'README.md')
if isinstance(content, list):
    for item in content:
        print(str(item))
else:
    print(str(content))
" 2>/dev/null) || CONTENT_FILES="README.md"

  [ -z "${CONTENT_FILES}" ] && CONTENT_FILES="README.md"

  log "Content files to index: $(echo "${CONTENT_FILES}" | tr '\n' ' ')"

  INDEXED_COUNT=0
  FAILED_COUNT=0

  while IFS= read -r FILE; do
    [ -z "${FILE}" ] && continue

    if [ ! -f "${FILE}" ]; then
      log_error "File not found: ${FILE} — skipping"
      FAILED_COUNT=$((FAILED_COUNT + 1))
      continue
    fi

    log "Indexing: ${FILE}..."
    FILE_CONTENT=$(cat "${FILE}" 2>/dev/null) || FILE_CONTENT=""

    if [ -z "${FILE_CONTENT}" ]; then
      log_error "Empty or unreadable file: ${FILE} — skipping"
      FAILED_COUNT=$((FAILED_COUNT + 1))
      continue
    fi

    # Construire le payload JSON avec python3 pour gérer l'échappement correctement
    PAYLOAD=$(python3 -c "
import json, sys
content = open('${FILE}').read()
payload = {
    'item': {
        'content': content,
        'team_scope': '${BRAIN_TEAM}',
        'project_scope': '${BRAIN_PROJECT}',
        'visibility': 'team',
        'confidence': 1.0,
        'truth_level': '${BRAIN_TRUTH_LEVEL}',
        'source': '${BRAIN_SOURCE}:${FILE}',
        'validation_status': 'pending',
    }
}
print(json.dumps(payload))
" 2>/dev/null) || PAYLOAD=""

    if [ -z "${PAYLOAD}" ]; then
      log_error "Failed to build payload for ${FILE} — skipping"
      FAILED_COUNT=$((FAILED_COUNT + 1))
      continue
    fi

    # POST vers memory-api
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
      -X POST "${MEMORY_API_URL}/v1/memory/upsert" \
      -H "Authorization: Bearer ${XBRAIN_BRIDGE_JWT}" \
      -H "X-Team-Scope: ${BRAIN_TEAM}" \
      -H "Content-Type: application/json" \
      -d "${PAYLOAD}" \
      --max-time 15 2>/dev/null) || HTTP_CODE="000"

    if echo "${HTTP_CODE}" | grep -qE "^(200|201)$"; then
      log "Indexed: ${FILE} (HTTP ${HTTP_CODE})"
      INDEXED_COUNT=$((INDEXED_COUNT + 1))
    else
      log_error "Failed to index ${FILE} (HTTP ${HTTP_CODE}) — continuing"
      FAILED_COUNT=$((FAILED_COUNT + 1))
    fi
  done <<< "${CONTENT_FILES}"

  log "Brain indexing complete: ${INDEXED_COUNT} indexed, ${FAILED_COUNT} failed"
fi

# ---------------------------------------------------------------------------
# Si type: mcp_tool → auto-enregistrer dans le gateway MCP
# ---------------------------------------------------------------------------

if [ "${BRAIN_TYPE}" = "mcp_tool" ]; then
  log "type=mcp_tool — registering in MCP gateway..."

  # Détecter l'URL Cloud Run du service (si disponible depuis env)
  MCP_SIDECAR_URL="${MCP_SIDECAR_URL:-}"

  if [ -z "${MCP_SIDECAR_URL}" ]; then
    # Tenter de détecter depuis gcloud si disponible
    MCP_SIDECAR_URL=$(gcloud run services describe "${BRAIN_SLUG}" \
      --region europe-west1 \
      --project xbrain-495115 \
      --format='value(status.url)' 2>/dev/null) || MCP_SIDECAR_URL=""
  fi

  if [ -z "${MCP_SIDECAR_URL}" ]; then
    log_error "MCP_SIDECAR_URL not set and could not detect Cloud Run URL — skipping MCP registration"
  else
    REGISTER_PAYLOAD=$(python3 -c "
import json
print(json.dumps({
    'tool_name': '${BRAIN_SLUG}',
    'sidecar_url': '${MCP_SIDECAR_URL}',
    'description': '${BRAIN_NAME} (auto-registered by brain-index.sh)',
}))
" 2>/dev/null)

    if [ -n "${REGISTER_PAYLOAD}" ]; then
      REGISTER_HTTP=$(curl -s -o /dev/null -w "%{http_code}" \
        -X POST "${MEMORY_API_URL}/v1/tools/register" \
        -H "Authorization: Bearer ${XBRAIN_BRIDGE_JWT}" \
        -H "Content-Type: application/json" \
        -d "${REGISTER_PAYLOAD}" \
        --max-time 10 2>/dev/null) || REGISTER_HTTP="000"

      if echo "${REGISTER_HTTP}" | grep -qE "^(200|201|409)$"; then
        log "MCP tool registered: ${BRAIN_SLUG} → ${MCP_SIDECAR_URL} (HTTP ${REGISTER_HTTP})"
      else
        log_error "MCP tool registration failed (HTTP ${REGISTER_HTTP}) — continuing anyway"
      fi
    fi
  fi
fi

# ---------------------------------------------------------------------------
# Fin — toujours exit 0 (fail-soft)
# ---------------------------------------------------------------------------

log "Done."
exit 0
