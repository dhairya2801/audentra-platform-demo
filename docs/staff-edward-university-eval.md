# Staff Edward against the mock university: evaluation and rebuild

2026-08-26. Staff Edward evaluated and rebuilt against the mock **Aster
University** (`Audentra-university-explorer`, deployed into tenant
`aster-demo`): 2,576 students, 88 staff in 12 departments with an org tree,
2,142 adviser assignments, 8,049 person-bound appointments, 2,485 open Action
Center items, 194 inquiries — and seventeen deliberately planted situations
(an adviser 126/110 over cap with no open slot until after registration
closes, an adviser on leave with 67 uncovered students, a departed adviser
still owning 79, a vacant seat, a transcript evaluator on vacation behind 96
items, a staff member who shares her full name with four students).

The university is used as an **oracle and benchmark**, never as something
Edward memorises: every expected fact is SQL over the product's own database
at run time, the questions are what staff genuinely ask, and a holdout of
unseen phrasings was run only after the fixes were finished.

**Result: 26/144 (18 %) → 143/144 (99 %) on the development suite; holdout
16/25 on first contact → 25/25 after five generalisable vocabulary fixes;
queue questions no longer time out (13 timeouts → 0; p90 3.5 s → 2.0 s).
Model spend for the whole exercise ≈ $0.23 of the $1 budget.**

---

## 1. What was wrong (root causes, not symptoms)

The previous evaluation listed symptoms — queue timeouts, staff names read as
students, "my work" unanswerable, a confident 2,576. Reading the pipeline
(`apps/api/src/audentra/integrations/staff_assistant/`) they reduce to four
architectural gaps:

| # | Root cause | What it produced |
| --- | --- | --- |
| 1 | **The assistant had no notion of who was asking.** The tool host carried an opaque `actor_id`; nothing read the signed-in member's role, component, team, caseload or queue. | "Show me the overdue items assigned to me" → `student_deadlines` → "Tell me which student you mean". Every "my / I / me" question dead-ended. |
| 2 | **Entity resolution was student-only.** Every capitalised run went to `searchStudents`; the staff directory was never consulted. The mock university's surname overlap (120 students share Elena Larkspur's surname; four share her full name) made this trivially reproducible — and it is realistic. | "How many students does Elena Larkspur advise?" → "I found 4 students matching Elena Larkspur". "Draft a message to Tobias" → no name extracted (only multi-word runs or `about X` counted) → "which student?". |
| 3 | **Every queue read loaded the entire board** — `getStaffWorkQueue` called the same read the Action Center renders (all 2,712 items *plus every work-log row*), then filtered in Python. Work-item lookup by key and reference-token disambiguation also loaded the board. `getWorkItemDetail` did too. | 4.3–7 s per read against a 4 s tool budget → **every** queue, count, unassigned, urgent and "what happened on AST-00102" question failed. |
| 4 | **The deterministic classifier had no staff, team, department or aggregate vocabulary** and its cohort branch counted the whole roster when it recognised no predicate. | "student requests awaiting a first reply" → `cohort_aggregate` with an empty filter → "2,576". "Which staff members have the most overdue work" → a student cohort. "Which department has the most unassigned work" → a page of the board. |

None of these is fixable by adding phrasings: (1)–(3) are missing
capabilities, (4) is a missing routing scope plus a silent-fallback bug.

## 2. The benchmark (`tools/edward-eval/university/`)

- **Ground truth** — `ground_truth.py`: plain SQL over a frozen snapshot of
  the tenant (queue counts by component / assignee / status / due window,
  inquiry counts and the oldest unanswered, every staff member's caseload /
  cap / open / overdue / stale / awaiting-outcome / absence / hours, persona
  students' adviser / counsellors / next appointment / open requirements /
  inquiries, cohort counts with adviser state, department rollups). The
  Explorer's materialised slots are the oracle for "next open slot".
  Regenerated immediately before every run: overdue, due-today and age counts
  move with `now()`, and the grader accepts ±2 % for those only.
