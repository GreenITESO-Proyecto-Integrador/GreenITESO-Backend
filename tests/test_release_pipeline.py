"""Focused, local checks for the release workflow contract.

These tests deliberately use only the standard library. They do not contact
Google Cloud or GitHub; the shell release script is exercised with fake
``gcloud`` and ``docker`` executables.
"""

from __future__ import annotations

import json
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
MIGRATION_GATE = ROOT / "scripts" / "should-run-neon-migration.py"


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
        "  *'artifacts docker images describe'*'@sha256:'*) echo 'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' ;;\n"
        "  *'artifacts docker images describe'*':sha-'*) exit 1 ;;\n"
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
    assert (
        "images describe us-docker.pkg.dev/image-project/images/greeniteso@sha256:"
        + "a" * 64
    ) in log
    assert ":sha-" not in log


def test_promotion_uses_source_digest_without_looking_up_merge_commit_tag(
    tmp_path: Path,
) -> None:
    env = _base_env(tmp_path)
    env["RELEASE_SHA"] = "c" * 40
    log = _fake_commands(tmp_path)
    result = subprocess.run(
        [str(SCRIPT)], cwd=ROOT, env=env, text=True, capture_output=True, check=False
    )
    assert result.returncode == 0, result.stderr
    rendered = log.read_text()
    assert "@sha256:" + "a" * 64 in rendered
    assert ":sha-" + "c" * 40 not in rendered


def test_promotion_digest_mismatch_stops_before_migration(tmp_path: Path) -> None:
    env = _base_env(tmp_path)
    env["EXPECTED_IMAGE_DIGEST"] = "sha256:" + "b" * 64
    log = _fake_commands(tmp_path)
    result = subprocess.run(
        [str(SCRIPT)], cwd=ROOT, env=env, text=True, capture_output=True, check=False
    )
    assert result.returncode != 0
    assert "did not confirm the expected source digest" in result.stderr
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
        "GITHUB_REPOSITORY": "GreenITESO-Proyecto-Integrador/GreenITESO-Backend",
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
        'if [ "$1 $2" = "run list" ]; then\n'
        '  case " $* " in *" --commit "*) exit 2 ;; esac\n'
        "  echo 123; exit 0\n"
        "fi\n"
        'if [ "$1" = api ]; then\n'
        '  [ "$3" = --jq ] || exit 2\n'
        '  case "$4" in *"$EXPECTED_ARTIFACT_NAME"*) echo 456 ;; *) exit 2 ;; esac\n'
        "  exit 0\n"
        "fi\n"
        'if [ "$1 $2" = "run download" ]; then\n'
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
        "SOURCE_ENV": "dev",
        "GITHUB_REPOSITORY": "GreenITESO-Proyecto-Integrador/GreenITESO-Backend",
        "EXPECTED_ARTIFACT_NAME": f"release-digest-dev-{'a' * 40}",
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
    target_sha = _git(
        repo, "commit-tree", source_tree, "-p", source_sha, "-m", "merged target"
    )
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "origin", f"{source_sha}:refs/heads/dev")
    _git(repo, "push", "origin", f"{target_sha}:refs/heads/preprod")
    return repo, source_sha, target_sha


