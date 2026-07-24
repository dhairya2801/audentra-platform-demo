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
- Responsive dashboard plus enrollment, requirement detail, documents,
  messages, appointments, payments, profile, and help pages
- Working profile saves, message read state, appointment scheduling, document
  metadata creation, simulated deposits, and idempotent offer acceptance
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

Open <http://localhost:3000>, choose **Continue with demo student**, and the
fictional student begins at onboarding. Protected routes require the HTTP-only
demo session cookie. After onboarding is completed, `/` and `/onboarding` both
route returning visits to `/dashboard`. Profile changes, read messages,
appointments, payments, and document metadata persist in the ignored
`tools/demo-api/.data/state.json` fixture.

Use `npm run demo:reset` while the server is stopped to restore the deterministic
first-visit scenario. This preview records document metadata and simulated
payment results only; do not enter real student, document, or payment data.

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
[system architecture](docs/01-system-architecture.md) and
[student feature flows](docs/03-student-portal-feature-flows.md).

The placeholder demo identity is intentionally isolated behind configuration.
Replacing it with Keycloak JWT verification does not require changing domain
services, database ownership, or frontend API contracts.
