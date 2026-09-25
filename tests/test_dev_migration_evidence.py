"""Contract tests for exact-SHA Neon migration evidence checks."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify-dev-migration-evidence.py"
REPOSITORY = "GreenITESO-Proyecto-Integrador/GreenITESO-Backend"
SOURCE_SHA = "a" * 40
STAGING_SHA = "b" * 40
TEST_RUN_ID = "100"
MIGRATION_RUN_ID = 200
CLOUD_RELEASE_RUN_ID = 300

spec = importlib.util.spec_from_file_location("dev_evidence", SCRIPT)
assert spec is not None and spec.loader is not None
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class FakeGitHub:
    def __init__(self, *, source_sha: str = SOURCE_SHA) -> None:
        self.repository = REPOSITORY
        self.source_sha = source_sha
        self.parent_sha = source_sha
        self.parent_conclusion = "success"
        self.migration_conclusion = "success"
        self.migration_status = "completed"
        self.migration_event = "workflow_run"
        self.migration_path = gate.WORKFLOW_PATH
        self.migration_repo = REPOSITORY
        self.migration_step_conclusions = {
            gate.MIGRATION_STEP: "success",
            gate.SMOKE_STEP: "success",
        }
        self.artifact_names = [f"{gate.EVIDENCE_PREFIX}{source_sha}-{TEST_RUN_ID}"]
        self.cloud_artifact_names: list[str] = []
        self.cloud_release = {
            "id": CLOUD_RELEASE_RUN_ID,
            "status": "completed",
            "conclusion": "success",
            "event": "push",
            "head_branch": "dev",
            "head_sha": source_sha,
            "head_repository": {"full_name": REPOSITORY},
            "path": gate.DEV_RELEASE_WORKFLOW_PATH,
        }
        self.dev_pr = {
            "merged_at": "2026-09-24T12:00:00Z",
            "merge_commit_sha": source_sha,
            "base": {"ref": "dev", "repo": {"full_name": REPOSITORY}},
            "head": {"ref": "feature/example", "repo": {"full_name": REPOSITORY}},
        }
        self.staging_pr = {
            "merged_at": "2026-09-25T12:00:00Z",
            "merge_commit_sha": STAGING_SHA,
            "base": {"ref": "preprod", "repo": {"full_name": REPOSITORY}},
            "head": {
                "ref": "dev",
                "sha": source_sha,
                "repo": {"full_name": REPOSITORY},
            },
        }
        self.fail_at: str | None = None

    def get(self, endpoint: str) -> dict[str, Any] | list[Any]:
        if self.fail_at == endpoint:
            raise gate.EvidenceError("simulated API outage")
        if endpoint == f"commits/{self.source_sha}/pulls":
            return [self.dev_pr]
        if endpoint == f"commits/{STAGING_SHA}/pulls":
            return [self.staging_pr]
        if endpoint.startswith("actions/runs/") and endpoint.endswith(
            "/jobs?per_page=100"
        ):
            steps = [
                {"name": name, "conclusion": conclusion}
                for name, conclusion in self.migration_step_conclusions.items()
            ]
            return {
                "jobs": [
                    {
                        "status": "completed",
                        "conclusion": self.migration_conclusion,
                        "steps": steps,
                    }
                ]
            }
        if endpoint == f"actions/runs/{MIGRATION_RUN_ID}":
            return {
                "status": self.migration_status,
                "conclusion": self.migration_conclusion,
                "event": self.migration_event,
                "path": self.migration_path,
                "repository": {"full_name": self.migration_repo},
                # workflow_run executes against the default ref. This is not
                # the tested SHA; its triggering SHA is verified via artifact.
                "head_sha": "default-ref-sha",
            }
        if endpoint.startswith("actions/workflows/tests.yaml/runs?"):
            return {
                "total_count": 1,
                "workflow_runs": [
                    {
                        "id": int(TEST_RUN_ID),
                        "status": "completed",
                        "conclusion": self.parent_conclusion,
                        "event": "push",
                        "head_branch": "dev",
                        "head_sha": self.parent_sha,
                        "head_repository": {"full_name": REPOSITORY},
                        "path": gate.TEST_WORKFLOW_PATH,
                    }
                ],
            }
        if endpoint.startswith("actions/workflows/deploy-dev.yml/runs?"):
            return {"total_count": 1, "workflow_runs": [self.cloud_release]}
        if endpoint == f"actions/runs/{TEST_RUN_ID}":
            return {
                "status": "completed",
                "conclusion": self.parent_conclusion,
                "event": "push",
                "head_branch": "dev",
                "head_sha": self.parent_sha,
                "head_repository": {"full_name": REPOSITORY},
                "path": gate.TEST_WORKFLOW_PATH,
            }
        if endpoint.startswith("actions/artifacts?"):
            requested_name = endpoint.split("name=", 1)[1].split("&", 1)[0]
            artifacts = [
                {
                    "name": name,
                    "expired": False,
                    "workflow_run": {
                        "id": (
                            CLOUD_RELEASE_RUN_ID
                            if name in self.cloud_artifact_names
                            else MIGRATION_RUN_ID
                        )
                    },
                }
                for name in self.artifact_names + self.cloud_artifact_names
                if name == requested_name
            ]
            return {
                "total_count": len(artifacts),
                "artifacts": artifacts,
            }
        raise AssertionError(f"Unexpected API endpoint: {endpoint}")

    def artifacts(self, name_prefix: str) -> list[dict[str, Any]]:
        data = self.get(f"actions/artifacts?name={name_prefix}&per_page=100&page=1")
        return [
            artifact
            for artifact in data["artifacts"]
            if artifact["name"] == name_prefix
        ]


def _event() -> dict[str, Any]:
    return {
        "pull_request": {
            "state": "open",
            "base": {"ref": "preprod", "repo": {"full_name": REPOSITORY}},
            "head": {
                "ref": "dev",
                "sha": SOURCE_SHA,
                "repo": {"full_name": REPOSITORY},
            },
        }
    }


def _verify_with(fake: FakeGitHub, event: dict[str, Any], mode: str) -> str:
    original = gate.GitHub
    gate.GitHub = lambda _repository: fake
    try:
        return gate.verify(event, mode, REPOSITORY)
    finally:
        gate.GitHub = original


def test_promotion_check_accepts_exact_sha_and_default_ref_migration_run() -> None:
    fake = FakeGitHub()
    assert _verify_with(fake, _event(), "pull_request") == SOURCE_SHA


def test_cloud_dev_release_artifact_proves_exact_sha_migration_and_smoke() -> None:
    fake = FakeGitHub()
    fake.artifact_names = []
    fake.cloud_artifact_names = [
        f"{gate.CLOUD_EVIDENCE_PREFIX}{SOURCE_SHA}-{CLOUD_RELEASE_RUN_ID}"
    ]
    assert _verify_with(fake, {"source_sha": SOURCE_SHA}, "source_sha") == SOURCE_SHA


def test_cloud_dev_release_rejects_wrong_sha_workflow_or_artifact_run() -> None:
    fake = FakeGitHub()
    fake.artifact_names = []
    fake.cloud_artifact_names = [
        f"{gate.CLOUD_EVIDENCE_PREFIX}{'c' * 40}-{CLOUD_RELEASE_RUN_ID}"
    ]
    try:
        _verify_with(fake, {"source_sha": SOURCE_SHA}, "source_sha")
    except gate.EvidenceError as error:
        assert "No completed Neon or cloud dev release" in str(error)
    else:
        raise AssertionError("accepted cloud evidence for a different SHA")

    fake = FakeGitHub()
    fake.artifact_names = []
    fake.cloud_artifact_names = [
        f"{gate.CLOUD_EVIDENCE_PREFIX}{SOURCE_SHA}-{CLOUD_RELEASE_RUN_ID}"
    ]
    fake.cloud_release["path"] = ".github/workflows/deploy-staging.yml"
    try:
        _verify_with(fake, {"source_sha": SOURCE_SHA}, "source_sha")
    except gate.EvidenceError:
        pass
    else:
        raise AssertionError("accepted evidence from a non-dev release workflow")

    fake = FakeGitHub()
    fake.artifact_names = []
    fake.cloud_artifact_names = [
        f"{gate.CLOUD_EVIDENCE_PREFIX}{SOURCE_SHA}-{CLOUD_RELEASE_RUN_ID + 1}"
    ]
    try:
        _verify_with(fake, {"source_sha": SOURCE_SHA}, "source_sha")
    except gate.EvidenceError:
        pass
    else:
        raise AssertionError("accepted an artifact not attached to the release run")


def test_staging_check_uses_exact_merged_dev_to_preprod_pr_head() -> None:
    fake = FakeGitHub()
    event = {"workflow_run": {"head_branch": "preprod", "head_sha": STAGING_SHA}}
    assert _verify_with(fake, event, "staging_merge") == SOURCE_SHA


def test_failed_or_skipped_migration_steps_do_not_prove_evidence() -> None:
    for required_step in (gate.MIGRATION_STEP, gate.SMOKE_STEP):
        for step_conclusion in ("failure", "skipped"):
            fake = FakeGitHub()
            fake.migration_step_conclusions[required_step] = step_conclusion
            try:
                _verify_with(fake, _event(), "pull_request")
            except gate.EvidenceError as error:
                assert "No completed Neon or cloud dev release" in str(error)
            else:
                raise AssertionError(
                    f"accepted {required_step} conclusion {step_conclusion}"
                )


def test_failed_migration_job_is_not_accepted() -> None:
    fake = FakeGitHub()
    fake.migration_conclusion = "failure"
    try:
        _verify_with(fake, _event(), "pull_request")
    except gate.EvidenceError as error:
        assert "No completed Neon or cloud dev release" in str(error)
    else:
        raise AssertionError("accepted a failed migration workflow")


def test_wrong_parent_sha_and_stale_artifact_are_rejected() -> None:
    fake = FakeGitHub()
    fake.parent_sha = "c" * 40
    try:
        _verify_with(fake, _event(), "pull_request")
    except gate.EvidenceError as error:
        assert "No completed Neon or cloud dev release" in str(error)
    else:
        raise AssertionError("accepted a test run for a different SHA")

    stale = FakeGitHub()
    stale.artifact_names = [f"{gate.EVIDENCE_PREFIX}{'c' * 40}-{TEST_RUN_ID}"]
    try:
        _verify_with(stale, _event(), "pull_request")
    except gate.EvidenceError as error:
        assert "No completed Neon or cloud dev release" in str(error)
    else:
        raise AssertionError("accepted evidence for a stale SHA")


def test_github_api_failure_fails_closed() -> None:
    fake = FakeGitHub()
    fake.fail_at = f"actions/runs/{TEST_RUN_ID}"
    try:
        _verify_with(fake, _event(), "pull_request")
    except gate.EvidenceError as error:
        assert "simulated API outage" in str(error)
    else:
        raise AssertionError("accepted evidence after an API failure")


def test_closed_unmerged_promotion_pr_is_rejected() -> None:
    event = _event()
    event["pull_request"]["state"] = "closed"
    try:
        _verify_with(FakeGitHub(), event, "pull_request")
    except gate.EvidenceError as error:
        assert "open same-repository" in str(error)
    else:
        raise AssertionError("accepted a closed, unmerged promotion PR")


def test_closed_unmerged_staging_promotion_is_rejected() -> None:
    fake = FakeGitHub()
    fake.staging_pr["merged_at"] = None
    event = {"workflow_run": {"head_branch": "preprod", "head_sha": STAGING_SHA}}
    try:
        _verify_with(fake, event, "staging_merge")
    except gate.EvidenceError as error:
        assert "same-repository dev-to-preprod promotion PR" in str(error)
    else:
        raise AssertionError("accepted staging migration without a merged PR")


def test_wrong_source_merge_provenance_is_rejected() -> None:
    fake = FakeGitHub()
    fake.dev_pr["merge_commit_sha"] = "d" * 40
    try:
        _verify_with(fake, _event(), "pull_request")
    except gate.EvidenceError as error:
        assert "exact merge commit" in str(error)
    else:
        raise AssertionError("accepted incorrect dev merge provenance")


def test_workflows_make_both_gates_required_and_use_read_only_permissions() -> None:
    promotion = (ROOT / ".github/workflows/verify-promotion-source.yml").read_text()
    migrations = (ROOT / ".github/workflows/database-migrations.yml").read_text()
    deploy = (ROOT / ".github/workflows/_deploy.yml").read_text()
    promote = (ROOT / ".github/workflows/promote.yml").read_text()
    pipeline = (ROOT / ".github/workflows/pipeline-tests.yml").read_text()
    assert "name: Enforce promotion chain" in promotion
    assert "actions: read" in promotion
    assert "pull-requests: read" in promotion
    assert "pull-requests: write" not in promotion
    assert "DEV_EVIDENCE_MODE: pull_request" in promotion
    assert "DEV_EVIDENCE_MODE: staging_merge" in migrations
    assert "actions: read" in migrations
    assert "upload-artifact@v4" in migrations
    assert (
        "dev-db-evidence-${{ github.event.workflow_run.head_sha }}-${{ github.event.workflow_run.id }}"
        in migrations
    )
    assert (
        migrations.index("Apply committed migrations with the direct migrator role")
        < migrations.index("Verify access through the pooled application role")
        < migrations.index("Publish successful dev migration evidence")
    )
    assert (
        "if: success() && github.event.workflow_run.head_branch == 'dev'" in migrations
    )
    assert migrations.index(
        "Require dev migration evidence for staging promotion"
    ) < migrations.index("Require environment-scoped database settings")
    assert migrations.index(
        "Recheck dev migration evidence for staging promotion"
    ) < migrations.index("Require environment-scoped database settings")
    assert (
        "dev-cloud-db-evidence-${{ inputs.release_sha }}-${{ github.run_id }}" in deploy
    )
    assert "if: success() && inputs.environment == 'dev'" in deploy
    assert "DEV_EVIDENCE_MODE=source_sha" in promote
    assert promote.index("DEV_EVIDENCE_MODE=source_sha") < promote.index("gh pr create")
    assert "Rerun this workflow after the successful dev release" in promote
    assert pipeline.count('"tests/test_dev_migration_evidence.py"') == 2
    assert (
        "tests/test_release_pipeline.py tests/test_dev_migration_evidence.py"
        in pipeline
    )
    assert "current_source_sha" in promote


def test_cloud_release_marker_follows_both_successful_smoke_boundaries() -> None:
    release = (ROOT / "scripts/release.sh").read_text()
    migration_wait = release.index('gcloud run jobs execute "$MIGRATION_JOB_NAME"')
    smoke_wait = release.index('gcloud run jobs execute "$SMOKE_JOB_NAME"')
    marker = release.index("app_role_smoke=success")
    app_deploy = release.index('gcloud run deploy "$CLOUD_RUN_SERVICE"')
    assert migration_wait < smoke_wait < marker < app_deploy
