# Changelog

This changelog summarizes user-visible releases. Detailed notes are separated
by persona so students, staff, and implementation teams can review only the
changes that affect them.

## 2026-07-31 — Student experience and staff operations

### Student users

- Expanded onboarding into a complete, resumable enrollment-intake flow with
  housing and roommate preferences, support interests, emergency contacts,
  family permissions, review, signature, and deposit planning.
- Added clearer enrollment progress and optional follow-up summaries.
- Redesigned Financials with an accessible cost-coverage visualization,
  clearer balance context, aid sources, and required-document actions.
- Added Campus Life search and category filters.
- Added Edward voice input and optional spoken replies on supported browsers.
- Improved small-text readability and responsive behavior across portal pages.
- Connected staff document decisions, published content, and notifications to
  the same student-facing records used by the portal.

See [Detailed student changelog](docs/changelog/2026-07-31-student-experience.md).

### Staff users

- Added authenticated staff operations routes for each tenant.
- Added Today, Action Center, shared Task Board, Students, Messages, Journeys,
  Campus Life, Academics, Knowledge Base, Core Plays, and Edward workspaces.
- Added a Jira-style task board with drag-and-drop status changes, assignment,
  prioritization, escalation, student context, and append-only activity.
- Added a personal Action Center with prioritized students, risk evidence,
  recommended actions, and communication history.
- Added structured journey, event, club, and course editing. Raw YAML remains an
  internal versioned persistence format and is no longer shown to staff.
- Added Edward-assisted configuration drafts with explicit review and publish
  confirmation.
- Added a deterministic 400-student local cohort for workflow and scale testing.
- Improved task-board sizing, scrolling, responsive behavior, and journey-label
  wrapping at desktop and mobile breakpoints.

See [Detailed staff changelog](docs/changelog/2026-07-31-staff-operations.md).

### Engineering and operations

- Added shared staff contracts, staff API routes, PostgreSQL migration
  `0018_staff_action_center.sql`, audit/outbox integration, local credential
  authentication, and durable preview-state upgrades.
- Added tenant-owned journey, Campus Life, and academic configuration documents.
- Added lifecycle-safe detached portal commands that avoid nested npm process
  trees on Windows.
- Added student/staff architecture, sequence diagrams, state-freshness guidance,
  production boundaries, and an implementation runbook.
- Expanded rendered web, API, state-store, and portal lifecycle coverage.

See [Staff portal implementation and operations](docs/24-staff-portal-implementation-and-operations.md).
