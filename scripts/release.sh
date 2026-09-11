#!/usr/bin/env bash

# Promote one approved commit through a migration gate and Cloud Run deploy.
# The service is updated only after the one-shot job succeeds.
set -euo pipefail

required() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "${name} is required for a ${RELEASE_ENVIRONMENT:-unknown} release" >&2
    exit 2
  fi
}

for variable in \
  GCP_PROJECT_ID GCP_REGION ARTIFACT_REGISTRY_LOCATION ARTIFACT_REGISTRY_REPO \
  IMAGE_PROJECT_ID CLOUD_RUN_SERVICE RELEASE_ENVIRONMENT RELEASE_SHA \
  DJANGO_SECRET_KEY_SECRET DATABASE_URL_SECRET DATABASE_URL_UNPOOLED_SECRET \
  DJANGO_ALLOWED_HOSTS MIGRATION_JOB_NAME RUNTIME_SERVICE_ACCOUNT \
  MIGRATION_SERVICE_ACCOUNT CLOUD_RUN_MAX_INSTANCES CLOUD_RUN_CONCURRENCY; do
  required "$variable"
done

if [[ ! "$RELEASE_SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "RELEASE_SHA must be a full 40-character commit SHA" >&2
  exit 2
fi

case "$RELEASE_ENVIRONMENT" in
  dev|staging|production) ;;
  *)
    echo "RELEASE_ENVIRONMENT must be dev, staging, or production" >&2
    exit 2
    ;;
esac

if [[ "${BUILD_IMAGE:-false}" != true && "${BUILD_IMAGE:-false}" != false ]]; then
  echo "BUILD_IMAGE must be true or false" >&2
  exit 2
fi
if [[ "$RELEASE_ENVIRONMENT" != dev && "${BUILD_IMAGE:-false}" == true ]]; then
  echo "BUILD_IMAGE=true is only allowed for dev releases" >&2
  exit 2
fi
if [[ "$RELEASE_ENVIRONMENT" != dev && -z "${EXPECTED_IMAGE_DIGEST:-}" ]]; then
  echo "EXPECTED_IMAGE_DIGEST is required for ${RELEASE_ENVIRONMENT} releases" >&2
  exit 2
fi
if [[ ! "$CLOUD_RUN_MAX_INSTANCES" =~ ^[1-9][0-9]*$ ]] ||
   [[ ! "$CLOUD_RUN_CONCURRENCY" =~ ^[1-9][0-9]*$ ]]; then
  echo "CLOUD_RUN_MAX_INSTANCES and CLOUD_RUN_CONCURRENCY must be positive integers" >&2
  exit 2
fi
if [[ "$RUNTIME_SERVICE_ACCOUNT" == "$MIGRATION_SERVICE_ACCOUNT" ]]; then
  echo "RUNTIME_SERVICE_ACCOUNT and MIGRATION_SERVICE_ACCOUNT must be distinct" >&2
  exit 2
fi
if [[ "$DJANGO_ALLOWED_HOSTS" == *$'\n'* || "$DJANGO_ALLOWED_HOSTS" == *'@'* ]]; then
  echo "DJANGO_ALLOWED_HOSTS cannot contain newlines or @ (the release uses @ as the gcloud delimiter)" >&2
  exit 2
fi

IMAGE_REGISTRY="${ARTIFACT_REGISTRY_LOCATION}-docker.pkg.dev/${IMAGE_PROJECT_ID}/${ARTIFACT_REGISTRY_REPO}"
IMAGE="${IMAGE_REGISTRY}/greeniteso"
IMAGE_TAG="sha-${RELEASE_SHA}"
IMAGE_BY_TAG="${IMAGE}:${IMAGE_TAG}"

if [[ "${BUILD_IMAGE:-false}" == true ]]; then
  gcloud auth configure-docker "${ARTIFACT_REGISTRY_LOCATION}-docker.pkg.dev" --quiet
  docker build --tag "$IMAGE_BY_TAG" --file Dockerfile .
  docker push "$IMAGE_BY_TAG"
fi

# Resolve the source release tag and, when promotion supplied a successful
# source record, require that it still resolves to the recorded digest.
IMAGE_DIGEST="$(gcloud artifacts docker images describe "$IMAGE_BY_TAG" \
  --project="$IMAGE_PROJECT_ID" \
  --format='value(image_summary.digest)' | tr -d '[:space:]')"
