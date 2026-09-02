# Edward READ generalization suite (`read-gen`)

An **unseen** read-plane bank for Student Edward and Staff Edward whose ground
truth comes straight from the product database. The tuned banks (student-v3,
staff-db v2, university) were the development set for the current routing
regexes and pass ~95–100 %; this bank exists to compare models
(gpt-4o-mini vs gpt-5.6-luna) and read architectures (deterministic vs
hybrid vs model-planned) on questions nobody tuned for.

Rules the bank was written under:

- every case was written **before** anything was run against Edward;
- every expectation is a template over `ground-truth.json` (plain SQL) or a
  structural check (tool called, student resolved, no action claimed);
- a case is never edited to make a run pass — a wrong *data* expectation may
  be corrected and is recorded in `CHANGELOG.md`;
- the holdout split was written last and is not run until the final comparison.

## Layout

| file | purpose |
| --- | --- |
| `ground_truth.py` | asyncpg SQL over the snapshot → `artifacts/read-gen-eval/ground-truth.json` |
| `personas.mjs` | the 15 student and 9 staff personas (refs + why each is here) |
| `cases/common.mjs` | template helpers and shared hallucination traps |
| `cases/*.mjs` | one file per category (16) — the development split |
| `cases.mjs` | development index (validates ids/categories) |
| `holdout-cases.mjs` | holdout split (~30 cases) |
| `grade.mjs` | pure grading: templates, per-turn checks, failure classes |
| `run.mjs` | the runner (student + staff endpoints, traces, summary, report) |
| `export-bank.mjs` | flat CSV of every turn → `docs/edward-read-gen-question-bank.csv` |
| `CHANGELOG.md` | every post-hoc case correction |

## Environment

A running API host bound to the frozen snapshot, with trace debug on and demo
headers allowed (default `http://127.0.0.1:45710`; override with
`READ_GEN_BASE_URL`). Ground truth reads the same database:

```
DATABASE_URL=postgresql://vv:vv_local_password@127.0.0.1:5433/vv_enrollment_staffdb_eval
tenant 00000000-0000-7000-8000-000000000003 (aster-demo; 2,577 students, 88 staff)
```

Set `OPENAI_MODEL` to the model the host runs so spend is priced correctly
(`tools/edward-eval/src/pricing.mjs`).

## Commands (from the platform root)

```sh
# (a) regenerate ground truth — do this right before a run; time-relative
#     facts (overdue, today, this week, bookable now) use the DB clock
DATABASE_URL=postgresql://vv:vv_local_password@127.0.0.1:5433/vv_enrollment_staffdb_eval \
  uv run --directory apps/api --locked python ../../tools/edward-eval/read-gen/ground_truth.py \
  > artifacts/read-gen-eval/ground-truth.json

# prove the bank and the ground truth agree (no host needed)
node tools/edward-eval/read-gen/run.mjs --lint
node tools/edward-eval/read-gen/run.mjs --lint --holdout

# (b) development split
node tools/edward-eval/read-gen/run.mjs --batch rg-dev-4o-mini
OPENAI_MODEL=gpt-5.6-luna node tools/edward-eval/read-gen/run.mjs --batch rg-dev-luna
node tools/edward-eval/read-gen/run.mjs --deterministic --batch rg-dev-4o-mini-det
node tools/edward-eval/read-gen/run.mjs --planner model --batch rg-dev-4o-mini-modelplanner

# (c) holdout split (only for the final comparison)
node tools/edward-eval/read-gen/run.mjs --holdout --batch rg-holdout-4o-mini

# (d) re-grade a stored batch against the current case specs, no host
node tools/edward-eval/read-gen/run.mjs --regrade rg-dev-4o-mini --batch rg-dev-4o-mini-regraded

# subsets
node tools/edward-eval/read-gen/run.mjs --ids rg-adv-001,rg-act-003 -v
node tools/edward-eval/read-gen/run.mjs --category multi_turn --actor-kind staff

# question bank CSV
node tools/edward-eval/read-gen/export-bank.mjs

# unit tests for the grader
node --test tools/edward-eval/test/read-gen-grade.test.js
```

