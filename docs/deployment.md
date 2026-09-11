# Deployment

## Current state

Automatic deployment is disabled. The repository variable
`CLOUD_DEPLOYMENT_ENABLED` is currently unset, so the four historical caller
jobs on `dev`, `test`, `preprod`, and `prod` are skipped before a runner,
GitHub Environment, or deployment secret is made available. The callers and
branches remain for compatibility; this branch makes no Cloud Run deployment
claim.

The deployment GitHub Environments are `dev`, `staging`, and `production`;
`production` has required reviewers. A separate `copilot` environment is for
tooling. GCP runtime configuration and the replacement migration pipeline
remain pending the GCP owner's release review. No four-environment GCP
topology is implied.

## Approved release mapping

The intended mapping in replacement [PR30](https://github.com/GreenITESO-Proyecto-Integrador/GreenITESO-Backend/pull/30)
is Git `dev` → `dev`, Git `staging` → `staging`, and Git `main` →
`production` (Neon branch `production`). PR30 is proposed alignment and is not
represented here as merged, deployed, or production evidence.

## Enablement gate

The GCP owner may set `CLOUD_DEPLOYMENT_ENABLED=true` only after the
replacement release workflow has been reviewed and is ready to provision the
Cloud Run Django runtime and run the reviewed migration pipeline. Until then,
the variable stays unset.

When enabled, the reusable workflow remains fail closed. It accepts only the
approved `dev`, `staging`, and `production` names, requires the environment
scoped GCP and Artifact Registry settings, and requires
`DJANGO_RUNTIME_CONFIG_READY=true`. The opt-in guard does not bypass these
checks; legacy `test`/`preprod`/`prod` inputs continue to be rejected until
the replacement callers provide the approved mapping.

The legacy callers are:

| Workflow | Trigger branch | Current input | Build |
| --- | --- | --- | --- |
| `deploy-dev.yml` | `dev` | `dev` | yes |
| `deploy-test.yml` | `test` | `test` | no |
| `deploy-preprod.yml` | `preprod` | `preprod` | no |
| `deploy-prod.yml` | `prod` | `prod` | no |

The table records retained workflow inputs, not configured deployment
environments. The replacement release workflow owns the future branch and
environment mapping.
