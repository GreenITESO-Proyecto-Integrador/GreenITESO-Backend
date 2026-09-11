"""Focused, local checks for the release workflow contract.

These tests deliberately use only the standard library. They do not contact
Google Cloud or GitHub; the shell release script is exercised with fake
``gcloud`` and ``docker`` executables.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "release.sh"
PROVENANCE = ROOT / "scripts" / "verify-source-release.sh"
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
        "RUNTIME_SERVICE_ACCOUNT": "runtime@project.iam.gserviceaccount.com",
        "MIGRATION_SERVICE_ACCOUNT": "migrator@project.iam.gserviceaccount.com",
        "CLOUD_RUN_MAX_INSTANCES": "3",
        "CLOUD_RUN_CONCURRENCY": "40",
        "EXPECTED_IMAGE_DIGEST": "sha256:" + "a" * 64,
        "RELEASE_RECORD_PATH": str(tmp_path / "release-record.txt"),
        "BUILD_IMAGE": "false",
    }


def _fake_commands(
    tmp_path: Path, *, migrate_status: int = 0, smoke_status: int = 0
) -> Path:
    fake_bin = tmp_path / "bin"
    log = tmp_path / "commands.log"
    gcloud = fake_bin / "gcloud"
    gcloud.write_text(
        "#!/bin/sh\n"
        f'echo gcloud "$@" >> {log}\n'
        'case "$*" in\n'
        "  *'artifacts docker images describe'*) echo 'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' ;;\n"
        "  *'run jobs describe'*) echo 'NOT_FOUND' >&2; exit 1 ;;\n"
        f"  *'run jobs execute'*'-smoke'*) exit {smoke_status} ;;\n"
        f"  *'run jobs execute'*) exit {migrate_status} ;;\n"
        "esac\n"
    )
    gcloud.chmod(0o755)
    docker = fake_bin / "docker"
    docker.write_text(f'#!/bin/sh\necho docker "$@" >> {log}\n')
    docker.chmod(0o755)
    return log


def _run_release(
    tmp_path: Path, *, migrate_status: int = 0
) -> tuple[subprocess.CompletedProcess[str], str]:
    env = _base_env(tmp_path)
    log = _fake_commands(tmp_path, migrate_status=migrate_status)
    result = subprocess.run(
        [str(SCRIPT)], cwd=ROOT, env=env, text=True, capture_output=True, check=False
    )
    return result, log.read_text()


def test_release_requires_configuration() -> None:
    env = os.environ.copy()
    env.pop("GCP_PROJECT_ID", None)
    result = subprocess.run(
        [str(SCRIPT)], cwd=ROOT, env=env, text=True, capture_output=True, check=False
    )
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
    assert (
        "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" in log
    )
    assert "DATABASE_URL_UNPOOLED_SECRET" not in log
    assert "DJANGO_ALLOWED_HOSTS=greeniteso.example" in log


def test_promotion_digest_mismatch_stops_before_migration(tmp_path: Path) -> None:
    env = _base_env(tmp_path)
    env["EXPECTED_IMAGE_DIGEST"] = "sha256:" + "b" * 64
    log = _fake_commands(tmp_path)
    result = subprocess.run(
        [str(SCRIPT)], cwd=ROOT, env=env, text=True, capture_output=True, check=False
    )
    assert result.returncode != 0
    assert "does not match registry digest" in result.stderr
    assert "run jobs" not in log.read_text()


def test_non_dev_missing_digest_stops_before_gcloud(tmp_path: Path) -> None:
    env = _base_env(tmp_path)
    env.pop("EXPECTED_IMAGE_DIGEST")
    log = _fake_commands(tmp_path)
    result = subprocess.run(
        [str(SCRIPT)], cwd=ROOT, env=env, text=True, capture_output=True, check=False
    )
    assert result.returncode != 0
    assert "EXPECTED_IMAGE_DIGEST is required" in result.stderr
    assert not log.exists()


def test_non_dev_build_is_rejected_before_gcloud(tmp_path: Path) -> None:
    env = _base_env(tmp_path)
    env["BUILD_IMAGE"] = "true"
    log = _fake_commands(tmp_path)
    result = subprocess.run(
        [str(SCRIPT)], cwd=ROOT, env=env, text=True, capture_output=True, check=False
    )
    assert result.returncode != 0
    assert "only allowed for dev" in result.stderr
    assert not log.exists()


def test_source_provenance_without_successful_run_fails(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    gh = fake_bin / "gh"
    gh.write_text("#!/bin/sh\nexit 0\n")
    gh.chmod(0o755)
    output = tmp_path / "github-output"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "RELEASE_ENVIRONMENT": "staging",
        "RELEASE_SHA": "a" * 40,
        "GITHUB_OUTPUT": str(output),
    }
    result = subprocess.run(
        [str(PROVENANCE)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert "No successful" in result.stderr
    assert not output.exists()


def _fake_provenance_gh(tmp_path: Path, record: str) -> Path:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    gh = fake_bin / "gh"
    quoted_record = shlex.quote(record)
    gh.write_text(
        "#!/bin/sh\n"
        'if [ "$2" = list ]; then echo 123; exit 0; fi\n'
        'if [ "$2" = download ]; then\n'
        '  mkdir -p "$7"\n'
        f"  printf '%s' {quoted_record} > \"$7/release-record.txt\"\n"
        "  exit 0\n"
        "fi\n"
        "exit 2\n"
    )
    gh.chmod(0o755)
    return fake_bin


def _run_provenance(tmp_path: Path, record: str) -> subprocess.CompletedProcess[str]:
    fake_bin = _fake_provenance_gh(tmp_path, record)
    output = tmp_path / "github-output"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "RELEASE_ENVIRONMENT": "staging",
        "RELEASE_SHA": "a" * 40,
        "GITHUB_OUTPUT": str(output),
    }
    return subprocess.run(
        [str(PROVENANCE)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_source_provenance_accepts_matching_success_record(tmp_path: Path) -> None:
    digest = "sha256:" + "a" * 64
    result = _run_provenance(
        tmp_path, f"environment=dev\nrelease_sha={'a' * 40}\nimage_digest={digest}\n"
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "github-output").read_text() == f"image_digest={digest}\n"


def test_source_provenance_rejects_mismatched_artifact(tmp_path: Path) -> None:
    digest = "sha256:" + "a" * 64
    result = _run_provenance(
        tmp_path,
        f"environment=production\nrelease_sha={'a' * 40}\nimage_digest={digest}\n",
    )
    assert result.returncode != 0
    assert "does not match" in result.stderr


def test_source_provenance_rejects_empty_digest(tmp_path: Path) -> None:
    result = _run_provenance(
        tmp_path, f"environment=dev\nrelease_sha={'a' * 40}\nimage_digest=\n"
    )
    assert result.returncode != 0
    assert "does not match" in result.stderr


def test_multiple_hosts_are_encoded_as_one_env_value(tmp_path: Path) -> None:
    env = _base_env(tmp_path)
    env["DJANGO_ALLOWED_HOSTS"] = "greeniteso.example,admin.greeniteso.example"
    _fake_commands(tmp_path)
    result = subprocess.run(
        [str(SCRIPT)], cwd=ROOT, env=env, text=True, capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr
    command_log = (tmp_path / "commands.log").read_text()
    assert (
        "^@^DJANGO_ENV=staging@DJANGO_DEPLOYED=true@DJANGO_CONNECTION_ROLE=app@DJANGO_ALLOWED_HOSTS=greeniteso.example,admin.greeniteso.example"
        in command_log
    )


def test_workflows_use_three_environments_and_no_token_push() -> None:
    workflow = WORKFLOW.read_text()
    promote = PROMOTE.read_text()
    assert "workflow_call" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "migration" in workflow.lower()
    assert "actions/upload-artifact@v4" in workflow
    assert "verify-source-release.sh" in workflow
    assert workflow.index(
        "Verify successful source release provenance"
    ) < workflow.index("Authenticate to Google Cloud")
    assert "actions: read" in workflow
    assert (
        "actions: read"
        in (ROOT / ".github" / "workflows" / "deploy-staging.yml").read_text()
    )
    assert (
        "--service-account=${MIGRATION_SERVICE_ACCOUNT}"
        in (ROOT / "scripts" / "release.sh").read_text()
    )
    assert (
        '--service-account="$RUNTIME_SERVICE_ACCOUNT"'
        in (ROOT / "scripts" / "release.sh").read_text()
    )
    assert "staging" in promote and "production" in promote
    assert "image_digest" in promote
    assert "verify-source-release.sh" in promote
    assert "git push" not in promote


def test_production_environment_uses_architecture_main_branch() -> None:
    production = (ROOT / ".github/workflows/deploy-production.yml").read_text()
    promote = PROMOTE.read_text()
    assert "branches: [main]" in production
    assert "source_ref: main" in production
    assert "environment: production" in production
    assert "production) source_ref=staging; target_ref=main;" in promote
    assert "git/refs/heads/${TARGET_REF}" in promote
    assert '--ref "$TARGET_REF"' in promote


def test_smoke_failure_does_not_deploy_or_record(tmp_path: Path) -> None:
    env = _base_env(tmp_path)
    log = _fake_commands(tmp_path, smoke_status=1)
    result = subprocess.run(
        [str(SCRIPT)], cwd=ROOT, env=env, text=True, capture_output=True, check=False
    )
    assert result.returncode != 0
    assert "db_smoke" in log.read_text()
    assert "run deploy" not in log.read_text()
    assert not Path(env["RELEASE_RECORD_PATH"]).exists()


def test_smoke_uses_only_runtime_identity_before_deploy(tmp_path: Path) -> None:
    result, log = _run_release(tmp_path)
    assert result.returncode == 0, result.stderr
    smoke = next(line for line in log.splitlines() if "db_smoke" in line)
    assert "--service-account=runtime@project.iam.gserviceaccount.com" in smoke
    assert "DATABASE_URL=database-pooled:latest" in smoke
    assert "database-direct" not in smoke
    assert log.index("db_smoke") < log.index("run deploy")


def test_release_waits_for_tests_of_approved_sha() -> None:
    workflow = WORKFLOW.read_text()
    assert "uses: ./.github/workflows/tests.yaml" in workflow
    assert "ref: ${{ inputs.release_sha }}" in workflow
    assert "needs: validate" in workflow


def test_invalid_job_name_stops_before_migration(tmp_path: Path) -> None:
    env = _base_env(tmp_path)
    env["MIGRATION_JOB_NAME"] = "a" * 44
    log = _fake_commands(tmp_path)
    result = subprocess.run(
        [str(SCRIPT)], cwd=ROOT, env=env, text=True, capture_output=True, check=False
    )
    assert result.returncode != 0
    assert "MIGRATION_JOB_NAME" in result.stderr
    assert not log.exists()
