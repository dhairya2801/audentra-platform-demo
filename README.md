# VV Enrollment Platform

Production-oriented foundation for a stateful student enrollment portal.
Authentication-aware entry routing sends a first-time student into resumable
onboarding and sends a returning student directly to their dashboard.

The codebase is a modular monolith plus an independently deployable outbox
worker. That keeps transactions and early product development simple while
preserving clear service boundaries for a later Kubernetes migration.

## What is implemented

- One-time, server-persisted, nine-step onboarding with ordered validation,
  optimistic versions, resume behavior, and completion gating
- Credential sign-up/sign-in with normalized unique contacts, scrypt password
  hashing, hashed opaque sessions, revocation, and account-scoped preview data
- Responsive dashboard plus enrollment, requirement detail, documents,
  messages, appointments, payments, profile, and help pages
- Working profile saves, message read state, appointment scheduling, original
  document uploads with reviewable structured extraction, simulated deposits,
  and idempotent offer acceptance
- Edward AI with a bounded student context, OpenRouter integration, usage
  reporting, safe navigation actions, and a useful no-key guided mode
- Loading, empty, validation, failure, retry, confirmation, and success states
- NestJS/Fastify API with validation, correlation IDs, typed errors, and CORS
- PostgreSQL schema, deterministic seed, migrations, audit log, idempotency
  records, and transactional outbox
- Batched, allowlisted student activity ingestion
- Outbox worker with leases, bounded retries, dead-lettering, event receipts,
  and a student dashboard projection
- Stateful, no-dependency preview API for running the entire portal without
  Docker while keeping the frontend on the same typed contracts
- Local PostgreSQL, Keycloak, MinIO, Mailpit, API, worker, and web stack
- Unit, contract, rendered-HTML, and integration tests plus CI

## Repository layout

```text
apps/
  api/       Student portal API and PostgreSQL migrations
  web/       Student portal user interface
  worker/    Transactional outbox processor and projections
packages/
  contracts/ Shared API and event contracts
infra/       Dockerfiles, Compose stack, and local service configuration
docs/        Architecture, security, tracking, agentic, and feature flows
tools/       Stateful no-Docker preview API and local launch script
```

## Run the portal now, without Docker

Requirements: Node.js 22.13 or newer and npm.

```bash
npm run demo:reset
npm run dev:portal
```

Open <http://localhost:3000>, create an account with an email, international
phone number, and password, then complete onboarding. Protected routes require
the HTTP-only credential session cookie; the dashboard remains gated until
onboarding is complete. Each account receives its own student state file and
upload directory. Profile changes, read messages, appointments, payments,
documents, provider responses, and reviewed extraction results persist in the
ignored `tools/demo-api/.data/` directory.

`npm run dev:portal` keeps both processes in the foreground. API request lines
contain `service:"vv-demo-api"` and a `requestId`. A document attempt also
prints `document_extraction_started`, any bounded retry/failure, and
`document_extraction_completed` with the same document/request IDs, provider,
model, course count, terminal status, and duration. Do not redirect this command
to a file when you want to watch parsing live.

Use `npm run demo:reset` while the server is stopped to restore the deterministic
first-visit scenario. This remains a development fixture; do not enter real
student, document, or payment data.

### Enable Edward and document extraction

The portal works without an LLM key: Edward uses a deterministic navigation
guide and uploaded originals remain available with extraction marked as waiting
for configuration. To enable the real agentic paths:

```bash
cp .env.example .env
```

Set `OPENROUTER_API_KEY` in `.env`, optionally choose
`OPENROUTER_MODEL`, and restart `npm run dev:portal`. The launcher reads the
root `.env`; the key stays server-side and is never included in the web bundle.
PDF/JPEG/PNG uploads are capped at 10 MB. PDFs are preprocessed locally with
Python/PyMuPDF into bounded text and rendered page images, then sent as
multimodal evidence to the configured model. The model returns JSON-only text;
the server normalizes, bounds, and review-gates the result.
Extracted fields must be selected by the student before they enter the review
state.

For faster text-only transcript extraction, also set `GROQ_API_KEY` and
`TRANSCRIPT_PARSING=groq`. That path uses `GROQ_MODEL` (default
`openai/gpt-oss-120b`), extracts PDF text without rendering pages, and requests
strict JSON-schema output. Set `TRANSCRIPT_PARSING=openrouter` to restore
text-plus-image transcript evidence. Non-transcript documents remain on the
OpenRouter multimodal path.

The complete Compose stack exposes the same routes through the Nest API. It
stores document metadata and review state in PostgreSQL, stores originals in
MinIO through the S3 API, and atomically claims extraction work so an
idempotent upload replay does not spend LLM tokens twice.

## Run the complete local stack

Requirements: Docker Desktop with Compose, Node.js 22.13 or newer, and npm.

```bash
cp .env.example .env
docker compose --env-file .env -f infra/compose.yaml up --build
```

Then open:

- Student portal: <http://localhost:3000>
- API readiness: <http://localhost:4000/health/ready>
- Mailpit: <http://localhost:8025>
- Keycloak: <http://localhost:8080>
- MinIO console: <http://localhost:9001>

The Compose migration job applies the schema and loads the deterministic demo
student automatically. Data remains in named volumes between restarts.

## Run checks without Docker

Each deployable package has its own lockfile and can be verified independently:

```bash
npm --prefix packages/contracts ci --workspaces=false
npm --prefix apps/api ci --workspaces=false
npm --prefix apps/worker ci --workspaces=false
npm --prefix apps/web ci --workspaces=false

npm --prefix packages/contracts run typecheck
npm --prefix apps/api run typecheck
npm --prefix apps/api test
npm --prefix apps/worker run typecheck
npm --prefix apps/worker test
npm --prefix apps/web run typecheck
npm --prefix apps/web run lint
npm --prefix apps/web test
npm --prefix tools/demo-api test
```

API and worker unit/integration tests use isolated in-memory or mocked
boundaries. CI additionally starts PostgreSQL, applies the real migration and
seed, and builds every package.

## Architectural documentation

Start with [the documentation index](docs/README.md), then read the
[current implementation map](docs/14-current-implementation-map.md),
[domain model reference](docs/15-domain-model-reference.md), and
[deployment/security runbook](docs/16-deployment-security-and-cicd.md). The
[system architecture](docs/01-system-architecture.md) and
[student feature flows](docs/03-student-portal-feature-flows.md) explain the
longer-term design. The
[agentic runtime guide](docs/11-agentic-runtime.md) covers Edward, document
extraction, cost controls, and the path from the local adapter to production.

The no-Docker credential adapter is intentionally isolated from domain logic.
The PostgreSQL migrations define the production identity boundary: a
single-use, hashed student invitation links an already-admitted student to a
credential account; password and session tokens are never stored in plaintext;
email/SMS verification delivery remains provider-adapted. Replacing the local
JSON adapter with that service or institutional OIDC does not change student
domain ownership or frontend API contracts.

## Deployed preview

The synthetic-data preview is available at
<https://aster.34-30-254-45.sslip.io>. It runs the production web build, the
account-isolated preview API, and Caddy on a hardened Google Compute Engine
`e2-micro`.

The host uses IAP-only SSH, OS Login, Shielded VM Secure Boot, default-deny
firewalls, root-owned releases, and sandboxed containers. A successful push to
`main` runs the full CI suite and then deploys the exact Git commit through
keyless GitHub OIDC/Google Workload Identity Federation. See the
[runbook](docs/16-deployment-security-and-cicd.md) and
[changelog](CHANGELOG.md).