def _run_promotion_provenance(
    tmp_path: Path,
    repo: Path,
    source_sha: str,
    release_sha: str,
    *,
    release_environment: str = "staging",
    source_ref: str = "dev",
    source_environment: str = "dev",
    source_workflow: str = "deploy-dev.yml",
    run_head_sha: str | None = None,
    run_ids: tuple[str, ...] = ("123",),
    fail_run_list: bool = False,
) -> subprocess.CompletedProcess[str]:
    fake_bin = tmp_path / "promotion-bin"
    fake_bin.mkdir(exist_ok=True)
    gh = fake_bin / "gh"
    record = f"environment={source_environment}\nrelease_sha={source_sha}\nimage_digest=sha256:{'a' * 64}\n"
    quoted_record = shlex.quote(record)
    gh.write_text(
        "#!/bin/sh\n"
        'if [ "$1 $2" = "run list" ]; then\n'
        f"  {'exit 29' if fail_run_list else ':'}\n"
        '  case " $* " in *" --commit "*) exit 2 ;; esac\n'
        f'  [ "$4" = "{source_workflow}" ] || exit 2\n'
        f"  printf '%s\\n' {shlex.quote(chr(10).join(run_ids))} # run head SHA is {run_head_sha or source_sha}\n"
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = api ]; then\n'
        '  case "$2" in */actions/runs/123/artifacts) ;; *) exit 0 ;; esac\n'
        '  [ "$3" = --jq ] || exit 2\n'
        '  case "$4" in *"$EXPECTED_ARTIFACT_NAME"*) echo 456 ;; *) exit 2 ;; esac\n'
        "  exit 0\n"
        "fi\n"
        'if [ "$1 $2" = "run download" ]; then\n'
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
        "RELEASE_ENVIRONMENT": release_environment,
        "RELEASE_SHA": release_sha,
        "SOURCE_REF": source_ref,
        "SOURCE_RELEASE_SHA": source_sha,
        "SOURCE_ENV": source_environment,
        "GITHUB_REPOSITORY": "GreenITESO-Proyecto-Integrador/GreenITESO-Backend",
        "EXPECTED_ARTIFACT_NAME": f"release-digest-{source_environment}-{source_sha}",
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


def test_production_finds_staging_artifact_when_pull_request_run_head_differs(
    tmp_path: Path,
) -> None:
    repo, dev_sha, preprod_sha = _provenance_repo(tmp_path)
    tree = _git(repo, "rev-parse", f"{preprod_sha}^{{tree}}")
    production_sha = _git(
        repo,
        "commit-tree",
        tree,
        "-p",
        preprod_sha,
        "-m",
        "merged production release",
    )
    result = _run_promotion_provenance(
        tmp_path,
        repo,
        preprod_sha,
        production_sha,
        release_environment="production",
        source_ref="preprod",
        source_environment="staging",
        source_workflow="deploy-staging.yml",
        run_head_sha=dev_sha,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "promotion-output").read_text() == (
        f"image_digest=sha256:{'a' * 64}\n"
    )


def test_promotion_provenance_finds_artifact_on_an_older_successful_run(
    tmp_path: Path,
) -> None:
    repo, source_sha, release_sha = _provenance_repo(tmp_path)
    result = _run_promotion_provenance(
        tmp_path, repo, source_sha, release_sha, run_ids=("456", "123")
    )
    assert result.returncode == 0, result.stderr


def test_promotion_provenance_reports_run_list_failure(tmp_path: Path) -> None:
    repo, source_sha, release_sha = _provenance_repo(tmp_path)
    result = _run_promotion_provenance(
        tmp_path, repo, source_sha, release_sha, fail_run_list=True
    )
    assert result.returncode != 0
    assert "Unable to list successful deploy-dev.yml runs" in result.stderr


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
    assert "group: db-release-${{ inputs.environment }}" in workflow
    assert "queue: max" in workflow
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
    assert (
        "release_sha: ${{ github.event.pull_request.merge_commit_sha }}" in production
    )
    assert "github_environment: production" in production
    assert "main" in promote
    assert "preprod" in promote
    assert "pull-requests: write" in promote
    assert "gh pr create" in promote
    assert "git/refs/heads/" not in promote
    assert "contents: write" not in promote


