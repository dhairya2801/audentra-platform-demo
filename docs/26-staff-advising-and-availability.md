# 26 · Staff identity, adviser relationships, availability and person-bound booking

Migration `0048_staff_advising_and_availability.sql` turned the staff side of
Audentra from "a display name, an email and a component" into a product model
that can answer *who is responsible for this student, who can they see, when,
and what is this staff member's own situation*. This document is the reference
for that model, the routes over it, and the development-only "log in as staff"
capability that goes with it.

## The model

| Table | What it is | Key rules |
|---|---|---|
| `staff_member` (+ columns) | Identity and organisational position: `title`, `role_code`, `manager_id` (composite FK, same tenant), `employment_status` (`active` · `on_leave` · `departed`), `employment_type`, `started_at`/`ended_at`/`leave_until`, `timezone`, `office_location`, `caseload_cap`, `student_facing`, `appointment_types`, `external_ref`. | `active` keeps its authentication meaning. `departed ⇒ active=false` is a CHECK. A person on leave can sign in but cannot be booked. |
| `student_staff_assignment` | A standing relationship between a student and a person, by role: `primary_advisor`, `admissions_counselor`, `financial_aid_counselor`, `international_adviser`, `housing_coordinator`. History is kept (`assigned_at`, `ended_at`, `source`, `note`). | At most one *current* assignment per (student, role) — partial unique index. Both FKs are tenant-composite. |
| `staff_availability` | Weekly working pattern in the person's own time zone: `weekday` (0 = Sunday), `start_minute`/`end_minute`, `slot_minutes`, `modality`, `location`, `appointment_types` (empty = any). | Nothing else is stored about "open slots". |
| `staff_time_off` | Absences and blocks: `leave`, `vacation`, `sick`, `training`, `conference`, `blocked`, `other`; `blocks_bookings`. | A note-only entry (conference, still taking calls) can coexist with hard leave. |
| `student_appointment` (+ columns) | Now with a person: `staff_member_id`, `ends_at`, `modality`, `location`, `booked_via`, `cancelled_at`/`cancel_reason`, `rescheduled_to_id`, `outcome_note`, `version`. Types gain `academic_advising` and `international_check_in`; statuses gain `no_show` and `rescheduled`. | **Durable conflict guard:** unique `(tenant_id, staff_member_id, starts_at) WHERE status='scheduled'`. |
| `staff_auth_session` | `authentication_method` gains `demo` (no credential account, no federated identity). | Resolved only while development flows are on; refused at the HTTP boundary in production or non-demo `AUTH_MODE`. |

Open slots are **derived, never stored**: `audentra.domain.scheduling` computes
them from the weekly pattern minus blocking absences minus live appointments,
in the person's zone. The same function decides whether a booking is legal, so
the calendar a student sees and the rule that refuses a bad booking cannot
disagree. Advising completion is likewise derived from `academic_advising`
appointments (`completed` → met; `scheduled` in the future → booked; `no_show`
→ missed) rather than stored as a flag.