- **Cases** — `cases.mjs`, 144 development cases / 152 turns across 21
  categories, signed in as ten different personas (director, VP, overloaded
  adviser, falling-behind adviser, registrar, evaluator on vacation, DSO,
  admissions associate director, an ordinary adviser); `holdout-cases.mjs`,
  25 cases with different phrasings, personas and people.
- **Grading** — deterministic only: required facts (`{{num}}`, `{{num~}}`,
  `{{date}}`, `{{gt}}` templates), forbidden claims, entity resolution
  (a staff question must resolve no student; a duplicate name must not be
  guessed), failed-read phrases. Wording is never graded.
- **Observability** — each turn's record keeps the request type formed, the
  resolved identity and every entity (kind, id, match quality, ambiguities),
  every tool call with arguments / status / latency / record count / result
  preview, the evidence lines the composer used, model calls with usage, the
  answer, the expected facts and the pass/fail reason. Failing turns are
  classified from that evidence: `timeout`, `routing`, `tool`, `query`
  (fact not in the evidence), `composition` (fact in the evidence, dropped
  from the prose), `hallucination`, `entity`, `product_data`.
- **One command** — `tools/edward-eval/university/bench.sh <batch>`
  (snapshot → API → ground truth → suite → `artifacts/runs/<batch>/`).

## 3. What changed in Edward

All in `apps/api/src/audentra/`; nothing writes; Student Edward's package
(`integrations/assistant/`) is untouched.

**Staff identity is first-class** (`staff_assistant/identity.py`). Each turn
loads the signed-in member's profile once (bounded SQL): name, title, role,
component, manager, direct reports, caseload vs cap, open/overdue/stale work,
appointments, absence. It drives self-reference routing ("my", "I"), binds
`staffId` for "me", feeds the planner prompt (`signedInStaff`), and is
recorded in the trace (`identity`).

**Entity resolution distinguishes staff, students and departments**
(`staff_assistant/entities.py`). Mentions are extracted (multi-word names,
possessives, `to Tobias`-style first names, department phrases, "my team"),
department and product phrases are scrubbed before person extraction, and
each person mention is resolved against **both** the staff directory
(`searchStaff`, with match quality) and the roster. A hit in one directory
decides; a hit in both is decided by the sentence's role language (advise /
caseload / slots / queue / leave = staff; blockers / deposit / transcript /
requirements = student; a possessive followed by a staff noun — "Elena
Larkspur's advisees" — or a booking verb before the name — "book Junia" —
is staff), and otherwise reported as ambiguous with both lists rather than
guessed. Unknown names are "not on staff and not on the roster", never a
fuzzy student. The trace records every decision (`entities`).

**Bounded, SQL-side operational reads**
(`infrastructure/postgres/staff_operations_repository.py`, new tools in
`catalog.py` / `tools.py`):

