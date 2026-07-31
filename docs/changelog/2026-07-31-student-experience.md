# Student Experience Changelog — 2026-07-31

## Audience

These notes describe changes visible to prospective and enrolled students in
the tenant student portal. The local preview uses synthetic data; no real
student information should be entered into it.

## Onboarding and offer acceptance

The onboarding experience now supports a broader, resumable student intake
flow instead of a short profile form.

### Added student choices

- Review the admitted program, term, campus, and prefilled student details.
- Choose a residency-review path without letting the browser decide official
  residency status.
- Compare on-campus, off-campus, commuting, family, and undecided housing paths.
- Rank residence preferences and record room type, roommate-matching, lifestyle,
  and community preferences when on-campus housing is selected.
- Request general information about tuition or housing protection without
  purchasing a policy during onboarding.
- Select clubs, community interests, first-month goals, and support topics.
- Request a separate housing or academic accommodations follow-up without
  collecting medical documentation in the general portal.
- Add multiple emergency contacts.
- Define optional parent, guardian, or supporter permissions with a purpose,
  information scope, and expiration.
- Review the complete submission, sign electronically, and choose the intended
  enrollment-deposit path.

### Safety and persistence

- Each step is saved to the server with optimistic version protection.
- Returning students resume the current step instead of restarting.
- Official eligibility, residency, aid, transcript, and payment decisions remain
  server-governed workflows.
- Sensitive accommodation evidence remains outside the general onboarding form.

## Enrollment page

- Added an overall progress summary calculated from canonical requirements.
- Kept each requirement linked to its dedicated detail or submission workflow.
- Added an “Onboarding follow-ups” section showing the student’s residency,
  optional protection-information, and accommodations requests.
- Added clearer privacy wording and a direct advisor-booking action.
- Document processing remains server-owned: once upload is accepted, navigating
  to another page does not cancel parsing.

## Financials

- Added an accessible cost-coverage donut showing accepted grants,
  scholarships, loans, payments, deposits, and remaining balance.
- Added clearer cost-of-attendance, accepted-aid, payment, possible-aid, and
  estimated-balance summaries.
- Kept funding sources and financial-aid requirements in separate, scannable
  sections.
- Increased the size and contrast of small legend, explanatory, and checklist
  text following readability feedback.
- Financial amounts remain governed records; staff cannot change balances by
  editing portal content.

## Campus Life

- Added club search by name, category, and description.
- Added category filters with live result counts.
- Added a useful empty state and a one-click reset to show all clubs.
- Staff-published events and clubs now use the same student-facing Campus Life
  projection, so approved content changes appear without duplicating records.

## Edward for students

- Added browser speech-recognition input where supported.
- Added optional spoken responses using browser speech synthesis.
- Added visible listening state, microphone-error handling, and a control to
  disable spoken replies.
- Typed chat remains available when voice APIs are unavailable or permission is
  denied.
- Edward remains advisory and cannot make an official enrollment, financial,
  eligibility, or document decision.

## Support and communication

- Added clearer admissions contact and advisor-booking links in the portal shell.
- Added a student inquiry form that feeds the staff Message Portal in the local
  preview.
- Staff can optionally create an unread portal message after a material document
  or inquiry decision.
- External email, SMS, and voice delivery are not yet connected; those channels
  remain provider-adapter work for a later production phase.

## Responsive and accessibility improvements

- Increased minimum text sizes for small supporting copy.
- Improved layout behavior for Financials, Campus Life, onboarding, and shared
  portal navigation at tablet and mobile widths.
- Added clearer focus, progress, selected, loading, error, empty, and success
  states.
- Preserved accessible names for financial graphics, filters, voice controls,
  requirements, and staff-generated student actions.

## What students should expect after a staff change

| Staff action | Student-visible result |
| --- | --- |
| Accept or reject a document | Requirement and document status refresh from the canonical record. |
| Send a portal notification | Unread message count updates on polling, focus, or page open. |
| Publish a journey version | New journeys use the new version; completed tasks are not silently rewritten. |
| Publish an event or club | Campus Life reads the latest published projection. |
| Publish a course | My Classrooms and Edward academic context read the latest catalog projection. |

## Current preview boundaries

- Student and staff identities are deterministic synthetic fixtures.
- Staff outreach channels are simulations.
- The local preview uses a serialized JSON store; PostgreSQL remains the
  production target.
- Institutional deployment still requires university SSO, SIS/CRM imports,
  approved communication providers, and production authorization policies.
