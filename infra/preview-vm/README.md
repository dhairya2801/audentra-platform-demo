# Audentra synthetic preview on one Compute Engine VM

This profile runs the canonical FastAPI backend for synthetic/internal product
testing. It does not provision GCP resources and is not a real university
production design.

## Architecture

```text
Vercel frontend
      |
      | HTTPS, credentials: include, X-Tenant-Slug: aster
      v
Compute Engine VM
  Caddy :80/:443 (only public application entrypoint)
      |
      v
  FastAPI :4000 (Docker network only)
      |--------------------|
      v                    v
  PostgreSQL :5432     MinIO :9000
      ^                    ^
      |                    |
      +------ worker ------+
```

The Compose services are exactly `postgres`, `minio`, `minio-init`, `migrate`,
`seed`, `api`, `worker`, and `caddy`. There is no Keycloak, Mailpit, Redis,
Cloud SQL, GKE, Cloud Run, external load balancer, or Cloud Armor dependency.
Caddy disables proxy buffering for low-latency SSE delivery.

PostgreSQL, MinIO, and Caddy state use stable named volumes. Recreating a
container or running `docker compose down` does not remove them. Never use
`docker compose down --volumes` for an environment whose data must survive.

## Prerequisites

- A reviewed checkout of this repository on a 64-bit Linux VM.
- Docker Engine with the Docker Compose v2 plugin.
- `bash`, `curl`, `flock`, `git`, `python3`, `tar`, and `sha256sum`.
- A public DNS A/AAAA record for `API_DOMAIN` pointing to the VM before the
  public certificate test. Caddy obtains and renews the certificate.
- Either a locally built immutable platform image or a readable container
  registry. Artifact Registry is optional for the first manual deployment.
- A stable Vercel/custom frontend origin for CORS.

The scripts do not install Docker, create a VM, change GCP firewall rules,
configure IAM, create DNS records, or provision any other cloud resource.

## Protected environment

The tracked [`.env.example`](./.env.example) contains safe placeholders and
comments. The live file is `/opt/audentra-platform/shared/.env`, owned by root
with mode `0600`; never commit or print it.

Required operator-provided configuration:

- `API_DOMAIN`
- `WEB_ORIGIN`
- `PLATFORM_IMAGE_REPOSITORY`
- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`
- `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `MINIO_BUCKET`
- `DOCUMENT_WORKER_TOKEN`
- `VV_STAFF_INVITATION_CODE`

The example also documents `AUDENTRA_ENV=preview`, `AUTH_MODE=demo`, and
`BROWSER_AUTH_REQUIRED=true`. Compose enforces those three values and does not
allow the protected environment to weaken them. It also derives `DATABASE_URL`
and fixes `API_INTERNAL_URL=http://api:4000` on the private Docker network.

Optional provider configuration:

- `OPENAI_API_KEY`, `OPENAI_MODEL`
- `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`
- `OPENROUTER_DOCUMENT_MODEL`, `OPENROUTER_TRANSCRIPTION_MODEL`
- `OPENROUTER_APP_URL`, `OPENROUTER_APP_NAME`
- `TRANSCRIPT_PARSING`, `GROQ_API_KEY`, `GROQ_MODEL`

Leave model API keys empty for deterministic/guided Edward behavior and no
external model cost.

## First deployment from a fresh VM

These commands build the image on the VM, so Artifact Registry is not required.
Run them from the reviewed repository root. They create containers and local
Docker volumes, but no GCP resources.

1. Create and edit the protected environment:

   ```bash
   sudo install -d -m 0750 -o root -g root /opt/audentra-platform/shared
   sudo install -m 0600 -o root -g root \
     infra/preview-vm/.env.example /opt/audentra-platform/shared/.env
   sudoedit /opt/audentra-platform/shared/.env
   sudo infra/preview-vm/bootstrap-host.sh
   ```

   Replace every `CHANGE_ME`/example hostname. Use URL-safe random values for
   the PostgreSQL credentials, at least 32 random characters for
   `DOCUMENT_WORKER_TOKEN`, at least 24 for database/storage passwords, and at
   least 16 for `VV_STAFF_INVITATION_CODE`. The bootstrap validates names and
   lengths without printing values.

2. Build the canonical platform image for the checked-out commit:

   ```bash
   release_sha="$(git rev-parse HEAD)"
   sudo docker build \
     --file infra/docker/api.Dockerfile \
     --tag "audentra-platform:${release_sha}" \
     .
   ```

   Keep `PLATFORM_IMAGE_REPOSITORY=audentra-platform` for this local-image flow.
   If a registry is introduced later, set it to the repository name without a
   tag, for example `REGION-docker.pkg.dev/PROJECT/REPOSITORY/api`.

3. Package only the reviewed VM profile and fingerprint seed inputs:

   ```bash
   seed_revision="$(
     git ls-tree -r --full-tree "${release_sha}" -- \
       apps/api/src/audentra/infrastructure/seeding \
       apps/api/assets/config/tenants \
       apps/api/assets/portal-media \
       apps/api/migrations \
       | sha256sum | cut -d' ' -f1
   )"
   git archive \
     --format=tar.gz \
     --output="/tmp/audentra-platform-release-${release_sha}.tar.gz" \
     "${release_sha}" \
     infra/preview-vm
   ```

