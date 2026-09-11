"""Reproduce and resolve a compatible Django migration leaf conflict.

The fixture is written into a temporary directory and never touches the
application's migration tree.  By default this command starts a disposable
PostgreSQL 18 container, runs the rehearsal, and removes the container.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import psycopg

CONTAINER_PASSWORD = "rehearsal-password"
CONTAINER_USER = "rehearsal"
CONTAINER_DATABASE = "rehearsal"


def write_fixture(root: Path) -> None:
    """Create an isolated Django project with two incompatible migration leaves."""
    files = {
        "manage.py": '''#!/usr/bin/env python3
import os
import sys

if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "fixture_project.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)
''',
        "fixture_project/__init__.py": "",
        "fixture_project/settings.py": '''import os
from urllib.parse import unquote, urlsplit

DATABASE_URL = os.environ["MIGRATION_REHEARSAL_DATABASE_URL"]
parsed = urlsplit(DATABASE_URL)

SECRET_KEY = "migration-rehearsal"
DEBUG = False
ALLOWED_HOSTS = ["*"]
INSTALLED_APPS = ["rehearsal_records"]
MIDDLEWARE = []
ROOT_URLCONF = "fixture_project.urls"
USE_TZ = True
TIME_ZONE = "UTC"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(parsed.path.lstrip("/")),
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname,
        "PORT": str(parsed.port or "5432"),
    }
}
''',
        "fixture_project/urls.py": "urlpatterns = []\n",
        "rehearsal_records/__init__.py": "",
        "rehearsal_records/apps.py": '''from django.apps import AppConfig


class RehearsalRecordsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "rehearsal_records"
''',
        "rehearsal_records/models.py": '''from django.db import models


class SharedRecord(models.Model):
    label = models.CharField(max_length=100)
    created_at = models.DateTimeField()
    team_a_code = models.CharField(max_length=40, null=True)
    team_b_code = models.CharField(max_length=40, null=True)

    class Meta:
        db_table = "rehearsal_shared_record"
''',
        "rehearsal_records/migrations/__init__.py": "",
        "rehearsal_records/migrations/0001_initial.py": '''from django.db import migrations, models
from django.utils import timezone


def seed_shared_record(apps, schema_editor):
    SharedRecord = apps.get_model("rehearsal_records", "SharedRecord")
    SharedRecord.objects.create(label="seed", created_at=timezone.now())


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name="SharedRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("label", models.CharField(max_length=100)),
                ("created_at", models.DateTimeField()),
            ],
            options={"db_table": "rehearsal_shared_record"},
        ),
        migrations.RunPython(seed_shared_record, migrations.RunPython.noop),
    ]
''',
        "rehearsal_records/migrations/0002_team_a.py": '''from django.db import migrations, models


def set_team_a_code(apps, schema_editor):
    SharedRecord = apps.get_model("rehearsal_records", "SharedRecord")
    SharedRecord.objects.filter(label="seed").update(team_a_code="A-READY")


class Migration(migrations.Migration):
    dependencies = [("rehearsal_records", "0001_initial")]
    operations = [
        migrations.AddField(
            model_name="sharedrecord",
            name="team_a_code",
            field=models.CharField(max_length=40, null=True),
        ),
        migrations.RunPython(set_team_a_code, migrations.RunPython.noop),
    ]
''',
        "rehearsal_records/migrations/0002_team_b.py": '''from django.db import migrations, models


def set_team_b_code(apps, schema_editor):
    SharedRecord = apps.get_model("rehearsal_records", "SharedRecord")
    SharedRecord.objects.filter(label="seed").update(team_b_code="B-READY")


class Migration(migrations.Migration):
    dependencies = [("rehearsal_records", "0001_initial")]
    operations = [
        migrations.AddField(
            model_name="sharedrecord",
            name="team_b_code",
            field=models.CharField(max_length=40, null=True),
        ),
        migrations.RunPython(set_team_b_code, migrations.RunPython.noop),
    ]
''',
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def run_manage(root: Path, database_url: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run one management command and return its captured result."""
    environment = os.environ.copy()
    environment["MIGRATION_REHEARSAL_DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "manage.py", *arguments],
        cwd=root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def show_result(label: str, result: subprocess.CompletedProcess[str]) -> None:
    """Print command evidence without exposing a database URL."""
    output = (result.stdout + result.stderr).strip()
    print(f"[{label}] exit={result.returncode}")
    if output:
        print(output)


def assert_failed(label: str, result: subprocess.CompletedProcess[str]) -> None:
    """Require a command intended to expose the conflict to fail."""
    if result.returncode == 0:
        raise AssertionError(f"{label} unexpectedly succeeded")


def add_merge_migration(root: Path) -> None:
    """Add the reviewed merge node after the conflict has been observed."""
    (root / "rehearsal_records/migrations/0003_merge_team_leaves.py").write_text(
        '''from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("rehearsal_records", "0002_team_a"),
        ("rehearsal_records", "0002_team_b"),
    ]
    operations = []
''',
        encoding="utf-8",
    )


def database_snapshot(database_url: str) -> tuple[set[str], tuple[str, str, str]]:
    """Read expected columns and the migrated seed row from PostgreSQL."""
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'rehearsal_shared_record'
            ORDER BY ordinal_position
            """
        )
        columns = {row[0] for row in cursor.fetchall()}
        cursor.execute(
            """
            SELECT label, team_a_code, team_b_code
            FROM rehearsal_shared_record
            ORDER BY id
            """
        )
        row = cursor.fetchone()
    if row is None:
        raise AssertionError("migration data check found no seed row")
    return columns, row


def postgres_version(database_url: str) -> str:
    """Return PostgreSQL's server version number for the evidence check."""
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute("SHOW server_version_num")
        return str(cursor.fetchone()[0])


def wait_for_postgres(database_url: str) -> None:
    """Wait briefly for the disposable container to accept connections."""
    last_error: Exception | None = None
    for _ in range(60):
        try:
            with psycopg.connect(database_url, connect_timeout=1):
                return
        except psycopg.Error as error:
            last_error = error
            time.sleep(1)
    raise RuntimeError(f"PostgreSQL did not become ready: {last_error}")


def start_postgres() -> tuple[str, str]:
    """Start PostgreSQL 18 on an ephemeral host port and return URL/container."""
    container = f"migration-rehearsal-{uuid.uuid4().hex[:10]}"
    completed = subprocess.run(
        [
            "docker",
            "run",
            "--detach",
            "--rm",
            "--name",
            container,
            "--env",
            f"POSTGRES_USER={CONTAINER_USER}",
            "--env",
            f"POSTGRES_PASSWORD={CONTAINER_PASSWORD}",
            "--env",
            f"POSTGRES_DB={CONTAINER_DATABASE}",
            "--publish",
            "127.0.0.1::5432",
            "postgres:18",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"could not start postgres: {completed.stderr.strip()}")
    port_result = subprocess.run(
        ["docker", "port", container, "5432/tcp"],
        check=True,
        capture_output=True,
        text=True,
    )
    host_port = port_result.stdout.strip().rsplit(":", maxsplit=1)[-1]
    url = f"postgresql://{CONTAINER_USER}:{CONTAINER_PASSWORD}@127.0.0.1:{host_port}/{CONTAINER_DATABASE}"
    return url, container


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("MIGRATION_REHEARSAL_DATABASE_URL"),
        help="Disposable PostgreSQL URL; omit it to start postgres:18 with Docker.",
    )
    parser.add_argument(
        "--keep-container",
        action="store_true",
        help="Keep the auto-started container for inspection (for debugging only).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    database_url = args.database_url
    container: str | None = None
    try:
        if database_url is None:
            database_url, container = start_postgres()
        wait_for_postgres(database_url)
        version = postgres_version(database_url)
        print(f"[database] PostgreSQL server_version_num={version}")
        if not version.startswith("18"):
            raise AssertionError(f"expected PostgreSQL 18, got server_version_num={version}")

        with tempfile.TemporaryDirectory(prefix="django-migration-rehearsal-") as temporary:
            root = Path(temporary)
            write_fixture(root)

            conflict_plan = run_manage(root, database_url, "migrate", "--plan")
            show_result("before migrate --plan (expected conflict)", conflict_plan)
            assert_failed("migrate --plan", conflict_plan)

            conflict_check = run_manage(root, database_url, "makemigrations", "--check", "--dry-run")
            show_result("before makemigrations --check --dry-run (expected conflict)", conflict_check)
            assert_failed("makemigrations --check --dry-run", conflict_check)

            add_merge_migration(root)
            migrated = run_manage(root, database_url, "migrate", "--no-input")
            show_result("after merge migrate", migrated)
            if migrated.returncode != 0:
                raise AssertionError("merge migration did not apply")

            clean_plan = run_manage(root, database_url, "migrate", "--plan")
            show_result("after migrate --plan", clean_plan)
            clean_check = run_manage(root, database_url, "makemigrations", "--check", "--dry-run")
            show_result("after makemigrations --check --dry-run", clean_check)
            if clean_plan.returncode != 0 or clean_check.returncode != 0:
                raise AssertionError("resolved migration graph is not clean")

            columns, row = database_snapshot(database_url)
            expected_columns = {"id", "label", "created_at", "team_a_code", "team_b_code"}
            expected_row = ("seed", "A-READY", "B-READY")
            print(f"[after snapshot] columns={sorted(columns)}")
            print(f"[after snapshot] seed_row={row}")
            if columns != expected_columns or row != expected_row:
                raise AssertionError(
                    f"unexpected schema/data: columns={columns!r}, row={row!r}"
                )
            print("RESULT: conflict reproduced, compatible merge applied, schema/data match expected")
    finally:
        if container is not None and not args.keep_container:
            subprocess.run(["docker", "rm", "--force", container], check=False, capture_output=True)
        elif container is not None:
            print(f"[container] retained={container}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
