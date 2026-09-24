"""Fail-closed event gate for post-merge Neon migrations."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def should_run_migration(
    event: dict[str, Any], repository: str, cloud_deployment_enabled: str
) -> bool:
    """Allow only successful same-repository pushes to dev/preprod."""
    workflow_run = event.get("workflow_run")
    if not isinstance(workflow_run, dict):
        return False

    head_repository = workflow_run.get("head_repository")
    return (
        workflow_run.get("conclusion") == "success"
        and workflow_run.get("event") == "push"
        and workflow_run.get("head_branch") in {"dev", "preprod"}
        and isinstance(head_repository, dict)
        and head_repository.get("full_name") == repository
        and cloud_deployment_enabled.strip().lower() != "true"
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
