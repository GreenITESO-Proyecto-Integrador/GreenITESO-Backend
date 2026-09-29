.PHONY: start start-asgi migrate migrate-direct makemigrations-check test lint gunicorn gunicorn-asgi

ENV_FILE ?= ../.env
ENV_PATH := $(abspath $(ENV_FILE))
DJANGO = if [ -z "$$DJANGO_ENV" ]; then set -a; . $(ENV_PATH); set +a; fi; python manage.py

# --- Development Servers ---

# Standard WSGI development server
start:
	@$(DJANGO) runserver 0.0.0.0:8000

# Local ASGI development server with auto-reload
start-asgi:
	@if [ -z "$$DJANGO_ENV" ]; then set -a; . $(ENV_PATH); set +a; fi; uvicorn green_iteso.asgi:application --host 0.0.0.0 --port $${PORT:-8000} --reload

# --- Production Servers ---

# Production ASGI server (Gunicorn process manager + Uvicorn async workers)
gunicorn-asgi:
	@$(DJANGO) check --deploy
	@if [ -z "$$DJANGO_ENV" ]; then set -a; . $(ENV_PATH); set +a; fi; gunicorn green_iteso.asgi:application \
		-k uvicorn.workers.UvicornWorker \
		--bind 0.0.0.0:$${PORT:-8000} \
		--workers $${WEB_CONCURRENCY:-2} \
		--timeout $${GUNICORN_TIMEOUT:-60}

# Default production alias pointing to the ASGI runner
gunicorn: gunicorn-asgi

# --- Database & Migrations ---

migrate:
	@$(DJANGO) migrate

migrate-direct:
	@if [ -z "$$DJANGO_ENV" ]; then set -a; . $(ENV_PATH); set +a; fi; DATABASE_URL="$${DATABASE_URL_UNPOOLED:?DATABASE_URL_UNPOOLED is required for migrations}" DJANGO_CONNECTION_ROLE=direct python manage.py migrate

makemigrations-check:
	@$(DJANGO) makemigrations --check --dry-run

# --- Testing & Quality ---

test:
	@$(DJANGO) check
	@cd .. && if [ -z "$$DJANGO_ENV" ]; then set -a; . $(ENV_PATH); set +a; fi; pytest

lint:
	cd .. && ruff check app/
	cd .. && ruff format --check app/