`npm run eval:edward:read-gen`, `eval:edward:read-gen:holdout` and
`eval:edward:read-gen:bank` wrap the three main commands.

Note `uv run --directory apps/api` changes the working directory, so the
script path is `../../tools/...` (as for the sibling suites).

## Flags

| flag | effect |
| --- | --- |
| `--batch <name>` | artifacts under `artifacts/runs/<name>/` (transcript.json, summary.json, report.md) |
| `--id x` / `--ids a,b` | case subset |
| `--category c[,d]` | category subset |
| `--actor-kind student\|staff` | actor subset |
| `--holdout` | holdout split instead of dev |
| `--deterministic` | sends `x-edward-mode: deterministic` |
| `--planner deterministic\|hybrid\|model` | sends `x-edward-read-planner` |
| `--regrade <batch>` | grade a stored transcript without calling Edward |
| `--lint` | resolve every template against the ground truth; no host |
| `-v` | print each turn's answer and failures |

## Case shape

```js
one(id, category, actorKind, actor, question, expectedBehavior, {
  requestTypes: [...],           // soft: PARTIAL if the classifier disagrees
  requiredTools / anyOfTools / forbiddenTools,
  resolvedStudentId: "gt:students.SYN-…​.id" | null,   // staff only
  resolvedStudentIn: "cohorts.sameName.<Name>.entries", // "any of these"
  facts: [f(desc, pattern), soft(desc, pattern)],       // critical unless soft
  factGroups: [[…], […]],        // satisfied when every fact in ONE group matches
  forbidden: [forbid(desc, pattern)],                    // hallucination traps
  actionIntents: "none" | "any", proposeOrClarify: true, mustAsk: true,
  allowUnavailable: true,        // do not fail on "couldn't read …"
})
```

Templates: `{{gt:path}}` (escaped scalar), `{{re:path}}` (regex the ground
truth derived, e.g. `bookablePattern`, `namePattern`), `{{num:path}}`,
`{{num~:path}}` (±2 %), `{{date:path}}` (ISO / "Sep 4" / "4 September" /
"9/4"), `{{any:path}}` and `{{any:path|field}}` (alternation),
`{{all:path}}`. A template whose value is absent today makes the turn `SKIP`
rather than aborting the batch.

## How ground truth is derived (fact family → source)

| family | SQL source |
| --- | --- |
| identity | `student` + `person` (+ `student_profile` preferred name), `admission_offer` → `program`, `enrollment_journey.status` |
| advisers / counselors | `student_staff_assignment` (open rows) → `staff_member` (name, title, email, office, employment status, leave_until) |
| adviser bookable now | `staff_member` active + student-facing + `staff_availability` rows present + no booking-blocking `staff_time_off` covering now. Exact next slot is **not** derived (see below) |
| availability pattern | `staff_availability` weekday/start/end/modality, rendered as tolerant time regexes |
| requirements | `student_requirement` (not retired) × `requirement_definition_version` (title, office, blocking); open = status ∉ completed/waived/not_applicable; overdue = open ∧ due_at < now() |
| documents | `document_record` (file name, category, status) + latest `document_review_decision` (reason label, student message) |
| deposit | `payment_transaction` type=enrollment_deposit status=succeeded; due date from the deposit requirement |
| appointments | `student_appointment` (+ staff name); upcoming = scheduled ∧ starts_at ≥ now() |
| messages | `student_message` (read_at IS NULL = unread) |
| inquiries | `student_inquiry` (open = new/open/waiting_on_student, not archived) |
| work items | `staff_work_item` open = status ∉ done/cancelled; assignee → `staff_member`; head = priority (urgent>high>medium>low), then due date; oldest = created_at |
| recent changes | `student_requirement_status_event`, latest `document_review_decision`, latest `student_message`, recently completed work items |
| staff profile | `staff_member` + manager, direct reports, caseload counts by role, work counts (open/overdue/urgent/high/blocked/in-progress/transcript-topic), appointments today / next 7 days / Mon–Sun week, inquiries |
| advisees with overdue work | primary advisees having an open `staff_work_item` with due_at < now() |
| caseload peers (authorization traps) | other open assignees of the same adviser, alphabetical, excluding same-name students |
| cohorts / same-name | counts of no-adviser / adviser on leave / departed / unpaid deposit; every student named Lucia Zephyrine, Caleb Dunmire, Omar Vellacourt, Gustav Fennwick with program and adviser; probes that the "unknown" and misspelled names have zero rows; `staff_member` has no phone column; `student_risk_assessment` row count |

