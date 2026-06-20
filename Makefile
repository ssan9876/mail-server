# ============================================================================
# mail-server — developer task runner
# ============================================================================
.DEFAULT_GOAL := help
COMPOSE := docker compose
COMPOSE_PROD := docker compose -f docker-compose.yml

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# --- Lifecycle (dev) -------------------------------------------------------
.PHONY: up
up: ## Build + start the full stack (dev, with overrides)
	$(COMPOSE) up -d --build

.PHONY: down
down: ## Stop and remove containers
	$(COMPOSE) down

.PHONY: restart
restart: down up ## Restart the stack

.PHONY: logs
logs: ## Tail logs from all services
	$(COMPOSE) logs -f --tail=100

.PHONY: ps
ps: ## Show service status
	$(COMPOSE) ps

# --- Lifecycle (prod) ------------------------------------------------------
.PHONY: prod-up
prod-up: ## Start the stack in production mode (no dev overrides)
	$(COMPOSE_PROD) up -d --build

.PHONY: prod-down
prod-down: ## Stop the production stack
	$(COMPOSE_PROD) down

# --- Backend ---------------------------------------------------------------
.PHONY: migrate
migrate: ## Apply Alembic migrations (Step 2+)
	$(COMPOSE) exec backend alembic upgrade head

.PHONY: makemigration
makemigration: ## Autogenerate a migration: make makemigration m="message"
	$(COMPOSE) exec backend alembic revision --autogenerate -m "$(m)"

.PHONY: test
test: ## Run backend test suite
	$(COMPOSE) exec backend pytest -q

.PHONY: lint
lint: ## Lint + type-check backend
	$(COMPOSE) exec backend ruff check .
	$(COMPOSE) exec backend mypy app

.PHONY: shell
shell: ## Open a shell in the backend container
	$(COMPOSE) exec backend bash

# --- Utilities -------------------------------------------------------------
.PHONY: psql
psql: ## Open psql in the postgres container
	$(COMPOSE) exec postgres psql -U $${POSTGRES_USER:-mailserver} -d $${POSTGRES_DB:-mailserver}

.PHONY: env
env: ## Create .env from the example if missing
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example — edit it before `make up`.")

.PHONY: config
config: ## Render and validate the merged compose config
	$(COMPOSE) config

.PHONY: backup
backup: ## Dump the database to backup.sql
	$(COMPOSE) exec -T postgres pg_dump -U $${POSTGRES_USER:-mailserver} $${POSTGRES_DB:-mailserver} > backup.sql
	@echo "Wrote backup.sql"

.PHONY: certs
certs: ## Issue Let's Encrypt certificates (set WEB_HOSTNAME/MAIL_HOSTNAME in .env)
	$(COMPOSE) run --rm certbot certonly --webroot -w /var/www/acme \
		-d $${WEB_HOSTNAME} -d $${MAIL_HOSTNAME} \
		--email $${ACME_EMAIL} --agree-tos --no-eff-email
	$(COMPOSE) restart nginx postfix dovecot