Deliberately not modelled: vacancies (a vacant seat is "students with no
adviser", which the product already surfaces), materialised slot rows, a
general HR system (payroll, contracts, org units beyond `component` +
`manager_id`).

## Routes

Student (`/v1/student/…`, student or delegate with the `appointments` scope for writes):

- `GET advising` — primary adviser and other counsellors with each person's
  availability summary, derived advising status, and **gaps**:
  `no_primary_adviser`, `adviser_departed`, `adviser_on_leave`,
  `adviser_no_open_slots`.
- `GET appointments/availability?type=&staffMemberId=&from=&to=` — open slots
  per person (the assigned person for the type by default; otherwise everyone
  who offers it), with a `reason` when nothing is bookable.
- `POST appointments` — now accepts `staffMemberId` and `modality`. With a person
  (explicit or assigned) the time must be one of their open slots; otherwise
  `409` with `APPOINTMENT_SLOT_TAKEN`, `STAFF_UNAVAILABLE`,
  `OUTSIDE_WORKING_HOURS`, `APPOINTMENT_OFF_GRID`, `STAFF_MEMBER_ON_LEAVE`,
  `STAFF_MEMBER_DEPARTED`, `STAFF_NO_AVAILABILITY`, `STAFF_DOES_NOT_OFFER_TYPE`.
  Without any person for the type, the legacy "any future time" booking still
  works so tenants without published hours are not broken.
- `POST appointments/{id}/cancel`, `POST appointments/{id}/reschedule` (idempotent;
  the old row becomes `rescheduled` with `rescheduled_to_id`, the new one is
  booked under the same rules in one transaction).
- `GET appointments` now returns the person, `endsAt`, place and lifecycle fields.

Staff (`/v1/staff/…`):

- `GET me` — identity, manager, `directReports` and `team` (the whole reporting
  subtree, up to four levels, each with `level` and `reportsTo`; a director sees
  the adviser who left even when that adviser reported to an assistant director),
  each with caseload vs cap, work backlog, next open slot and **flags**:
  `departed_with_caseload`, `on_leave_with_caseload`, `over_cap`,
  `no_open_slots`, `falling_behind` (≥3 stale in-progress items or ≥5
  appointments still unclosed three days after they took place), `spare_capacity`; own caseload
  by role, own work counts (open, overdue, stale in-progress, appointments
  awaiting an outcome), availability (weekly pattern, absences, open/booked next
  14 days), today's appointments, and a `componentSummary` for managers
  (students with a departed/on-leave adviser, accepted students without one,
  unassigned/overdue component items).
- `GET caseload?role=` — the students assigned to me with program, checklist
  progress, advising status, next appointment and open/overdue work.
- `GET appointments?staffMemberId=&from=&to=` — a person's calendar (mine by default).
- `PATCH appointments/{id}` — `completed` · `no_show` · `cancelled` with an
  outcome note; allowed for the person on the appointment or their manager.

Edward: the student `getStudentAppointments` tool now carries the person and
the adviser gaps; the staff ownership tool reports `advisorModel:
"primary_advisor"` with the adviser and gaps instead of `"none"`.

## Development-only staff login

`GET /v1/auth/demo/staff/directory?q=` and `POST /v1/auth/demo/staff/sign-in-as
{staffRef}` (UUID, email or `external_ref`) open the staff portal as a chosen
staff member without a password. The safeguards, in layers:

1. `_development_only` at the route: `404 DEVELOPMENT_AUTH_DISABLED` in
   production or when `AUTH_MODE≠demo`.
2. `_require_development_flows` in the auth adapter (same 404).
3. The tenant must have `demo_auth_enabled=true`; the lookup is inside that
   predicate, so a valid reference from another tenant is "not found".
4. The session is stored with `authentication_method='demo'`; the schema forbids
   it from carrying a credential account, `resolve_staff` refuses it whenever
   development flows are off, and `get_auth_context` refuses it in production
   or non-demo mode even if a row exists.
5. Departed people are refused with `409 STAFF_MEMBER_DEPARTED` so the product
   state is visible rather than masked by a generic failure.

The portal draws the panel only when `demoStaffLoginEnabled` says so
(`NEXT_PUBLIC_DEMO_STAFF_LOGIN_ENABLED`, then the student flag, then
`NODE_ENV=development`); that is convenience, not a control.

## Tests

- `tests/test_scheduling_domain.py` — slot derivation and refusal reasons, no DB.
- `tests/test_auth_http.py` — demo staff routes, cookie, production/OIDC refusal.
- `tests/test_staff_advising_postgres_integration.py` — the full lifecycle on
  PostgreSQL: adviser view and gaps, availability, booking/conflict/hours/time-off
  refusals, idempotent replay, reschedule, cancel, on-leave and departed advisers,
  demo staff login, `/me`, caseload, calendar, staff close-out, director's team.
- Explorer: `npm run test:deploy` compares the deployed synthetic university,
  person by person, with the Explorer's ground truth through the product API.
- Portals: `tools/browser-e2e/specs/staff-advising.spec.ts` drives both portals
  against the deployed synthetic university.
