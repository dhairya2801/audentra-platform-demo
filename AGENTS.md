# Audentra Platform contributor guide

This repository is the active backend. It owns FastAPI routes, PostgreSQL,
object storage, authentication, workers, migrations, seed data, durable events,
and the canonical API contract. Student and staff UI changes belong in the
sibling `Audentra-portals` repository.

## Repository boundaries

- Keep `packages/contracts` canonical here and synchronize its consumer snapshot
  in `Audentra-portals` whenever a public contract changes.
- Add a new numbered migration for every schema change. Never rewrite an applied
  migration.
- Never commit `.env`, credentials, uploaded student files, local database data,
  model prompts containing student data, or captured provider responses.
- Tenant ID and actor authorization must be enforced in every query, including
  background jobs and realtime event reads.

## Durable workflow rules

- API requests commit canonical state and enqueue durable work. LLM calls,
  speech-to-text, document processing, and other slow operations run in workers.
- Make event consumers idempotent and retry-safe. Preserve originals and expose
  retry/dead-letter state instead of silently discarding failed work.
- Realtime delivery is an invalidation layer, not the source of truth. Persist
  notifications/events before streaming them and support cursor replay.
- Build AI context from canonical, tenant-scoped records. Aggregate related
  activity behind a quiet window rather than invoking a model per message.
- Content publication is deterministic: validate, version, materialize, audit,
  and emit an outbox event in one transaction. Do not require an LLM to publish
  student-facing content.
- Production-facing writes must not depend on the in-memory preview repository.

## Validation

Run the relevant subset while developing and the full gates before handoff:

```text
npm run lint
npm run typecheck
npm test
```

Database and object-storage integration tests require their documented isolated
test services. Do not point tests at a developer or preview database.
