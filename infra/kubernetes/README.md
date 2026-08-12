# Audentra platform on Kubernetes

These manifests run the FastAPI API and the durable Python worker as separate,
portable Kubernetes workloads. They deliberately use only core Kubernetes APIs
and Kustomize, so the base applies unchanged to GKE and EKS. Cloud-specific
identity, secret-store, DNS, certificate, ingress-controller, and database
choices remain outside the base.

## What the base owns

- two API replicas behind `audentra-platform-api` on port `4000`;
- a separately scalable worker; PostgreSQL leases and idempotency make multiple
  worker replicas safe;
- health probes, resource requests/limits, HPA, PDB, non-root/read-only
  containers, and a scoped ingress policy;
- a public API ingress template at `api.example.edu`;
- a migration Job template that is intentionally not applied with the running
  workload.

The platform does **not** deploy PostgreSQL or object storage. Use managed
PostgreSQL plus a private S3-compatible bucket (GCS with the appropriate
adapter, AWS S3, or an approved compatible provider). This is necessary for a
multi-node cluster and avoids state tied to a single Kubernetes node.

## Runtime secret contract

Before applying the platform workload, provide a secret named
`audentra-platform-runtime` in the `audentra` namespace. It must contain the
non-public settings described by the active `.env.example`, including at least:

- `DATABASE_URL`;
- `OBJECT_STORAGE_ENDPOINT`, `OBJECT_STORAGE_BUCKET`,
  `OBJECT_STORAGE_ACCESS_KEY`, and `OBJECT_STORAGE_SECRET_KEY` (or the
  equivalent workload-identity-backed values supported by the selected adapter);
- `DOCUMENT_WORKER_TOKEN`;
- `OPENROUTER_API_KEY` when AI features are enabled;
- the staff invitation/authentication values required by the selected auth mode.

Do not commit a filled Kubernetes Secret. In GKE, use Workload Identity with
Secret Manager or an approved External Secrets controller. In EKS, use IRSA
with AWS Secrets Manager or an approved External Secrets controller. Both
approaches materialize the same Kubernetes secret contract, which keeps these
manifests portable.

## Build and release order

1. Build the platform image from `infra/docker/api.Dockerfile` using the API
   target, publish an immutable SHA tag, and replace the overlay image tag.
2. Set the real portal hostname in `WEB_ORIGIN` and `OPENROUTER_APP_URL`.
   Set `api.example.edu` and the TLS secret in `base/api-ingress.yaml` (or patch
   them in a deployment overlay).
3. Render and validate the chosen overlay:

   ```text
   kubectl kustomize infra/kubernetes/overlays/gke
   # or
   kubectl kustomize infra/kubernetes/overlays/eks
   ```

4. Run the migration Job using the same immutable image before the new API or
   worker rollout. `jobs/migrate-job.yaml` uses `generateName` so every release
   gets a distinct, auditable Job. Render it with the release image, create it,
   wait for completion, and stop the release if it fails.
5. Apply the platform overlay, then wait for the API deployment to become
   available before deploying the portals release.

The migration is deliberately separate from the Deployment: a completed Job
must never rerun just because a Deployment is reconciled or autoscaled.

## GKE and EKS overlays

`overlays/gke` and `overlays/eks` only replace the image registry/name. They
are not separate architectures. Copy either overlay into a real environment
overlay and patch:

- immutable image tag;
- `WEB_ORIGIN` / `OPENROUTER_APP_URL`;
- API ingress host, TLS secret, and `ingressClassName` if the cluster does not
  have a default ingress class;
- resource sizes and HPA ranges after observing real production load.

For SSE, configure the chosen ingress controller/load balancer with a stream
idle timeout above the API reconnect interval and disable response buffering for
the events endpoints. The portal will reconnect with a durable cursor; the
event stream remains an invalidation channel and PostgreSQL remains canonical.

## Safety checks

- Ensure Metrics Server is installed before relying on the HPAs.
- Keep the API and portal hosts separate unless a single edge explicitly routes
  both paths and builds the portal with the matching API base URL.
- Do not enable a default-deny egress policy until database, object-storage,
  OpenRouter, auth, DNS, and observability destinations have explicit rules.
- Verify readiness, a staff SSE connection, a student SSE connection, document
  upload/parse, and a worker retry after each first deployment.
