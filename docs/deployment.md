# Deployment: three-environment release pipeline

The proposed promotion path has three deployed environments:

```
feature branch --PR--> dev --(promote)--> staging --(promote)--> main [production]
```

The Git branch names are `dev`, `staging`, and `main`, following Notion diagram 5. The corresponding GitHub Environments and Neon branches are `dev`, `staging`, and `production`. This repository change
does not create or rename remote branches. GitHub now has application Environments `dev`, `staging`, and `production` (`copilot` is tooling). Staging allows only Git `staging`; production allows only Git `main` and requires review by Fernando (`luci-efe`). The `staging` Git branch, branch protections, GCP configuration and secrets remain setup work.

The three replacement release callers are opt-in through the repository
variable `CLOUD_DEPLOYMENT_ENABLED`. It is currently unset, so pushes and
manual dispatches skip the release job before a runner, GitHub Environment,
or deployment secret is made available. The caller guard is intentionally
outside the reusable workflow; setting the variable to `true` still runs all
release validation, source provenance checks, migration checks, and the
fail-closed GCP configuration check.

The GCP owner should set `CLOUD_DEPLOYMENT_ENABLED=true` only after reviewed
replacement [PR30](https://github.com/GreenITESO-Proyecto-Integrador/GreenITESO-Backend/pull/30)
or its successor is ready for deployment. PR30 remains proposed alignment;
GCP runtime configuration and secrets are still setup work.

Before enabling promotion, create the `staging` Git branch at the reviewed release commit and verify `main` can advance by fast-forward. Retain legacy `test`, `preprod`, and `prod` branches until the team explicitly retires them. Promotion deliberately requires existing target branches and fast-forward history.

## Release behavior

`.github/workflows/deploy-dev.yml` builds and pushes an image tagged with the
full approved commit SHA. The reusable `.github/workflows/_deploy.yml` resolves
that tag to an Artifact Registry digest and writes a release record only after
the service deploy succeeds. Staging and production reuse the recorded digest;
they do not rebuild the image.

`.github/workflows/promote.yml` advances the target branch through the GitHub
API and explicitly dispatches the target release workflow with the approved
commit SHA. It does not rely on a `GITHUB_TOKEN` branch push to trigger another
workflow. The target release checks that its branch still points to the
approved SHA immediately before migration. A promotion also looks up a
successful source release run for that exact SHA, downloads its post-deploy
digest record, and passes that digest to the target. The target verifies the
recorded digest still matches Artifact Registry before migration. Per-environment
releases are serialized with `cancel-in-progress: false`; a stale queued
release fails before it can run a migration.

The same provenance check runs inside the reusable workflow for every staging
and production entry point, including a direct branch push or manual dispatch.
An empty or user-supplied digest cannot bypass the successful source-release
check. Non-dev invocations of `scripts/release.sh` also fail closed without a
verified expected digest.

Every release first runs the PostgreSQL 18 migration/test workflow against the exact approved SHA. The release job depends on that successful check before accessing GCP.

Each release then runs `scripts/release.sh` in this order:

1. Resolve the commit image tag and verify any source release digest.
2. Create or update a Cloud Run Job using that digest and run
   `make -C app migrate-direct` with one task and zero retries.
3. Wait for the migration job to succeed.
4. Create/update `${MIGRATION_JOB_NAME}-smoke` with the same digest, the runtime service account and only pooled app credentials; execute read-only `db_smoke` and wait for success.
5. Deploy the same digest to the Cloud Run service.

If migration or the app-role smoke check fails, the script exits before the Cloud Run service deploy, so the
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

The three GitHub Environments exist. After the project and region choices are confirmed, add these values to each Environment as secrets:

- `GCP_PROJECT_ID`
- `GCP_REGION`
- `GCP_WORKLOAD_IDENTITY_PROVIDER`
- `GCP_SERVICE_ACCOUNT`
- `ARTIFACT_REGISTRY_LOCATION`
- `ARTIFACT_REGISTRY_REPO`
- `IMAGE_PROJECT_ID` (the project hosting the shared Artifact Registry image)
- `CLOUD_RUN_SERVICE`
- `MIGRATION_JOB_NAME` (lowercase Cloud Run name, at most 43 characters to reserve `-smoke`)
- `RUNTIME_SERVICE_ACCOUNT` (pooled runtime credential)
- `MIGRATION_SERVICE_ACCOUNT` (direct migration credential)
- `CLOUD_RUN_MAX_INSTANCES` (small positive bound, for example `3`)
- `CLOUD_RUN_CONCURRENCY` (bounded Gunicorn-aligned concurrency, for example `40`)
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

The GitHub deploy identity needs permission to update jobs and services and to
act as the two runtime identities. The migration service account should have
only direct database secret access and image pull access. The runtime service
account should have only pooled database secret access and image pull access.
The dev release identity also needs Artifact Registry write permission. The
service is deployed with explicit max instances and concurrency, and the
Gunicorn command uses the foundation defaults of two workers and two threads.
Configure the exact IAM policy after the GCP project is selected.

Useful primary references:

- [Execute Cloud Run jobs](https://docs.cloud.google.com/run/docs/execute/jobs)
- [`gcloud run jobs create`](https://docs.cloud.google.com/sdk/gcloud/reference/run/jobs/create)
- [`gcloud run jobs update`](https://docs.cloud.google.com/sdk/gcloud/reference/run/jobs/update)
- [Cloud Run traffic migration and rollback](https://cloud.google.com/run/docs/rollouts-rollbacks-traffic-migration)
- [GitHub workflow triggers](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)
- [GitHub Actions concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)

## Release dependency and rollback

This draft now depends on Backend PR32 (and its schema foundation) because the release image must contain `db_smoke`. Do not enable it before the schema decisions and initial role credentials are approved. The smoke job checks database access using the runtime identity before deployment; it is not an HTTP check of the serving revision. An HTTP acceptance check remains part of the first Cloud Run integration.

If the new service revision fails application acceptance after deployment, select the last known-good revision in the same service and route traffic back with `gcloud run services update-traffic SERVICE --to-revisions=PREVIOUS_REVISION=100 --region=REGION --project=PROJECT`. Confirm its image digest against the last successful release record before selecting it. Verify the service and app-role smoke check afterward. Do not automatically reverse database migrations: the old revision must remain compatible through expand/contract. If schema compatibility is uncertain, stop promotion and use a forward fix or the approved recovery procedure. A failed release must not be promoted as successful.

IAM scoped to named jobs must include both `MIGRATION_JOB_NAME` (migration identity) and `${MIGRATION_JOB_NAME}-smoke` (runtime identity). Both identities also need access to the Django secret-key reference.
