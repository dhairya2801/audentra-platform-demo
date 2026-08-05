# GCP preview deployment

This profile deploys the FastAPI API, outbox worker, PostgreSQL, and MinIO to
the existing hardened Compute Engine preview VM. GitHub Actions builds the
immutable Python image off-host, pushes it to the immutable
`audentra-platform` Artifact Registry repository, transfers only the reviewed
Compose configuration through IAP, and invokes the root-owned deployment
command. The VM pulls through its read-only runtime service account. No
long-lived Google key is stored in GitHub or on the VM.

The runtime uses `AUDENTRA_ENV=preview`: synthetic demo authentication remains
available, browser authentication is required, and cookies are Secure over
HTTPS. `AUDENTRA_ENV=production` still rejects the demo identity adapter.

Persistent state is held in the named `audentra-platform-postgres-data` and
`audentra-platform-minio-data` volumes. `docker compose down` does not delete
them. The deployment always runs checksum-verified migrations. It fingerprints
the seed implementation, tenant fixtures, media, and migrations and runs the
idempotent demo seed only when those inputs change, before switching the API
and worker to the new image.

Bootstrap once from a reviewed checkout on the VM:

```bash
sudo infra/preview-vm/bootstrap-host.sh
```

The bootstrap copies only provider configuration from the protected legacy
environment, generates new database/storage/worker/staff credentials, creates
the private `audentra-preview` Docker network, and installs
`/usr/local/sbin/audentra-platform-deploy`.

It preserves `OPENROUTER_DOCUMENT_MODEL` from the current or legacy protected
environment and writes `qwen/qwen3.7-flash` when the setting is absent. This is
separate from `OPENROUTER_MODEL`; `TRANSCRIPT_PARSING=groq` continues to use
`GROQ_MODEL` for transcript extraction.

The protected environment is `/opt/audentra-platform/shared/.env` with mode
`0600`. Never commit it. Retrieve the generated preview staff password through
an authorized IAP/OS Login session when needed.
