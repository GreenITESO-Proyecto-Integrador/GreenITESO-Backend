# Deployment: trunk-based promotion pipeline

Four long-lived branches map 1:1 to four GCP environments. Code flows one
direction only: `dev` → `test` → `preprod` → `prod`.

```
feature branch --PR--> dev --(promote)--> test --(promote)--> preprod --(promote)--> prod
```

- Feature branches merge into `dev` via normal PRs (gated by the existing
  `Ruff` and `Precommit` checks).
- Pushing to `dev` builds the Docker image once, tags it `sha-<short-sha>`,
  pushes it to Artifact Registry, and deploys it to the `dev` Cloud Run
  service.
- Promotion to `test`, `preprod`, and `prod` is triggered manually via the
  **Promote** GitHub Action (`.github/workflows/promote.yml`,
  `workflow_dispatch`). It fast-forwards the target branch to the tip of the
  previous stage's branch — no rebuild happens, so the exact image that was
  tested in `dev` is the one that reaches `prod`.
- Pushing to `test`/`preprod`/`prod` (which only happens via that
  fast-forward) redeploys the same `sha-<short-sha>` image to that
  environment's Cloud Run service.
- The `prod` deploy runs under the `prod` GitHub Environment, which is
  configured with required reviewers — this is the manual approval gate
  before production.

## Current deployment gate

The legacy reusable workflow fails closed until the Cloud Run contract is
ready. It rejects the old `test`/`preprod` names, requires all environment
scoped GCP and Artifact Registry settings, and requires the environment
variable `DJANGO_RUNTIME_CONFIG_READY=true`. PR30 is still pending; set that
variable only after the Cloud Run service receives the Django runtime configuration through the
approved secret/configuration path and the reviewed one shot migration
pipeline is available. Until then, a push to a deployment branch fails with a
dependency message instead of starting a runtime that cannot import settings.

The replacement release workflow can remove this gate once it supplies the
same settings contract and runs reviewed migrations before serving the new
revision. No deployment is implied by this local foundation branch.

## One-time GCP setup (per environment project)

Create four GCP projects, e.g. `greeniteso-dev`, `greeniteso-test`,
`greeniteso-preprod`, `greeniteso-prod`. For each:

1. Enable the Cloud Run and Artifact Registry APIs.
2. Create a deploy service account with `roles/run.admin` and
   `roles/iam.serviceAccountUser`.
3. Set up Workload Identity Federation (a pool + provider trusting
   `token.actions.githubusercontent.com`, restricted to this repo) so
   GitHub Actions can authenticate without a long-lived JSON key.
4. Create a Cloud Run service (or let the first deploy create it).

Additionally, on the `dev` project only:

- Create one Artifact Registry Docker repository — this is the **shared**
  registry every environment pulls the promoted image from.
- Grant that repo's `roles/artifactregistry.writer` to the dev service
  account, and `roles/artifactregistry.reader` to the `test`/`preprod`/`prod`
  service accounts so they can pull the image built in `dev`.

## One-time GitHub setup

1. Create branches `dev`, `test`, `preprod`, `prod` from `main`.
2. Create four GitHub Environments named `dev`, `test`, `preprod`, `prod`.
   In each, add these environment secrets:
   - `GCP_PROJECT_ID`
   - `GCP_WORKLOAD_IDENTITY_PROVIDER`
   - `GCP_SERVICE_ACCOUNT`
   - `GCP_REGION`
   - `ARTIFACT_REGISTRY_REPO`
   - `CLOUD_RUN_SERVICE`
   - `DEV_GCP_PROJECT_ID` (same value — the dev project ID — in all four,
     since it identifies where the shared image lives)
   Add the environment variable `DJANGO_RUNTIME_CONFIG_READY` separately;
   leave it unset until the approved runtime and migration pipeline are ready,
   then set it to `true`.
3. On the `prod` Environment, add required reviewers under protection rules.
   This is what makes the `prod` deploy pause for manual approval.
4. Add branch protection to `dev`/`test`/`preprod`/`prod` requiring the
   `Ruff`/`Precommit` checks, and restrict direct pushes so that `test`,
   `preprod`, and `prod` only ever advance via the **Promote** workflow.

## Known limitation

The legacy workflow still contains the historical `test`/`preprod` callers and
does not yet provision the Cloud Run Django runtime settings itself. The
fail-closed gate above prevents those callers from deploying the gunicorn image
until the environment names, runtime secret path, and reviewed migration step
are supplied by the replacement release workflow.
