# Staff Operations Changelog — 2026-07-31

## Audience

These notes describe the new enrollment-operations workspace for admissions,
registrar, financial-aid, student-life, and related university staff. The local
workspace is a functional product preview backed by synthetic data.

## Authentication and tenant routing

- Added tenant-aware staff routes at `/<tenant>/staff`.
- Added a local staff sign-in boundary with scrypt-hashed fixture credentials,
  opaque session tokens, an HTTP-only cookie, expiry, and sign-out.
- Kept student and staff sessions separate.
- Production still requires institutional OIDC/SAML, tenant membership, roles,
  component permissions, and field-level authorization.

## Today workspace

- Added a concise operating summary for assigned work, urgent students,
  unassigned items, inquiries, and content health.
- Added quick entry points into the personal Action Center, shared task board,
  Messages, and student-experience editors.
- Added explicit labels distinguishing live, preview, and simulated capabilities.

## Personal Action Center

- Added the signed-in staff member’s highest-priority student queue.
- Added deterministic melt-risk score, category, diagnosis, and evidence for the
  synthetic preview cohort.
- Added the recommended next action, expected impact, urgency, and linked task.
- Added journey progress, requirements, documents, communication history, and
  prior outcomes in one decision surface.
- Added task-status updates and simulation-only delegated outreach.
- Kept risk explanations labeled as test intelligence; they are not an approved
  production model.

## Shared Task Board

- Added Jira-style To do, In progress, and Done columns.
- Added drag-and-drop status changes and inspector-based status updates.
- Added search plus component and ownership filters.
- Added assignee, priority, due date, escalation flag, notes, student summary,
  document-review context, and append-only activity.
- Added expected-version conflict protection so a stale staff tab cannot silently
  overwrite a colleague’s newer change.
- Aligned the board and inspector to one viewport-height workbench, retained
  independent column scrolling, and removed the large empty area below columns.
- Added status accents, readable count badges, card hover/selection states, thin
  scrollbars, and mobile horizontal scrolling without body overflow.

## Student records

- Added a searchable 400-student deterministic cohort for local testing.
- Added program, class year, journey status, requirement counts, document counts,
  risk context, preferences, and related staff work.
- Added permitted operational preference updates with optimistic versions.
- Prevented staff from rewriting signed consent, identity evidence, financial
  balances, or transcript outcomes through a generic editor.

## Message Portal

- Added student inquiry intake, assignment, status, response note, and resolution.
- Added optional student inbox notification after a staff response.
- Added ownership and version checks to prevent lost updates.
- Email, SMS, and voice remain placeholders until governed provider adapters are
  available.

## Journeys

- Added onboarding and enrollment-checklist tabs sourced from tenant-managed,
  versioned configuration.
- Added readable task type, owner, required/optional state, dependencies, and
  points.
- Fixed task-type/points overflow at the annotated desktop width.
- Made each journey row clickable and added a structured editor for title,
  instructions, task type, submission type, owner, points, dependencies, and
  required status.
- Removed raw YAML from the staff interface. YAML remains the internal versioned
  storage and validation format.
- Added Edward natural-language drafts with a visible change summary, warnings,
  and explicit confirm-and-publish action.
- Preserved the rule that new versions apply to new journeys while completed
  student work retains historical configuration.

## Campus Life

- Added upcoming-event management alongside club management.
- Made each event row clickable and added structured fields for title,
  description, dates, location, category, featured state, accent, visual theme,
  and registration URL.
- Kept club create/edit forms for ownership, category, description, contact,
  membership state, and current update.
- Removed the raw event YAML panel from the staff interface.
- Kept Edward-assisted draft and publish behavior for bulk or natural-language
  event changes.
- Published content feeds the student Campus Life projection.

## Academics

- Added a searchable student-visible course catalog.
- Made every course row clickable and added structured editing for code, title,
  description, credits, level, prerequisites, instructors, meeting pattern, and
  availability label.
- Removed the raw academic YAML panel from the staff interface.
- Kept Edward-assisted catalog drafts with review and confirmation.
- Explicitly separated editable catalog content from governed transcript,
  exemption, and completed-course outcomes.

## Knowledge Base and Core Plays

- Added create/edit forms for internal, student-facing, and shared knowledge
  cards.
- Added category, audience, status, version, summary, and body fields.
- Added create/edit forms for repeatable operational plays with triggers,
  audiences, ordered steps, and lifecycle status.
- Preserved these as tenant content records rather than hard-coded page copy.

## Edward for staff

- Added a natural-language staff workspace for reading context and drafting
  controlled plans.
- Added configuration drafts for journeys, events, and courses.
- Required explicit staff confirmation before publishing a draft.
- Kept general cross-workspace writes in preview mode and external outreach in
  simulation mode.

## Shared-state behavior

| State | Current freshness |
| --- | --- |
| Task status, assignee, and escalation | Immediate local result plus five-second/focus refresh. |
| Personal Action Center | Refetch after mutation, on interval, and on focus. |
| Published configuration | Immediate publisher refresh; other sessions converge on refresh. |
| Student unread notifications | Fifteen-second/focus refresh in the student portal. |
| Document parsing | Server-owned job with bounded polling on the document surface. |

Polling is the first implementation. A later SSE gateway should send only
tenant-scoped invalidations; browsers must refetch canonical records instead of
treating a socket payload as a new source of truth.

## Current preview boundaries

- The 400 students, risk explanations, communication histories, and many staff
  tasks are synthetic test fixtures.
- Local staff authentication is not institutional authentication.
- Managed configuration and personal-priority projections still use the durable
  local preview adapter.
- Production outreach, approved risk scoring, SIS/CRM import, role mapping, and
  tenant-scoped PostgreSQL configuration versions remain follow-up work.
