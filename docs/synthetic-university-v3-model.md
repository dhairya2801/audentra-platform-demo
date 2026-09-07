# Aster v3 institutional and data model

> Historical v3 baseline report. The subsequent [canonical runtime upgrade](synthetic-university-runtime-report.md)
> connects this world to the portals and Edward, completes staff operations,
> and records the implementation, evaluation and remaining limits.

## Boundaries and authority

There are three distinct artifacts. Do not join them by an assumed identical
population or treat them as interchangeable.

| Artifact | Authority and relationship |
| --- | --- |
| `apps/api/assets/demo/synthetic-university-v1.json.gz` | Original deterministic 3,000-student seed; stable student and adviser identities; legacy state matrix and persona assertions apply only to that baseline |
| `vv_enrollment_synthu`, tenant `aster-demo` | Existing v2 product runtime, audited read-only: 2,577 students / 88 staff; current Edward continues using this schema |
| `artifacts/university-v3/university.sqlite` | New canonical **evaluation-world** database; deterministic reconstruction, not a DB dump or live projection; 3,000 source student IDs and 25 source adviser IDs retained |

The original importer explicitly drops unsupported domains rather than hiding
them in JSON. Therefore adding a SQLite world does not silently add production
registration, ledger, housing or historical reasoning tools. An adapter is a
separate, explicit engineering step. The other 63 v3 staff identities are local
reconstructions distributed across all 20 offices, not a claim to reproduce the
live v2 staff roster. Existing v2 calendars and operational workloads remain
available in the unchanged runtime baseline.

Source precedence in v3: applicable approved policy at the effective date +
publication cutoff; valid individual approval from its authorized office;
canonical record and its revisions; procedure within its policy authority;
guides and communications. A message or scenario prompt cannot approve a waiver,
settle a payment, release a hold, or change a policy. `source_path`,
`content_hash`, audience, office and version travel with policy evidence.

## Clocks and money

The world clock is **2026-09-08 16:00 UTC / 12:00 America/New_York**. It is not
wall-clock “today.” Local calendar dates are converted with `zoneinfo` rather
than a fixed offset. Fall add/drop is 11 September at 23:59 EDT (12 September
03:59 UTC); Spring add/drop is 29 January at 23:59 EST (30 January 04:59 UTC).
Policy windows and consent/exception windows are half-open `[start, end)`.

`event.effective_at` says when the underlying fact took effect;
`event.recorded_at` says when Aster knew it. A historical-knowledge query requires
both cutoffs. A grade correction effective in May but received 4 September must
not appear in the institution's knowledge on 1 September. `document_revision`
retains the same two-clock vocabulary. All other dossier tabs explicitly show
the current snapshot; this is **not** full bitemporal reconstruction of every
record table. A future-dated schedule is allowed; a future completed posting is
not.

All money is **integer USD cents**. Annual award ceilings and semester posting
amounts are separate. Ledger signs are charges/refunds/reversals positive,
payments/aid/approved charge credits negative. An approved charge credit points to the original charge; cancellation does not erase the bill history. A negative account balance means a credit owed; a posted
refund is distinct evidence. A deposit is a credited advance, never a second
charge. Books and personal costs are COA estimates, not ledger charges. The
v3 packaging policy contains fictional test parameters, not assertions about
actual federal award-year maxima.

## Relational domains

The exact DDL is `tools/university/migrations/0001_world.sql`; inspect every
column, check, foreign key and unique index in the explorer's Model view.

| Domain | Entities and important relationships | Authority / history |
| --- | --- | --- |
| Build | `meta`, `source_issue` | Source hashes, fixed clock, seed, explicit corrected vs intentional issues; separate manifest includes DB checksum |
| Institution | `office`, `staff`, `staff_absence` | Office responsibilities and service levels from v2 directory; reporting lines, capacities, leave coverage; all 20 offices have named contacts |
| Student lifecycle | `student`, `application`, `assignment`, `term`, `program` | Stable source IDs; separate admit term and current registrations; explicit historical continuing cohort; named adviser and ownership window |
| Academic catalog | `course`, `prerequisite`, `requirement` | Institutional 107-course catalog, not legacy 42-course generator catalog; required grades and recommended terms |
| Academic activity | `section`, `enrollment`, `transfer_credit`, `waitlist`, `sap_evaluation` | Course attempts and final grades, evaluated/pending transfer decisions, exact 48-hour offer lifecycle, cumulative SAP from actual attempts |
| Knowledge | `policy`, `policy_link`, `calendar` | Full corpus, versions, supersession, facets, publication/effectivity, related sources and owning office |
| Documents | `document`, `document_revision` | Original document IDs/status vocabulary, evidence revisions and rejection reasons; current status must equal latest revision |
| Funding | `fund`, `award`, `disbursement` | Eligibility and annual ceiling by fund; accepted award before installments; scheduled/held vs posted; employment cannot create aid postings |
| Accounts | `payment`, `ledger`, `hold` | Settled payment/disbursement FK links, exact opposite reversal, posted balances, term-scoped holds, explicit authorized release |
| Residence | `residence`, `bed`, `housing` | Physical 1,350-bed inventory in six halls; unique active bed/student occupancy by term; compatible placement distinct from accommodation approval |
| Permissions | `consent`, `exception` | Subject, delegate/scope, effective window and revocation; approved exception must identify an approver from the owning office |
| Institutional work | `workflow`, `workflow_step`, `step_dependency`, `communication`, `appointment` | One accountable case owner, cross-office prerequisite DAG, completion evidence, delivery status and visibility; initial advising vs optional follow-up |
| Audit / actions | `event`, `action_receipt` | Correlation IDs, entity pointers, old/new states, two clocks; immutable release event and idempotent receipt committed with state |