## Personas

Students (external ref → property): SYN-001278 Lucia Zephyrine (in progress,
overdue, 7 open items, name ×3) · SYN-001645 Bruno Stonebrook (completed) ·
SYN-001726 Omar Vellacourt (no adviser, unpaid deposit, name ×4) · SYN-002720
Gustav Fennwick (no adviser, everything overdue, name ×2) · SYN-000897 Noor
Zephyrine (adviser on leave, no appointments ever) · SYN-000728 Adria Kettleby
(adviser departed) · SYN-001217 Petra Oakenshaw (unpaid overdue deposit,
upcoming appointment) · SYN-000631 Kwame Oakenshaw (rejected document, 5
assignments, urgent + blocked items) · SYN-001030 Hana Mossbank (two upcoming
appointments) · SYN-000061 Ada Kettleby (6 unread messages, archived inquiry)
· SYN-001566 Camila Calderwood (archived inquiry, rejected document) ·
SYN-000665 Greta Everlyn (zero open work, adviser on vacation) · plus
SYN-001366 / SYN-001898 (the other Lucias) and SYN-001941 (Omar in Chemistry)
for disambiguation.

Staff: SYN-STF-FA-C06 Greta Radcliffe (FA counselor, 541 assignments, 0
advisees) · SYN-ADV-000 Ada Ashgrove (77 advisees, vacation Sep 3–6) ·
SYN-STF-ADM-C08 Zelda Jokinen (admissions counselor) · SYN-STF-ISS-DSO2
Matthias Gunnarsson (276 open items) · SYN-STF-REG-DIR Aurelio Abernathy
(Registrar, 6 reports) · SYN-ADV-005 Junia Pemberwell (on leave) ·
SYN-STF-REG-REC Marcus Lee (back office) · SYN-STF-ADM-AD Priya Shah (0 work
items, 12 reports) · SYN-STF-ADV-DIR Leandro Hartigan (advising director).

## Known limitations

- **No exact next-open-slot.** The product derives slots from the weekly
  pattern, time off and booked appointments in the adviser's timezone;
  reproducing that would be a second implementation. Cases grade the
  bookable / not-bookable statement (`bookablePattern`, derived from the
  same rows) and never a slot time.
- **No open help inquiries exist** in the snapshot (all 194 are archived), so
  the "open inquiries" persona is the archived-inquiry student and cases only
  assert honest empty states.
- **Every student has exactly one unread message** (the welcome) except Ada
  Kettleby (6) — she is the only tuned-bank student reused here.
- Several students carry a `scheduled` appointment whose start is already in
  the past (awaiting outcome); the student read treats only future ones as
  upcoming, and cases follow that.
- Wording checks are regexes; a correct answer in unforeseen wording can
  still FAIL. Inspect `report.md` before believing a number, and record any
  expectation fix in `CHANGELOG.md`.
- Time-relative facts drift: regenerate the ground truth right before each
  run; Ada Ashgrove's vacation (Sep 3–6) and Harriet Vasquez's (to Sep 4)
  change `bookablePattern` day by day, by design.
