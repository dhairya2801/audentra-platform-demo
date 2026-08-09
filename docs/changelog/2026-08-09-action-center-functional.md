# Functional Enrollment Action Center — platform

## Outcome

The platform now supports a complete enrollment/onboarding action lifecycle from
student activity through staff resolution. Canonical state is committed first;
slow parsing, transcription, and AI enrichment run asynchronously and can be
retried without blocking API workers or losing source data.

## Canonical work and interaction model

- Added durable work-item types, priorities, statuses, assignment, escalation,
  SLA fields, comments, history, interactions, communications, outcomes, and
  follow-ups.
- Added staff-created work items with idempotency and optimistic version checks.
- Added append-only audit history for creation, assignment, status transitions,
  selected channel, communications, outcomes, and automatic resolution.
- Added deterministic scheduled action rules for inactivity and approaching
  requirement deadlines.

## Student help and document recovery

- Student help requests can be linked to an exact enrollment requirement.
- Submission immediately creates a routed staff notification and triage item.
- Transcript parsing failures retain the original and create or reopen one
  high-priority human-review task rather than duplicating work.
- A successful replacement or retry resolves linked help/review work, records
  why it was resolved, and notifies both student and staff.

## AI and media processing

- OpenRouter multimodal parsing supports PDF and image transcript input.
- Call speech-to-text is an independent durable job with retryable, failed, and
  configuration-required states.
- One quiet-window enrichment run produces task summary, communication outcome,
  suggested follow-up, and updated student summary from bounded canonical
  context.
- Student context includes previous summary, profile, onboarding/enrollment,
  requirements, documents, transcript courses, open work, communications, and
  recorded outcomes.

## Realtime delivery

- Added durable tenant-scoped notification and invalidation records.
- Added cursor-based staff SSE replay so reconnects do not lose updates.
- Kept polling as a bounded availability fallback rather than the primary path.

## Verification

- Backend lint, Ruff formatting, mypy/TypeScript type checks, and the complete
  repository test command pass.
- The Python suite reports 493 passing and 15 environment-gated integration
  tests skipped when isolated PostgreSQL/S3 test services are not configured.
- Browser validation exercised help creation, transcript failure/recovery,
  automatic task resolution, staff outreach, student delivery, and outcome
  completion against the running API and worker.
