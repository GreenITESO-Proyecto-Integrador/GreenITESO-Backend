#!/usr/bin/env bash

# Verify that a non-dev release is backed by a successful release of the same
# commit in the previous environment. This runs before any GCP authentication.
set -euo pipefail

: "${RELEASE_ENVIRONMENT:?RELEASE_ENVIRONMENT is required}"
: "${RELEASE_SHA:?RELEASE_SHA is required}"

case "$RELEASE_ENVIRONMENT" in
  staging) SOURCE_ENV=dev; SOURCE_WORKFLOW=deploy-dev.yml ;;
  production) SOURCE_ENV=staging; SOURCE_WORKFLOW=deploy-staging.yml ;;
  *) echo "Source provenance is only required for staging or production" >&2; exit 2 ;;
esac

if [[ ! "$RELEASE_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "RELEASE_SHA must be a full 40-character commit SHA" >&2
  exit 2
fi

source_run_id="$(gh run list --workflow "$SOURCE_WORKFLOW" \
  --branch "$SOURCE_ENV" --commit "$RELEASE_SHA" --status completed \
  --limit 50 --json databaseId,conclusion \
  --jq 'map(select(.conclusion == "success")) | .[0].databaseId // empty')"
if [ -z "$source_run_id" ]; then
  echo "No successful ${SOURCE_WORKFLOW} run exists for ${RELEASE_SHA}; refusing ${RELEASE_ENVIRONMENT} release." >&2
  exit 1
fi

artifact_name="release-digest-${SOURCE_ENV}-${RELEASE_SHA}"
record_dir="$(mktemp -d)"
trap 'rm -rf "$record_dir"' EXIT
gh run download "$source_run_id" --name "$artifact_name" --dir "$record_dir"
record_file="$record_dir/release-record.txt"
if [ ! -f "$record_file" ]; then
  echo "Successful source run has no release record artifact; refusing release." >&2
  exit 1
fi

record_environment="$(awk -F= '$1 == "environment" {print $2}' "$record_file")"
record_sha="$(awk -F= '$1 == "release_sha" {print $2}' "$record_file")"
source_digest="$(awk -F= '$1 == "image_digest" {print $2}' "$record_file")"
if [ "$record_environment" != "$SOURCE_ENV" ] || [ "$record_sha" != "$RELEASE_SHA" ] ||
   [[ ! "$source_digest" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "Source release record does not match ${SOURCE_ENV}/${RELEASE_SHA} or has no valid digest." >&2
  exit 1
fi

if [[ -n "${REQUESTED_IMAGE_DIGEST:-}" && "$REQUESTED_IMAGE_DIGEST" != "$source_digest" ]]; then
  echo "Requested image digest does not match the successful source release digest." >&2
  exit 1
fi

echo "image_digest=${source_digest}" >> "${GITHUB_OUTPUT:-/dev/stdout}"
