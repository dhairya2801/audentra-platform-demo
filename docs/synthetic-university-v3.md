# Synthetic university v3 — an evaluation world and Aster Atlas

> Historical v3 baseline report. The subsequent [canonical runtime upgrade](synthetic-university-runtime-report.md)
> connects this world to the portals and Edward, completes staff operations,
> and records the implementation, evaluation and remaining limits.

Completed in the isolated `synthetic-university/platform` and `…/portals`
worktrees. Neither repository's main branch was modified or merged. The v2
PostgreSQL database was inspected in read-only transactions; no live tenant
records were changed. Edward's implementation was not changed.

**Open the explorer:** from `platform`, run
`apps/api/.venv/bin/python tools/university/server.py`, then visit
**http://127.0.0.1:4310**. The generated database is already present locally.
For a fresh checkout, first run
`apps/api/.venv/bin/python tools/university/build.py`.

[Operating instructions and API](../tools/university/README.md) ·
[Institutional/data model](synthetic-university-v3-model.md) ·
[Original v2 report](synthetic-university-v2.md)

## Findings: three different things were called “the university”

The audit covered the original generator, state matrix, personas and invariant
suite; packaged archive and Python importer; the full 88-document institutional
corpus, office/calendar/program/catalog sources and enrichment code; existing
staff generation and deployment structure; evaluation banks and their reported
limitations; and the actual `aster-demo` tenant in `vv_enrollment_synthu`.

The population archive, enriched product database, and separate staff/explorer
generation pipeline were not one reproducible artifact. This matters more than
a row-count discrepancy: an evaluator could accidentally take policy from one
world, an identity from another and a frozen expected balance from a third.

Tenant-scoped read-only inspection confirmed the v2 report's **2,577 students
and 88 staff**. Database-wide counts include other tenants and must not be used
as Aster counts. Significant existing runtime facts:

| Domain | Observed v2 tenant records |
| --- | ---: |
| Staff/student assignments | 7,782 |
| Staff time-off records | 26 |
| Student appointments | 8,051 |
| Staff work items | 2,720 |
| Inquiries / student messages | 194 / 2,584 |
| Offices | 20 |
| Knowledge documents / sections | 88 / 376 |
| Academic calendar entries | 75 |
| Catalog courses / named program requirements | 107 / 195 |
| Transcript-credit rows | **0** |
| SAP snapshots | 2,577 |
| Financial awards | 5,455 |
| Payments | 1,894 |
| Payment plans / FERPA authorizations | **0 / 0** |
| Documents / requirement status events | 9,855 / 20,617 |

The previous environment had real strengths: varied student states, stable
personas, document histories, rich staff operational workloads, cross-office
ownership, a substantial coherent policy corpus, and extensive read/write
regression banks. These baselines remain available. V3 is not an assertion that
all their staff-workload breadth has been reproduced.

## Major weaknesses, independently verified

1. **Accidental impossibilities.** The packaged source contains **195 duplicate
   hall/bed labels**, **1,020 federal/state awards to international students**, and
   **531 completed disbursements after its own 5 August clock**. These are source
   defects; v2 had separately repaired international funding in PostgreSQL.
2. **Financial amounts were shaped to produce desired balances.** The source
   includes Pell at **$29,406** and SEOG at **$29,411**. Annual tuition was charged
   to one term, work-study could become an account credit, and accepted awards,
   scheduled aid and actual postings were easy to conflate.
3. **Academics lacked the facts needed to reason.** The 42-course/90-section
   generator and 107-course institutional catalog were disconnected. There were
   no canonical course registrations or actual transcript-credit rows behind
   the product's SAP snapshots. Named program plans covered **28–54 credits**
   of **120–130-credit** degrees, so they could not support a full degree audit.
4. **Temporal truth was underspecified.** Student, staff and evaluation clocks
   differed. Policy files were almost entirely one version. A date could mean
   source effectivity, publication, recording, deadline or completed action,
   without enough structure to distinguish them.
5. **Real workflows existed mainly as text or snapshots.** A successful review
   could be mistaken for disbursement, clearance, notification or completion.
   Pending payment, bank return, waitlist expiry, revoked consent and scoped
   exception combinations were sparse or absent.