def test_neon_migrations_only_run_after_protected_nonproduction_branch_updates() -> (
    None
):
    workflow = DATABASE_MIGRATIONS.read_text()
    tests_workflow = (ROOT / ".github" / "workflows" / "tests.yaml").read_text()
    assert 'workflows: ["Django tests"]' in workflow
    assert "types: [completed]" in workflow
    assert "branches: [dev, preprod]" in workflow
    assert "needs.gate.outputs.eligible == 'true'" in workflow
    assert workflow.count("scripts/should-run-neon-migration.py") == 2
    assert "steps.mode.outputs.eligible == 'true'" in workflow
    assert "cancel-in-progress" not in workflow
    assert "secrets." not in workflow.split("  migrate:", 1)[0]
    assert "workflow_dispatch:" not in workflow
    assert "uses: actions/setup-python@v5" in workflow
    assert 'python-version: "3.14"' in workflow
    assert "pull_request:" not in workflow
    assert "github.event.pull_request.merged" not in workflow
    assert "pull-requests: read" in workflow
    assert "head_sha" in workflow
    assert "environment: ${{ github.event.workflow_run.head_branch }}" in workflow
    assert (
        "commits/{commit_sha}/pulls"
        in (ROOT / "scripts/should-run-neon-migration.py").read_text()
    )
    assert (
        "group: db-release-${{ github.event.workflow_run.head_branch == 'preprod' && 'staging' || 'dev' }}"
        in workflow
    )
    assert "queue: max" in workflow
    assert "DATABASE_URL_UNPOOLED" in workflow
    assert "DATABASE_URL" in workflow
    assert workflow.count("persist-credentials: false") == 3
    assert "path: trusted-gate" in workflow
    assert "python3 trusted-gate/scripts/should-run-neon-migration.py" in workflow
    assert 'gh api "repos/${GITHUB_REPOSITORY}/branches/${TARGET_BRANCH}"' in workflow
    assert "git ls-remote origin" not in workflow
    assert (
        workflow.index("Install Django dependencies")
        < workflow.index("Skip a stale queued migration")
        < workflow.index("Require environment-scoped database settings")
    )
    migration_tip_check = (
        workflow.split("- name: Recheck migration eligibility", 1)[1]
        .split("- name: Skip a stale queued migration", 1)[1]
        .split("- name:", 1)[0]
    )
    assert "GH_TOKEN: ${{ github.token }}" in migration_tip_check
    assert "db_smoke" in workflow
    assert (
        "DJANGO_ENV: ${{ github.event.workflow_run.head_branch == 'preprod' && 'staging' || 'dev' }}"
        in workflow
    )
    assert "production" not in workflow
    assert workflow.index("Apply committed migrations") < workflow.index(
        "Verify access through the pooled application role"
    )
    assert "continue-on-error" not in workflow
    assert "scripts/rehearse-migration-conflict.py" in tests_workflow
    assert "group: db-release-${{ inputs.environment }}" in WORKFLOW.read_text()
    assert "queue: max" in WORKFLOW.read_text()


