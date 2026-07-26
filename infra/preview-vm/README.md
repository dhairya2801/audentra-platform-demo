# Free VM preview deployment

This deployment profile runs the production web build, the account-isolated
preview API, and Caddy on a small VM. Caddy is the only public service and
routes `/v1/*` to the API while serving the portal from the same HTTPS origin.

It deliberately does not start PostgreSQL, MinIO, Keycloak, Mailpit, or the
outbox worker. Those services remain available in `infra/compose.yaml`, but the
complete stack needs more memory than an `e2-micro` VM provides.

Copy `.env.example` to `.env`, replace `SITE_HOST`, add server-side provider
keys, and keep the file mode at `0600`. The Compose volume
`vv-edgent-preview_preview-data` preserves accounts, student state, documents,
and provider response attempts across container restarts.

Start or update the preview:

```bash
docker compose --env-file .env -f compose.yaml up -d --build
docker compose --env-file .env -f compose.yaml ps
```

Only TCP ports 80 and 443 and UDP port 443 should be public. Do not expose the
API or the persistent data volume directly.

On a new Ubuntu VM:

1. Run `provision-host.sh` through `sudo`.
2. Run `harden-host.sh` through `sudo`.
3. Place the protected environment at `/opt/vv-edgent/shared/.env` with root
   ownership and mode `0600`.
4. Install `deploy-release.sh` as
   `/usr/local/sbin/vv-edgent-deploy`, owned by root and mode `0755`.
5. Configure the repository-specific GitHub OIDC/WIF identity described in
   `../../docs/16-deployment-security-and-cicd.md`.

The deployment user is deliberately not added to the Docker group because the
Docker socket is root-equivalent. CI uploads an immutable Git archive through
IAP and invokes the root-owned deployment command. The command serializes
deployments, validates the archive, builds one image at a time, switches the
active release atomically, waits for health, and restores the previous release
on failure.

The complete hardening and operations guide is
`../../docs/16-deployment-security-and-cicd.md`.
