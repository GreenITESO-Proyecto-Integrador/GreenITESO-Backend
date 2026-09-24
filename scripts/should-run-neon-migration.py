"""Fail-closed event gate for post-merge Neon migrations."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any


def should_run_migration(
    event: dict[str, Any], repository: str, cloud_deployment_enabled: str
) -> bool:
    """Allow only a successful push that is exactly a merged PR result."""
    workflow_run = event.get("workflow_run")
    if not isinstance(workflow_run, dict):
        return False

    target_branch = workflow_run.get("head_branch")
    commit_sha = workflow_run.get("head_sha")
    head_repository = workflow_run.get("head_repository")
    if not (
        workflow_run.get("conclusion") == "success"
        and workflow_run.get("event") == "push"
        and target_branch in {"dev", "preprod"}
        and isinstance(commit_sha, str)
        and bool(commit_sha)
        and isinstance(head_repository, dict)
        and head_repository.get("full_name") == repository
        and cloud_deployment_enabled.strip().lower() != "true"
    ):
        return False

    try:
        response = subprocess.run(
            [
                "gh",
                "api",
                f"repos/{repository}/commits/{commit_sha}/pulls",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        associated_prs = json.loads(response.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
        return False

    if not isinstance(associated_prs, list):
        return False
    return any(
        isinstance(pr, dict)
        and pr.get("merged_at") is not None
        and pr.get("merge_commit_sha") == commit_sha
        and isinstance(pr.get("base"), dict)
        and pr["base"].get("ref") == target_branch
        and isinstance(pr.get("head"), dict)
        and isinstance(pr["head"].get("repo"), dict)
        and pr["head"]["repo"].get("full_name") == repository
        for pr in associated_prs
    )


def main() -> int:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not event_path or not output_path:
        raise RuntimeError("GITHUB_EVENT_PATH and GITHUB_OUTPUT are required")

    event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    eligible = should_run_migration(
        event,
        os.environ.get("GITHUB_REPOSITORY", ""),
        os.environ.get("CLOUD_DEPLOYMENT_ENABLED", ""),
    )
    with Path(output_path).open("a", encoding="utf-8") as output:
        output.write(f"eligible={'true' if eligible else 'false'}\n")
    print(
        "Eligible for protected non-production migration."
        if eligible
        else "Not eligible; no Neon credentials will be requested."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
