.PHONY: init-local compose-up compose-down compose-logs migrate migrate-direct db-smoke makemigrations-check test lint runDev

ENV_FILE ?= .env
COMPOSE_BIN ?= $(shell if docker compose version >/dev/null 2>&1; then echo "docker compose"; else echo docker-compose; fi)

init-local:
	@test -f $(ENV_FILE) || cp .env.example $(ENV_FILE)
	@echo "Local environment is ready in $(ENV_FILE). Review disposable values before use."

compose-up: init-local
	$(COMPOSE_BIN) up --build

compose-down:
	$(COMPOSE_BIN) down

compose-logs:
	$(COMPOSE_BIN) logs -f app db

migrate:
	$(COMPOSE_BIN) exec -T app python app/manage.py migrate --no-input

migrate-direct:
	@set -a; . $(ENV_FILE); set +a; export DATABASE_URL="postgresql://$$POSTGRES_USER:$$POSTGRES_PASSWORD@db:5432/$$POSTGRES_DB"; $(COMPOSE_BIN) exec -T -e DATABASE_URL="$$DATABASE_URL" -e DJANGO_CONNECTION_ROLE=direct app python app/manage.py migrate --no-input

db-smoke:
	$(COMPOSE_BIN) exec -T app python app/manage.py db_smoke --timeout 5

makemigrations-check:
	$(COMPOSE_BIN) exec -T app python app/manage.py makemigrations --check --dry-run

test:
	$(COMPOSE_BIN) exec -T app pytest app/tests

lint:
	ruff check app/

runDev: compose-up
