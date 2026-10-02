# October 2 teammate handoff

Both repositories use `test.audentra-2oct`:
[Platform](https://github.com/dhairya2801/audentra-platform-demo/tree/test.audentra-2oct),
[Portals](https://github.com/dhairya2801/audentra-portals-demo/tree/test.audentra-2oct).

## Local synthetic demo

Requirements: Node 22, Python 3.12+, uv, Docker Compose. From this checkout:

```bash
tools/handoff/setup-local.sh
tools/handoff/start-local.sh
```

Setup generates ignored `.env.handoff` with random local credentials, starts
separate `audentra-handoff` PostgreSQL and MinIO containers on loopback ports
5548/9148/9149, applies all migrations through `0080_financial_award_terms.sql`,
and imports the deterministic university, catalog, Ada/Camila task board,
document evidence and financial fixtures. It does not copy the deployed database
or uploaded files. Subsequent actions are preserved; use fresh dedicated volumes
to rebuild a baseline. Initial import can take several minutes. The historical four-step overlay is
not applied: its guard rejects the current generated prerequisite state.
Existing canonical prerequisites and student work are preserved.

The API listens on `http://127.0.0.1:4000`; `/health/ready` must return ready.
The frontend instructions are in the companion Portals README. Use Ada at
`/sign-in` and Camila at `/staff`. Shared-demo edits made on the hosted site are
not part of this synthetic baseline. Exact live data requires a separately
approved, secure database/object-storage transfer; never commit such exports.

`.env.handoff` names: `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`,
`HANDOFF_POSTGRES_PORT`, `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `MINIO_BUCKET`,
`HANDOFF_STORAGE_PORT`, `HANDOFF_STORAGE_CONSOLE_PORT`, `DOCUMENT_WORKER_TOKEN`,
`FERPA_DELEGATE_LINK_SECRET`, `VV_STAFF_INVITATION_CODE`, `OPENAI_API_KEY`,
`OPENAI_MODEL`. The launcher derives `DATABASE_URL` and the `OBJECT_STORAGE_*`
settings and enables credentialed auth for `http://localhost:3000`.

Set your own server-side `OPENAI_API_KEY` to enable Edward (`gpt-6-luna`); without
it, the local launcher explicitly disables model calls. Optional extraction
providers use `OPENROUTER_API_KEY` or `GROQ_API_KEY`; external voice additionally
requires `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, and
`VOICE_AGENT_INTERNAL_TOKEN`. These providers are not needed for the manual
review demo. No background worker is started, matching the hosted frozen demo.

Normal non-frozen environments use `npm run dev:worker` with the same protected
runtime settings. Do not start a worker against a frozen reset-enabled database.
External SSO requires provider registration plus `API_PUBLIC_URL`,
`GOOGLE_OAUTH_CLIENT_ID`/`GOOGLE_OAUTH_CLIENT_SECRET` or
`MICROSOFT_OAUTH_CLIENT_ID`/`MICROSOFT_OAUTH_CLIENT_SECRET`; callbacks are
`<API_PUBLIC_URL>/v1/auth/sso/{google|microsoft}/callback`. Neither provider is
configured on the current demo.

## Hosted deployment and rollback

The verified topology is Vercel at `https://test.audentra.ai`, server-side
`API_PROXY_ORIGIN` over HTTPS to VM Caddy, then a private API/PostgreSQL/MinIO
Docker network. Only Caddy exposes host ports. Vercel builds from `apps/web` with
`npm run vercel-build`; preserve the project's configured API proxy and build
flags. Keep `NEXT_PUBLIC_API_BASE_URL` empty so sessions remain first-party.
API CORS must include the exact portal origin with credentials. Public frontend
variables are build-time configuration; no provider key belongs there.

The VM uses `/opt/audentra-platform/current`, root-owned
`/opt/audentra-platform/shared/.env` (0600), and each release's private
`demo-deployment.env` (0600). Both `infra/preview-vm/compose.yaml` and
`infra/preview-vm/demo-reset.override.yaml` are required. Preserve the existing
`DEMO_DATABASE_NAME`, `DEMO_STORAGE_BUCKET`, `PLATFORM_IMAGE_URI`, runtime secrets,
frozen baseline and object volumes. Templates are under `infra/preview-vm`.

For an API update, retain the current release/image, build
`docker build -f infra/docker/api.Dockerfile -t audentra-platform:<commit> .`
from the reviewed commit on the VM, prepare a new release directory, and copy
its predecessor's protected `demo-deployment.env`, changing only
`PLATFORM_IMAGE_URI`. Compare rendered Compose settings before switching:

```bash
# Run on the VM with root access; RELEASE is the reviewed absolute release path.
docker compose --env-file /opt/audentra-platform/shared/.env \
  --env-file "$RELEASE/demo-deployment.env" \
  -f "$RELEASE/infra/preview-vm/compose.yaml" \
  -f "$RELEASE/infra/preview-vm/demo-reset.override.yaml" \
  up -d --no-deps --no-build api
```

Verify container health and public `/health/ready` before changing `current`.
Rollback uses the same command with the retained previous release, then restores
`current`. No migration is required for this handoff's document-link fix. Never
run the generic seeded-preview deployment script against this frozen demo.
Frontend rollback re-promotes the retained Vercel production deployment.

## Provenance and checks

The frontend base is deployed `cf092dd`; the backend base is deployed `84f6860`.
Direct VM inspection matched all 175 installed Python files and 618 release
files to that backend commit: no VM-only source changes. Handoff includes the
pending staff document-link/PDF-preview fixes, a clean-checkout Node test fix,
local setup helpers and removal of personal paths/test identity details.
Original worktrees and default branches are preserved.

Secret scans cover tracked source and reachable history, explicitly including
OpenAI patterns. No real provider key was detected; 15 scanner matches are
synthetic test passwords, idempotency identifiers and a fake LiveKit fixture.
The configured VM secrets were absent from release files; they remain in the
protected runtime environment. Existing shared history has not been rewritten.
If a previously exposed key is discovered elsewhere, rotate it; deleting a
current file cannot revoke it. Personal paths/test identity details removed at
the branch tip remain in existing shared history.

Frontend typecheck/lint/tests/build and Vercel production build pass (24 existing
lint warnings). Backend lint/typecheck/build and Node tests pass; Python has
1,464 passing and 172 skipped tests, but its pre-existing coverage gate fails at
62.24% against 67%. The focused staff-document authorization and email tests
pass (19 tests), including isolated PostgreSQL authorization checks.

Live Chrome desktop/mobile staff sign-in, Task Board, filters, navigation and
error/retry passed. Student sign-in, core routes, HTTPS redirects, secure cookies,
readiness and allowed/denied CORS passed. WebKit launch is blocked on this host by
missing `libavif.so.13`. External SSO, paid model calls, external delivery and
payment capture are not certified by these checks.
