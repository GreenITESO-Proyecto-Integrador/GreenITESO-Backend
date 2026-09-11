"""Database and settings checks for the local backend foundation."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.conf import settings
from django.db import connection

from green_iteso.security import redact_database_url
from green_iteso.settings.base import database_from_url

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_timezone_contract() -> None:
    """Business timestamps use the Mexico City timezone while remaining aware."""
    assert settings.TIME_ZONE == "America/Mexico_City"
    assert settings.USE_TZ is True


def test_database_is_postgresql() -> None:
    """The foundation cannot silently fall back to SQLite."""
    assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql"


@pytest.mark.django_db(transaction=True)
def test_real_postgresql_transaction() -> None:
    """Exercise an actual database transaction and verify the server major version."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('server_version_num')")
        version = int(cursor.fetchone()[0])
    assert 180000 <= version < 190000


def test_database_url_redacts_credentials() -> None:
    """Connection strings are safe to include in diagnostic errors."""
    redacted = redact_database_url("postgresql://alice:secret@example.test:5432/db")
    assert redacted == "postgresql://alice:***@example.test:5432/db"
    assert "secret" not in redacted
    assert redact_database_url(
        "postgresql://alice@example.test:5432/db?password=secret&sslmode=require"
    ) == ("postgresql://alice@example.test:5432/db?password=%2A%2A%2A&sslmode=require")
    assert (
        redact_database_url("not-a-database-url?password=secret")
        == "<redacted database URL>"
    )


def test_deployed_database_roles_are_explicit() -> None:
    """SSL and pooler role checks prevent swapping app and migration URLs."""
    with pytest.raises(RuntimeError, match="sslmode"):
        database_from_url(
            "postgresql://alice:secret@example.test:5432/db", require_ssl=True
        )
    with pytest.raises(RuntimeError, match="sslmode=verify-full"):
        database_from_url(
            "postgresql://alice:secret@example.test:5432/db?sslmode=require",
            require_ssl=True,
        )
    with pytest.raises(RuntimeError, match="pooler"):
        database_from_url(
            "postgresql://alice:secret@example.test:5432/db?sslmode=verify-full",
            require_ssl=True,
            expected_pooled=True,
        )
    direct = database_from_url(
        "postgresql://alice:secret@example.test:5432/db?sslmode=verify-full&channel_binding=require",
        require_ssl=True,
        expected_pooled=False,
    )
    assert direct["HOST"] == "example.test"
    assert direct["OPTIONS"] == {
        "sslmode": "verify-full",
        "sslrootcert": "system",
        "channel_binding": "require",
    }

    custom_ca = database_from_url(
        "postgresql://alice:secret@example.test:5432/db?sslmode=verify-full&sslrootcert=%2Fetc%2Fgreeniteso-ca.pem",
        require_ssl=True,
    )
    assert custom_ca["OPTIONS"] == {
        "sslmode": "verify-full",
        "sslrootcert": "/etc/greeniteso-ca.pem",
    }


def test_missing_environment_fails_fast() -> None:
    """A fresh checkout does not pick an environment or cloud settings implicitly."""
    environment = os.environ.copy()
    for key in (
        "DJANGO_ENV",
        "DJANGO_SECRET_KEY",
        "DATABASE_URL",
        "DATABASE_URL_UNPOOLED",
    ):
        environment.pop(key, None)
    # An empty process value deliberately wins over the local dotenv example.
    environment["DJANGO_ENV"] = ""
    environment["PYTHONPATH"] = str(PROJECT_ROOT / "app")
    result = subprocess.run(
        [sys.executable, "-c", "import green_iteso.settings"],
        cwd=PROJECT_ROOT / "app",
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "DJANGO_ENV is required" in result.stderr
