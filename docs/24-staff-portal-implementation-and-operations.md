# Staff Portal Implementation and Operations

## Purpose

This document is the implementation handoff for the first functional staff
portal slice. It explains where the staff pages live, how they share state with
the student portal, how tenant content is published, how the local preview is
operated, and which boundaries must change before institutional deployment.

## Entry points

| Surface | Route | Main implementation |
| --- | --- | --- |
| Tenant staff portal | `/<tenant>/staff` | `apps/web/app/[tenant]/staff/page.tsx` |
| Default staff portal | `/staff` | `apps/web/app/staff/page.tsx` |
| Staff workspace | Hash-selected views under the staff route | `apps/web/app/staff/staff-portal.tsx` |
| Task/student inspector | Shared staff operational components | `apps/web/app/staff/staff-action-center.tsx` |
| Browser API client | `/v1/staff/*` requests and invalidation hooks | `apps/web/app/lib/api-client.ts` |
| Local preview API | Stateful staff and student routes | `tools/demo-api/src/http-api.js` |
| Production staff API slice | Work items, student preferences, document decisions | `apps/api/src/staff/` |

## Page responsibilities

| Page | Responsibility | Write boundary |
| --- | --- | --- |
| Today | Staff-specific operational summary | Read model only |
| Action Center | Prioritized assigned students and next actions | Work-item update or simulated outreach |
| Task Board | Team-wide enrollment work coordination | Versioned work-item mutation |
| Students | Search and permitted student operations | Field-authorized student mutation |
| Messages | Inquiry ownership, response, and resolution | Versioned inquiry plus optional student message |
| Journeys | Onboarding and enrollment flow definitions | Versioned configuration publish |
| Campus Life | Events and clubs | Versioned event publish or club mutation |
| Academics | Student-visible course catalog | Versioned configuration publish |
| Knowledge Base | Trusted tenant guidance | Versioned content mutation |
| Core Plays | Repeatable operating procedures | Versioned play mutation |
| Edward | Context lookup and draft preparation | Confirmation required before any write |

## Canonical data and shared effects

Student and staff pages are projections over the same tenant-scoped business
records. A staff decision must not create a second UI-owned copy of student
state.

The production PostgreSQL slice currently includes:

- `staff_member`, `staff_work_item`, and append-only `staff_work_log`.
- student onboarding, profile, requirements, documents, and messages.
- expected-version checks and row locks for concurrent writes.
- audit and transactional-outbox records in the same business transaction.

Migration `apps/api/migrations/0018_staff_action_center.sql` introduces the
staff workflow records. `apps/api/src/staff/staff-action.store.ts` performs the
transactional staff mutations.

The local preview mirrors this behavior in a serialized state file so the
complete student/staff flow can run without Docker. It is not the production
multi-instance persistence or authorization design.

## Managed tenant configuration

Source documents live under `config/tenants/<tenant>/`:

- `journeys.yaml`
- `campus-life.yaml`
- `academics.yaml`

Raw YAML is intentionally hidden from staff. Staff members use structured
forms or describe a change to Edward. The current local web implementation
loads the versioned document, applies the structured change while preserving
unrelated fields, serializes the document, and submits the complete candidate
with `expectedVersion`. The API parses and validates the candidate before
publishing a new version and refreshing the student-facing projection.

Edward follows the same boundary:

1. Staff submits a natural-language instruction and current version.
2. Edward returns an internal configuration draft, change summary, and warnings.
3. Staff reviews the human-readable plan.
4. Staff explicitly confirms publication.
5. The API validates, versions, publishes, and returns canonical state.

For production, move parsing and record-level patch construction entirely to
the server. Store the original document, canonical parsed JSON, schema version,
author, validation result, immutable version, status, and publication time in
tenant-scoped PostgreSQL tables.

## Authentication and authorization

The local preview uses a fixture staff credential, scrypt password hashing,
hashed opaque sessions, an HTTP-only cookie, and an eight-hour expiry. It is
appropriate only for local or controlled preview environments.

The production target is institutional OIDC/SAML with:

- tenant membership and active-employment checks;
- role and university-component mapping;
- separate permissions for student view, preference update, document decision,
  reassignment, bulk action, escalation, configuration publish, and content
  approval;
- FERPA-aware access logging, redaction, and retention;
- step-up or dual approval for high-risk bulk actions.

## Freshness and concurrency

- Work-item writes carry `expectedVersion` and return `409 VERSION_CONFLICT`
  when another staff session has already changed the record.
- Active staff views refetch on a short interval and when the browser regains
  focus.
- Student notification counts refetch on a lighter interval and focus.
- Document parsing belongs to the server and continues after navigation.
- External delivery belongs behind the transactional outbox.

The next real-time step is SSE invalidation after the outbox. The event should
identify the stale resource and tenant; the client then refetches the canonical
record. Keep low-frequency polling as recovery.

## Synthetic cohort boundary

The preview seeds 400 deterministic students to exercise assignment,
prioritization, scrolling, filtering, risk explanations, histories, document
reviews, and conflict behavior at a useful product scale.

These records are test fixtures only. Production must import authorized student
records from the SIS/CRM and must never run the synthetic-cohort seed against an
institutional tenant.

## Local operation

Supported detached commands:

```bash
npm run portal:start
npm run portal:status
npm run portal:stop
```

The launcher starts the API and web processes directly, records one managed
process id, captures logs, detects occupied ports, and stops the whole managed
tree. On PowerShell installations that block `npm.ps1`, the same launcher can
be called directly:

```powershell
& 'C:\Program Files\nodejs\node.exe' tools\portal-process.mjs start
& 'C:\Program Files\nodejs\node.exe' tools\portal-process.mjs status
& 'C:\Program Files\nodejs\node.exe' tools\portal-process.mjs stop
```

Default URLs:

- Web: `http://localhost:3000`
- API: `http://127.0.0.1:4000`
- Staff: `http://localhost:3000/aster/staff`

## Validation completed for this slice

- Web TypeScript compilation.
- Production web build.
- ESLint with zero errors; seven existing image-optimization warnings remain.
- 15 rendered web tests.
- 48 stateful preview API tests.
- 59 production API tests from the completed staff/action-center slice.
- Four portal lifecycle tests.
- Desktop browser checks at the annotated `1156 × 912` and wide
  `1900 × 976` sizes.
- Mobile browser checks at `390 × 844` with no body overflow.
- Click-through checks for journey, event, and course editors.

## CI/CD behavior

`.github/workflows/ci.yml` has two relevant triggers:

- Pull requests run the complete `validate` job.
- Pushes to `main` run `validate`, then deploy the hardened VM preview after
  validation succeeds.

A direct push to a feature branch publishes the branch to GitHub but does not
run this workflow until a pull request is opened or the branch is merged to
`main`.

## Production work remaining

1. Persist managed configuration, inquiry, communication, risk, and personal
   action-center projections in PostgreSQL.
2. Replace fixture staff authentication with university identity and roles.
3. Replace deterministic risk fixtures with an approved, monitored policy or
   model and retain evidence plus model version.
4. Import students from governed university systems.
5. Add confirmed, idempotent email/SMS/voice adapters behind the outbox.
6. Add SSE invalidation while retaining optimistic versions and polling recovery.
7. Add approval workflows and immutable history for knowledge and Core Plays.
