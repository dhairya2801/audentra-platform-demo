# Cloud Run preview deployment

This directory describes the Vekend-owned `audentra` preview deployment. It is
intentionally private while the backend uses demo authentication and until the
portals are connected through a deliberately designed public edge.

## Resource contract

| Resource | Name |
| --- | --- |
| Region | `us-central1` |
| Artifact Registry repository | `audentra-platform` |
| API Cloud Run service | `audentra-api-preview` |
| Migration Cloud Run Job | `audentra-migrate-preview` |
| Seed Cloud Run Job | `audentra-seed-preview` |
| Outbox Cloud Run Job | `audentra-worker-once-preview` |
| Scheduler | `audentra-worker-dispatch-preview` |
| Cloud SQL instance | `audentra-preview-postgres` (connector-enforced) |
| Cloud Storage bucket | `audentra-906906351296-preview-documents` |
| Runtime service account | `audentra-platform-runtime@audentra.iam.gserviceaccount.com` |
| CI/CD service account | `audentra-platform-cd@audentra.iam.gserviceaccount.com` |
| Optional AI secret | `audentra-preview-openrouter-api-key` (not required for readiness) |

The API has Cloud Run IAM enabled. It is not publicly callable, even though
its current ingress is `all`; that ingress permits the Cloud Run job to invoke
it using a Google-signed ID token. No frontend is connected by this deployment.

## One-time IAM setup

An Owner or IAM administrator must run `configure-preview-iam.sh` once before
the first deployment. The script creates the repository/branch/environment-
restricted GitHub Workload Identity provider and grants narrowly scoped roles
to the existing deployment, runtime, and scheduler service accounts. A project
Editor cannot perform this setup because Editors cannot modify IAM policies or
create Workload Identity Federation providers.

The configuration deliberately uses immutable GitHub organization and
repository numeric IDs in addition to `refs/heads/main` and the protected
`preview` environment. It does not create or download a service-account key.

After the setup is complete, merging to `main` deploys automatically. The same
workflow can be dispatched on `main` to retry the initial release without a
dummy commit.

## What `deploy-preview.sh` does

GitHub Actions invokes `deploy-preview.sh` only after all CI jobs pass on
`main`. It:

1. deploys and waits for the database migration job;
2. deploys and waits for the deterministic preview seed job;
3. deploys the API with `min-instances=0`, a three-instance cap, Cloud SQL Auth
   Proxy, Cloud Storage workload identity, and Secret Manager references;
4. deploys and executes a bounded outbox worker job; and
5. configures Cloud Scheduler to launch one worker job every five minutes as a
   durable polling bridge until the outbox is connected to Cloud Tasks/Pub/Sub.

The jobs start from zero for every execution. Long document/AI work runs in a
job, never on the interactive API request path.

Direct invocations perform the private API readiness check inline with a
Google-signed identity token. GitHub Actions sets
`DEFER_AUTHENTICATED_READINESS_CHECK=true`, finishes the worker and scheduler
reconciliation, then uses its pinned Google authentication action to mint a
short-lived ID token for the exact Cloud Run URL and verifies
`/health/ready`. This keeps the service private without granting the deployment
account broad token-creation permissions.

## Security boundaries

- Runtime values are Secret Manager references, not GitHub secrets or YAML.
- The OpenRouter key is optional for the synthetic preview. Until a
  Vekend-owned key is stored, the gateway remains in its tested no-key mode.
- The runtime service account gets bucket-scoped object access, Cloud SQL
  access, secret access, and API invoke permission only.
- GitHub uses Workload Identity Federation restricted to
  `Audentra-ai/Audentra-platform` and `refs/heads/main`; no service-account
  key is created.
- The database is connector-enforced with no authorized networks, so Cloud Run
  reaches it only through the Cloud SQL Auth Proxy using workload identity; the
  bucket has uniform bucket-level access with public access prevention.

## Future public edge

When the Vercel portal is connected, introduce a custom API domain behind an
external Application Load Balancer, change API ingress to
`internal-and-cloud-load-balancing`, add Cloud Armor, and split staff traffic
to a separately routed/allowlisted surface. Do not attempt to allowlist Vercel
for direct browser API calls.