def _run_migration_gate(
    tmp_path: Path,
    *,
    event_name: str = "push",
    conclusion: str = "success",
    merged: bool = True,
    base_branch: str = "dev",
    head_repository: str = "GreenITESO-Proyecto-Integrador/GreenITESO-Backend",
    cloud_deployment_enabled: str = "",
    associated_prs: list[dict[str, object]] | None = None,
    associated_prs_response: str | None = None,
    api_failure: bool = False,
) -> tuple[subprocess.CompletedProcess[str], str]:
    event_path = tmp_path / "event.json"
    output_path = tmp_path / "github-output"
    fake_bin = tmp_path / "bin"
    tmp_path.mkdir(parents=True, exist_ok=True)
    repository = "GreenITESO-Proyecto-Integrador/GreenITESO-Backend"
    commit_sha = "a" * 40
    fake_bin.mkdir()
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "if os.environ.get('FAKE_API_FAILURE') == 'true':\n"
        "    sys.exit(1)\n"
        "print(os.environ['FAKE_ASSOCIATED_PRS'])\n",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    if associated_prs is None:
        associated_prs = (
            [
                {
                    "merged_at": "2026-01-01T00:00:00Z",
                    "merge_commit_sha": commit_sha,
                    "base": {"ref": base_branch},
                    "head": {"repo": {"full_name": head_repository}},
                }
            ]
            if merged
            else []
        )
    event_path.write_text(
        json.dumps(
            {
                "workflow_run": {
                    "event": event_name,
                    "conclusion": conclusion,
                    "head_branch": base_branch,
                    "head_sha": commit_sha,
                    "head_repository": {"full_name": head_repository},
                }
            }
        ),
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "GITHUB_EVENT_PATH": str(event_path),
        "GITHUB_OUTPUT": str(output_path),
        "GITHUB_REPOSITORY": repository,
        "CLOUD_DEPLOYMENT_ENABLED": cloud_deployment_enabled,
        "FAKE_ASSOCIATED_PRS": (
            associated_prs_response
            if associated_prs_response is not None
            else json.dumps(associated_prs)
        ),
        "FAKE_API_FAILURE": "true" if api_failure else "false",
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
    }
    result = subprocess.run(
        ["python3", str(MIGRATION_GATE)],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    return result, output_path.read_text(
        encoding="utf-8"
    ) if output_path.exists() else ""


def test_migration_gate_allows_only_successful_merged_pr_results(
    tmp_path: Path,
) -> None:
    for branch in ("dev", "preprod"):
        result, output = _run_migration_gate(tmp_path / branch, base_branch=branch)
        assert result.returncode == 0, result.stderr
        assert "eligible=true" in output


def test_migration_gate_rejects_nonmerged_and_unsupported_events(
    tmp_path: Path,
) -> None:
    for index, overrides in enumerate(
        (
            {"event_name": "pull_request", "merged": False},
            {"conclusion": "failure"},
            {"merged": False},
            {"base_branch": "main"},
        )
    ):
        result, output = _run_migration_gate(
            tmp_path / str(index),
            **overrides,
        )
        assert result.returncode == 0, result.stderr
        assert "eligible=false" in output


def test_migration_gate_rejects_unsupported_branch_forks_and_cloud_mode(
    tmp_path: Path,
) -> None:
    cases = (
        {"base_branch": "main"},
        {"head_repository": "attacker/fork"},
        {"cloud_deployment_enabled": "true"},
    )
    for index, overrides in enumerate(cases):
        result, output = _run_migration_gate(tmp_path / str(index), **overrides)
        assert result.returncode == 0, result.stderr
        assert "eligible=false" in output


def test_migration_gate_requires_the_associated_merged_pr_result(
    tmp_path: Path,
) -> None:
    mismatched_commit = [
        {
            "merged_at": "2026-01-01T00:00:00Z",
            "merge_commit_sha": "b" * 40,
            "base": {"ref": "dev"},
            "head": {
                "repo": {
                    "full_name": "GreenITESO-Proyecto-Integrador/GreenITESO-Backend"
                }
            },
        }
    ]
    result, output = _run_migration_gate(tmp_path, associated_prs=mismatched_commit)
    assert result.returncode == 0, result.stderr
    assert "eligible=false" in output


def test_migration_gate_fails_if_github_cannot_confirm_associated_pr(
    tmp_path: Path,
) -> None:
    result, output = _run_migration_gate(tmp_path, api_failure=True)
    assert result.returncode != 0
    assert "migration eligibility is unknown" in result.stderr
    assert output == ""


def test_migration_gate_fails_closed_on_malformed_github_json(
    tmp_path: Path,
) -> None:
    result, output = _run_migration_gate(tmp_path, associated_prs_response="not-json")
    assert result.returncode != 0
    assert "invalid pull-request response" in result.stderr
    assert output == ""


def test_migration_gate_fails_closed_on_unexpected_github_response_shape(
    tmp_path: Path,
) -> None:
    result, output = _run_migration_gate(
        tmp_path, associated_prs_response='{"prs": []}'
    )
    assert result.returncode != 0
    assert "unexpected pull-request response" in result.stderr
    assert output == ""


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
    assert (
        "DJANGO_ENV: ${{ github.event.workflow_run.head_branch == 'preprod' && 'staging' || 'dev' }}"
        in migration
    )
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
    assert "TARGET_ENVIRONMENT: ${{ inputs.environment }}" in reusable
    assert "'${{ inputs.environment }}' GitHub Environment" not in reusable
    assert "persist-credentials: false" in reusable
    assert 'gh api "repos/${GITHUB_REPOSITORY}/branches/${RELEASE_REF}"' in reusable
    assert (
        reusable.count('gh api "repos/${GITHUB_REPOSITORY}/branches/${RELEASE_REF}"')
        == 2
    )
    assert reusable.index(
        "Recheck release ref before the release script"
    ) < reusable.index("Migrate and deploy the release digest")
    assert "git ls-remote origin" not in reusable
    stale_check = reusable.split("- name: Reject stale release", 1)[1].split(
        "- name:", 1
    )[0]
    assert "GH_TOKEN: ${{ github.token }}" in stale_check


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
