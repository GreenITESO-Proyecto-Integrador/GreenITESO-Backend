# Deployment: three-environment release pipeline

The proposed promotion path has three deployed environments:

```
feature branch --PR--> dev --(promote)--> staging --(promote)--> production
```

The branch names are `dev`, `staging`, and `production`. This repository change
does not create or rename remote branches. The existing GitHub setup currently
has only the `dev` Environment, so the staging and production Environments,
branch protections, and production reviewers remain setup work.

## Release behavior

`.github/workflows/deploy-dev.yml` builds and pushes an image tagged with the
full approved commit SHA. The reusable `.github/workflows/_deploy.yml` then
resolves that tag to an Artifact Registry digest. Staging and production reuse
the same digest; they do not rebuild the image.

`.github/workflows/promote.yml` advances the target branch through the GitHub
API and explicitly dispatches the target release workflow with the approved
commit SHA. It does not rely on a `GITHUB_TOKEN` branch push to trigger another
workflow. The target release checks that its branch still points to the
approved SHA immediately before migration. Per-environment releases are
serialized with `cancel-in-progress: false`; a stale queued release fails
before it can run a migration.

Each release runs `scripts/release.sh` in this order:

1. Resolve the immutable image digest.
2. Create or update a Cloud Run Job using that digest and run
   `make -C app migrate-direct` with one task and zero retries.
3. Wait for the migration job to succeed.
4. Deploy the same digest to the Cloud Run service.

If migration fails, the script exits before the Cloud Run service deploy, so the
previous serving revision remains active. There is no automatic reverse
migration, `makemigrations`, or startup migration. Schema changes must follow
the expand/contract pattern because the previous application revision remains
live while the migration job runs.

The application/schema gate is intentionally visible at the migration step.
Review the custom User and T9 migrations before enabling those releases in a
deployed environment; this pipeline does not infer that review from a passing
image build.

The migration job receives only the direct database secret
(`DATABASE_URL_UNPOOLED`) and uses `DJANGO_CONNECTION_ROLE=direct`. The Cloud
Run service receives only the pooled database secret (`DATABASE_URL`) and uses
`DJANGO_CONNECTION_ROLE=app`. Both receive the Django secret key through a
Secret Manager reference. Secret values are never placed in workflow files.

## Required GitHub Environment configuration

Create the `dev`, `staging`, and `production` GitHub Environments after the
project and region choices are confirmed. Add these values to each Environment
as secrets:

- `GCP_PROJECT_ID`
- `GCP_REGION`
- `GCP_WORKLOAD_IDENTITY_PROVIDER`
- `GCP_SERVICE_ACCOUNT`
- `ARTIFACT_REGISTRY_LOCATION`
- `ARTIFACT_REGISTRY_REPO`
- `IMAGE_PROJECT_ID` (the project hosting the shared Artifact Registry image)
- `CLOUD_RUN_SERVICE`
- `MIGRATION_JOB_NAME`
- `DJANGO_SECRET_KEY_SECRET` (Secret Manager secret ID or reference)
- `DATABASE_URL_SECRET` (pooled runtime connection)
- `DATABASE_URL_UNPOOLED_SECRET` (direct migration connection)
- `DJANGO_ALLOWED_HOSTS`

The GCP project and region are intentionally unresolved pending the user’s
choice. Until those values and Workload Identity Federation are configured, a
release fails with the missing setting name; it does not report a skipped green
deployment. Local tests mock `gcloud` and verify migration failure prevents a
service deploy. They are preparation evidence and do not claim Cloud Run
execution.

The service account used by each release needs permission to run and update
Cloud Run Jobs and deploy Cloud Run services, plus permission to read the
referenced Secret Manager secrets and pull the shared image. The dev release
also needs Artifact Registry write permission. Configure the exact IAM policy
after the GCP project is selected.

Useful primary references:

- [Execute Cloud Run jobs](https://docs.cloud.google.com/run/docs/execute/jobs)
- [`gcloud run jobs create`](https://docs.cloud.google.com/sdk/gcloud/reference/run/jobs/create)
- [`gcloud run jobs update`](https://docs.cloud.google.com/sdk/gcloud/reference/run/jobs/update)
- [Cloud Run traffic migration and rollback](https://cloud.google.com/run/docs/rollouts-rollbacks-traffic-migration)
- [GitHub workflow triggers](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)
- [GitHub Actions concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)
