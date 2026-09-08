
# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

GreenITESO Backend is a Django-based REST API project using Python 3.14. Currently, it has a minimal Django configuration with in-memory settings and a single index view as the entry point. The project uses Docker + devcontainer for consistent development environments and enforces code quality through Ruff linting and Conventional Commits via CI/CD.

## Directory Structure

```
├── app/                          # Main application directory
│   ├── GreenITESO/              # Django application package
│   │   ├── __init__.py
│   │   └── main.py              # Django entry point with in-memory config
│   ├── Makefile                 # Development commands
│   ├── requirements.txt          # Python dependencies
│   └── pyproject.toml            # Ruff linting configuration
├── .devcontainer/                # VS Code dev container config
├── .github/workflows/            # CI/CD pipelines
├── .dockerignore                 # Exclude files from Docker builds
├── .gitignore                    # Exclude files from version control
├── Dockerfile                    # Docker image for containerized dev
└── Makefile                      # Root-level dev commands
```

## Common Development Commands

### Local Development (Docker + Devcontainer)

```sh
# Run dev server via Make (builds Docker image and runs container)
make -C . runDev

# Or directly inside container/venv:
make -C app start     # Runs: python GreenITESO/main.py runserver 0.0.0.0:8000
```

**Note:** The Dockerfile mounts `/app` to `/workspace` and forwards port 8000. Dev server runs at `http://localhost:8000`.

### Code Quality

```sh
# Run Ruff linter (static analysis, import sorting, type hints validation)
ruff check app/

# Format code with Ruff
ruff format app/

# Run tests (pytest configured in requirements.txt, but no tests/ dir yet)
pytest app/
```

### Django Management

The project uses in-memory Django configuration (no `manage.py` or `settings.py` files). All Django setup is in [app/GreenITESO/main.py](app/GreenITESO/main.py). To add Django management commands, wrap them via the same `execute_from_command_line()` pattern.

Example:
```sh
python GreenITESO/main.py createsuperuser
python GreenITESO/main.py migrate
```

## Code Quality Rules (Ruff)

Strict linting configuration enforced via [app/pyproject.toml](app/pyproject.toml):

- **Type hints required** on all function signatures (ANN rules) — exceptions: `self`, `cls` parameters, and explicit `Any` usage allowed
- **PEP 8 naming** — `PascalCase` for classes, `snake_case` for functions/variables, `UPPER_CASE` for constants
- **Django linting** (DJ rules) active
- **Import sorting** via isort (I rules)
- **No bare except** blocks (explicit `Exception:` or specific types required)
- **Bugbear checks** enabled (B rules) for common Python mistakes

Line length limit disabled (formatter manages it). Check [app/pyproject.toml](app/pyproject.toml) for full exclusions.

## Version Control & Docker

### .gitignore
Excludes Python cache, venv, IDE files, env secrets, OS files, logs from Git. Committed to repo — team uses same rules.

### .dockerignore
Optimizes Docker builds by excluding git, docs, IDE config, dev container. Reduces image size and build time.

## CI/CD Pipeline

Runs on push/PR via [.github/workflows/precommit.yaml](.github/workflows/precommit.yaml):

1. **Commit lint**: PR titles must follow Conventional Commits (feat:, fix:, docs:, etc.). Scope optional.
2. **Ruff lint**: Static analysis on `app/` directory. Must pass before merge.

Enforce locally with pre-commit hooks if desired (currently not configured).

## Architecture Notes

### Django Configuration
- **In-memory configuration** in [app/GreenITESO/main.py](app/GreenITESO/main.py) — no external settings file yet
- `DEBUG=True` hardcoded for development; plan migration to environment-based config
- `ALLOWED_HOSTS=["*"]` permissive; tighten before production
- No database configured yet (uses Django's default SQLite in memory)

### Future Structure
As the project grows, plan for:
- Separate `apps/` directory for Django apps (models, views, serializers)
- Environment-based settings (`settings/base.py`, `settings/dev.py`, `settings/prod.py`)
- `tests/` directory with pytest fixtures and integration tests
- `api/urls.py` for URL routing once REST endpoints exist

## Development Setup

### Initial Setup
```sh
# Clone and enter container
docker build -t greeniteso-backend .
docker run --rm -it -p 8000:8000 -v $(PWD)/app:/workspace greeniteso-backend

# Inside container, server auto-starts via Makefile
```

### VS Code + Devcontainer
Opening the repo in VS Code with "Dev Containers" extension installed will auto-prompt to reopen in container. Extensions pre-configured:
- **Python** (ms-python.python) — linting, debugging
- **Ruff** (charliermarsh.ruff) — linter + formatter integration
- **Django** (batisteo.vscode-django) — Django snippets and tooling

Python interpreter auto-set to `/opt/venv/bin/python`. Format-on-save enabled with Ruff.

### Team Workflow (18-person team)

Each team member:
1. Clone repo locally: `git clone ...`
2. Open in VS Code → auto-rebuilds devcontainer
3. Edit files locally in `/app`
4. Container sees changes instantly via mount
5. Test in container (dev server, linting, tests)
6. Commit + push from local terminal

**SSH forwarding** (`SSH_AUTH_SOCK`) configured for future private repo access if needed. Harmless if unused.

**Environment:** Same Dockerfile ensures identical setup across team. No "works on my machine" issues.

## Testing

No test files exist yet. When adding tests:
- Create `tests/` directory at root or inside `app/`
- Use pytest (already in requirements.txt)
- Suggested: add `pytest tests/` to app/Makefile for convenience
- Follow pytest conventions (test_*.py or *_test.py files)

## Git Workflow

- **Commits**: Use Conventional Commits (`feat: ...`, `fix: ...`, `docs: ...`). See [.github/workflows/precommit.yaml](.github/workflows/precommit.yaml) for allowed types.
- **PRs**: Title must match commit convention; scope optional.
- **Linting**: Ruff must pass CI before merge.

## Deployment

### Google Cloud Run

Project deploys to Google Cloud Run. CI/CD pipeline:
1. Push to main/PR
2. GitHub Actions builds Docker image
3. Push to Google Container Registry (GCR)
4. Deploy to Cloud Run

**Dockerfile notes:**
- `CMD` runs dev server (change to gunicorn for production)
- Environment variables handled via Cloud Run secrets
- Logs → stdout (Cloud Run captures automatically)
- Health checks recommended for production

**No `volume/logs` mount** — logs to stdout for Cloud Run compatibility.

## Known Gaps

- No database backend configured (in-memory SQLite only)
- No authentication/authorization system yet
- No API serializers or viewsets (pure Django views currently)
- No environment-based configuration (dev/test/prod)
- No error handling middleware
- No gunicorn/production WSGI server configured yet
- CI/CD for Cloud Run not yet configured

