#!/usr/bin/env bash

# Promote one already approved commit through a migration gate and Cloud Run
# deployment. The image is addressed by digest after its immutable commit tag
# is resolved; a migration failure therefore cannot activate a new revision.

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
  DJANGO_ALLOWED_HOSTS MIGRATION_JOB_NAME; do
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

IMAGE_REGISTRY="${ARTIFACT_REGISTRY_LOCATION}-docker.pkg.dev/${IMAGE_PROJECT_ID}/${ARTIFACT_REGISTRY_REPO}"
IMAGE="${IMAGE_REGISTRY}/greeniteso"
IMAGE_TAG="sha-${RELEASE_SHA}"
IMAGE_BY_TAG="${IMAGE}:${IMAGE_TAG}"

if [[ "${BUILD_IMAGE:-false}" == true ]]; then
  gcloud auth configure-docker "${ARTIFACT_REGISTRY_LOCATION}-docker.pkg.dev" --quiet
  docker build --tag "$IMAGE_BY_TAG" --file Dockerfile .
  docker push "$IMAGE_BY_TAG"
fi

# Resolve the registry digest only after the commit-tagged image exists. All
# environments receive this digest, so a later tag mutation cannot change the
# release payload.
IMAGE_DIGEST="$(gcloud artifacts docker images describe "$IMAGE_BY_TAG" \
  --project="$IMAGE_PROJECT_ID" \
  --format='value(image_summary.digest)' | tr -d '[:space:]')"
if [[ ! "$IMAGE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "Artifact Registry did not return a valid digest for ${IMAGE_BY_TAG}" >&2
  exit 1
fi
IMAGE_BY_DIGEST="${IMAGE}@${IMAGE_DIGEST}"

RUNTIME_ENV="DJANGO_ENV=${RELEASE_ENVIRONMENT},DJANGO_DEPLOYED=true,DJANGO_CONNECTION_ROLE=app,DJANGO_ALLOWED_HOSTS=${DJANGO_ALLOWED_HOSTS}"
MIGRATION_ENV="DJANGO_ENV=${RELEASE_ENVIRONMENT},DJANGO_DEPLOYED=true,DJANGO_CONNECTION_ROLE=direct,DJANGO_ALLOWED_HOSTS=${DJANGO_ALLOWED_HOSTS}"
RUNTIME_SECRETS="DJANGO_SECRET_KEY=${DJANGO_SECRET_KEY_SECRET}:latest,DATABASE_URL=${DATABASE_URL_SECRET}:latest"
MIGRATION_SECRETS="DJANGO_SECRET_KEY=${DJANGO_SECRET_KEY_SECRET}:latest,DATABASE_URL_UNPOOLED=${DATABASE_URL_UNPOOLED_SECRET}:latest"

JOB_ARGS=(
  "--image=${IMAGE_BY_DIGEST}"
  "--tasks=1"
  "--max-retries=0"
  "--parallelism=1"
  "--command=make"
  "--args=-C,app,migrate-direct"
  "--set-env-vars=${MIGRATION_ENV}"
  "--set-secrets=${MIGRATION_SECRETS}"
  "--region=${GCP_REGION}"
  "--project=${GCP_PROJECT_ID}"
  "--quiet"
)

if gcloud run jobs describe "$MIGRATION_JOB_NAME" \
  --region="$GCP_REGION" --project="$GCP_PROJECT_ID" >/dev/null 2>&1; then
  gcloud run jobs update "$MIGRATION_JOB_NAME" "${JOB_ARGS[@]}"
else
  gcloud run jobs create "$MIGRATION_JOB_NAME" "${JOB_ARGS[@]}"
fi

echo "Running migration job ${MIGRATION_JOB_NAME} for ${RELEASE_ENVIRONMENT}"
gcloud run jobs execute "$MIGRATION_JOB_NAME" \
  --region="$GCP_REGION" --project="$GCP_PROJECT_ID" --wait

# Keep this command after the successful --wait. Cloud Run continues serving
# the previous revision if the job fails, because this deploy is never reached.
gcloud run deploy "$CLOUD_RUN_SERVICE" \
  --image="$IMAGE_BY_DIGEST" \
  --region="$GCP_REGION" \
  --project="$GCP_PROJECT_ID" \
  --set-env-vars="$RUNTIME_ENV" \
  --set-secrets="$RUNTIME_SECRETS" \
  --quiet

echo "Released ${IMAGE_BY_DIGEST} to ${RELEASE_ENVIRONMENT}/${CLOUD_RUN_SERVICE}"