4. Deploy the release:

   ```bash
   sudo /usr/local/sbin/audentra-platform-deploy \
     "${release_sha}" "${seed_revision}"
   ```

The deploy command locks concurrent releases, validates the archive and
protected configuration, validates Caddy, starts PostgreSQL and MinIO, creates
the database schema through all immutable migrations, creates the private
MinIO bucket, runs the synthetic seed only
when its fingerprint changes, then starts API, worker, and Caddy. It waits for
all long-running services to become healthy before selecting the release. It
does not run `down`, remove volumes, prune images, or reset the database.
The deploy command also clears ambient shell variables before Compose renders
the profile, so a model key or database setting exported by an operator cannot
override the protected VM environment accidentally.

Seeding uses `AUDENTRA_ENV=preview`; application safety still rejects seeding in
`production`. API restarts never trigger the seed service.

## Subsequent deployments

From the new reviewed commit, repeat the image build, seed fingerprint,
`git archive`, and `audentra-platform-deploy` commands above. Migrations run on
every deployment and are checksum-verified/idempotent. Seed runs only if the
seed code, tenant configuration, portal media, or migrations changed. Existing
PostgreSQL/MinIO/Caddy volumes remain attached.

The script keeps the current plus three recent release directories and attempts
to restore the prior application image if the new API/worker/edge health gate
fails.

## Operations

Define this command prefix when inspecting the VM:

```bash
compose=(
  sudo env --ignore-environment
  PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
  docker compose
  --project-name audentra-platform-preview
  --env-file /opt/audentra-platform/shared/.env
  --env-file /opt/audentra-platform/shared/deployment.env
  --file /opt/audentra-platform/current/infra/preview-vm/compose.yaml
)
```

Status and health:

```bash
"${compose[@]}" ps
curl --fail --silent --show-error "https://<backend-domain>/health"
curl --fail --silent --show-error "https://<backend-domain>/health/ready"
curl --fail --silent --show-error \
  "https://<backend-domain>/v1/tenants/aster/bootstrap"
```

`/health` is liveness. `/health/ready` checks the runtime's database-backed
readiness and is the deployment health gate. The Aster tenant bootstrap is a
safe public smoke test.

Logs:

```bash
"${compose[@]}" logs --follow --tail=200 api worker caddy
"${compose[@]}" logs --tail=200 postgres minio migrate seed minio-init
```

Safe restart/stop/start:

```bash
"${compose[@]}" restart api worker caddy
"${compose[@]}" stop
"${compose[@]}" start
```

If a full Compose shutdown is necessary, `"${compose[@]}" down` preserves named
volumes. Do not add `--volumes`.

## GCP firewall exposure

Public application ingress:

- TCP 80: ACME/HTTP redirect to HTTPS.
- TCP 443: HTTPS API and SSE.
- UDP 443: optional HTTP/3; Compose publishes it, but the GCP rule may omit it
  initially because TCP 443 remains fully functional.

Administrative SSH should be restricted to IAP/authorized operators rather
than open to the public internet. Never create public ingress rules for:

- `4000` (FastAPI)
- `5432` (PostgreSQL)
- `9000` (MinIO API)
- `9001` (MinIO console/admin)
- Docker daemon or worker-internal endpoints

Compose has no host publication for those ports. Verify with
`docker compose config` and `docker compose ps` after every deployment change.

## Vercel integration and browser auth

Set the frontend build/runtime variable to:

```text
NEXT_PUBLIC_API_BASE_URL=https://<backend-domain>
```

Set backend `WEB_ORIGIN` to the frontend's exact origin, including `https://`
and with no trailing slash. Browser requests must use `credentials: "include"`;
the tenant-aware requests must send `X-Tenant-Slug: aster`. The preview sign-in
route sets an `HttpOnly`, `Secure`, `SameSite=None` cookie when
`SESSION_COOKIE_SAMESITE=none`, and
`BROWSER_AUTH_REQUIRED=true` rejects protected browser routes without it.

For the current preview, set `WEB_ORIGIN` exactly to
`https://audentra-portals-demo-web.vercel.app` and retain
`SESSION_COOKIE_SAMESITE=none`. FastAPI returns that exact origin with
credentialed CORS; it does not use a wildcard. Production retains
`SameSite=Lax`, and demo authentication remains unavailable there. Browsers or
privacy modes that block all third-party cookies can still prevent a Vercel
site from retaining a cookie issued by an unrelated backend site; same-site
custom domains avoid that browser-level limitation.

## GitHub Actions safety

The legacy Cloud Run job remains for reference, but it cannot run on a push to
`main`. It now requires a manual workflow dispatch from `main` plus the explicit
repository variable `ENABLE_LEGACY_CLOUD_RUN_DEPLOYMENT=true`. Do not set that
variable while preparing the new VM. All lint, test, typecheck, and container
build CI jobs still run normally.