6. **Evaluation quality was partly a phrasing test.** The v2 report itself shows
   regex passes overstating groundedness, weaker paraphrase performance, and
   holdout cases inspected and adjusted after initial runs. Answer-key leakage,
   unspecified clocks and model-specific routing limitations must not be
   confused with institutional reasoning competence.

## What changed and why

### A reproducible evaluation database, not an in-place product rewrite

V3 reconstructs a coherent relational world in SQLite using repository sources.
The product importer intentionally cannot represent many of the needed domains.
Silently placing new facts in production JSON or redesigning Edward to consume
new tables would cross the task boundary. An independent, documented world
adapter gives future evaluations access to the new domains without implying
that current Edward supports them already.

The v1 archive and v2 live tenant remain frozen comparison baselines. V3 retains
all 3,000 source student IDs and 25 adviser IDs, the 14 programs, six residences,
full institutional catalog and full policy corpus. It reconstructs operational
state rather than layering authoritative new balances over contradictory old
balances. The other 63 staff are generated locally across all 20 offices;
existing v2 staff identities/workloads are **not** silently treated as identical.

Every build records seed, fixed clock, source hashes, collection counts,
validation results and database checksum. The builder validates a temporary
file before replacing the baseline. Action sandboxes are independent copies;
rebuilding does not erase experiment runs. There is no DB dump, real student
data, provider call or live service dependency in the generated world.

### Academics with supporting evidence

The institutional catalog now drives actual sections, seats, schedules,
registrations, historical completed/withdrawn attempts, prerequisites and
transfer evaluations. Prerequisites require qualifying earlier completed work
or accepted transfer evidence; in-progress or pending credit is not quietly
counted. Initial registrations require a settled deposit, prior completed
advising and health-clearance evidence. Later expiry can create a new hold
without rewriting the earlier valid registration.

An explicit continuing cohort has four historical terms. SAP evaluations are
computed from those attempts, with GPA, pace, program-specific maximum and prior
evaluation linkage. Suspension holds aid; the ledger and financial holds follow
that decision. Missing GPA for a new student is not a failing GPA.

Named program requirements are preserved. Remaining degree credits are labeled
as unresolved core/elective distributions, with totals reconciled to published
program lengths. This fixes a misleading partial model without inventing a
complete curriculum or falsely certifying graduation.

### Reconciled finance and physical housing

The ledger uses integer cents, semester charges, a credited deposit, eligible
and capped annual awards, separate Fall/Spring installments and exact payment /
disbursement links. Employment authorizations never post as aid. Pending,
failed, settled and reversed transfers differ. Bank returns preserve original
postings. Refunds have positive settlement entries against existing credit
balances. A $250 balance and a $250.01 balance form a deliberate policy boundary
pair.

Housing now uses a physical 1,350-bed inventory with unique active occupancy per
student/bed/term. A functional accommodation approval is distinct from matching
and confirming a compatible bed. A waitlisted student is not billed as if they
already occupy a room; an existing overpayment remains a credit to resolve.

### Institutional history, exceptions and handoffs

The original corpus is retained in full. Four additional source documents add
v3 packaging parameters, two versions of settlement/release procedure, and
cross-office completion rules. Policies carry audience, authority, effective
window, publication time, source path and content hash. Retrieval reports
applies / does-not-apply / unknown from available facts; missing citizenship or
housing facets are not guessed.

Cases explicitly connect accountable owners, office-specific steps,
prerequisites and completion evidence. Scenarios include a bounced notification,
leave coverage, a rejected official transcript, approved reduced course load,
expired overload approval, revoked parent billing authorization, optional
advising no-show, stale advice, and a late-recorded grade correction.

A financial-hold release is executable in the world sandbox. It requires an
active Student Accounts actor, matching hold version, explicit confirmation,
posted overdue balance at most $250, and an idempotency key. State, event and
receipt commit atomically; concurrent identical requests produce one effect.
It cannot release another office's hold or treat a pending payment as money.

## Resulting world

The generated manifest is authoritative for exact counts. The completed build
contains **3,000 students, 88 staff, 20 offices, 14 programs, 107 courses,
781 sections, 14,559 course attempts, 2,968 SAP evaluations, 92 policy versions,
75 calendar entries, 1,350 physical beds, 12,343 document records and more than
74,000 events**. Counts describe coverage; they are not the quality target.

The schema includes 41 tables plus derived balance, load and academic-progress
views. [The model guide](synthetic-university-v3-model.md) documents authority,
entity relationships, state transitions, money, both clocks, existing PostgreSQL
mapping boundaries and the generation process. The explorer also exposes exact
DDL and build provenance.

