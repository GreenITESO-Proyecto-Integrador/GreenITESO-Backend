# Deployment and database release pipeline

The proposed promotion path has three deployed environments:

```
feature branch --PR--> dev --promotion PR--> preprod --promotion PR--> prod [production]
```

Git uses the existing protected branches `dev`, `preprod`, and `prod`.
GitHub Environments are `dev`, `preprod`, and `production`; these map to Neon
branches `dev`, `staging`, and `production`, respectively. There is no Git
`staging` branch. Fernando confirmed retaining Git `prod` on 2026-10-01; this
workflow does not create or rename remote branches. The duplicate GitHub
`staging` environment is not used.

As verified on 2026-10-01, `prod` protection requires one code-owner approval
and the `Enforce promotion chain` check, with admin enforcement; it does not
require `test` or dismiss stale approvals. Before enabling production release,
require the `test` check and update the production Environment's branch
allowlist from the nonexistent `main` to `prod`. This PR does not change those
GitHub policies. The production Environment already prevents self-review and
its only configured reviewer is `luci-efe`. A release initiated by that account
needs another configured reviewer; a release initiated by Isaac can instead
receive independent approval from `luci-efe`.

Cloud Run deployment remains opt-in through the repository variable
`CLOUD_DEPLOYMENT_ENABLED`; GitHub inspection on 2026-10-01 found it unset and
no GCP release settings in the repository or production Environment. External
provider state and organization-level configuration remain unverified. Do not
enable it until the GCP configuration, environment secrets,
and deployment acceptance checks are complete. The production workflow is
triggered only by a successfully merged `preprod` → `prod` PR and remains
behind the production GitHub Environment approval.

The `Promote` workflow only opens PRs (`dev` → `preprod`, then `preprod` →
`prod`); it never updates protected refs directly. It refuses a target branch
that does not exist. The `Enforce promotion chain` check requires the matching
source branch to belong to this repository; a fork with the same branch name
does not qualify. GitHub places checks triggered by a PR opened with the
workflow's `GITHUB_TOKEN` in an approval-required state. A repository writer
must select **Approve workflows to run** on that promotion PR before the
required `test` and `Enforce promotion chain` checks can pass; a human PR review
does not substitute for starting those checks. A GitHub App installation token
or fine-grained PAT would remove that extra workflow-run approval, but neither
is configured here. Keep the unused GitHub `staging` Environment intact;
release callers use `preprod` for staging and `production` for production.
For `dev` → `preprod`, Promote first requires evidence that the **exact dev
SHA** passed Django tests and then its dev database migration plus app-role
smoke, either through the standalone Neon job or (after GCP activation) the
Cloud Run release job. Run Promote only after that evidence is published; an
early run fails without opening a PR. If an already-open promotion PR tracks a
new dev tip before evidence is ready, its required check safely fails; rerun
the check or update the PR after the successful dev release publishes evidence.

## Neon schema migration after merge

PR checks continue to run migrations, `makemigrations --check`, the Django
suite, and `scripts/rehearse-migration-conflict.py` against disposable
PostgreSQL 18; the rehearsal injects parallel leaves, confirms Django rejects
the unresolved graph, then applies a compatible merge migration. PR checks do
not receive Neon credentials.
The `Neon database migrations` workflow listens for a successful `Django tests`
run caused by a push to protected `dev` or `preprod`. That push is the result of
a merged PR; PR-close events (including unmerged closures) cannot trigger this
workflow. The credential-free gate also checks GitHub's commit-associated PRs
and requires a merged same-repository PR whose base is that target branch and
whose `merge_commit_sha` exactly matches the tested SHA. This excludes direct
pushes without an associated merged PR. Both gate evaluations use the
default-branch copy of the verifier rather than the version in the tested
commit. Because `dev` is also the default branch, review controls on privileged
workflow changes are essential. As verified on 2026-10-01, `dev` requires the
`test` check and two approvals, dismisses stale approvals, and enforces rules
for admins; it does not require a code-owner review or checks against the
current base.
`preprod` requires `test`, `Enforce promotion chain`, and one code-owner
approval, dismisses stale approvals, requires checks against the current base,
and enforces rules for admins. The existing `.github/CODEOWNERS` file names
Ozcar, Isaac, and Fernando (`luci-efe`); recheck these policies if ownership or
release topology changes. A GitHub API/response failure leaves eligibility
unknown and fails the gate job visibly; it is not treated as a clean ineligible skip. Only an eligible
result starts the job with a Neon GitHub
Environment. After waiting for the lock, the job verifies eligibility again
and skips a stale SHA if the branch has advanced. Neon
migrations and Cloud Run releases share one database-release concurrency group
per target Neon environment, with `queue: max`, so an in-flight Cloud-mode
cutover cannot run both migration paths at once and out-of-order test finishes
cannot replace the current commit's queued migration. The queue supports up to
100 pending jobs; if it fills, rerun the latest successful test workflow after
the queue drains. Thus `dev` updates Neon `dev`, while Git `preprod` updates
Neon `staging`.

The standalone workflow checks the target branch tip after installing
dependencies and immediately before requesting database settings. A queued SHA
that is already stale is skipped; if a later merge lands after this final
check, the already-tested migration may finish and the later commit is handled
under the same serialized lock. GitHub Actions cannot atomically freeze branch
updates with a database transaction.

