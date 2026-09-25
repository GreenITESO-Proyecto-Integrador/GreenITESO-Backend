"""Require exact-SHA dev migration and app-role smoke evidence from Actions."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from typing import Any

WORKFLOW_PATH = ".github/workflows/database-migrations.yml"
TEST_WORKFLOW_PATH = ".github/workflows/tests.yaml"
MIGRATION_STEP = "Apply committed migrations with the direct migrator role"
SMOKE_STEP = "Verify access through the pooled application role"
EVIDENCE_PREFIX = "dev-db-evidence-"
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


class EvidenceError(RuntimeError):
    """GitHub evidence was missing, malformed, or did not meet the contract."""


class GitHub:
    def __init__(self, repository: str) -> None:
        self.repository = repository

    def get(self, endpoint: str) -> dict[str, Any] | list[Any]:
        try:
            response = subprocess.run(
                ["gh", "api", f"repos/{self.repository}/{endpoint}"],
                check=True,
                capture_output=True,
                text=True,
            )
            data = json.loads(response.stdout)
        except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
            raise EvidenceError(
                f"GitHub could not verify required evidence at {endpoint}."
            ) from None
        if not isinstance(data, (dict, list)):
            raise EvidenceError(f"GitHub returned invalid data at {endpoint}.")
        return data

    def artifacts(self, name: str) -> list[dict[str, Any]]:
        matches: list[dict[str, Any]] = []
        page = 1
        while True:
            data = self.get(f"actions/artifacts?name={name}&per_page=100&page={page}")
            if not isinstance(data, dict) or not isinstance(
                data.get("artifacts"), list
            ):
                raise EvidenceError("GitHub returned an invalid artifacts response.")
            matches.extend(
                item
                for item in data["artifacts"]
                if isinstance(item, dict)
                and item.get("name") == name
                and item.get("expired") is False
            )
            total_count = data.get("total_count")
            if not isinstance(total_count, int):
                raise EvidenceError("GitHub omitted the artifact count.")
            if page * 100 >= total_count:
                return matches
            page += 1


def _require_merged_dev_pr(api: GitHub, source_sha: str) -> None:
    associated = api.get(f"commits/{source_sha}/pulls")
    if not isinstance(associated, list):
        raise EvidenceError("GitHub returned invalid source PR provenance.")
    if not any(
        isinstance(pr, dict)
        and pr.get("merged_at") is not None
        and pr.get("merge_commit_sha") == source_sha
        and isinstance(pr.get("base"), dict)
        and pr["base"].get("ref") == "dev"
        and isinstance(pr.get("base", {}).get("repo"), dict)
        and pr["base"]["repo"].get("full_name") == api.repository
        and isinstance(pr.get("head"), dict)
        and isinstance(pr.get("head", {}).get("repo"), dict)
        and pr["head"]["repo"].get("full_name") == api.repository
        for pr in associated
    ):
        raise EvidenceError(
            f"Source {source_sha} is not the exact merge commit of a same-repository PR into dev."
        )


def _require_parent_test_run(api: GitHub, run_id: str, source_sha: str) -> None:
    run = api.get(f"actions/runs/{run_id}")
    if not isinstance(run, dict) or not (
        run.get("status") == "completed"
        and run.get("conclusion") == "success"
        and run.get("event") == "push"
        and run.get("head_branch") == "dev"
        and run.get("head_sha") == source_sha
        and isinstance(run.get("head_repository"), dict)
        and run["head_repository"].get("full_name") == api.repository
        and run.get("path") == TEST_WORKFLOW_PATH
    ):
        raise EvidenceError(
            f"Django tests run {run_id} does not prove a successful push of {source_sha} to this repository's dev branch."
        )


def _successful_dev_test_runs(api: GitHub, source_sha: str) -> list[str]:
    page = 1
    run_ids: list[str] = []
    while True:
        result = api.get(
            "actions/workflows/tests.yaml/runs?"
            f"event=push&branch=dev&head_sha={source_sha}&per_page=100&page={page}"
        )
        if not isinstance(result, dict) or not isinstance(
            result.get("workflow_runs"), list
        ):
            raise EvidenceError("GitHub returned invalid Django test run evidence.")
        run_ids.extend(
            str(run["id"])
            for run in result["workflow_runs"]
            if isinstance(run, dict)
            and isinstance(run.get("id"), int)
            and run.get("status") == "completed"
            and run.get("conclusion") == "success"
            and run.get("event") == "push"
            and run.get("head_branch") == "dev"
            and run.get("head_sha") == source_sha
            and isinstance(run.get("head_repository"), dict)
            and run["head_repository"].get("full_name") == api.repository
            and run.get("path") == TEST_WORKFLOW_PATH
        )
        total_count = result.get("total_count")
        if not isinstance(total_count, int):
            raise EvidenceError("GitHub omitted the Django test run count.")
        if page * 100 >= total_count:
            return run_ids
        page += 1


def _has_successful_required_steps(api: GitHub, run_id: str) -> bool:
    result = api.get(f"actions/runs/{run_id}/jobs?per_page=100")
    if not isinstance(result, dict) or not isinstance(result.get("jobs"), list):
        raise EvidenceError("GitHub returned invalid migration job evidence.")
    migration_count = 0
    smoke_count = 0
    for job in result["jobs"]:
        if not isinstance(job, dict) or not (
            job.get("status") == "completed" and job.get("conclusion") == "success"
        ):
            continue
        steps = job.get("steps")
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, dict) or step.get("conclusion") != "success":
                continue
            if step.get("name") == MIGRATION_STEP:
                migration_count += 1
            elif step.get("name") == SMOKE_STEP:
                smoke_count += 1
    return migration_count == 1 and smoke_count == 1


def _require_dev_evidence(api: GitHub, source_sha: str) -> None:
    if not SHA_PATTERN.fullmatch(source_sha):
        raise EvidenceError("The source SHA is not a full lowercase Git SHA.")
    _require_merged_dev_pr(api, source_sha)
    for parent_id in _successful_dev_test_runs(api, source_sha):
        _require_parent_test_run(api, parent_id, source_sha)
        name = f"{EVIDENCE_PREFIX}{source_sha}-{parent_id}"
        for artifact in api.artifacts(name):
            workflow_run = artifact.get("workflow_run")
            if not isinstance(workflow_run, dict) or not isinstance(
                workflow_run.get("id"), int
            ):
                continue
            migration_run_id = str(workflow_run["id"])
            migration_run = api.get(f"actions/runs/{migration_run_id}")
            if not isinstance(migration_run, dict) or not (
                migration_run.get("status") == "completed"
                and migration_run.get("conclusion") == "success"
                and migration_run.get("event") == "workflow_run"
                and migration_run.get("path") == WORKFLOW_PATH
                and isinstance(migration_run.get("repository"), dict)
                and migration_run["repository"].get("full_name") == api.repository
            ):
                continue
            if _has_successful_required_steps(api, migration_run_id):
                return
    raise EvidenceError(
        f"No completed Neon migration workflow proves both migration and app-role smoke success for dev SHA {source_sha}."
    )


def _source_sha_for_staging_merge(api: GitHub, merge_sha: str) -> str:
    associated = api.get(f"commits/{merge_sha}/pulls")
    if not isinstance(associated, list):
        raise EvidenceError("GitHub returned invalid staging merge provenance.")
    for pr in associated:
        if (
            isinstance(pr, dict)
            and pr.get("merged_at") is not None
            and pr.get("merge_commit_sha") == merge_sha
            and isinstance(pr.get("base"), dict)
            and pr["base"].get("ref") == "preprod"
            and isinstance(pr.get("base", {}).get("repo"), dict)
            and pr["base"]["repo"].get("full_name") == api.repository
            and isinstance(pr.get("head"), dict)
            and pr["head"].get("ref") == "dev"
            and isinstance(pr.get("head", {}).get("repo"), dict)
            and pr["head"]["repo"].get("full_name") == api.repository
            and isinstance(pr["head"].get("sha"), str)
        ):
            return pr["head"]["sha"]
    raise EvidenceError(
        f"Staging SHA {merge_sha} is not the exact merge commit of a same-repository dev-to-preprod promotion PR."
    )


def verify(event: dict[str, Any], mode: str, repository: str) -> str:
    api = GitHub(repository)
    if mode == "pull_request":
        pr = event.get("pull_request")
        if not isinstance(pr, dict):
            raise EvidenceError("Pull request event data is missing.")
        base = pr.get("base")
        head = pr.get("head")
        if not (
            pr.get("state") == "open"
            and isinstance(base, dict)
            and base.get("ref") == "preprod"
            and isinstance(base.get("repo"), dict)
            and base["repo"].get("full_name") == repository
            and isinstance(head, dict)
            and head.get("ref") == "dev"
            and isinstance(head.get("repo"), dict)
            and head["repo"].get("full_name") == repository
            and isinstance(head.get("sha"), str)
        ):
            raise EvidenceError(
                "Dev evidence applies only to an open same-repository dev-to-preprod promotion PR."
            )
        source_sha = head["sha"]
    elif mode == "staging_merge":
        run = event.get("workflow_run")
        if not isinstance(run, dict) or run.get("head_branch") != "preprod":
            raise EvidenceError("Staging evidence requires a preprod workflow_run.")
        merge_sha = run.get("head_sha")
        if not isinstance(merge_sha, str):
            raise EvidenceError("The staging merge SHA is missing.")
        source_sha = _source_sha_for_staging_merge(api, merge_sha)
    else:
        raise EvidenceError(f"Unsupported evidence gate mode: {mode}")
    _require_dev_evidence(api, source_sha)
    return source_sha


def main() -> int:
    try:
        event_path = os.environ["GITHUB_EVENT_PATH"]
        mode = os.environ["DEV_EVIDENCE_MODE"]
        repository = os.environ["GITHUB_REPOSITORY"]
        with open(event_path, encoding="utf-8") as event_file:
            event = json.load(event_file)
        if not isinstance(event, dict):
            raise EvidenceError("GitHub event payload is invalid.")
        source_sha = verify(event, mode, repository)
        print(f"Verified dev migration and app-role smoke evidence for {source_sha}.")
        return 0
    except (KeyError, OSError, json.JSONDecodeError, EvidenceError) as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
