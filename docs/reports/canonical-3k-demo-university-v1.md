# Canonical 3K demo university v1

## Result

The synthetic university generator is now a canonical Audentra population. It is
exported deterministically, imported into the real PostgreSQL domain model, and
served to the Student Portal, Student Edward, and Staff through the same
repositories every other tenant uses. There is no adapter, no parallel store,
and no Edward-specific view of the data.

- Platform branch: `feat/canonical-demo-university`
- Platform baseline SHA: `a062592f93ab2a290d2603e4614006aa9b5ba7ab`
- Portals branch: `chore/sync-staff-edward-contracts`
- Portals baseline SHA: `9c21265` (`Merge remote-tracking branch 'origin/main' into chore/sync-staff-edward-contracts`)
- PostgreSQL test target: disposable `postgres:16` container `audentra-s5-pg`, bound to `127.0.0.1:55445`, databases `audentra_s5` (runtime), `audentra_s5_test` / `audentra_s5_int` (suites), `audentra_s5_compact` (default-profile check). `vv_enrollment` was never touched.
- Nothing was merged; no branch outside this session was modified.

Population, as imported:

| | |
|---|---|
| Students generated | 3,000 |
| Students imported | 2,576 |
| Students rejected | 424 (143 denied, 281 waitlisted — see [Rejected records](#rejected-records-and-why)) |
| Rows written | 70,069 across 21 tables |
| Generator invariant violations | 0 |
| Post-import domain invariant violations | 0 |
| Seed wall clock (migrate excluded) | 18.1 s |

---

## Generator

`tools/demo-api/src/synthetic-university/` — unchanged by this work.

- `generate.js` — the whole institution from one integer seed
- `state-matrix.js` — 25 states forced into the population rather than left to the dice
- `personas.js` — 10 named students whose situations make a question interesting
- `invariants.js` — `validateUniverse`, human-readable violation strings
- `random.js` — mulberry32; no `Math.random`, no `Date.now`

Defaults: seed `20260810`, 3,000 students, reference instant `2026-08-05T12:00:00.000Z`.
Generation takes ~0.9 s, validation ~0.07 s, and returns 0 violations.

### Original generator schema

29 collections. The ones with volume:

| Collection | Rows |
|---|---|
| `students` | 3,000 |
| `applications` | 3,000 |
| `checklistTasks` | 22,856 |
| `documents` | 12,343 |
| `accountLedger` | 11,389 |
| `aidAwards` | 5,791 |
| `sapStatus` | 2,857 |
| `internationalRequirements` | 2,724 |
| `registrationEligibility` | 2,576 |
| `fafsaRecords` | 2,516 |
| `disbursements` | 1,893 |
| `orientationRegistrations` | 1,892 |
| `housingApplications` | 1,663 |
| `verificationRequirements` | 1,390 |
| `holds` | 1,316 |
| `housingAssignments` | 1,061 |

Plus catalogues: `terms` (3), `programs` (14), `advisors` (25), `courses` (42),
`sections` (90), `residenceHalls` (6), `mealPlans` (4), `holdTypes` (5),
`checklistTaskCatalogue` (8), `documentCategories` (8), `awardFunds` (8),
`orientationSessions` (6), `costOfAttendance` (6), `personas` (10).

---

## Canonical PostgreSQL mapping

`apps/api/src/audentra/infrastructure/seeding/synthetic_university.py` is the only
place the translation happens. Every enumerated value goes through an explicit
table keyed by the generator's vocabulary; an unmapped value raises rather than
reaching the database.

### Entities

| Generator | Canonical table | Notes |
|---|---|---|
| `students` | `person`, `student`, `student_profile` | `student.external_ref` holds `SYN-000042`; `person.id` is `uuid5` of the student id |
| `programs` | `program` | `source_status = 'synthetic_preview'` |
| `terms` | `academic_term` | |
| `advisors` | `staff_member` | 25 rows, `component` = department |
| `applications` | `admission_offer` | admitted only; `accepted_at` is the generator's own `accept_offer` completion instant |
| `checklistTasks` + `documents` + `fafsaRecords` | `enrollment_journey`, `student_requirement` | 8 canonical requirements per journey |
| `documents` | `document_record` | `NOT_SUBMITTED` and `WAIVED` produce no row — the absence *is* the state |
| `fafsaRecords`, `verificationRequirements` | `financial_document_requirement` | |
| `aidAwards` | `student_financial_award` | vocabularies coincide; asserted, not assumed |
| `costOfAttendance` + `accountLedger` | `student_financial_summary` | `external_payments_cents` = the ledger's `student_payment` lines |
| `sapStatus` | `student_sap_status` | |
| `accountLedger` (deposit line) | `payment_transaction` | the receipt is the canonical fact |
| `housingApplications`, `housingAssignments` | `student_onboarding.payload` + `housing_preference` requirement | see gaps |
| — | `student_message`, `student_appointment` | one welcome message per student; an advising appointment where the generator says advising happened |
| — | `staff_work_item`, `staff_work_log` | 300 items, capped and round-robined across the 25 advisers |

### Vocabulary translation

Document status → `document_record.status`:

| Generator | Canonical |
|---|---|
| `NOT_SUBMITTED` | *(no row)* |
| `WAIVED` | *(no row)* |
| `UPLOADED` | `uploaded` |
| `UNDER_REVIEW` | `under_review` |
| `ACCEPTED` | `accepted` |
| `REJECTED` | `rejected` |
| `NEEDS_RESUBMISSION` | `rejected` — PostgreSQL has no `needs_resubmission`, and "the office looked at it and did not accept it" is the true statement |

Document category → `document_record.category`: `transcript→transcript`,
`immunization→health`, `photo_id→identity`, `residency_affidavit→residency`,
`verification_worksheet→financial_aid`, `tax_return_transcript→financial_aid`,
`i20_support→other`, `english_proficiency→other`.

SAP status: `suspension → not_meeting`. The schema has no `suspension`.

FAFSA state → `financial_document_requirement.status`: `not_received→not_started`,
`received→submitted`, `selected_for_verification→under_review`,
`verification_complete→verified`, `rejected→action_required`.

### One deliberate divergence from the checklist

The generator's `complete_fafsa` task is "did you file a FAFSA". The canonical
requirement `financial_aid_verification` is "is verification finished". A student
selected for verification has filed and still has work to do, so the import reads
the verification requirements rather than the checklist tick. This produces 611
students with aid verification genuinely open — a cohort the checklist reading
would have hidden.

### Requirement status distribution after import

| Code | Distribution |
|---|---|
| `profile_verification` | completed 2,576 |
| `identity_document` | completed 2,576 |
| `official_transcript` | completed 1,588 · ready 469 · under_review 368 · rejected 151 |
| `financial_aid_verification` | completed 1,534 · in_progress 611 · ready 301 · under_review 74 · rejected 56 |
| `immunization_record` | completed 1,863 · ready 469 · rejected 244 |
| `housing_preference` | completed 1,202 · blocked 684 · waived 461 · ready 229 |
| `enrollment_deposit` | completed 1,892 · in_progress 368 · ready 316 |
| `orientation_registration` | completed 1,741 · blocked 684 · ready 151 |

---

## What the product corrected, and what that cost

The first import wrote `housing_preference = blocked` for students whose deposit
had not posted — the generator's own rule. Every one of those came back `ready`.

`reconcile_journey_routes` recomputes every `blocked`/`ready` requirement from
`depends_on_codes` alone, and Aster's `housing_preference` declares no
dependencies. The importer had invented a prerequisite that the product does not
model, and the product quietly overruled it. That is precisely the failure mode
the Edward parity work found in hand-written fixtures, caught here by the engine
rather than by a demo.

The fix was to make the prerequisite real for this tenant rather than to drop it:

- `assets/config/tenants/aster-demo/journeys.yaml` declares
  `housing_preference.depends_on: [enrollment_deposit]`
- `_ensure_tenant_workflow` accepts per-tenant `dependency_overrides`, used only
  by the demo campus
- The invariants are re-verified *after* journey publication, not only after the
  import, so the settled rows are what gets asserted

684 blocked housing steps now survive reconciliation, and "why can't I apply for
housing?" is answerable from the requirement graph.

---

## Rejected records and why

424 applicants (14%) were not imported.

| Reason | Count |
|---|---|
| Applicant decision is `denied`, not admitted | 143 |
| Applicant decision is `waitlisted`, not admitted | 281 |

Audentra models enrollment. Every path that creates a `student` row —
`sign_up_student`, the compact seeder, the fixture — creates an `admission_offer`
alongside it, and `admission_offer.status` has no `waitlisted` value. A denied or
waitlisted applicant genuinely has no offer, and importing one produced an
account whose `/v1/student/dashboard` and `/v1/student/financials` returned 404:
a state production has never held.

The two alternatives were both worse. Fabricating an `offered` row would have put
424 fictional offers in the canonical table. Leaving the accounts half-broken
would have made one demo sign-in in seven look like a bug. Skipping them keeps
every imported student fully functional, and the gap is counted in the report
object rather than hidden.

**Recommendation:** if the product grows an applicant-without-offer state, these
424 become importable and the rejection rule should go.

---

## Gaps: generator concepts with no canonical home

Counted in `SyntheticUniverseReport.dropped_records` so they stay visible.

| Concept | Rows | Disposition |
|---|---|---|
| `holds` | 1,316 | **Gap.** No canonical holds table. Hold-driven blocking is partly visible through `blocked` requirement statuses, but the hold itself, its office, and its release are not representable. |
| `registrationEligibility` | 2,576 | Derived, not stored. The gates map onto requirement statuses the portal already computes. |
| `internationalRequirements` | 2,724 | **Gap.** I-20, SEVIS, and visa-interview tracking has no canonical table. The supporting *documents* are imported as `other`; the requirement rows are not. |
| `disbursements` | 1,893 | Rolled into `student_financial_award` amounts. No canonical disbursement schedule. |
| `housingAssignments` | 1,061 | Hall, room label, and meal plan land in `student_onboarding.payload`. There is no canonical room-assignment table. |
| `accountLedger` lines | 11,389 | Aggregated into `student_financial_summary` and the deposit `payment_transaction`. No canonical line-item ledger. |
| Document status history | 39,698 | Only the current status is canonical. |
| `orientationRegistrations` | 1,892 | Collapsed into the `orientation_registration` requirement status. Session choice is not stored. |
| `courses`, `sections` | 132 | Not imported; the tenant's academic catalogue comes from `academics.yaml`. |
| `residenceHalls`, `mealPlans` | 10 | Not imported as generator rows; the demo tenant copies Aster's `housing_residence_option` catalogue instead. |

**Not imported by policy.** The generator produces no melt probability, recovery
probability, or risk score, and none was invented. The staff cohort read reports
`risk.score: 0`, `modelVersion: "not-evaluated"`, `"No deterministic risk score
has been generated for this student."` — which is true.

**One importer-supplied field.** `student_sap_status.attempted_credits` has no
generator source, and a completion rate over zero attempted credits is arithmetic
nonsense. The importer supplies a denominator by admit type (first-year and
international 24, transfer 60) and this line is the disclosure.

---

## Compact vs full-demo seed architecture

`apps/api/src/audentra/infrastructure/seeding/profile.py` adds one setting:

```
DEMO_SEED_PROFILE=compact              # default; unchanged behaviour
DEMO_SEED_PROFILE=synthetic_university # compact PLUS the demo campus
```

`audentra-seed --profile synthetic_university` overrides the variable. The larger
profile is strictly **additive**: it never removes the compact fixture, so the
fast suite still reseeds 14 funnel students against the same database.

| | compact | synthetic_university |
|---|---|---|
| Tenants | `aster`, `harvard` | `aster`, `harvard`, `aster-demo` |
| Students | 28 | 28 + 2,576 |
| Seed wall clock | 3.6 s | 18.1 s |
| Purpose | fast deterministic integration tests | running the product |

Verified: with the default profile the `aster-demo` tenant does not exist, and
Aster still holds exactly 14 students.

### The demo tenant

| | |
|---|---|
| Tenant id | `00000000-0000-7000-8000-000000000003` |
| Slug | `aster-demo` |
| Name | Aster University |
| Campus | Aster Main Campus |
| Academic year | 2026-2027 |
| Staff | 3 office staff + 25 imported advisers |

Created by the seeder, not by a migration — a migration runs in production and
this tenant must not exist there. Its managed configuration is Aster's document
set with the single housing-dependency difference; its AI runtime is cloned from
Aster's published configuration by id-prefix rewrite, so re-seeding converges
instead of stacking prompt versions.

---

## The packaged archive

The API image has no JavaScript runtime, so the generator output is exported to a
verified asset:

- `tools/export-synthetic-university.mjs` generates, validates, gzips at level 9,
  and writes both the archive and the constants that pin it
- `apps/api/assets/demo/synthetic-university-v1.json.gz` — 3.24 MiB, SHA-256
  `41b73c0352edb21844a5c6654a89e3868e4e095695c4561e47d68b325e7b2947`
- `synthetic_university_asset.py` — digest, byte length, seed, student count,
  reference instant, and per-collection row counts

The loader checks the digest against the **compressed** bytes before
decompressing, then asserts the meta block and all 28 collection counts. A
truncated or swapped archive fails immediately rather than producing a plausible
half-population.

Drift is closed from the other side:
`tools/demo-api/test/synthetic-university-asset.test.js` regenerates from the
recorded inputs, re-compresses identically, and asserts the digest matches. A
stale archive fails `npm run test:node` with the one command that fixes it.

Determinism verified: two exports produced byte-identical archives with the same
digest.

---

## Schema, migration, and indexes

`apps/api/migrations/0039_student_external_reference.sql` — the next free
ordinal after 0038.

**`student.external_ref varchar(64)`**, nullable, unique per tenant when present,
format-checked. A UUID is the right primary key and the wrong thing to type into
a sign-in box; institutions issue a short reference for exactly that purpose and
the generator already produces one.

**`enrollment_journey_student_idx (tenant_id, student_id)`** — added on evidence,
not on principle. `enrollment_journey` was reachable only by primary key or
`(tenant_id, offer_id)`, so every per-student lookup was a sequential scan. With
14 journeys nobody noticed; with 2,576 the staff cohort page paid for one scan
per row it rendered.

| Query | Before | After |
|---|---|---|
| Journey by student | 0.361 ms (Seq Scan) | 0.039 ms (Index Scan) |
| Staff cohort page of 20 | 17.5 ms | 10.2 ms |

No other index was added. The one remaining sequential scan on a synthetic-only
path — case-insensitive `external_ref` lookup at sign-in — costs 0.66 ms and runs
once per sign-in; an expression index would be an index serving a dev-only path.

### One query change, no index needed

`demo_student_by_reference` originally matched `s.id = :uuid OR
upper(s.external_ref) = upper(:ref)` in one statement, which plans as a
sequential scan. The reference arrives once, at sign-in; the UUID arrives on
**every subsequent request** from the session cookie. Splitting it into two
predicates took the hot path from 1.1 ms sequential scan to 0.098 ms index scan.

---

## Timing and query performance

Seed, on the disposable container:

| Phase | Time |
|---|---|
| Generation (JS, export only) | 0.92 s |
| Generator validation | 0.07 s |
| Archive load + digest verify | 0.38 s |
| Mapping (3,000 → 70,069 rows) | 0.44 s |
| Database write (batched `executemany`, 1,000 rows) | 5.10 s |
| Total seed wall clock incl. compact fixture and journey publication | 18.1 s |

The hand-written per-row seeder was not rewritten. The compact path is untouched
and still runs in 3.6 s; the bulk path is a separate, scoped writer in the import
module.

Endpoint latency against the 2,576-student tenant (warm, 3 runs, median):

| Endpoint | Time |
|---|---|
| `POST /v1/auth/demo/sign-in-as` | 6 ms |
| `GET /v1/student/requirements` | 9 ms |
| `GET /v1/student/documents` | 11 ms |
| `GET /v1/student/financials` | 17 ms |
| `GET /v1/student/dashboard` | 19 ms |
| `GET /v1/staff/action-center` | 353 ms |
| `GET /v1/staff/workspace` | 556 ms |

The staff workspace returns a 1,000-student cohort page in one response; that is
where its half-second goes.

---

## Authentication and demo sign-in

### What existed

`vv_demo_session` proves demo access; the tenant resolves it to its *first*
student. Enough for a 14-student fixture, useless for a 2,576-student one where
the entire point is opening a chosen student.

### What was added

`POST /v1/auth/demo/sign-in-as` with `{"studentRef": "SYN-000042"}`, accepting the
institution reference or the UUID.

Server-side gating, in order:

1. **Environment.** `_development_only` — the same guard as the existing demo
   sign-in. Production returns 404 `DEVELOPMENT_AUTH_DISABLED`.
2. **Adapter.** `PostgresDevelopmentAuth` refuses to construct outside
   development, preview, or test.
3. **Tenant opt-in.** The lookup requires `tenant.demo_auth_enabled = true` and
   `tenant.status = 'active'`.
4. **Tenant scope.** The tenant predicate is part of the query, not a check on
   the result, so a valid identifier belonging to another university returns
   "not found" and never a session.

The chosen student rides in a second cookie, `vv_demo_student`, valued
`<uuid>.<hmac>` where the HMAC covers **tenant and student** under the deployment's
demo session token. A cookie minted for one university cannot be replayed at
another. The signature is a precondition, never the authorization: the request
handler still resolves the student inside the authenticated tenant, so a
forged-but-valid signature over a foreign student still fails. A malformed or
tampered cookie is not an error — it is simply not a choice, and the browser
degrades to the tenant's default demo identity.

The cookie is cleared by plain demo sign-in, by demo sign-out, and by the guided
onboarding reset.

**Nothing about production authentication was weakened.** No new production
route, no relaxed check, no widened credential. Verified: the endpoint returns
404 under `environment="production"`.

### Login does not reset the student

`sign-in-as` performs reads only. It does not call `reset_demo_fixture` and
shares no code path with it.

---

## Portals changes

| File | Change |
|---|---|
| `apps/web/app/lib/demo-student-login.ts` | new — the gate helper and reference validator, framework-free |
| `apps/web/app/lib/api-client.ts` | `signInDemoStudent`, `signOutDemoStudent`, `DemoAuthSession` |
| `apps/web/app/sign-in/sign-in-client.tsx` | the `DemoStudentSignIn` panel below the normal form |
| `apps/web/app/globals.css` | `.auth-demo-student` — dashed border and muted panel, visually subordinate to the real form |
| `apps/web/tests/demo-student-login.test.mjs` | new — gate, validator, and a production-build assertion |
| `apps/web/.env.example` | documents `NEXT_PUBLIC_DEMO_STUDENT_LOGIN_ENABLED` |

The panel: an eyebrow reading "Development only", a single **Student ID** field
placeholdered `SYN-000042`, and **Continue**. On success it reports the resolved
student's name and follows `bootstrap.initialRoute`. No student browser was
built; entering a known ID is the requirement and search would need a roster
endpoint that does not exist.

Gating follows the Edward Lab pattern already in the repo: on under
`NODE_ENV=development`, otherwise requires an explicit build-time flag. Verified
against the production bundle — the environment branch is inlined as
`NODE_ENV: "production"` and cannot open the panel.

---

## Representative students for manual testing

Sign in at `/aster-demo/sign-in` → **Log in as demo student**.

| ID | Name | Onboarding | Deposit | Notable state |
|---|---|---|---|---|
| `SYN-000000` | Wren Halloway | completed | paid | Fully ready — all eight requirements complete |
| `SYN-000001` | Tobias Quillfeather | completed | paid | Deposited, transcript and immunization never submitted |
| `SYN-000002` | Marisol Fennwick | in_progress | unpaid | Transcript **under review**, housing and orientation **blocked** |
| `SYN-000003` | Devon Ashgrove | completed | paid | Transcript **rejected**, aid verification not started |
| `SYN-000004` | Ingrid Thistlebrook | in_progress | unpaid | Three account holds in the generator, partial tuition coverage |
| `SYN-000005` | Cassius Pemberwell | completed | paid | No FAFSA, no aid, paying out of pocket |
| `SYN-000006` | Odalys Brightwater | completed | paid | Aid disbursed, credit balance awaiting refund |
| `SYN-000007` | Ines Calderwood | in_progress | unpaid | International, selected for verification, aid **in progress** |
| `SYN-000008` | Rufus Tanglewood | completed | paid | Transfer admit, transcript under review |
| `SYN-000009` | Georgina Underhollow | in_progress | unpaid | Deposit deadline passed with nothing paid |

Coverage of the requested sample: new admit (`SYN-000002`), accepted but deposit
unpaid (`SYN-000004`), deposited with missing transcript (`SYN-000001`),
deposited with an aid issue (`SYN-000003`), housing blocked (`SYN-000002`,
`SYN-000004`, `SYN-000007`, `SYN-000009`), onboarding almost complete
(`SYN-000008`), fully ready (`SYN-000000`), international (`SYN-000007`),
transfer (`SYN-000008`), deadline passed (`SYN-000009`). Students are assigned
across 25 advisers.

Not covered: **declined offers**. The generator has no student who declined; its
decision axis is admitted / waitlisted / denied. `admission_offer.status =
'declined'` exists only in the compact fixture (Ines Duarte). See next steps.

---

## Verification

### Student Portal

Every read returns 200 for imported students:
`bootstrap`, `dashboard`, `requirements`, `documents`, `financials`, `payments`,
`onboarding`, `profile`, `messages`, `appointments`, `academics`, `campus-life`,
`housing-plan`.

For all ten personas, the portal's requirement statuses equal
`SELECT` over `student_requirement` — checked by direct comparison against SQL.

### Student Edward

Edward's language layer needs a model credential this environment does not have,
so it was verified where the facts live: the tool host, through
`execute_tool_reads` on the production wiring.

For all ten personas, plus a deterministic 25-student sample nobody curated:

- `getOnboardingChecklist` == `student.list_requirements` (codes, statuses, open count)
- `getStudentAccountSummary.depositPaid` == a succeeded deposit on the Payments page
- `getEnrollmentHolds.depositState` == `getEnrollmentState.depositState` == the account summary's
- `getDocumentStatuses` == `get_student_documents` (ids and statuses)
- `getFinancialAidStatus.requiredDocuments` == the Financials page's
- `getFinancialAidSummary` cost / accepted / pending / remaining == the Financials page's
- `getAcademicStanding` GPA and SAP status == the Financials page's
- `getEnrollmentState.admission` program and offer status == the dashboard's

Cross-domain: for a student with housing blocked, Edward reads
`housing_preference = blocked` **and** an open `enrollment_deposit` **and**
`depositPaid: false` in one turn — the deposit-and-housing question is answerable
from the graph rather than from prose.

### Staff Edward

- `find_students(CohortFilter())` total == `SELECT count(*) FROM student` for the tenant
- `find_students(deposit_state="paid")` total == distinct students with a succeeded deposit
- `summarize_students(group_by="deposit_state")` buckets sum to the headcount and the `paid` bucket matches the same SQL
- `search_students("Wren Halloway")` finds the synthetic student by name
- `get_student_overview` agrees with that student's own portal on onboarding status, program, offer status, deposit, and requirement counts
- `get_student_overview` returns `None` for a demo-campus student asked for under Aster's identity

Staff workspace over HTTP: 1,000-student cohort page, 26 distinct assignees, all
three journey stages present.

### Login persistence — the regression this exists to prevent

Student `SYN-000006` (aid disbursed, refund due, deposit paid):

1. Recorded a database digest over requirements, documents, payments, onboarding
2. Signed in, captured a portal fingerprint over six endpoints
3. Signed out
4. Signed in again
5. Portal fingerprint: `82d6966c50ad6f46` → `82d6966c50ad6f46`
6. Database digest: `034e7eb11aaad606a99bb5f6b4235b5f` → unchanged

Also asserted in the integration suite for `SYN-000000`, `SYN-000006`, and
`SYN-000009`, signing in by both the reference and the UUID.

### Tenant isolation

- A demo-campus reference presented against `aster` → 404
- A demo-campus UUID presented against `aster` → 404
- A demo-campus student id in an Aster auth context → portal read raises
- Empty, oversized, unknown, and SQL-injection-shaped references → clean 404
- No work item is assigned to staff outside its tenant
- No requirement, document, journey, or payment crosses tenants (asserted by query)

### Post-import domain invariants

Ten queries that must each return zero rows, run inside the import transaction
**and again** after journey publication:

deposit without an accepted offer · deposit whose offer belongs to another
student · requirement outside its journey's tenant · journey for an offer never
accepted · housing complete while the deposit is not · document filed against
another student's requirement · student whose person is in another tenant · work
item assigned across tenants · onboarding complete with no completion instant ·
aid award accepting more than was offered.

All zero. The import raises rather than committing if any fires.

### Idempotency

Re-running `seed_relational_data` with the synthetic profile converges: the
student count is unchanged. Getting there surfaced a real defect — the second
seed collided on `requirement_definition_version_uidx`, because publishing
`journeys.yaml` replaces the requirement graph with new ids under the same codes
and version. Fixed by bootstrapping the journey only for a tenant that has none,
and reading the **active** journey definition from the database for the import.

---

## Test, lint, and typecheck results

| Suite | Result |
|---|---|
| `npm run lint:api` (ruff check + format) | pass, 191 files |
| `npm run typecheck:api` (mypy) | pass, 191 files, 0 issues |
| `pytest` (default) | **772 passed**, 104 skipped |
| `pytest -m postgres` | **71 passed** |
| `pytest tests/test_postgres_synthetic_university.py` | **57 passed** |
| `npm run test:node` | **140 passed** (6 + 116 + 18) |
| Portals `npm --workspace @vv/web run typecheck` | pass |
| Portals `npm --workspace @vv/web run lint` | pass, 0 errors (15 pre-existing `<img>` warnings) |
| Portals `npm --workspace @vv/web run test` | **65 passed** |

### New tests

- `apps/api/tests/test_synthetic_university_mapping.py` — 28 tests, no database.
  Archive integrity, tampered/missing archive rejection, **every generated
  vocabulary value has a canonical destination and every destination is a schema
  value**, requirement derivation cases, onboarding state, signed-cookie round
  trip and forgery, seed profiles.
- `apps/api/tests/test_postgres_synthetic_university.py` — 57 tests against
  PostgreSQL. Import shape, idempotency, invariants, state spread, login
  persistence, portal/Edward parity for ten personas and a 25-student sample,
  staff cohort arithmetic, staff overview parity, tenant isolation, index usage.
- `apps/api/tests/test_auth_http.py` — 5 added. Signed cookie issuance, 404 for an
  unresolvable student, forged cookie degrading safely, plain demo sign-in
  clearing the choice, production 404.
- `apps/api/tests/test_seeding.py` — 2 added. The demo campus configuration is
  Aster's plus exactly the housing dependency; the synthetic profile is additive.
- `tools/demo-api/test/synthetic-university-asset.test.js` — 2 tests. The archive
  matches its constants and a fresh export.
- `apps/web/tests/demo-student-login.test.mjs` — 5 tests. Gate defaults, flag
  override, validator, production-build gating.

### One pre-existing failure, unrelated

`tests/test_auth_postgres_integration.py::test_real_postgres_browser_auth_and_deterministic_reset`
fails when any other database-backed test has already run against the same
database — it needs a pristine one. Confirmed pre-existing: it reproduces with
this session's `apps/api/src` and `apps/api/tests` changes stashed, and it fails
after modules that predate this work. It passes in isolation and in CI's
per-module invocation. Not fixed here; it is not this session's file.

---

## Known data-quality weaknesses

1. **No declined offers.** The generator has no student who turned an offer down.
   `admission_offer.status = 'declined'` is unexercised in the demo tenant.
2. **`identity_document` is uniformly complete.** The generator's `photo_id`
   document is `ACCEPTED` for every admitted student, so the requirement has no
   variance at all. Every other document-backed requirement spans four states.
3. **No `submitted` requirement state.** `UPLOADED` documents (266 in the
   universe) are almost all `residency_affidavit`, which backs no requirement, so
   the `submitted` status never appears in `student_requirement`.
4. **`attempted_credits` is importer-supplied**, not generated. See gaps.
5. **Documents are placeholders.** `document_record` rows carry
   `storage_provider = 'local_placeholder'` and a synthetic storage key; no bytes
   exist in object storage. Document *content* views will not render. Statuses,
   categories, and requirement links are real.
6. **Seeding 300 work items enqueues ~600 `action_center_ai_job` rows** via the
   `staff_work_item` insert trigger — the same behaviour the compact seed has at
   1/60th the scale. A worker with a live AI provider will process them. This is
   why the work-item count is capped rather than one-per-blocked-student.
7. **Holds and international requirements are invisible.** 1,316 holds and 2,724
   immigration requirements exist in the generator and nowhere in the product, so
   Edward cannot answer "do I have a hold?" for the demo population.
8. **Class years are derived**, not generated: term start year plus four, or plus
   two for transfer admits.

---

## Recommended next steps

1. **Add a declined-offer state to the generator.** One `STATE_MATRIX` entry and
   a persona; the import already handles `admission_offer.status = 'declined'`
   through the same path, and it would exercise the "declined offer must not have
   a deposit" invariant against real rows.
2. **Give `photo_id` real variance** in `documentStatusFor`, so
   `identity_document` stops being uniformly complete.
3. **Decide whether holds are a domain concept.** They are the largest gap and
   the one students ask about most directly. Either a `student_hold` table or an
   explicit product decision that requirement blocking is the only hold model.
4. **Run the Edward evaluation harness against the demo campus.** The reads are
   verified; the language layer over 2,576 real students is untested and is where
   the remaining risk sits.
5. **Consider an applicant state** so waitlisted and denied applicants become
   importable, closing the 424-record gap.
6. **Bound the staff workspace cohort page.** 1,000 students in one response is
   the reason it takes half a second; the pagination already exists in
   `find_students`.

---

## Commits and files changed

### Platform — `feat/canonical-demo-university`

| Area | Files |
|---|---|
| Generator export | `tools/export-synthetic-university.mjs`, `tools/demo-api/test/synthetic-university-asset.test.js` |
| Packaged asset | `apps/api/assets/demo/synthetic-university-v1.json.gz`, `.../seeding/synthetic_university_asset.py` |
| Import | `.../seeding/synthetic_university.py`, `.../seeding/profile.py` |
| Seeder wiring | `.../seeding/relational.py`, `.../seeding/cli.py` |
| Demo tenant config | `apps/api/assets/config/tenants/aster-demo/{journeys,academics,campus-life}.yaml` |
| Migration | `apps/api/migrations/0039_student_external_reference.sql` |
| Demo sign-in | `.../interfaces/http/demo_identity.py`, `auth_routes.py`, `dependencies.py`, `.../core/ports.py`, `.../postgres/auth_repository.py`, `.../contracts/requests.py` |
| Tests | `test_synthetic_university_mapping.py`, `test_postgres_synthetic_university.py`, `test_auth_http.py`, `test_seeding.py` |
| Report | `docs/reports/canonical-3k-demo-university-v1.md` |

### Portals — `chore/sync-staff-edward-contracts`

`apps/web/app/lib/demo-student-login.ts`, `apps/web/app/lib/api-client.ts`,
`apps/web/app/sign-in/sign-in-client.tsx`, `apps/web/app/globals.css`,
`apps/web/tests/demo-student-login.test.mjs`, `apps/web/.env.example`.

### Running it

```bash
# platform
node tools/export-synthetic-university.mjs        # only when the generator changes
uv run --directory apps/api audentra-migrate
AUDENTRA_ENV=development uv run --directory apps/api \
  audentra-seed --data --profile synthetic_university

# portals
npm --workspace @vv/web run dev                   # panel is on under NODE_ENV=development
# open /aster-demo/sign-in → "Log in as demo student" → SYN-000000
```
