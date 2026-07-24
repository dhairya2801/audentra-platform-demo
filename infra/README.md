# Local infrastructure

The Compose stack runs the web application, API, outbox worker, PostgreSQL,
MinIO, Keycloak, and Mailpit. A one-shot migration service applies the API
migrations and deterministic development seed before the application services
start.

## Start

From the repository root:

```sh
docker compose --env-file infra/.env.example -f infra/compose.yaml up --build
```

The defaults are development-only and require no cloud credentials:

| Service | URL |
| --- | --- |
| Web | `http://localhost:3000` |
| API | `http://localhost:4000` |
| Worker health | `http://localhost:3002/health/ready` |
| Keycloak | `http://localhost:8080` |
| Mailpit | `http://localhost:8025` |
| MinIO console | `http://localhost:9001` |

Keycloak imports the `vv-local` realm. Its demo student is
`student@vv.local` / `student_local_password`. The current API uses its
explicit demo identity adapter; the realm exists so the real adapter can be
introduced without replacing local infrastructure.

To override ports or development credentials, copy `infra/.env.example` to
`infra/.env`, change the values, and pass that file with `--env-file`.

## State and recovery

PostgreSQL, MinIO, and Mailpit data use named volumes. `docker compose down`
keeps them. `docker compose down --volumes` permanently removes local
development state and should only be used intentionally.

Application images use multi-stage builds, run as the non-root `node` user,
expose health checks, and receive `SIGTERM` with a 30-second worker grace
period. Production should inject credentials through the platform secret
manager and use managed PostgreSQL, object storage, and identity services.