## Scenarios now represented

Thirty curated cases have concrete records and evaluator-only rubrics:
**19 development / 11 holdout-designated**. The split is a workflow convention,
not a claim that an exposed case remains blind.

| Capability | Representative cases |
| --- | --- |
| Student-specific reasoning | Clear control, received vs accepted transcript, rejected official seal, independent health hold |
| Policy interactions | $250 / $250.01 threshold pair, F-1 drop with and without approved reduced load, expired overload |
| Financial reasoning | Pending vs failed payment, stale settlement advice, bank reversal, credit owed vs refund settled |
| Temporal reasoning | Grade effective in May but recorded in September; superseded policy; offer expiry; released historical hold |
| Academics / what-if | Actual 12 → 8 credit CS 101 drop, affected future prerequisites, pending transfer B not yet accepted |
| Staff / workflow reasoning | Adviser on leave, five-step verification handoff, accommodation before placement, bounced decision notice |
| Privacy / identity | Revoked parent consent, minor without blanket disclosure authority, same-name student pair |
| Cohorts / proactive work | Auditable pending-payment/financial-hold intersection, no-show follow-up without invented penalty |
| Safe actions / outcomes | Confirmed versioned hold release, exact retry receipt, independent blocker surviving financial clearance |

Rubrics are not stored in student rows, policy bodies, agent evidence or event
labels. The operator scenario route can deliberately reveal them; never register
that route as a model tool.

## Aster Atlas: an explorer for reasoning

The UI is a standalone engineering application under
`portals/tools/university-explorer`, served by the local world API. It uses a
warm paper/forest-green visual system, restrained serif typography, an
institutional map, responsive layouts and native accessible controls. It is
separate from the student and staff product navigation.

- **Observatory:** institutional structure, build scale and meaningful entry
  points into pending payments, independent holds and cross-office outcomes.
- **Students:** name/ID/email search, stage/residency/hold filters, paginated
  results and dossiers with academics, financials, revisions, relationships and
  two-clock timelines. Empty evidence and uncertain state are explicit.
- **Academic world:** program requirements, prerequisites and unresolved
  distributions. Student what-if analysis shows credit change, downstream
  courses, exception basis and limits without mutating records.
- **People and offices:** contacts, service levels, capacities and explicit
  leave coverage.
- **Cases:** visual office handoffs with ready/blocked/completed states and
  evidence; one accountable owner stays visible.
- **Policy library:** full text, audience filtering, superseded-version switch,
  dates, applicability facets and exact source/hash provenance.
- **Calendar and residences:** term/domain filters, New York deadlines and
  physical bed occupancy with pending compatible-placement cases.
- **Scenario studio:** curated prompts, links into real student evidence,
  copyable questions and explicitly revealed evaluator rubrics.
- **Action laboratory:** review a specific hold, select a qualified actor,
  confirm, execute only in a sandbox and inspect the resulting release.
- **Model/provenance:** corrected vs intentional issues, exact DDL, manifest
  download, source-to-evidence explanation and live-product boundary.

The interface does not claim to chat with Edward or execute unimplemented
workflow actions. It exposes the world against which those capabilities can be
built and tested.

## Validation

Domain and browser verification are independent of Edward/model calls:

- **17 world tests** plus **27 legacy generator/archive tests**; deterministic byte-identical rebuild, alternate seed, foreign keys, financial
  conservation, eligibility/caps, chronology, document terminal state, occupancy,
  course capacity/conflicts, prerequisites, advising/deposit/health gates,
  workflow dependencies, policy windows, SAP evidence and program credit totals.
- Adversarial tests deliberately corrupt records; validators must reject them.
- Time tests distinguish effectivity from recording and daylight-saving offsets.
- Action tests cover wrong actor, wrong office, no confirmation, stale version,
  unsettled funds, idempotency-key misuse, concurrency and baseline isolation.
- Browser tests cover ten explorer views, search, dossier what-if, time lens,
  policy history, rubric reveal, confirmed release, unchanged baseline, mobile
  horizontal overflow and browser errors. Local screenshots are generated under
  `portals/artifacts/university-explorer/`.

