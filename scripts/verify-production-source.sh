#!/usr/bin/env bash

# Confirms that the commit just pushed to prod is the merge commit of a merged,
# same-repository preprod -> prod pull request before the production release
# may use the production GitHub Environment's secrets.
#
# The release runs on a push to prod (ref refs/heads/prod), because GitHub
# matches environment deployment-branch rules against the run's ref and a
# pull_request run uses refs/pull/<n>/merge, which the production Environment
# rightly does not allow. A push carries no pull-request data, so it is looked
# up through the API instead. prod only changes through promotion PRs (branch
# protection plus the promotion-chain check), so this is defense in depth.
set -euo pipefail

: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"
: "${RELEASE_SHA:?RELEASE_SHA is required}"

if [[ ! "$RELEASE_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "RELEASE_SHA must be a full 40-character commit SHA" >&2
  exit 2
fi
if [[ ! "$GITHUB_REPOSITORY" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
  echo "GITHUB_REPOSITORY must look like owner/repo" >&2
  exit 2
fi

response="$(gh api "repos/${GITHUB_REPOSITORY}/commits/${RELEASE_SHA}/pulls")"

RESPONSE="$response" python3 - <<'PY'
import json
import os
import sys

repository = os.environ["GITHUB_REPOSITORY"]
sha = os.environ["RELEASE_SHA"]

try:
    pulls = json.loads(os.environ["RESPONSE"])
except ValueError:
    print("Unexpected non-JSON response from the pull-request lookup.", file=sys.stderr)
    sys.exit(1)
if not isinstance(pulls, list):
    print("Unexpected pull-request response; refusing to release.", file=sys.stderr)
    sys.exit(1)


def promotes_preprod(pull: object) -> bool:
    if not isinstance(pull, dict):
        return False
    head = pull.get("head") or {}
    base = pull.get("base") or {}
    head_repo = head.get("repo") or {}
    return bool(
        pull.get("merged_at")
        and pull.get("merge_commit_sha") == sha
        and base.get("ref") == "prod"
        and head.get("ref") == "preprod"
        and head_repo.get("full_name") == repository
    )


matches = [pull for pull in pulls if promotes_preprod(pull)]
if not matches:
    print(
        f"Commit {sha} on prod is not the merge of a merged preprod -> prod "
        f"pull request from {repository}; refusing production release.",
        file=sys.stderr,
    )
    sys.exit(1)
print(f"Verified promotion PR #{matches[0].get('number')} for {sha}.")
PY
