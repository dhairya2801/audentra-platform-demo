# Student Portal Functional Acceptance

This document defines the minimum behavior required before the student portal
can be described as functional. A polished dashboard alone is not sufficient.

## Entry and routing

| Condition | Required destination |
|---|---|
| No authenticated session | `/sign-in` |
| Authenticated, onboarding incomplete | Current resumable `/onboarding` step |
| Authenticated, onboarding complete | `/dashboard` |

The routing decision is returned by the API bootstrap endpoint and must not be
derived solely from `localStorage`, a query parameter, or client-owned state.

## Required routes

| Route | Required behavior |
|---|---|
| `/sign-in` | Establishes a development session or starts the production identity-provider redirect |
| `/onboarding` | Loads, saves, resumes, validates, reviews, and completes onboarding |
| `/dashboard` | Shows the authoritative offer, journey progress, deadlines, and next action |
| `/enrollment` | Lists all assigned enrollment requirements and their current states |
| `/enrollment/requirements/:slug` | Explains one requirement and embeds its valid action; database UUIDs are not exposed as navigation |
| `/documents` | Lists uploaded and onboarding-signed document records, supports unclassified uploads, exposes extraction review, and opens immutable signed PDFs |
| `/messages` | Lists student messages and persists read state |
| `/appointments` | Lists and schedules an enrollment appointment |
| `/payments` | Shows deposit state and records an idempotent development payment |
| `/profile` | Loads and saves student-editable profile preferences |
| `/help` | Lists support channels and searchable FAQ content |

Every primary navigation destination must render a real state. There must be no
placeholder links to missing pages.

Protected routes must reject an absent or invalid session. Signing out must
invalidate the development session and return the student to `/sign-in`.

## Onboarding acceptance

1. The student establishes an authenticated session.
2. Bootstrap returns `onboarding_required`.
3. The student advances through versioned steps.
4. Each successful step is persisted server-side.
5. Reloading or using another device resumes at the first incomplete step.
6. Completion is idempotent and records `completed_at`.
7. A material completion audit event is appended.
8. Subsequent bootstrap requests return `/dashboard`.
9. A new policy acknowledgement does not erase the original completion record.

## Functional interaction acceptance

- Accepting an offer updates the dashboard and creates the enrollment journey.
- Reading a message removes it from the unread count after reload.
- Creating a document record makes it visible after reload.
- Completing onboarding creates one immutable, tenant-branded PDF for each
  signed onboarding document. Typed or drawn signatures appear in the expected
  field, and the PDFs remain visible and downloadable from My Documents.
- A completed onboarding record missing its generated packet is repaired
  idempotently when My Documents is opened; repeated requests create no
  duplicate signed records.
- A document requirement accepts its file inline without redirecting to
  `/documents` or asking the student to classify it.
- The parser classifies from contents. A mismatch is visible and cannot advance
  the enrollment requirement.
- Scheduling an appointment makes it visible after reload.
- Recording the development deposit changes the payment and requirement state
  without creating a second charge when the idempotency key is replayed.
- Saving profile preferences survives reload.
- Activity events remain non-blocking and contain only allowlisted properties.

## Failure-state acceptance

Each remote operation must visibly support:

- initial loading;
- empty data;
- validation failure;
- authorization/session failure;
- server or network failure;
- retry;
- successful completion;
- prevention of accidental duplicate submission.

## Quality gate

The slice is ready for handoff only when:

- all routes build;
- type checking and linting pass;
- API and UI contract tests pass;
- production dependency audit has no high or critical findings;
- a browser walkthrough covers sign-in, first-time onboarding, dashboard,
  message read, profile update, appointment scheduling, and offer acceptance;
- desktop and phone viewports have no horizontal overflow;
- browser console logs contain no unexplained errors.
