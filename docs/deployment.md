# Deployment

## Current state

Automatic deployment is disabled. The repository variable
`CLOUD_DEPLOYMENT_ENABLED` is currently unset, so the historical caller
jobs on `dev`, `preprod`, and `prod` are skipped before a runner,
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
checks; legacy `preprod`/`prod` inputs continue to be rejected until
the replacement callers provide the approved mapping.

The legacy callers are:

| Workflow | Trigger branch | Current input | Build |
| --- | --- | --- | --- |
| `deploy-dev.yml` | `dev` | `dev` | yes |
| `deploy-preprod.yml` | `preprod` | `preprod` | no |
| `deploy-prod.yml` | `prod` | `prod` | no |

The table records retained workflow inputs, not configured deployment
environments. The replacement release workflow owns the future branch and
environment mapping.

## Real-time notifications: single worker

Real-time notifications use Channels with `InMemoryChannelLayer`, which only
delivers events to WebSockets held by the **same process**. With several
Gunicorn workers a notification is saved but silently skips every socket that
lives in another worker, so the runtime is pinned to one process until a shared
channel layer exists.

- `make -C app gunicorn-asgi` defaults to `--workers ${WEB_CONCURRENCY:-1}`.
- `make gunicorn-asgi` runs `check --deploy` first. The system check
  `notifications.E001` fails the start when `WEB_CONCURRENCY` is greater than 1
  and `CHANNEL_LAYERS` is still the in-memory backend (`E002` when the value is
  not an integer). Do not set `WEB_CONCURRENCY` above 1 in the environment.
- Horizontal scaling is also affected: every extra instance is another process,
  so the Cloud Run service must use `max-instances=1` for now.
- `app/tests/test_notifications_workers.py` fails if the Makefile default is
  raised, or if more workers are allowed without a shared layer.

To lift the limit, add `channels-redis`, point `CHANNEL_LAYERS` at a Redis
instance taken from a secret (`CHANNEL_REDIS_URL`), add a cross-process
delivery test, and then remove the pin and `notifications.E001`.
