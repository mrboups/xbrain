SHELL := /bin/bash
.DEFAULT_GOAL := help

# Charger .env si présent (silencieux si absent)
-include .env
export

# === Variables ===
COMPOSE := docker compose -f infrastructure/docker-compose.yml --env-file .env
VM_HOST ?= __VM_HOST__
VM_USER ?= user
SSH_KEY ?= $$HOME/.ssh/xbrain_key
SSH := ssh -i $(SSH_KEY) -o BatchMode=yes $(VM_USER)@$(VM_HOST)
RSYNC := rsync -avz --delete --exclude='.git' --exclude='node_modules' --exclude='__pycache__' --exclude='.venv' --exclude='backups'

# === Help ===
.PHONY: help
help:  ## Affiche cette aide
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# === Local dev ===
.PHONY: build
build:  ## Build toutes les images Docker localement
	$(COMPOSE) build

.PHONY: up
up:  ## docker compose up -d (local)
	$(COMPOSE) up -d

.PHONY: down
down:  ## docker compose down (local)
	$(COMPOSE) down

.PHONY: logs
logs:  ## Tail logs de tous les services (local)
	$(COMPOSE) logs -f --tail=100

.PHONY: ps
ps:  ## Liste containers + healthchecks
	$(COMPOSE) ps

# === Tests ===
.PHONY: test
test:  ## Lance les tests memory-api
	cd apps/memory-api && pytest -v

.PHONY: lint
lint:  ## Lint Python (ruff)
	cd apps/memory-api && ruff check .
	cd apps/librechat-bridge && ruff check .
	cd apps/openwebui-pipeline && ruff check .

.PHONY: fmt
fmt:  ## Format Python (ruff format)
	cd apps/memory-api && ruff format .
	cd apps/librechat-bridge && ruff format .
	cd apps/openwebui-pipeline && ruff format .

# === Deploy VM ===
.PHONY: sync
sync:  ## Rsync code vers la VM (sans deploy)
	$(RSYNC) ./ $(VM_USER)@$(VM_HOST):/home/$(VM_USER)/xbrain/

.PHONY: deploy
deploy: env-check preflight sync  ## Sync + (re)build + up sur la VM
	@# Phase 14 remote guard — env-check only reads the LOCAL .env; the VM .env is
	@# the one that actually boots the containers, and project memory
	@# (project_xbrain_vm_env_gotchas) confirms VM .env vars go missing. Check the
	@# SAME 5 now-mandatory vars remotely before syncing a config that would
	@# crashloop memory-api/mcp-brain, break ingress, block CORS, or kill @mentions.
	$(SSH) 'cd /home/$(VM_USER)/xbrain && for v in OAUTH_ISSUER_URL OAUTH_RESOURCE_URL CORS_ALLOWED_ORIGIN_REGEX XBRAIN_BASE_DOMAIN AGENT_MENTION_ALIASES; do grep -qE "^$$v=.+" .env || { echo "ABORT: VM .env is missing $$v"; exit 1; }; done' || (echo "ABORT: the VM .env is missing a now-mandatory var — deploying would crashloop memory-api/mcp-brain, or silently break the ingress / CORS / @mentions. See 14-06-SUMMARY.md DEPLOY-PREREQ."; exit 1)
	$(SSH) 'cd /home/$(VM_USER)/xbrain && docker compose -f infrastructure/docker-compose.yml --env-file .env up -d --build'

.PHONY: vm-logs
vm-logs:  ## Tail logs sur la VM
	$(SSH) 'cd /home/$(VM_USER)/xbrain && docker compose -f infrastructure/docker-compose.yml logs -f --tail=100'

.PHONY: vm-ps
vm-ps:  ## ps sur la VM
	$(SSH) 'cd /home/$(VM_USER)/xbrain && docker compose -f infrastructure/docker-compose.yml ps'

.PHONY: vm-down
vm-down:  ## Arrête le stack sur la VM (sans détruire les volumes)
	$(SSH) 'cd /home/$(VM_USER)/xbrain && docker compose -f infrastructure/docker-compose.yml down'

.PHONY: ssh
ssh:  ## Ouvre une session SSH interactive sur la VM
	ssh -i $(SSH_KEY) $(VM_USER)@$(VM_HOST)

# === Backup / Restore ===
.PHONY: backup
backup:  ## Lance un backup manuel (cf. plan 01-06)
	$(SSH) 'cd /home/$(VM_USER)/xbrain && docker compose -f infrastructure/docker-compose.yml exec -T xbrain-backup /scripts/backup.sh'

.PHONY: restore-test
restore-test:  ## Test restore sur env clean (cf. plan 01-06, success criterion 5)
	@echo "Voir infrastructure/scripts/restore-test.sh"
	bash infrastructure/scripts/restore-test.sh

# === Utilities ===
.PHONY: env-check
env-check:  ## Vérifie que toutes les vars critiques sont dans .env
	@bash -c 'source .env 2>/dev/null && for v in POSTGRES_PASSWORD GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET BRIDGE_SHARED_SECRET MEILI_MASTER_KEY OPENWEBUI_SECRET_KEY OAUTH_ISSUER_URL OAUTH_RESOURCE_URL CORS_ALLOWED_ORIGIN_REGEX XBRAIN_BASE_DOMAIN AGENT_MENTION_ALIASES; do \
		if [ -z "$${!v}" ]; then echo "MISSING: $$v — see .planning/phases/14-portability-foundation/14-06-SUMMARY.md (DEPLOY-PREREQ)"; exit 1; fi; \
	done && echo "All required env vars present."'

.PHONY: preflight
preflight:  ## Pre-deploy crashloop guard — same 5 vars as env-check, actionable messages (B3)
	@bash infrastructure/scripts/preflight-env.sh .env
