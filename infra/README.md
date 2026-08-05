# Local infrastructure

The Compose stack runs the FastAPI application API, Python outbox worker,
PostgreSQL, MinIO, Keycloak, and Mailpit. A one-shot migration service applies
the checksummed SQL migrations, then a development-only seed service installs
the integrity-checked 264-row compatibility dataset and verifies seven MinIO
media objects before the application services start.

The migration, development seed, API, and worker roles use the same Python
image from `infra/docker/api.Dockerfile`. Compose selects `audentra-migrate`,
`audentra-seed`, `audentra-api`, or `audentra-worker` as the process command, so
releases promote one backend artifact while the API and worker remain
independently deployable.

## Start

From the repository root:

```sh
docker compose --env-file infra/.env.example -f infra/compose.yaml up --build
```

For direct-process development, start PostgreSQL/MinIO first and then run from
the repository root:

```sh
uv sync --directory apps/api --locked --all-groups
npm ci
npm run db:migrate
npm run db:seed
npm run dev:api
```

Run `npm run dev:worker` in a second terminal, or use
`npm run worker:once` to drain one bounded batch. The standard verification
commands are `npm run lint`, `npm run typecheck`, and `npm test`.

The defaults are development-only and require no cloud credentials:

| Service | URL |
| --- | --- |
| API | `http://localhost:4000` |
| API liveness | `http://localhost:4000/health` |
| API readiness | `http://localhost:4000/health/ready` |
| Keycloak | `http://localhost:8080` |
| Mailpit | `http://localhost:8025` |
| MinIO console | `http://localhost:9001` |

Keycloak imports the `vv-local` realm. Its demo student is
`student@vv.local` / `student_local_password`. The current FastAPI composition
uses the explicit `AUTH_MODE=demo` identity adapter; Keycloak is available for
developing the real identity adapter but is not yet consumed by the API.
`AUDENTRA_ENV=production` therefore fails closed: production cannot start with
demo authentication. Mailpit is likewise local integration infrastructure; a
production messaging adapter is still required.

To override ports or development credentials, copy `infra/.env.example` to
`infra/.env`, change the values, and pass that file with `--env-file`.

`OPENROUTER_MODEL` configures Edward and other non-extraction OpenRouter calls.
Document extraction uses the independently configurable
`OPENROUTER_DOCUMENT_MODEL` (default `qwen/qwen3.7-flash`). Groq transcript
routing remains controlled only by `TRANSCRIPT_PARSING=groq` and `GROQ_MODEL`.

Run the separately deployed Audentra portals repository on
`http://localhost:3000`, or set `WEB_ORIGIN` to its actual origin.

`audentra-seed` requires exactly one of `--data`, `--media`, or `--all` and
fails closed when `AUDENTRA_ENV=production`. It is safe to rerun: relational
rows use insert-only conflicts under an advisory-locked transaction, and media
uploads are skipped when object size and SHA-256 metadata already match.

## State and recovery

PostgreSQL, MinIO, and Mailpit data use named volumes. `docker compose down`
keeps them. `docker compose down --volumes` permanently removes local
development state and should only be used intentionally.

Application images use multi-stage builds and run as the non-root `audentra`
user. The API exposes liveness and PostgreSQL-backed readiness endpoints. The
worker is a headless process with no HTTP listener; supervise its process and
alert on outbox lag, retries, and dead letters. Both processes receive
`SIGTERM`, and Compose gives the worker a 30-second shutdown grace period.
Production should inject credentials through the platform secret manager and
use managed PostgreSQL, object storage, and identity services.
