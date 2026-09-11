.PHONY: start migrate migrate-direct makemigrations-check test lint gunicorn

ENV_FILE ?= ../.env
ENV_PATH := $(abspath $(ENV_FILE))
DJANGO = if [ -z "$$DJANGO_ENV" ]; then set -a; . $(ENV_PATH); set +a; fi; python manage.py

start:
	@$(DJANGO) runserver 0.0.0.0:8000

gunicorn:
	@$(DJANGO) check --deploy
	@if [ -z "$$DJANGO_ENV" ]; then set -a; . $(ENV_PATH); set +a; fi; gunicorn green_iteso.wsgi:application --bind 0.0.0.0:$${PORT:-8000} --workers $${WEB_CONCURRENCY:-2} --threads $${GUNICORN_THREADS:-2} --timeout $${GUNICORN_TIMEOUT:-60}

migrate:
	@$(DJANGO) migrate

migrate-direct:
	@if [ -z "$$DJANGO_ENV" ]; then set -a; . $(ENV_PATH); set +a; fi; DATABASE_URL="$${DATABASE_URL_UNPOOLED:?DATABASE_URL_UNPOOLED is required for migrations}" DJANGO_CONNECTION_ROLE=direct python manage.py migrate

makemigrations-check:
	@$(DJANGO) makemigrations --check --dry-run

test:
	@$(DJANGO) check
	@cd .. && if [ -z "$$DJANGO_ENV" ]; then set -a; . $(ENV_PATH); set +a; fi; pytest

lint:
	ruff check green_iteso tests
