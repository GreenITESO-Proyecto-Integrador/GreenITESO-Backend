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
DATABASE_MIGRATIONS = ROOT / ".github" / "workflows" / "database-migrations.yml"


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


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, text=True, capture_output=True, check=True
    )
    return result.stdout.strip()


def _provenance_repo(tmp_path: Path) -> tuple[Path, str, str]:
    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    subprocess.run(
        ["git", "init", "--bare", str(remote)], check=True, capture_output=True
    )
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    _git(repo, "config", "user.name", "Release Test")
    _git(repo, "config", "user.email", "release-test@example.invalid")
    (repo / "source.txt").write_text("same release tree\n")
    _git(repo, "add", "source.txt")
    _git(repo, "commit", "-m", "source release")
    source_sha = _git(repo, "rev-parse", "HEAD")
    source_tree = _git(repo, "rev-parse", "HEAD^{tree}")
    target_sha = _git(repo, "commit-tree", source_tree, "-p", source_sha, "-m", "merged target")
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "origin", f"{source_sha}:refs/heads/dev")
    _git(repo, "push", "origin", f"{target_sha}:refs/heads/preprod")
    return repo, source_sha, target_sha


def _run_promotion_provenance(
    tmp_path: Path, repo: Path, source_sha: str, release_sha: str
) -> subprocess.CompletedProcess[str]:
    fake_bin = tmp_path / "promotion-bin"
    fake_bin.mkdir(exist_ok=True)
    gh = fake_bin / "gh"
    record = (
        f"environment=dev\nrelease_sha={source_sha}\n"
        f"image_digest=sha256:{'a' * 64}\n"
    )
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
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "RELEASE_ENVIRONMENT": "staging",
        "RELEASE_SHA": release_sha,
        "SOURCE_REF": "dev",
        "SOURCE_RELEASE_SHA": source_sha,
        "GITHUB_OUTPUT": str(tmp_path / "promotion-output"),
    }
    return subprocess.run(
        [str(PROVENANCE)],
        cwd=repo,
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


def test_promotion_provenance_accepts_different_merge_sha_with_identical_tree(
    tmp_path: Path,
) -> None:
    repo, source_sha, release_sha = _provenance_repo(tmp_path)
    result = _run_promotion_provenance(tmp_path, repo, source_sha, release_sha)
    assert result.returncode == 0, result.stderr


def test_promotion_provenance_rejects_target_only_tree_change(tmp_path: Path) -> None:
    repo, source_sha, release_sha = _provenance_repo(tmp_path)
    _git(repo, "checkout", "--detach", release_sha)
    (repo / "target-only.txt").write_text("not in the tested source image\n")
    _git(repo, "add", "target-only.txt")
    _git(repo, "commit", "-m", "target-only change")
    release_sha = _git(repo, "rev-parse", "HEAD")
    _git(repo, "push", "origin", f"{release_sha}:refs/heads/preprod")
    result = _run_promotion_provenance(tmp_path, repo, source_sha, release_sha)
    assert result.returncode != 0
    assert "does not match the merged release commit" in result.stderr


def test_promotion_provenance_rejects_advanced_source_branch(tmp_path: Path) -> None:
    repo, source_sha, release_sha = _provenance_repo(tmp_path)
    (repo / "source.txt").write_text("new source commit\n")
    _git(repo, "add", "source.txt")
    _git(repo, "commit", "-m", "advance source")
    advanced_sha = _git(repo, "rev-parse", "HEAD")
    _git(repo, "push", "origin", f"{advanced_sha}:refs/heads/dev")
    result = _run_promotion_provenance(tmp_path, repo, source_sha, release_sha)
    assert result.returncode != 0
    assert "Stale source release" in result.stderr


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
    assert "preprod" in promote and "main" in promote
    assert "gh pr create" in promote
    assert "pull-requests: write" in promote
    assert "git/refs/heads/" not in promote
    assert "contents: write" not in promote
    assert "git push" not in promote


def test_production_release_is_tied_to_main_without_branch_bypass() -> None:
    production = (ROOT / ".github/workflows/deploy-production.yml").read_text()
    promote = PROMOTE.read_text()
    assert "branches: [main]" in production
    assert "release_ref: main" in production
    assert "source_ref: preprod" in production
    assert "source_release_sha: ${{ github.event.pull_request.head.sha }}" in production
    assert "release_sha: ${{ github.event.pull_request.merge_commit_sha }}" in production
    assert "github_environment: production" in production
    assert "main" in promote
    assert "preprod" in promote
    assert "pull-requests: write" in promote
    assert "gh pr create" in promote
    assert "git/refs/heads/" not in promote
    assert "contents: write" not in promote


def test_neon_migrations_only_run_after_protected_nonproduction_branch_updates() -> None:
    workflow = DATABASE_MIGRATIONS.read_text()
    assert 'workflows: ["Django tests"]' in workflow
    assert "types: [completed]" in workflow
    assert "branches: [dev, preprod]" in workflow
    assert "workflow_run.event == 'push'" in workflow
    assert "vars.CLOUD_DEPLOYMENT_ENABLED != 'true'" in workflow
    assert "workflow_run.conclusion == 'success'" in workflow
    assert "pull_request:" not in workflow
    assert "workflow_dispatch:" not in workflow
    assert "environment: ${{ github.event.workflow_run.head_branch }}" in workflow
    assert "group: neon-migration-${{ github.event.workflow_run.head_branch }}" in workflow
    assert "DATABASE_URL_UNPOOLED" in workflow
    assert "DATABASE_URL" in workflow
    assert "db_smoke" in workflow
    assert "DJANGO_ENV: ${{ github.event.workflow_run.head_branch == 'preprod' && 'staging' || 'dev' }}" in workflow
    assert "production" not in workflow
    assert workflow.index("Apply committed migrations") < workflow.index("Verify access through the pooled application role")
    assert "cancel-in-progress: false" in workflow
    assert "continue-on-error" not in workflow


def test_preprod_git_branch_targets_preprod_environment_and_staging_database() -> None:
    caller = (ROOT / ".github" / "workflows" / "deploy-staging.yml").read_text()
    migration = DATABASE_MIGRATIONS.read_text()
    assert "branches: [preprod]" in caller
    assert "github_environment: preprod" in caller
    assert "release_ref: preprod" in caller
    assert "source_ref: dev" in caller
    assert "source_release_sha: ${{ github.event.pull_request.head.sha }}" in caller
    assert "release_sha: ${{ github.event.pull_request.merge_commit_sha }}" in caller
    assert "environment: staging" in caller
    assert "DJANGO_ENV: ${{ github.event.workflow_run.head_branch == 'preprod' && 'staging' || 'dev' }}" in migration
    production = (ROOT / ".github" / "workflows" / "deploy-production.yml").read_text()
    assert "branches: [main]" in production
    assert "github_environment: production" in production
    assert "release_ref: main" in production
    assert "source_ref: preprod" in production


def test_release_callers_are_opt_in_until_cloud_is_enabled() -> None:
    for filename in ("deploy-dev.yml", "deploy-staging.yml", "deploy-production.yml"):
        caller = (ROOT / ".github" / "workflows" / filename).read_text()
        assert "vars.CLOUD_DEPLOYMENT_ENABLED == 'true'" in caller
        assert "uses: ./.github/workflows/_deploy.yml" in caller
        assert "secrets: inherit" in caller

    for filename in ("deploy-staging.yml", "deploy-production.yml"):
        caller = (ROOT / ".github" / "workflows" / filename).read_text()
        assert "types: [closed]" in caller
        assert "pull_request.merged == true" in caller
        assert "pull_request.head.repo.full_name == github.repository" in caller
        assert "release_sha:" in caller
        assert "workflow_dispatch:" not in caller

    reusable = WORKFLOW.read_text()
    assert "Validate release configuration" in reusable
    assert "for name in GCP_PROJECT_ID" in reusable


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