if [[ ! "$IMAGE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "Artifact Registry did not return a valid digest for ${IMAGE_BY_TAG}" >&2
  exit 1
fi
if [[ -n "${EXPECTED_IMAGE_DIGEST:-}" ]]; then
  if [[ ! "$EXPECTED_IMAGE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    echo "EXPECTED_IMAGE_DIGEST must be a sha256 digest" >&2
    exit 2
  fi
  if [[ "$IMAGE_DIGEST" != "$EXPECTED_IMAGE_DIGEST" ]]; then
    echo "Source release digest ${EXPECTED_IMAGE_DIGEST} does not match registry digest ${IMAGE_DIGEST}" >&2
    exit 1
  fi
fi
IMAGE_BY_DIGEST="${IMAGE}@${IMAGE_DIGEST}"

# gcloud uses comma-separated KEY=VALUE pairs by default. The @ delimiter
# preserves comma-separated ALLOWED_HOSTS values as one environment value.
RUNTIME_ENV="^@^DJANGO_ENV=${RELEASE_ENVIRONMENT}@DJANGO_DEPLOYED=true@DJANGO_CONNECTION_ROLE=app@DJANGO_ALLOWED_HOSTS=${DJANGO_ALLOWED_HOSTS}"
MIGRATION_ENV="^@^DJANGO_ENV=${RELEASE_ENVIRONMENT}@DJANGO_DEPLOYED=true@DJANGO_CONNECTION_ROLE=direct@DJANGO_ALLOWED_HOSTS=${DJANGO_ALLOWED_HOSTS}"
RUNTIME_SECRETS="DJANGO_SECRET_KEY=${DJANGO_SECRET_KEY_SECRET}:latest,DATABASE_URL=${DATABASE_URL_SECRET}:latest"
MIGRATION_SECRETS="DJANGO_SECRET_KEY=${DJANGO_SECRET_KEY_SECRET}:latest,DATABASE_URL_UNPOOLED=${DATABASE_URL_UNPOOLED_SECRET}:latest"

JOB_ARGS=(
  "--image=${IMAGE_BY_DIGEST}"
  "--tasks=1"
  "--max-retries=0"
  "--parallelism=1"
  "--task-timeout=900s"
  "--service-account=${MIGRATION_SERVICE_ACCOUNT}"
  "--command=make"
  "--args=-C,app,migrate-direct"
  "--set-env-vars=${MIGRATION_ENV}"
  "--set-secrets=${MIGRATION_SECRETS}"
  "--region=${GCP_REGION}"
  "--project=${GCP_PROJECT_ID}"
  "--quiet"
)

job_error="$(mktemp)"
trap 'rm -f "$job_error"' EXIT
job_status=0
gcloud run jobs describe "$MIGRATION_JOB_NAME" \
  --region="$GCP_REGION" --project="$GCP_PROJECT_ID" >/dev/null 2>"$job_error" || job_status=$?
if (( job_status == 0 )); then
  gcloud run jobs update "$MIGRATION_JOB_NAME" "${JOB_ARGS[@]}"
elif grep -Eiq 'not found|not_found|does not exist' "$job_error"; then
  gcloud run jobs create "$MIGRATION_JOB_NAME" "${JOB_ARGS[@]}"
else
  cat "$job_error" >&2
  exit "$job_status"
fi

echo "Running migration job ${MIGRATION_JOB_NAME} for ${RELEASE_ENVIRONMENT}"
gcloud run jobs execute "$MIGRATION_JOB_NAME" \
  --region="$GCP_REGION" --project="$GCP_PROJECT_ID" --wait

# Verify the same image using only the app identity before changing traffic.
# This catches missing grants, incorrect pooled credentials and ORM failures.
SMOKE_JOB_NAME="${MIGRATION_JOB_NAME}-smoke"
gcloud run jobs deploy "$SMOKE_JOB_NAME" \
  --image="$IMAGE_BY_DIGEST" \
  --tasks=1 --parallelism=1 --max-retries=0 --task-timeout=60s \
  --service-account="$RUNTIME_SERVICE_ACCOUNT" \
  --command=python --args=app/manage.py,db_smoke,--timeout,15 \
  --set-env-vars="$RUNTIME_ENV" --set-secrets="$RUNTIME_SECRETS" \
  --region="$GCP_REGION" --project="$GCP_PROJECT_ID" --quiet
gcloud run jobs execute "$SMOKE_JOB_NAME" \
  --region="$GCP_REGION" --project="$GCP_PROJECT_ID" --wait

# Keep this command after the successful --wait. A failed job leaves the
# previous service revision serving because this deploy is never reached.
gcloud run deploy "$CLOUD_RUN_SERVICE" \
  --image="$IMAGE_BY_DIGEST" \
  --region="$GCP_REGION" \
  --project="$GCP_PROJECT_ID" \
  --service-account="$RUNTIME_SERVICE_ACCOUNT" \
  --command=make \
  --args=-C,app,gunicorn \
  --concurrency="$CLOUD_RUN_CONCURRENCY" \
  --max="$CLOUD_RUN_MAX_INSTANCES" \
  --set-env-vars="$RUNTIME_ENV" \
  --set-secrets="$RUNTIME_SECRETS" \
  --quiet

RELEASE_RECORD_PATH="${RELEASE_RECORD_PATH:-release-record.txt}"
cat > "$RELEASE_RECORD_PATH" <<EOF
environment=${RELEASE_ENVIRONMENT}
release_sha=${RELEASE_SHA}
image=${IMAGE}
image_digest=${IMAGE_DIGEST}
EOF
echo "Released ${IMAGE_BY_DIGEST} to ${RELEASE_ENVIRONMENT}/${CLOUD_RUN_SERVICE}"