Foreign keys are enabled on every connection. Student-scoped data uses the
single generated tenant; `meta.tenant` pins the identity. This is not a
multi-tenant production schema. Production adapters must bind tenant and actor
authorization outside the model's arguments.

## Derived truth

- `account_balance`: sum of **posted ledger** entries per student and term.
  Accepted awards and pending payments are not subtracted a second time.
- `current_load`: credits from `enrollment.status='enrolled'` joined to actual
  section courses. Withdrawn/completed attempts are not current load.
- `academic_progress`: attempted/earned credits and credit-weighted GPA from
  completed/withdrawn Aster attempts. W affects pace but not GPA; F affects both.
  Transfer rows remain separately identified; do not assume the view includes
  them. Current transfer personas are not assigned a fabricated prior Aster GPA.
- `sap_evaluation`: cumulative Aster attempted/earned credits and GPA at each
  historical term boundary; 2.0 GPA, 67% pace, 150% published program length.
  First failure produces warning; repeat failure suspension; no attempts means
  not evaluated. Suspended continuing students' Fall aid is held and their
  accounts/holds rebuilt coherently. Successful appeal/probation paths remain
  an explicit extension gap, not a fabricated approval.
- Saved cohort: enrolled students with Fall registrations **and** an active
  financial hold **and** a pending Fall payment. Deduplicate student identity;
  denominator is all enrolled students with Fall registrations. Failed transfers
  are not pending. Two matching curated students are the stable expected result.

Program requirement credits sum to the published degree total, but the named
catalog plans were only partial. V3 labels remaining core/elective credits as
such. An unresolved distribution bucket is not permission to certify graduation.

## State transitions and completion

| Record | Lifecycle and gate |
| --- | --- |
| Document | Not submitted → uploaded → under review → accepted / rejected / needs resubmission; waiver and later expiry retained with reasons |
| Payment | Pending → posted or failed; posted → reversed preserves original credit and adds opposite entry |
| Disbursement | Scheduled / held → posted only against accepted, eligible award; future semester installments stay scheduled |
| Financial hold | Active → released only by active Student Accounts actor, posted overdue balance ≤ $250, matching version, explicit confirmation; unrelated holds survive |
| Waitlist | Waiting → offered → accepted or expired; offer is not enrollment; reserved offers count against seat inventory |
| Housing | Waitlisted → assigned only to a real bed; approval/exemption and inventory reservation are separate facts |
| Exception | Requested → approved / denied; approval requires correct office and effective window; expired approval cannot authorize a current action |
| Case step | Blocked → ready after all prerequisites complete/waived → complete with evidence; delivery failure cannot complete notification |
| Case | Open / waiting → resolved only when every required step is complete/waived; assignment stays with one owner |
| Action | Confirmed request → atomic state/event/receipt commit; retry same key+payload replays; changed payload or stale version rejects |

Only financial-hold release is currently implemented as a public sandbox write.
Other lifecycles are represented in data and checks; the explorer intentionally
does not pretend their buttons execute production workflows.

## Working with the existing PostgreSQL model

For current Edward investigations, start from tenant-scoped `student` / `person`
and `student_profile`; admission and enrollment use `admission_offer`,
`enrollment_journey`, `student_requirement` and status events; documents use
`document_record` and `document_review_decision`; finance uses
`student_financial_award`, `student_financial_summary`, `payment_transaction`
and `student_sap_status`. Those runtime summaries have different semantics from
v3 ledger/attempt-derived truth. Staff use `staff_member`,
`student_staff_assignment`, availability/time off, appointments,
`staff_work_item`, work links/logs and inquiry/reply tables. Policies use
`institution_knowledge_document` / `_section`, `institution_office` and
`academic_calendar_entry`. Managed content is canonical after publication;
YAML/Markdown are seed inputs, not competing live truth.

Do not bulk copy v3 rows into these tables: term amounts, external identifiers,
state vocabularies, authorization, event consumers and derived projections need
an explicit mapping. Do not “fix” a frozen v2 evaluation by changing its expected
facts to whatever the new world happens to say. Keep build identity in each run.
