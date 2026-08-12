# Functional content management — platform

## Outcome

Campus Life, the academic catalog, Knowledge Base cards, Core Plays, and clubs
now use durable tenant-scoped records. Staff publication commits deterministic
database state; student portals read the latest active state on navigation or
refresh. Enrollment and onboarding journey builders remain a separate domain.

## Campus Life lifecycle

- Added stable event source identities and per-event versions so a staff edit
  updates the same event instead of creating a new logical record.
- Added persistent student registrations and append-only registration history.
- Serialized registration against event publication so a student cannot register
  against a concurrently retired or changed event.
- Made duplicate requests idempotent and allowed a student to register again if
  a previously retired event is intentionally republished.
- Rejected unknown, inactive, past, and stale-version registration attempts with
  explicit domain errors.
- Soft-retired removed events, cancelled active registrations, retained their
  history, and created affected-student messages before hiding the cards.
- Created student messages for material changes such as a revised start time or
  location. Audit and outbox records are committed with the publication.

## Academic catalog media

- Added nullable `relatedVideos` metadata to courses.
- Accepted at most five HTTPS YouTube video or playlist entries per course and
  rejected unsupported hosts or malformed URLs.
- Added tenant seed metadata for official university/open-course material while
  keeping the platform independent of third-party playback.

## Durable staff content

- Added PostgreSQL-backed Knowledge Base cards and Core Plays with lazy default
  materialization for existing tenants.
- Added create/update behavior for Knowledge Base, Core Play, and club content,
  including tenant media checks, optimistic versions, audit entries, and outbox
  events.
- Kept content publication independent from AI. These records are not injected
  into model context yet.

## Schema and compatibility

- Migration `0029_content_management.sql` adds stable identifiers, versions,
  course media, registrations, registration history, Knowledge Base cards, and
  Core Plays without rewriting prior migrations.
- The canonical temporary TypeScript contract now exposes event versions,
  registration state, registration inputs/results, and course-video metadata.

## Verification

- Ruff formatting/lint and mypy pass. The complete Python suite reports 503
  passing tests, 15 environment-gated integration skips, and 67.02% coverage
  against the 67% gate; all 59 Node workspace tests also pass.
- Tests cover publication, duplicate registration, stale versions, retirement,
  reactivation, tenant isolation, media validation, audit, and outbox behavior.
- Browser validation exercised staff rescheduling and cancellation against a
  registered student and verified refresh-driven reads plus notification delivery.
