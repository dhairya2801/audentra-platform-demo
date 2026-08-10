# Action Center lifecycle hardening

Date: 2026-08-10

## Outcome

The Action Center now treats every communication, document result, and AI
projection as a durable, versioned lifecycle. The API commits the student and
staff record first; workers handle slow processing after the transaction has
completed. A provider failure, stale browser, or burst of communication cannot
silently discard the original evidence or overwrite newer staff context.

## Communication and AI lifecycle

- A portal help request or student reply creates or reopens the linked
  requirement work item, preserves the source message, records staff history,
  and routes a durable staff notification in the same transaction.
- Staff-recorded communications create an immutable `communication_event`.
  Outbound portal messages additionally create the student's inbox message in
  that transaction; external email and voice delivery remain provider-adapter
  work rather than a browser-side simulation of delivery.
- Every interaction maintains a monotonically increasing source version. New
  evidence resets a five-minute quiet window and upserts one deduplicated
  `interaction_enrichment` job. The delay is bounded so an active conversation
  cannot prevent a useful summary indefinitely.
- A claimed job records the source version it is processing. If newer evidence
  arrives while it runs, the completed job returns to pending and rebuilds from
  the newer canonical snapshot instead of persisting stale coverage.
- Interaction enrichment writes an outcome revision, conversation signals, and
  student-summary revision from one schema-constrained model response. Task
  insight jobs independently write task summary, objective, success definition,
  suggested approach, and suggested channel.

## Documents and calls

- Document extraction remains an outbox-driven worker command. The original
  upload remains stored on every parser failure; failures create or reopen a
  single review task and successful replacement evidence resolves linked help
  and review work with audit history.
- Call recordings are stored before transcription. The transcription worker uses
  leases, bounded concurrency, retry/backoff, and revisioned transcripts; a
  failed transcription never removes the audio or an earlier transcript.
- A completed transcript becomes communication evidence and queues the normal
  interaction-enrichment path. Staff can retry safely from the Action Center.

## Provider boundary

- `openai/gpt-5.6-luna` and `openai/gpt-5.6-luna-pro` use OpenRouter strict
  JSON Schema with provider parameter enforcement for supported extraction and
  Action Center operations.
- Unsupported tenant-selected routes retain JSON-object mode plus strict local
  normalization and validation. Backend-generated metadata is serialized by the
  application and is not delegated to the model schema.
- No live external email inbox poller, email sender, or telephony webhook is
  enabled in this release. A future adapter must persist external input as an
  `inbox_event` or a recording before the scheduler/worker processes it.

## Integrity and visibility

- Canonical data is protected by tenant scoping, expected versions, row locks,
  idempotency keys, append-only audit/work history, and retry-safe worker
  claims.
- Staff and student realtime streams are durable invalidation journals with
  cursor replay. They tell a portal to refetch canonical REST data; they never
  become a competing source of truth.
