#!/usr/bin/env bash

# Verify that a non-dev release is backed by a successful release of the same
# commit in the previous environment. This runs before any GCP authentication.
set -euo pipefail

: "${RELEASE_ENVIRONMENT:?RELEASE_ENVIRONMENT is required}"
: "${RELEASE_SHA:?RELEASE_SHA is required}"
SOURCE_RELEASE_SHA="${SOURCE_RELEASE_SHA:-$RELEASE_SHA}"

case "$RELEASE_ENVIRONMENT" in
  staging) SOURCE_ENV=dev; EXPECTED_SOURCE_BRANCH=dev; SOURCE_WORKFLOW=deploy-dev.yml ;;
  production) SOURCE_ENV=staging; EXPECTED_SOURCE_BRANCH=preprod; SOURCE_WORKFLOW=deploy-staging.yml ;;
  *) echo "Source provenance is only required for staging or production" >&2; exit 2 ;;
esac

SOURCE_BRANCH="${SOURCE_REF:-$EXPECTED_SOURCE_BRANCH}"
if [ "$SOURCE_BRANCH" != "$EXPECTED_SOURCE_BRANCH" ]; then
  echo "${RELEASE_ENVIRONMENT} releases must use source branch ${EXPECTED_SOURCE_BRANCH}." >&2
  exit 2
fi

if [[ ! "$RELEASE_SHA" =~ ^[0-9a-f]{40}$ || ! "$SOURCE_RELEASE_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "RELEASE_SHA and SOURCE_RELEASE_SHA must be full 40-character commit SHAs" >&2
  exit 2
fi

if [ -n "${SOURCE_REF:-}" ]; then
  current_source_sha="$(git ls-remote origin "refs/heads/${SOURCE_BRANCH}" | awk '{print $1}')"
  if [ "$current_source_sha" != "$SOURCE_RELEASE_SHA" ]; then
    echo "Stale source release: ${SOURCE_BRANCH} is ${current_source_sha:-missing}, requested ${SOURCE_RELEASE_SHA}." >&2
    exit 1
  fi
fi

if [ "$SOURCE_RELEASE_SHA" != "$RELEASE_SHA" ]; then
  git fetch --no-tags origin "$SOURCE_RELEASE_SHA"
  release_tree="$(git rev-parse "${RELEASE_SHA}^{tree}")"
  source_tree="$(git rev-parse "${SOURCE_RELEASE_SHA}^{tree}")"
  if [ "$release_tree" != "$source_tree" ]; then
    echo "Source image tree does not match the merged release commit; refusing release." >&2
    exit 1
  fi
fi

artifact_name="release-digest-${SOURCE_ENV}-${SOURCE_RELEASE_SHA}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"
record_dir="$(mktemp -d)"
trap 'rm -rf "$record_dir"' EXIT

# For pull_request workflows, GitHub may index a run by the PR head SHA even
# though the release record describes the merged base-branch SHA. Find the
# immutable artifact by its exact release SHA instead of assuming run.head_sha.
source_run_id=""
while IFS= read -r candidate_run_id; do
  [[ "$candidate_run_id" =~ ^[0-9]+$ ]] || continue
  artifacts_json="$(gh api \
    "repos/${GITHUB_REPOSITORY}/actions/runs/${candidate_run_id}/artifacts")"
  artifact_id="$(printf '%s' "$artifacts_json" | jq -r \
    --arg name "$artifact_name" \
    '.artifacts[]? | select(.name == $name and .expired == false) | .id' \
    | head -n 1)"
  if [ -n "$artifact_id" ]; then
    source_run_id="$candidate_run_id"
    break
  fi
done < <(gh run list --workflow "$SOURCE_WORKFLOW" --status completed \
  --limit 1000 --json databaseId,conclusion \
  --jq 'map(select(.conclusion == "success")) | .[].databaseId')

if [ -z "$source_run_id" ]; then
  echo "No successful ${SOURCE_WORKFLOW} run contains artifact ${artifact_name}; refusing ${RELEASE_ENVIRONMENT} release." >&2
  exit 1
fi

gh run download "$source_run_id" --name "$artifact_name" --dir "$record_dir"
record_file="$record_dir/release-record.txt"
if [ ! -f "$record_file" ]; then
  echo "Successful source run has no release record artifact; refusing release." >&2
  exit 1
fi

record_environment="$(awk -F= '$1 == "environment" {print $2}' "$record_file")"
record_sha="$(awk -F= '$1 == "release_sha" {print $2}' "$record_file")"
source_digest="$(awk -F= '$1 == "image_digest" {print $2}' "$record_file")"
if [ "$record_environment" != "$SOURCE_ENV" ] || [ "$record_sha" != "$SOURCE_RELEASE_SHA" ] ||
   [[ ! "$source_digest" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "Source release record does not match ${SOURCE_ENV}/${RELEASE_SHA} or has no valid digest." >&2
  exit 1
fi

if [[ -n "${REQUESTED_IMAGE_DIGEST:-}" && "$REQUESTED_IMAGE_DIGEST" != "$source_digest" ]]; then
  echo "Requested image digest does not match the successful source release digest." >&2
  exit 1
fi

echo "image_digest=${source_digest}" >> "${GITHUB_OUTPUT:-/dev/stdout}"
