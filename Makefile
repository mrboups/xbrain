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
	@# Phase 14 remote guard, Phase 15 generalised: `preflight` above only reads the LOCAL .env, but
	@# the VM .env is the one that actually boots the containers, and project memory
	@# (project_xbrain_vm_env_gotchas) confirms VM .env vars go missing. `sync` has just pushed the
	@# current preflight-env.sh to the VM, so run THAT script against the VM's OWN .env — ONE
	@# implementation of the rule, enforced on both sides, instead of a second inline copy that can
	@# drift from the first. It checks the 5 now-mandatory vars AND the Phase 15
	@# COMPOSE_PROFILES/EDITION invariant (saas profile + EDITION=oss => session-bridge 404s silently).
	$(SSH) 'cd /home/$(VM_USER)/xbrain && bash infrastructure/scripts/preflight-env.sh .env' \
	  || (echo "ABORT: the VM .env failed preflight — see the FATAL message above. Deploying would crashloop memory-api/mcp-brain, silently break ingress/CORS/@mentions, or 404 session-bridge's register frame."; exit 1)
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
.PHONY: oss-init
oss-init:  ## Generate a zero-external-key .env for the OSS-light core
	@bash infrastructure/scripts/oss-init.sh $(ARGS)

.PHONY: env-check
env-check:  ## Vérifie que toutes les vars critiques sont dans .env (saas creds gated behind COMPOSE_PROFILES=saas)
	@# CORE = always required (boot-fatal). SAAS creds (GOOGLE_*/MEILI_MASTER_KEY/OPENWEBUI_SECRET_KEY)
	@# are required ONLY when COMPOSE_PROFILES contains `saas`, so a zero-external-key install passes
	@# `make deploy` (D-16-01 / SC#4). Uses make's own $(COMPOSE_PROFILES) for deterministic precedence
	@# Use `make env-check COMPOSE_PROFILES=saas` — a command-line VARIABLE ASSIGNMENT, which is the
	@# only form that beats the `-include .env` value. The env-var form (`COMPOSE_PROFILES=saas make
	@# env-check`) does NOT: a makefile assignment overrides the environment, so .env's empty value
	@# would win and the saas branch would silently not fire.
	@bash -c 'source .env 2>/dev/null; \
		REQ="POSTGRES_PASSWORD BRIDGE_SHARED_SECRET OAUTH_ISSUER_URL OAUTH_RESOURCE_URL CORS_ALLOWED_ORIGIN_REGEX XBRAIN_BASE_DOMAIN AGENT_MENTION_ALIASES"; \
		case ",$(COMPOSE_PROFILES)," in *,saas,*) REQ="$$REQ GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET MEILI_MASTER_KEY OPENWEBUI_SECRET_KEY";; esac; \
		for v in $$REQ; do \
			if [ -z "$${!v}" ]; then echo "MISSING: $$v (required for profile [$(COMPOSE_PROFILES)]; GOOGLE_*/MEILI_MASTER_KEY/OPENWEBUI_SECRET_KEY are needed only under the saas profile)"; exit 1; fi; \
		done; \
		echo "All required env vars present (profile: [$(COMPOSE_PROFILES)])."'

.PHONY: preflight
preflight:  ## Pre-deploy crashloop guard — same 5 vars as env-check, actionable messages (B3)
	@bash infrastructure/scripts/preflight-env.sh .env

.PHONY: verify-phase15
verify-phase15:  ## Phase 15 acceptance gate — compose-layer + live-boot (EDIT-01 + EDIT-02)
	@bash infrastructure/scripts/verify-phase15.sh

.PHONY: verify-phase18
verify-phase18:  ## Phase 18 acceptance gate — local auth real-Postgres + live zero-OAuth boot (LAUTH-01 + LAUTH-02)
	@bash infrastructure/scripts/verify-phase18.sh

.PHONY: verify-phase16
verify-phase16:  ## Phase 16 acceptance gate — clean-install: real core boot + SC#3 HTTP walk
	@bash infrastructure/scripts/verify-phase16.sh

# Phase 17 (CI lockstep). These cover what is provable WITHOUT a GitHub-Actions run: the
# pipeline is well-formed and correctly wired, and migrations apply under both editions.
# They do NOT prove the workflow runs — see docs/ci-lockstep.md for the residual.
.PHONY: verify-phase17-workflow
verify-phase17-workflow:  ## Phase 17 gate — actionlint (SKIP=FAIL) + the SC3 needs-graph proof
	@bash infrastructure/scripts/verify-phase17-workflow.sh

.PHONY: verify-phase17-full
verify-phase17-full:  ## Phase 17 gate — full-profile graph + GHCR override completeness (daemon-free)
	@bash infrastructure/scripts/verify-phase17-full.sh

.PHONY: verify-phase17-migrations
verify-phase17-migrations:  ## Phase 17 gate — alembic upgrade head under EDITION=oss AND saas (needs Docker)
	@cd apps/memory-api && pytest -m integration tests/test_migration_editions.py -v

.PHONY: verify-phase17
verify-phase17:  ## Phase 17 acceptance gate — the locally-verifiable set: workflow + full + migrations
	@$(MAKE) verify-phase17-workflow
	@$(MAKE) verify-phase17-full
	@$(MAKE) verify-phase17-migrations