Repository-wide gates were also run. Platform lint passes. Platform Python
unit tests report **1,373 passed / 150 skipped**, but the aggregate test command
fails its existing **67% coverage gate (63.21% measured)**. Platform typecheck
reports **eight errors in five existing test files**. The separate Node test
command encounters missing workspace executables/dependencies (including
`vitest`); the new world uses none of them. Portals lint/typecheck and production
build pass; portal tests report **128 passed / 1 failed** in the existing
production lab-route test (`200` vs expected `404`). These failures concern
unchanged runtime/test files and have not been disguised by lowering gates or
editing Edward. Service-backed tests remain skipped without their isolated test
services; the audited v2 DB was not used as a test target.

## Remaining gaps and honest limits

- **No automatic current-Edward integration.** New world domains need bound
  tool adapters before an end-to-end Edward score is meaningful.
- **V3 is not a replacement copy of all v2 staff operations.** It focuses on
  coherent, inspectable cases. The broader v2 staff absence/calendar/work-item
  population remains in the baseline; only source adviser IDs are continuous.
- **Partial curriculum and SAP scope.** Elective distributions, substitutions,
  repeat forgiveness, successful SAP appeal/probation and transfer-inclusive
  cumulative SAP need deeper modeling. Sections use simplified single weekly
  meeting blocks, not full contact-hour, instructor and lab timetables.
- **Limited executable actions.** Hold release is real within a fork. Payment
  processing, course registration/drop, room allocation, document review,
  workflow advancement and message delivery are data/scenario lifecycles, not
  complete action services. No bank, email or immigration system is contacted.
- **Limited historical reconstruction.** Events and policy retrieval support
  two clocks; other tables expose a current snapshot. There is no general
  rewind/replay engine or autonomous time-advancing job scheduler.
- **Finance breadth.** No full federal packaging formula, loan dependency
  limits, payment-plan installments, tax treatment, return-of-funds engine or
  exact course-drop refund calculation. Fictional packaging caps are labeled.
- **Narrow sensitive-domain detail.** Consent/functional accommodations are
  modeled without diagnoses or counseling notes. Conduct hearings, research
  supervision, graduate careers, payroll and staff HR are not simulated deeply.
- **Evaluation is not yet a world-class-agent benchmark score.** There are no
  model runs or learned-policy claims. The scenario split is inspectable by the
  operator; a future blind benchmark needs inaccessible rubrics and fresh,
  frozen cases. Generic population records still have fewer communications and
  exceptions than the curated situations.

## How future Edward evaluation should use this world

1. **Pin the build.** Save manifest, DB checksum, clock, source hashes, actor,
   student identity and tool schema with each run. Never mix v2 expected facts
   with v3 balances or dates.
2. **Bind identity outside the model.** Student tools receive one authorized
   student. Staff tools receive scoped capabilities. The operator HTTP API is
   not production authentication; neither arbitrary SQL nor the rubric route
   should be exposed to Edward.
3. **Start with evidence retrieval and uncertainty.** Require source IDs,
   policy version and relevant record reads. Distinguish “unknown,” “pending,”
   “not applicable,” and “complete.” Evaluate conflicting-source handling and
   missing evidence, not only a matching sentence.
4. **Evaluate reasoning separately from execution.** A hypothetical must not
   mutate. A proposed action must show a concrete target and prerequisites.
   An authorized action must create the expected state diff and one receipt;
   retries, wrong actors and stale versions must produce no extra effects.
5. **Measure outcomes, not fluent completion claims.** Case closure needs every
   prerequisite and delivered outcome evidence. Proactive guidance must name
   the next owner, preserve the deadline basis and avoid repeating an already
   completed or already delivered step.
6. **Use contrast pairs and temporal cuts.** Run pending/failed/posted,
   $250/$250.01, valid/expired approval, parent/revoked delegate, pre/post
   correction and pre/post policy version cases. For cohorts, verify membership,
   denominator, deduplication and timestamp, not just the count.
7. **Keep evaluation-only information separate.** Export packets through
   `evaluate.py`; keep `oracle.json` with the evaluator. Grade required facts,
   forbidden claims, authorized reads and final state independently. Have a
   human or secondary judge assess nuanced usefulness; do not count a regex
   match as full groundedness.
8. **Grow by failed institutional reasoning.** Add the missing relationship,
   authoritative source, transition or exception that makes a real question
   answerable. Add a negative invariant test and a contrast case before adding
   more random students or documents.