The migrator uses only `DATABASE_URL_UNPOOLED` (direct URL); the post-migration
`db_smoke` check uses only `DATABASE_URL` (pooled app URL). Django settings
enforce SSL, canonical environment host, and role-specific pooling. A failed
migration or smoke check fails the job. There is deliberately no automatic
production Neon migration in this workflow; production remains gated on the
Cloud Run release path and explicit production readiness authorization.
The standalone Neon workflow runs only while `CLOUD_DEPLOYMENT_ENABLED` is not
`true`. Once Cloud Run is enabled, its serialized one-shot migration job is the
single migration path, avoiding concurrent duplicate jobs against the same
branch.
`db_smoke --check-grants` requires schema USAGE without CREATE; table
SELECT/INSERT/UPDATE/DELETE without TRUNCATE on application tables, but only
SELECT on `django_migrations`; sequence
USAGE; and presence of every managed Django table. A database owner or
superuser is **not** a valid app credential. Grant defaults must be established
for the direct migrator role so future migration-created tables/sequences are
usable by the app role. The Infra owner must reapply and verify role grants
after a migration creates or restores the ledger, before this smoke and before
new traffic; default privileges cannot exempt one table. Until that operation
is automated and verified, a writable ledger fails the release closed. See
[the smoke contract](database-smoke.md).

Add these environment-scoped secrets to both GitHub Environments `dev` and
`preprod` before relying on automatic migration: `DATABASE_URL` (pooled app
role), `DATABASE_URL_UNPOOLED` (direct migrator role), `DJANGO_SECRET_KEY`, and
`DJANGO_ALLOWED_HOSTS`. The preprod URLs must connect to Neon `staging`, not a
Git branch named `staging`. Secret values must never enter the repository,
workflow logs, or issue comments. As verified on 2026-10-01, all four names are
configured in each non-production Environment, but runner connectivity and
authentication remain unverified until the first protected merge.
Both deployed URLs must use either `sslmode=verify-full` with a trusted CA that
libpq can actually load, or Neon `sslmode=require` with
`channel_binding=require`. Do not remove channel binding from the latter; see
the [database development guide](database-development.md) for the connection
contract and canonical host checks.
The local macOS read-only smoke needed `/etc/ssl/cert.pem`; that path is not a
portable Cloud Run or GitHub setting. Validate the runtime trust store before
activating the environment secret, without printing the URL.

Do not add a `preprod`-only deployment-branch rule to the GitHub `preprod`
Environment without changing and retesting this trigger. GitHub matches
environment branch rules against the workflow run's `GITHUB_REF`; for
`workflow_run`, that ref is the repository default branch (`dev`), even when
`workflow_run.head_branch` is `preprod`. The current `preprod` Environment has
no branch rule, so the credential-free gate above must enforce the tested
target branch and merged-PR provenance before the job can access its secrets.
The staging/production Cloud Run promotion paths instead use merged
`pull_request` events, whose refs are the destination branches. Recheck all
release paths if the default branch, trigger, or environment policy changes.

## Release behavior

`.github/workflows/deploy-dev.yml` builds and pushes an image tagged with the
full approved commit SHA. The reusable `.github/workflows/_deploy.yml` resolves
that tag to an Artifact Registry digest and writes a release record only after
the service deploy succeeds. Staging and production reuse the recorded digest;
they do not rebuild the image.

Successful promotion PR merges pass the merge commit as the target release SHA
and the PR head as the source-image SHA. The target branch must still point at
the merge SHA; the previous environment must have a successful release record
for the source SHA. Before reusing that image, the workflow reads the local
release tree and the source tree through the authenticated GitHub API and
requires identical Git trees, so merge/squash/rebase metadata cannot make a
different code tree masquerade as the tested image. Each release is serialized
with `queue: max`; stale releases fail before migration. The reusable release
checks its branch at job start and checks again immediately before the combined
migration/deploy command. If the branch advances after that last check, that
already-approved release may complete before the queued newer release; branch
updates themselves are not locked by Actions concurrency.
The source branch must still point at the source SHA when the job runs; a
newer source commit requires a fresh promotion PR.
Promotion is intentionally linear: if a target-only fix or conflict resolution
changes the tree, the release aborts. Move/review that fix in the preceding
environment and open a fresh promotion PR; do not bypass the tree check or
deploy a digest whose source tree differs from the tested target.

The provenance check maps Git `dev` to the `staging` release input, then Git
`preprod` to the `production` release input. An empty or user-supplied digest
cannot bypass the successful source-release check. Non-dev invocations of
`scripts/release.sh` fail closed without a verified expected digest. This
provenance handoff is contract-tested locally but has not been exercised on
GitHub Actions or GCP; keep cloud releases disabled until that validation is
completed.

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
- `CLOUD_RUN_CONCURRENCY` (start conservatively, for example `2`; tune only
  after staging load tests and a Neon connection-budget review)
- `DJANGO_SECRET_KEY_SECRET` (Secret Manager secret ID or reference)
- `DATABASE_URL_SECRET` (pooled runtime connection)
- `DATABASE_URL_UNPOOLED_SECRET` (direct migration connection)
- `DJANGO_ALLOWED_HOSTS`
- `MICROSOFT_TENANT_ID` and `MICROSOFT_CLIENT_ID` (environment-scoped Entra application identifiers; required for deployed login and passed to each Cloud Run revision)

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
service is deployed with explicit max instances and concurrency. The current
ASGI Gunicorn command uses two Uvicorn workers by default; it no longer sets
Gunicorn `--threads`. Do not carry forward the previous `40` concurrency
example without runtime load and database-connection evidence. Configure the
exact IAM policy after the GCP project is selected.

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
