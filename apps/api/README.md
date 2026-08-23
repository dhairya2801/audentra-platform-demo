# Audentra Platform API

FastAPI is the inbound HTTP adapter for a framework-neutral Python application core. The API
keeps the existing route and response contracts while PostgreSQL, S3-compatible storage, AI
providers, document processing, and the durable outbox worker remain replaceable adapters.

## Architecture

```text
portal -> FastAPI routes -> application service -> domain rules
                                      |-> PostgreSQL repositories
                                      |-> S3-compatible object storage
                                      |-> OpenRouter/Groq adapters

PostgreSQL outbox -> independent Python worker -> projections/internal API commands
```

FastAPI owns validation, CORS, request IDs, error translation, and dependency injection only.
Business behavior does not import FastAPI. SQLAlchemy uses bounded async pools; synchronous S3
and CPU-heavy document work run outside the event loop. The API and worker are separate processes
and can be scaled independently.

## Local development

Requirements: Python 3.11-3.13, [uv](https://docs.astral.sh/uv/), PostgreSQL, and an
S3-compatible bucket (MinIO works locally).

```powershell
Copy-Item .env.example .env
# Replace every placeholder in .env, then:
uv sync --locked --all-groups
uv run --env-file .env audentra-migrate
uv run --env-file .env audentra-seed --all
uv run --env-file .env audentra-api
```

Run the worker in another terminal after the API is ready:

```powershell
uv run --env-file .env python -m audentra.interfaces.worker.main
```

For reload during development, replace the API command with:

```powershell
uv run --env-file .env uvicorn audentra.interfaces.http.main:app --reload --host 0.0.0.0 --port 4000
```

Quality checks:

```powershell
uv run ruff check src tests
uv run mypy src
uv run pytest
uv run pytest --cov=audentra --cov-report=term-missing
```

PostgreSQL integration tests require `TEST_DATABASE_URL`. Migrations are explicit and never run
as an API startup side effect. Seeding is an explicit development/test-only command:
`--data` inserts the 264-row relational fixture, `--media` verifies/uploads seven JPEGs, and
`--all` runs them in that order. Exactly one mode is required, and production fails closed.
Relational rows use deterministic IDs and `ON CONFLICT DO NOTHING`; rerunning is safe and does not
overwrite local demo changes. Media files are all checksum-preflighted before any remote write.
The snapshot also fixes the reviewed default AI operation models/token limits instead of reading
provider-model overrides during seeding; runtime configuration remains editable in PostgreSQL.

## Containers and deployment

The Dockerfile uses a locked production dependency graph, compiled bytecode, Debian slim stages,
and an unprivileged UID/GID (`10001`). Its default command runs one API process per container;
scale with replicas rather than multiplying Uvicorn workers inside one container.

```powershell
docker build --tag audentra-platform-api:local .
docker build --target worker --tag audentra-platform-worker:local .
docker build --target seed --tag audentra-platform-seed:local .
docker run --rm --env-file .env audentra-platform-api:local audentra-migrate
docker run --rm --env-file .env audentra-platform-seed:local
docker run --rm --env-file .env -p 4000:4000 audentra-platform-api:local
```

The worker target reuses the same locked runtime layer but has a worker command and no API health
check:

```powershell
docker run --rm --env-file .env audentra-platform-worker:local
```

Deploy in this order: apply migrations once; optionally run the seed target in a non-production
preview; start/canary the API; verify `/health` and `/health/ready`; then start the worker.
`/health` is process liveness; `/health/ready` verifies
PostgreSQL connectivity and is the container health check. The worker needs private access to the
API through `API_INTERNAL_URL` and the same `DOCUMENT_WORKER_TOKEN`. Never expose internal worker
routes publicly. The Python worker has no HTTP listener; use process supervision/restart policy
for liveness and outbox-lag/error metrics for operational health.

Budget database connections before scaling: each API or worker process can use
`DB_POOL_SIZE + DB_MAX_OVERFLOW` connections. Keep the S3 bucket private, restrict credentials to
that bucket, terminate TLS at a trusted ingress, and inject secrets from the deployment platform
rather than baking them into the image or committing a populated `.env`.

### Production authentication guard

`AUTH_MODE=demo` is limited to development/test and is rejected when
`AUDENTRA_ENV=production`. `AUTH_MODE=oidc` implements a Google/Microsoft OIDC authorization-code
flow with PKCE for existing students in one server-configured tenant. It is a student-only proof:
credential authentication and staff authentication fail closed while it is selected, and
staff/leader/VP federation is not yet implemented. Follow
[`docs/runbooks/student-sso-local.md`](../../docs/runbooks/student-sso-local.md) before enabling it,
including its hosted-edge and callback-log requirements.

### Rollback

Keep the previous API and worker images available during the compatibility window. If a release
fails, pause the new worker, route traffic back to the prior API image, run contract/health smoke
tests, then resume its matching worker. Database migrations are forward-only: do not down-migrate
or delete data during rollback. Roll back application images only when every applied migration is
backward compatible with the prior release; otherwise restore from a tested backup in a controlled
maintenance window. The legacy Nest image can remain the emergency rollback target until FastAPI
parity is accepted.

## Environment compatibility

The main Nest-era names remain valid: `DATABASE_URL`, `API_PORT`, `WEB_ORIGIN`,
`DOCUMENT_WORKER_TOKEN`, `OBJECT_STORAGE_*`, `OPENROUTER_*`, `GROQ_*`, and `TRANSCRIPT_PARSING`.
`NODE_ENV` is accepted as a fallback for `AUDENTRA_ENV`, and `PORT` as a fallback for `API_PORT`.

These changes are intentional:

| Previous setting | Python setting/status |
|---|---|
| `WORKER_POLL_INTERVAL_MS` | `WORKER_POLL_INTERVAL_SECONDS` (seconds, supports decimals) |
| *(new)* | `AGENTIC_WORKFLOW_INTERVAL_SECONDS` (scheduled inbox/engagement scan cadence; default 300) |
| `WORKER_HEALTH_PORT` | Removed; the Python worker does not expose an HTTP health port |
| `DOCUMENT_PYTHON_BIN` | Removed; document processing runs directly in this Python process |
| `OPENROUTER_STORE_RESPONSES` | Removed; provider diagnostics use the durable repository journal |
| `OIDC_ISSUER_URL` | Not consumed; use the fixed Google/Microsoft provider settings documented in the student SSO runbook |
| `SMTP_HOST`, `SMTP_PORT` | Not consumed by the current API/worker composition |

The Python relational seed is an integrity-checked snapshot of a clean legacy Nest seed run: 264
rows across the same 46 tables, including both demo tenants, default student/offer IDs, AI runtime,
rewards, and staff queues. Its media mode intentionally preserves the legacy seven Aster uploads.
The four Harvard media metadata rows still reference Harvard-prefixed keys that the legacy seeder
never uploaded; this historical gap is not silently expanded by the compatibility seed.

See [.env.example](./.env.example) for every supported deployment setting and safe placeholders.
