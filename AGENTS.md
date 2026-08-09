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

## Managed-content invariants

- PostgreSQL is canonical for published Campus Life, academic catalog,
  Knowledge Base, Core Play, and club records. Tenant YAML is seed/import input,
  not a second runtime source of truth.
- Keep event and course `source_id` values stable across publications. Never
  hard-delete an event that may have registrations; retire it, cancel active
  registrations, preserve history, notify affected students, and omit it from
  subsequent student reads.
- Event registration must lock and re-read the current event version before
  writing. Reject missing, inactive, past, or stale events and make duplicate
  registration requests idempotent.
- Notify registered students when an event's date, time, location, availability,
  or cancellation state changes. Commit the content change, registration
  transition, message, audit entry, and outbox record atomically.
- Course media is optional. Accept only bounded HTTPS YouTube video or playlist
  metadata; never fetch, proxy, or store third-party media in the API.
- Knowledge Base, Core Play, and club CRUD must remain tenant-scoped, versioned,
  audited, and outbox-backed. These records do not enter LLM context until a
  separately reviewed retrieval policy is implemented.
- Enrollment and onboarding journey definitions are a separate, versioned graph
  and form-builder domain. Content-management changes must not rewrite completed
  journey work or bypass dependency-cycle and input-type validation.

## Validation

Run the relevant subset while developing and the full gates before handoff:

```text
npm run lint
npm run typecheck
npm test
```

Database and object-storage integration tests require their documented isolated
test services. Do not point tests at a developer or preview database.