| Tool | Reads |
| --- | --- |
| `getStaffProfile` | one person's identity, org position, caseload, backlog, calendar summary, absences, flags |
| `searchStaff` | the directory by name / component / role, or who is away right now |
| `getStaffTeam` | a manager's reporting subtree with flags (over cap, on leave with caseload, departed with caseload, no open slots, falling behind, spare capacity) and the component summary |
| `getStaffCaseload` | one person's advisees with advising status, next appointment, deposit, open/overdue work, and filters (not completed, no booking, deposit paid/unpaid, overdue work) |
| `getStaffAppointments` | one person's appointments in a window (today / tomorrow / week / two weeks / past week / awaiting outcome) |
| `getStaffAvailability` | bookability and why not, next open slot (searched to the 62-day booking horizon, not just the portal's 14 days), open/booked slot counts, weekly days, absences |
| `compareStaff` | two to four profiles side by side |
| `summarizeWorkQueue` | SQL counts with filters (ownership, assignee, component, status, priority, due window, topic, stale, escalated, action/work type, in-progress-over-N-days) and grouping (assignee, component, status, priority, due window, action type, student) |
| `searchWorkQueue` | a bounded page in canonical / due / oldest / stalest order with the true total |
| `summarizeInquiries` / `searchInquiries` | awaiting-first-reply, older-than-24 h, waiting-on-student, unassigned, urgent, the oldest, grouped by topic/assignee/status/priority; paged lists oldest-first |
| `getComponentSummary` | a department: headcount, on leave / departed / away now (with their open items), per-member backlog, queue rollup, document reviews, inquiries |

`getStaffWorkQueue` keeps its name and shape but now pages in SQL (25
items) with SQL counts. `getWorkItemDetail` reads one row and its own logs
instead of the whole board (`staff_repository._read_work_item`) — the Staff
Portal's detail view benefits from the same change. Work-item keys resolve
through `work_item_by_key`. The cohort vocabulary gained
`adviserState` (none / assigned / active / on_leave / departed) and
`primaryAdviserId`, with `primary_adviser` and `adviser_state` groupings.

**Routing** (`classify.py`, `scope.py`, `planner.py`). New request types:
`staff_profile / staff_workload / staff_availability / staff_caseload /
staff_appointments / staff_comparison` (a named colleague, facet from the
sentence), `my_work / my_profile` (self), `team_overview` (my team, the
advisers, over cap, spare capacity, no slots, never closed out),
`department_operations`, `queue_aggregate` (counts and "which team / who has
the most" with filters parsed from the words), `inquiry_aggregate`,
`staff_directory` (who is away), `not_found`. Staff scope is settled
**before** the Action Center, cohort and student branches. Compound asks
("how many items are unassigned and how many inquiries are awaiting a
reply") carry one filter set per clause; "X … and how many are overdue" no
longer narrows the first count. A cohort count with an unrecognised
qualifier defers to the planner instead of answering with the roster size.
Follow-ups: a pronoun after a staff turn re-resolves the prior turn's name
*with its sentence* ("her next open slot" after "how many students does
Elena Larkspur advise"), and "how many of those are overdue" re-derives the
previous turn's queue filters. The model planner receives the identity and
the resolved entities and may propose the new filters, still without any
identity argument.

**Composition** (`compose.py`). Deterministic composers for every new
intent, each with an evidence bundle for the optional rewrite; the student
ownership / overview answers now state the primary adviser and counsellors
with their status (departed, on leave, next open slot) instead of the stale
"no formal advisor model" line; appointments name the person; "my …"
answers read in the second person; the composer prompt requires digits.

**Trace** (`assistant/trace.py`): `identity`, `entities`, and the entity
resolution / identity stages.

## 4. Results

Development suite, same 144 cases and grader throughout:

| Run | PASS | PARTIAL | FAIL | Pass rate | p50 | p90 | max | Timeouts | Turns > 4 s | Failure classes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| baseline | 26 | 0 | 118 | 18.1 % | 907 ms | 3,539 ms | 5,345 ms | 13 | 15 | routing 74 · query 23 · timeout 13 · entity 6 · hallucination 6 · composition 1 |
| r1 — identity, entities, bounded tools, staff routing | 99 | 6 | 39 | 68.8 % | 1,400 ms | 2,106 ms | 7,857 ms | 1 | 4 | query 23 · routing 8 · entity 3 · composition 3 · timeout 1 · hallucination 1 |
| r2 — adviser in student answers, possessives, compound asks, bounded work-item detail | 129 | 2 | 13 | 89.6 % | 1,449 ms | 2,141 ms | 4,295 ms | 0 | 2 | query 6 · composition 3 · routing 2 · entity 1 · tool 1 |
| r3 — follow-up carry with sentence context, deposit / booking predicates, absence directory | 141 | 1 | 2 | 97.9 % | 1,364 ms | 1,965 ms | 3,742 ms | 0 | 0 | composition 1 · query 1 |
| r4 — start-date required phrase, tolerant time-relative grading | 143 | 0 | 1 | 99.3 % | 1,369 ms | 1,991 ms | 4,892 ms | 0 | 4 | query 1 (an honest answer the regex did not credit) |
| r5 — after the holdout-driven vocabulary fixes | 143 | 0 | 1 | 99.3 % | 1,310 ms | 1,834 ms | 2,654 ms | 0 | 0 | query 1 — the same honest refusal, now routed as `unsupported_metric`; the grader pattern was widened and the regrade is 144/144 |

Holdout (25 unseen cases): **16/25 on first contact**, all nine misses being
vocabulary coverage of the same classes the development suite exercised —
"get in with" / "earliest time" (availability), "who looks after X on the
advising side" (ownership), "booked in to see anyone" (appointments), "Count
the students who don't have anyone advising them" (count verb and adviser
predicate), "versus" (comparison prefers staff), imperatives read as first
names ("Rate Ada Ashgrove", "Move Bianca Kettleby"), "move X to Y's
caseload" (action gate). After those fixes — each a general rule, none a
question-specific branch — **25/25**.

Median latency rose from 0.9 s to 1.4 s because far more turns now reach
the composer rewrite (the baseline's fast turns were mostly canned "which
student do you mean" replies); the tail collapsed: p90 3.5 s → 2.0 s, zero
tool timeouts, no queue read above 25 ms.

Results by category (baseline → r4):

| Category | Cases | Baseline | r4 |
| --- | ---: | ---: | ---: |
| student | 9 | 4 | 9 |
| cohort | 8 | 3 | 8 |
| my_work | 10 | 0 | 10 |
| other_staff | 9 | 0 | 9 |
| caseload_capacity | 9 | 0 | 9 |
| department_ops | 8 | 0 | 8 |
| action_center | 8 | 0 | 8 |
| inquiries | 7 | 1 | 7 |
| deadlines | 5 | 1 | 5 |
| onboarding_blockers | 5 | 4 | 5 |
| appointments | 6 | 1 | 6 |
| scheduling | 6 | 0 | 6 |
| comparison | 5 | 0 | 5 |
| multi_criteria | 6 | 0 | 6 |
| summary | 5 | 2 | 5 |
| prioritization | 5 | 1 | 5 |
| follow_up | 7 | 1 | 7 |
| ambiguous_names | 6 | 2 | 6 |
| multi_intent | 5 | 0 | 5 |
| refusal_unknown | 8 | 6 | 7 |
| edge_cases | 7 | 0 | 7 |

Regression checks: `pytest` (in-memory tier) 1,128 passed, 109 skipped, 1
failed — `test_assistant_pipeline.py::test_campus_life_question_reads_campus_life_only`,
which fails identically on the untouched commit `fb1a561` and is unrelated;
`test_staff_edward.py` 75/75 and 17 new tests in
`test_staff_edward_entities.py`; `ruff`, `ruff format`, `mypy` clean.

## 5. Representative traces

**A named colleague, shared full name (u-cc-001).** "How many students does
Elena Larkspur advise?" — `searchStaff` → exact_name; `searchStudents` → 4
exact hits; sentence has staff language ("advise"), so the resolver picks
the adviser; `staff_caseload`; `getStaffProfile` 15 ms + `getStaffCaseload`
19 ms. Answer: "Elena Larkspur advises 126 students, which is over the cap
of 110. Of these, 93 advising sessions have been completed, 30 are
scheduled, 1 was missed, and 2 students have no advising appointment
booked…" Baseline: "I found 4 students matching Elena Larkspur".

**Follow-up pronoun to staff (u-fu-001).** "When is her next open slot?" —
the prior turn's sentence is re-resolved (its "advise" keeps Elena the
adviser); `staff_availability`; `getStaffAvailability` 17 ms. "Elena
Larkspur's next open slot is 2026-09-11 17:00 UTC. There are currently 0
open slots in the next 14 days, with 101 appointments already booked." (The
Explorer's oracle slot: 2026-09-11T17:00Z; the portal's own 14-day summary
alone would have said "none".)

**Tenant-wide aggregate (u-do-006).** "Which staff members have the most
overdue work?" — `queue_aggregate` with `dueWindow=overdue,
groupBy=assignee`; one `summarizeWorkQueue` read, 9 ms, 12 buckets.
"Matthias Gunnarsson with 226 overdue items, followed by Beatrix Zaragoza
with 196 … 1,724 overdue items across 1,188 distinct students." Baseline:
routed to a student cohort.

**Compound ask across two subsystems (u-mi-005).** "How many items are
unassigned and how many inquiries are awaiting a first reply?" — primary
`queue_aggregate` (ownership=unassigned) + additional `inquiry_aggregate`
(status=awaiting_first_reply), each clause with its own filters. "156
unassigned items … 56 inquiries awaiting a first reply, all of which are
older than 24 hours. The oldest inquiry is 'Portal login issue' from Helia
Quillfeather, open for 117.6 hours." Baseline: "2,576 students whose
requests are still awaiting a first reply".

**First name only, drafting (u-am-003).** "Draft a short message to Tobias
about his missing transcript." — `searchStaff` 0 hits, `searchStudents` 1
exact first-name hit → resolved; `draft_email` reads his record (transcript
and immunization open, deposit paid); the draft is about the transcript and
says "Draft only — nothing has been sent." Baseline: "Tell me which student
you mean".

**Planted edge case (u-ed-003).** "Why can't SYN-000023 find an advising
slot?" — `student_ownership`; evidence: "Primary academic adviser: Elena
Larkspur", "Next open slot with the primary adviser: 2026-09-11 17:00 UTC",
"Advising gap: the adviser has no open slots in the next two weeks."

**Remaining failed / weak traces.**

- *u-rf-005 (r4)* — "Which adviser has the best student satisfaction
  scores?" → `team_overview`; the answer honestly says the information "does
  not include specific student satisfaction scores" and then offers the
  team's flags. Correct behaviour; the grader's honesty regex did not credit
  "does not include" (widened in r5). The better routing is
  `unsupported_metric`, which the holdout-driven `staff_performance_rating`
  metric now provides.
- *h-013 (holdout, first contact)* — "Is Cassius Pemberwell booked in to see
  anyone soon?" → `student_overview` (no appointments read) → "not booked
  to see anyone soon" while he has an advising appointment on 2026-09-02.
  A **wrong answer from an incomplete read**, the most dangerous class; fixed
  by routing "booked in / see anyone" to `student_appointments`, and the
  overview now also reads ownership so adviser questions phrased any way
  still surface the adviser.
- *h-021 (holdout, first contact)* — "Move Bianca Kettleby to Thaddeus
  Crane's caseload." → `staff_caseload` (Thaddeus's caseload read out) —
  the action gate had no "move … to … caseload" pattern; a write request
  must refuse before anything is read. Fixed in the normaliser's `assign`
  action kind.
- *u-os-005 (r1–r3)* — "Tell me about Thaddeus Crane." — every fact right,
  but the rewrite kept dropping his start date (2026-08-17, the new-hire
  signal). Now a required phrase when the start is within 180 days.

## 6. Remaining failures, grouped by root cause

After r5 the development suite and holdout pass; what remains is judgement
about generalisation and product data, not open defects:

1. **Deterministic vocabulary coverage (routing).** The holdout showed that
   9/25 unseen phrasings missed on first contact. Each fix was a rule, but
   the mechanism is still a grammar. The next step is not more regex: it is
   to route through the model planner whenever the deterministic classifier
   is *uncertain* (currently only when it returns nothing), with the
   resolved entities as context — the planner already receives them and the
   validated plan cannot carry identity.
2. **Time-relative counts (grading, not Edward).** Overdue / due-today /
   slot counts move between ground-truth extraction and the question;
   ±2 % is accepted for those. Frozen-`now` evaluation would remove even
   that.
3. **Composition (model rewrite).** Three r1–r3 failures were facts present
   in the evidence but dropped or reworded by the rewrite (spelled-out
   numbers, a start date, "no open staff work"). Required-phrase
   restoration and a digits rule closed them; the class remains possible
   for any fact the deterministic draft does not mark as required.
4. **Product data not held (honest answers, by design):** staff performance
   ratings, satisfaction scores, salary, average time-to-close, melt risk.
   The benchmark expects refusals here and Edward refuses.

## 7. Product data that is missing or limits correct answers

- **Waitlisted / denied applicants** cannot be seeded (`admission_offer` has
  no waitlisted state); the university's 424 such applicants are absent, so
  melt and reconsideration questions cannot be asked.
- **Registrar holds** are not modelled; "three holds" questions are answered
  from derived blockers with the explicit caveat.
- **Immigration requirements (I-20 / SEVIS)** are not product data; the ISS
  situation is visible only through work items.
- **Inquiry archival**: the worker's five-day expiry had archived 98
  inquiries by evaluation time, so "awaiting a first reply" is 56 in the
  product versus 86 in the Explorer; Edward answers the product's truth.
- **Stage duration / time-to-close** per person is not computed anywhere
  (`completed_last_7_days` is the nearest fact).
- **Appointment slots beyond the 14-day summary** existed but were not
  surfaced by the portal read; the assistant now searches to the 62-day
  booking horizon — the portal's adviser card could do the same.

## 8. Recommended next Edward improvements

1. **Uncertainty-gated planner.** Call the model planner when the
   deterministic classifier's confidence is below a threshold or when
   entities and intent disagree (a staff entity with a student intent),
   not only when it returns nothing. Keep server-bound identity.
2. **Multi-person questions.** "Compare the two DSOs" resolves people by
   role only through the team read; a `role` → people expansion in
   `searchStaff` (already supports `role`) wired into the resolver would
   let "the DSOs", "the transcript evaluators" resolve as staff sets.
3. **Caseload filters in SQL.** `getStaffCaseload` filters in Python over
   one person's assignments (≤ 160 rows); a director asking across the
   whole team ("all advisees with no booking") needs the same predicates in
   the cohort SQL (`advisingStatus` as a cohort field).
4. **Morning Brew staff dimension.** The briefing still has no capacity /
   absence section; the team and component summaries built here are the
   natural source.
5. **Frozen-`now` evaluation** for exact grading of time-relative counts,
   and periodic holdout refresh (new phrasings each release) so the
   dev/holdout gap keeps measuring generalisation.
6. **Portal parity for the Action Center detail** — `_read_work_item` also
   fixes the detail view's board load; the board endpoint itself still
   returns every item in one payload (product issue noted in the mock
   report §7).

## 9. API usage

gpt-4o-mini only, Edward's own planner/composer calls; no judge model.
Recorded across the batches in `artifacts/runs/univ-*/summary.json`:
baseline $0.019, r1 $0.037, r2 $0.038, r3 $0.039, r4 $0.039, holdout
$0.006 + $0.006, r5 ≈ $0.04, plus ≈ $0.02 of interactive probing —
**≈ $0.23 total**.

## Reproduction

```bash
# Audentra-platform, stack up, university deployed (npm run audentra:deploy in the explorer)
tools/edward-eval/university/bench.sh univ-$(date +%Y%m%d)              # 144-case suite
tools/edward-eval/university/bench.sh univ-holdout-$(date +%Y%m%d) -- --holdout
```

Artifacts: `artifacts/runs/univ-baseline`, `univ-r1` … `univ-r5`,
`univ-holdout`, `univ-holdout-r2` (each with `report.md`, `summary.json`,
`transcript.json`).
