"""Mock mode must be refused outside a local, non-deployed dev process.

Settings are parsed at Django's import time from raw environment variables, so
this is exercised as a subprocess rather than through ``override_settings``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[3]

_BASE_ENV = {
    "DJANGO_SETTINGS_MODULE": "green_iteso.settings",
    "DJANGO_SECRET_KEY": "x",
    "DJANGO_ALLOWED_HOSTS": "localhost",
    "DATABASE_URL": "postgresql://u:p@127.0.0.1:5432/d",
    "PATH": os.environ.get("PATH", ""),
    "VIRTUAL_ENV": os.environ.get("VIRTUAL_ENV", ""),
}


def _check_settings(**overrides: str) -> subprocess.CompletedProcess[str]:
    env = {**_BASE_ENV, **overrides}
    return subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import django; django.setup(); "
                "from django.conf import settings; "
                "print(settings.MIDDLEWARE); "
                "print(settings.CORS_ALLOWED_ORIGINS)"
            ),
        ],
        cwd=APP_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def test_mock_mode_is_rejected_when_deployed() -> None:
    result = _check_settings(
        DJANGO_ENV="dev",
        DJANGO_DEPLOYED="true",
        DJANGO_CONNECTION_ROLE="app",
        MICROSOFT_AUTH_MODE="mock",
    )

    assert result.returncode != 0
    assert "MICROSOFT_AUTH_MODE=mock" in result.stderr


def test_mock_mode_is_rejected_outside_dev_environment() -> None:
    result = _check_settings(
        DJANGO_ENV="staging",
        DJANGO_DEPLOYED="true",
        DJANGO_CONNECTION_ROLE="app",
        MICROSOFT_AUTH_MODE="mock",
        CORS_ALLOWED_ORIGINS="https://staging.example.com",
    )

    assert result.returncode != 0
    assert "MICROSOFT_AUTH_MODE=mock" in result.stderr


def test_mock_mode_is_accepted_in_local_dev() -> None:
    result = _check_settings(
        DJANGO_ENV="dev",
        DJANGO_DEPLOYED="false",
        DJANGO_CONNECTION_ROLE="app",
        MICROSOFT_AUTH_MODE="mock",
    )

    assert result.returncode == 0, result.stderr


def test_unknown_auth_mode_is_rejected() -> None:
    result = _check_settings(
        DJANGO_ENV="dev",
        DJANGO_DEPLOYED="false",
        DJANGO_CONNECTION_ROLE="app",
        MICROSOFT_AUTH_MODE="firebase",
    )

    assert result.returncode != 0
    assert "MICROSOFT_AUTH_MODE" in result.stderr


def test_development_cors_middleware_is_local_only() -> None:
    result = _check_settings(
        DJANGO_ENV="dev",
        DJANGO_DEPLOYED="false",
        DJANGO_CONNECTION_ROLE="app",
        MICROSOFT_AUTH_MODE="mock",
    )

    assert result.returncode == 0, result.stderr
    assert "DevelopmentCorsMiddleware" in result.stdout
    assert "['http://localhost:3000']" in result.stdout


def test_development_cors_middleware_is_not_loaded_outside_dev() -> None:
    environment = {
        **_BASE_ENV,
        "DJANGO_ENV": "staging",
        "DJANGO_DEPLOYED": "true",
        "DJANGO_CONNECTION_ROLE": "app",
        "MICROSOFT_AUTH_MODE": "entra",
        "DATABASE_URL": (
            "postgresql://user:password@"
            "ep-withered-cake-axk8vlfi-pooler.c-4.us-east-2.aws.neon.tech:5432/db"
            "?sslmode=verify-full"
        ),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import green_iteso.settings.base as settings; "
                "print('DevelopmentCorsMiddleware' in settings.MIDDLEWARE); "
                "print(settings.CORS_ALLOWED_ORIGINS)"
            ),
        ],
        cwd=APP_DIR,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines() == ["False", "[]"]
