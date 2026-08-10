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
- A grouped student experience reminder must version-check and defer every
  displayed update atomically. The API owns the durable reminder state; the
  portal owns visit-scoped presentation so route navigation and realtime
  invalidations never create a second blocking dialog.
- Build AI context from canonical, tenant-scoped records. Aggregate related
  activity behind a quiet window rather than invoking a model per message.
- A communication-driven enrichment job represents an interaction source
  version. Coalesce arrivals behind `quiet_until`, preserve the newest requested
  version while a job is running, and rebuild from canonical evidence rather
  than letting an older result overwrite newer communication.
- External email or telephony integrations must durably ingest an `inbox_event`
  or stored recording first. Never invoke transcription or an LLM directly from
  a provider webhook or API request handler.
- Use strict OpenRouter JSON Schema for a verified model route whenever the
  feature has a code-owned schema; retain local normalization and validation as
  the server-side safety boundary for every provider response.
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
- For multi-page inputs, schema version `1` and the paged `form` object are
  canonical. Validate one to twenty pages, unique stable page IDs, unique field
  IDs across the complete form, and the existing bounded field palette. Keep a
  flattened `fields` value only as a temporary compatibility projection.
- Journey-template publication is ordinary canonical journey publication. The
  server must validate every generated node and dependency, use expected-version
  concurrency, reconcile active student journeys, and never trust template
  metadata as an authorization or validation bypass.
- Answer-driven branches use a target task's canonical `activation` rules. Each
  rule source must also be a direct prerequisite and must reference a bounded,
  deterministic checkbox, required selection, or bounded number answer from
  that source. Validate explicit switch cases (`equals`, `one_of`), default
  paths (`none_of`), and finite numeric threshold comparisons against the
  source field's published options and range.
- Reconcile routes after publication and after every requirement completion,
  including generic responses, uploads, payments, and staff document decisions.
  A false branch becomes `not_applicable` without earning a reward; it counts as
  complete for progress and convergence. Preserve started, rejected, submitted,
  reviewed, waived, expired, and completed evidence.
- A convergence task may depend on the decision plus every branch endpoint.
  Unselected endpoints satisfy the merge as `not_applicable`; the selected path
  must actually complete. Never restore the old prerequisite-only unlock SQL in
  one completion path, because it would bypass conditional routing.
- Built-in onboarding screens remain the protected first-time gate. Other
  onboarding-authored tasks materialize through the shared requirement engine
  after that gate so submissions, dependency unlocks, rewards, notifications,
  and audit behavior do not fork into a second runtime.

## Validation

Run the relevant subset while developing and the full gates before handoff:

```text
npm run lint
npm run typecheck
npm test
```

Database and object-storage integration tests require their documented isolated
test services. Do not point tests at a developer or preview database.
