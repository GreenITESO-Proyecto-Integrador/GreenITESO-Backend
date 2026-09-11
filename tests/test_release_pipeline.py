"""Focused, local checks for the release workflow contract.

These tests deliberately use only the standard library. They do not contact
Google Cloud or GitHub; the shell release script is exercised with fake
``gcloud`` and ``docker`` executables.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "release.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "_deploy.yml"
PROMOTE = ROOT / ".github" / "workflows" / "promote.yml"


def _base_env(tmp_path: Path) -> dict[str, str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    return {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "GCP_PROJECT_ID": "project",
        "GCP_REGION": "us-central1",
        "ARTIFACT_REGISTRY_LOCATION": "us",
        "ARTIFACT_REGISTRY_REPO": "images",
        "IMAGE_PROJECT_ID": "image-project",
        "CLOUD_RUN_SERVICE": "greeniteso",
        "RELEASE_ENVIRONMENT": "staging",
        "RELEASE_SHA": "a" * 40,
        "DJANGO_SECRET_KEY_SECRET": "django-secret",
        "DATABASE_URL_SECRET": "database-pooled",
        "DATABASE_URL_UNPOOLED_SECRET": "database-direct",
        "DJANGO_ALLOWED_HOSTS": "greeniteso.example",
        "MIGRATION_JOB_NAME": "greeniteso-migrate-staging",
        "BUILD_IMAGE": "false",
    }


def _fake_commands(tmp_path: Path, *, migrate_status: int = 0) -> Path:
    fake_bin = tmp_path / "bin"
    log = tmp_path / "commands.log"
    gcloud = fake_bin / "gcloud"
    gcloud.write_text(
        "#!/bin/sh\n"
        f"echo gcloud \"$@\" >> {log}\n"
        "case \"$*\" in\n"
        "  *'artifacts docker images describe'*) echo 'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' ;;\n"
        "  *'run jobs describe'*) exit 1 ;;\n"
        f"  *'run jobs execute'*) exit {migrate_status} ;;\n"
        "esac\n"
    )
    gcloud.chmod(0o755)
    docker = fake_bin / "docker"
    docker.write_text(f"#!/bin/sh\necho docker \"$@\" >> {log}\n")
    docker.chmod(0o755)
    return log


def _run_release(tmp_path: Path, *, migrate_status: int = 0) -> tuple[subprocess.CompletedProcess[str], str]:
    env = _base_env(tmp_path)
    log = _fake_commands(tmp_path, migrate_status=migrate_status)
    result = subprocess.run(["sh", str(SCRIPT)], cwd=ROOT, env=env, text=True, capture_output=True)
    return result, log.read_text()


def test_release_requires_configuration() -> None:
    env = os.environ.copy()
    env.pop("GCP_PROJECT_ID", None)
    result = subprocess.run(["sh", str(SCRIPT)], cwd=ROOT, env=env, text=True, capture_output=True)
    assert result.returncode != 0
    assert "GCP_PROJECT_ID is required" in result.stderr


def test_migration_failure_does_not_deploy(tmp_path: Path) -> None:
    result, log = _run_release(tmp_path, migrate_status=1)
    assert result.returncode != 0
    assert "run jobs execute" in log
    assert "run deploy" not in log
    assert "run services update" not in log


def test_success_uses_digest_and_deploys_after_migration(tmp_path: Path) -> None:
    result, log = _run_release(tmp_path)
    assert result.returncode == 0, result.stderr
    migration = log.index("run jobs execute")
    deploy = log.index("run deploy")
    assert migration < deploy
    assert "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" in log
    assert "DATABASE_URL_UNPOOLED_SECRET" not in log


def test_workflows_use_three_environments_and_no_token_push() -> None:
    workflow = WORKFLOW.read_text()
    promote = PROMOTE.read_text()
    assert "workflow_call" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "migration" in workflow.lower()
    assert "staging" in promote and "production" in promote
    assert "git push" not in promote
