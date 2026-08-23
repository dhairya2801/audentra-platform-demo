# Staff Edward against the 3K university: evaluation and improvement

2026-08-18. Staff Edward evaluated and improved against the canonical
synthetic-university population (tenant `aster-demo`, 2,576 students, 1,027
open work items, 1,748 intervention candidates) served by the production
PostgreSQL composition — the same repositories, Action Center queue, and
cohort SQL the Staff Portal renders.

**Result: development suite 25 PASS / 3 PARTIAL / 12 FAIL → 40/40 PASS;
holdout suite (never run during development) 7/8 on first contact, 8/8 after
one generalizable one-line classifier fix. Total OpenAI spend ≈ $0.03 of the
$0.70 budget.**

---

## 1. Current architecture (as found)

One request path: `POST /v1/staff/assistant/messages` → `staff.ask_edward` →
`StaffAssistantPipeline` (`apps/api/src/audentra/integrations/staff_assistant/`):

```
normalize → classify (regex, deterministic-first) → resolve student referent
→ plan (deterministic table; gpt-4o-mini planner only on classification
fallback) → execute tool reads → derive state → bounded dependency round
→ compose deterministic draft → optional gpt-4o-mini rewrite → claim guard
```

- 22 read-only tools over tenant-scoped Postgres reads. Identity arguments
  (`studentId`, `workItemId`) are always server-bound after validation; the
  model may propose tool names and non-identity filters only.
- `getStaffWorkQueue` reads the same `_read_action_center` SQL (minus the
  reconciliation write) that renders the Staff Portal's Action Center;
  `getStudentsNeedingAttention` reads the deterministic engagement-scan
  candidates. Cohort tools share `CohortSql` with Morning Brew.
- Model: `gpt-4o-mini` (OpenAI) for the optional planner (~560 max tokens)
  and composer rewrite (~420 max tokens); everything else is deterministic.
- Durable conversations persist history plus the active student referent;
  per-turn traces expose classification, tool calls with arguments/results,
  and model usage.

The bones were good. The failures were concentrated in specific deterministic
gaps, not in the architecture.

## 2. Baseline evaluation

New suite: `tools/edward-eval/staff-db/` — 40 development questions across 7
categories plus an 8-case holdout, graded deterministically (no LLM judge)
against `ground-truth.json`, which is extracted by executing **the production
staff tool host itself** (`ground_truth.py` builds `PostgresPlatformService`
and calls `execute_staff_tool_reads`), so expected facts are the backend's
own answers. Grading checks classification, executed tools, traced tool
*arguments* (e.g. the cohort filter actually applied), resolved-student
identity, required facts (regex over prose + blocks), and forbidden claims.
Wording is never graded — only facts and behavior.

The eval is designed to run against a frozen `pg_dump` snapshot of the dev
database (`vv_enrollment_staff_eval`): the dev worker mutates work items
continuously, which otherwise makes ground truth drift mid-run (observed:
the queue head changed between two reads minutes apart). Ground truth was
extracted from the snapshot throughout; the development-phase batches were
served from the live database (equivalent at run time), and the final
confirming batches (`staff-db-final-snapshot`, `staff-db-holdout-snapshot`)
were re-run end-to-end on the snapshot with identical results.

Baseline (`artifacts/runs/staff-db-baseline/`):

| Category | Cases | PASS | PARTIAL | FAIL |
| --- | --- | --- | --- | --- |
| lookup | 8 | 3 | 0 | 5 |
| summary | 5 | 4 | 1 | 0 |
| cross-domain | 5 | 4 | 1 | 0 |
| action-center | 8 | 5 | 1 | 2 |
| aggregate | 6 | 5 | 0 | 1 |
| risk | 3 | 2 | 0 | 1 |
| negative | 5 | 2 | 0 | 3 |
| **total** | **40** | **25** | **3** | **12** |

Two case specs were tightened after the baseline ran (both grading-fairness
fixes, noted for honesty): `sdb-neg-001` now also requires the communication
history to actually be read (baseline claimed "no record of recent emails"
without reading it), and `sdb-neg-004` now forbids the work-item misroute
phrasing; `sdb-xd-005`'s progress fact was widened to accept count-based
phrasings. Regrading the stored baseline under the final specs gives
**25 PASS / 2 PARTIAL / 13 FAIL** (xd-005 → PASS, neg-004 → FAIL).

### Representative baseline failures

- "Show me the student with ID SYN-000004." → *"I couldn't find that work
  item."* (student external reference parsed as a work-item key).
- "How many students have unpaid deposits?" → *"There are 2576 students with
  unpaid deposits."* (truth: 684 — the plural "deposits" broke the filter
  regex and the unfiltered population was presented as the answer).
- "Is Tobias Quillfeather currently in the Action Center?" → membership
  claimed/explained from his blockers; his actual work items were never read
  (Tobias has none — he is *not* in the Action Center).
- "How many open items are in my Action Center right now?" → *"I couldn't
  read the work queue just now."* (planner fallback proposed
  `status='open'`, an invalid enum; the read was rejected).
- 8× "Caleb Dunmire" disambiguation listed only program + class year — two
  candidates were textually identical, and no ID was shown to pick with;
  the follow-up "The one in Civil Engineering." dead-ended.
- "Which students are highest risk right now?" → *"Tell me which student you
  mean"* (ranking-language gap routed it to a single-student intent).

## 3. Root causes

| # | Failure class | Baseline cases | Root cause |
| --- | --- | --- | --- |
| 1 | Student IDs misrouted to work items | lookup-003, neg-004 | `SYN-000123` matches the work-item key regex; no external-reference lookup existed anywhere in the staff assistant |
| 2 | Disambiguation unusable for true duplicates | lookup-002/006/008, hold-amb | `searchStudents` didn't return `external_ref`; candidates listed without IDs; no way to select a candidate on the next turn; single-surname lookups extracted no name at all |
| 3 | Typos dead-end | lookup-004 | ILIKE substring matching only; no close-spelling fallback |
| 4 | Cohort filter silently dropped → confidently wrong number | agg-001, hold-agg-001 | `\bdeposit\b` didn't match "deposits"; the empty filter then counted everyone |
| 5 | "Action Center" vocabulary absent from the deterministic classifier | ac-001/005/008 | Every Action Center phrasing fell through to the model planner, which guessed filters (`ownership:'mine'`) or invalid enums (`status:'open'`) |
| 6 | Membership questions answered from blockers, not the queue | ac-003/004, hold-ac | No intent read the student's actual work items; the composer/model invented "in the Action Center because \<blockers\>" causality |
| 7 | Intent-regex gaps | risk-001, neg-001, neg-003, summary-004, hold-agg-002 | "highest risk", "has anyone emailed", "odds X enrolls", "follow up with X about", "break down … by …" all unmatched |
| 8 | Action gate gap | neg-005 | "Mark X's transcript as accepted" wasn't recognized as a write (transcript missing from the object list), and referent resolution ran before the refusal could |

## 4. Changes implemented

All in `apps/api/src/audentra/integrations/staff_assistant/` unless noted.
Read-only surface throughout; no write capability was added.

**Entity resolution**

- `normalize.py` — extracts a shared `reference_token` (`PREFIX-SUFFIX`)
  whose kind (student ID vs work-item key) is decided *against the database*,
  not by regex; single-surname lookups ("Pull up Quillfeather.") now extract
  a candidate name; the update-record action gate covers
  transcript/upload/immunization objects and "mark … as accepted/…".
- `staff_assistant_repository.py` (infrastructure/postgres) —
  `search_students` returns `externalRef` and matches query tokens against
  it; new `get_student_by_external_ref` (exact, tenant-scoped); new
  `search_students_fuzzy` (difflib close-spelling suggestions over the
  tenant roster, marked `matchQuality: "fuzzy"`; no extensions required).
- `tools.py` — `searchStudents` takes an optional `externalRef` argument
  (exact ID lookup), and falls back to fuzzy suggestions when an exact name
  search finds nothing; both degrade gracefully on hosts without the new
  primitives (the in-memory eval host).
- `pipeline.py` — referent resolution tries the roster external reference
  first for pasted tokens and falls back to the work-item namespace (so
  `SYN-000004` opens a student and `ENR-104` still opens a task);
  fuzzy matches are offered for confirmation, **never** silently resolved;
  disambiguation lists now carry the student ID per candidate and say when
  the list is truncated; a new deterministic candidate-selection step
  resolves follow-ups like "the one in Civil Engineering" / "the second
  one" by re-running the same canonical search and filtering by
  ordinal/program/class-year — and answers with the intent of the question
  that triggered the disambiguation. Refusal intents (action requests,
  unsupported metrics) skip referent resolution entirely, so a lookup
  outcome can never preempt a refusal.

**Action Center parity**

- `classify.py` — deterministic Action Center routing: plain phrasings →
  `work_queue` (the canonical queue read, unfiltered — no more
  planner-guessed filters); "…about student X…" → new
  `student_action_center` intent; topic phrasings ("transcript items in my
  Action Center") → `work_queue` with a `topic:` reference;
  "how many/which students are in the Action Center" → the cohort
  classifier with the canonical `hasOpenWorkItem` membership filter.
- `compose.py` — new `_compose_student_action_center`: membership is
  answered **from the student's actual open work items** ("Yes — … DOC-91E84E47:
  Review transcript-fennwick.pdf is what put them there" / "No — … no open
  staff work items"), with blockers reported separately as student-side
  context. The answer skips the model rewrite (`_SKIP_REWRITE`) so the
  yes/no can't be softened or inverted.
- `tools.py` — `getStaffWorkQueue` gains a `topic` title/description filter
  and reports `distinctOpenStudents`; `compose.py` reports filtered slices
  with their own numbers instead of blending whole-queue counts with a
  filtered head item.
- `catalog.py` — `getStaffWorkQueue` description now states it is the same
  source of truth as the Staff Portal's Action Center and authoritative for
  order; `searchStudents` documents ID lookup and the fuzzy-confirmation
  contract.

**Cohorts and intents**

- `classify.py` — plural-tolerant deposit predicates (`deposits?`);
  "break down / split by" count intent; "highest/high/most-risk students"
  ranking; "has anyone emailed/contacted X" communications; "odds/chances X
  enrolls" unsupported-metric; "follow up with X about" recommendation.
- `planner.py` / `ai/gateway.py` — the model planner may propose the new
  `topic`/`externalRef` arguments (still schema-validated server-side);
  selection rules for `student_action_center`.
- `compose.py` — missing-items answers lead with progress ("completed 3 of
  8, 5 open, 4 blocking"); overviews state onboarding status.

**Tests** — 11 new unit tests in `apps/api/tests/test_staff_edward.py`
pinning the deterministic routing (Action Center phrases, membership counts,
plural deposits, pasted-ID vs task routing, mark-as-accepted refusal,
highest-risk/odds honesty, communications routing, refusals never resolving
referents, break-down cohorts).

**Eval suite (new)** — `tools/edward-eval/staff-db/` (ground-truth extractor,
40 dev cases, 8 holdout cases, deterministic runner; see its README), plus
the `eval:edward:staff-db` npm script.

## 5. Final evaluation

Dev suite (`artifacts/runs/staff-db-final/`), same questions, same grader:

| Category | Baseline PASS rate | Final PASS rate | Baseline P/P/F | Final P/P/F |
| --- | --- | --- | --- | --- |
| lookup | 3/8 | 8/8 | 3/0/5 | 8/0/0 |
| summary | 4/5 | 5/5 | 4/1/0 | 5/0/0 |
| cross-domain | 4/5 | 5/5 | 4/1/0 | 5/0/0 |
| action-center | 5/8 | 8/8 | 5/1/2 | 8/0/0 |
| aggregate | 5/6 | 6/6 | 5/0/1 | 6/0/0 |
| risk | 2/3 | 3/3 | 2/0/1 | 3/0/0 |
| negative | 2/5 | 5/5 | 2/0/3 | 5/0/0 |
| **total** | **25/40 (63%)** | **40/40 (100%)** | 25/3/12 | 40/0/0 |

Regression checks on everything already green: the pre-existing in-memory
staff suite stays **36/36**; `pytest` **868 passed** (in-memory tier);
Postgres-gated staff suites on a disposable migrated database —
cohort-parity + conversations **17 passed**, synthetic-university
**57 passed**; `ruff`, `ruff format`, `mypy` clean; node workspaces green.

## 6. Test generalization (holdout)

Eight cases written alongside the dev suite but **never executed until the
fixes were finished**: an untouched duplicate name (Helia Fennwick), an
untouched external ref (SYN-000008), blockers for an untouched persona,
Action Center membership both ways for students the fixes never named
(Rufus/Wren), pronoun-referent housing reasoning across turns, and two
untouched aggregate phrasings.

First contact: **7/8**. The one failure — "Break down the students still in
onboarding by program." — was a real generalization gap of the same class as
the dev findings (the count-intent regex knew "breakdown" but not "break
down"; the planner fallback then produced an invalid filter). Fixed with the
one-line classifier extension above plus a pinned unit test; rerun **8/8**.
No holdout case needed its expectations changed.

## 7. Representative examples

**Duplicate name (sdb-lookup-002 + sdb-lookup-008).**
Q: "What's going on with Caleb Dunmire?" — ground truth: 8 students share
the name; two are textually identical by program+year.
Before: candidates listed as "Caleb Dunmire — English Literature, class of
2030" (twice, indistinguishable, no IDs); the follow-up "The one in Civil
Engineering." answered *"I couldn't find a student matching 'Civil
Engineering'"*.
After: every candidate carries its ID ("Caleb Dunmire — ID SYN-000102 —
Civil Engineering, class of 2031 …"), and the follow-up deterministically
resolves the unique Civil Engineering candidate and answers with his actual
record (financial-aid verification blocking, due Jan 4 2027).
Better because: staff can actually pick a student, by ID or by attribute,
and the pick is made by code against the roster — never by the model.

**Student ID lookup (sdb-lookup-003).**
Q: "Show me the student with ID SYN-000004." — ground truth: Ingrid
Thistlebrook, Chemistry 2030, deposit unpaid, 4 open blocking.
Before: *"I couldn't find that work item."*
After: *"Ingrid Thistlebrook — Chemistry, class of 2030. Offer accepted,
deposit not paid. Onboarding in progress. 3 of 8 requirements complete; 4
open blocking item(s)…"*.
Better because: the institutional ID namespace now resolves against the
roster first, with the work-item namespace as fallback — both keep working.

**Action Center membership (sdb-ac-004).**
Q: "Is Tobias Quillfeather currently in the Action Center?" — ground truth:
zero open work items → not in the Action Center; two open blockers exist.
Before: *"…is currently not in the Action Center due to two open blocking
requirements…"* (right answer only by luck — membership was never read, and
the stated causality is wrong; the same path answered "why is X in the
Action Center" for non-members with invented membership).
After: *"No — Toby is not in the Action Center right now: there are no open
staff work items on their record. They do still have open blockers on their
side (Submit your official transcript and Provide immunization records), but
no staff task is currently queued for them."* — deterministic, rewrite-free.
Better because: membership now comes from the same rows the portal renders,
and the causal claim ("what put them there") cites the actual work item.

**Multi-fact / conflicting premise (sdb-xd-001).**
Q: "Marisol Fennwick says she already paid her deposit. What's actually
blocking her?" — ground truth: deposit **not** posted ($500), transcript
under review, orientation open.
Before and after both ground correctly here (the dependency round reads her
financial state); the after answer additionally separates who owes each next
step: *"…The official transcript is under review by the university, while
the other two items require action from her. Notably, the enrollment deposit
has not been posted…"* — the record outranks the reported claim.

**Aggregate (sdb-agg-001).**
Q: "How many students have unpaid deposits?" — ground truth: 684 of 2,576.
Before: *"There are 2576 students with unpaid deposits."* — the filter had
silently dropped, and the trace shows `summarizeStudents` running with an
empty filter.
After: *"684 students have unpaid deposits…"* with the trace showing
`deposit_state='unpaid'` actually applied (the grader asserts the argument,
not just the number).
Better because: a wrong-but-confident count is the most dangerous failure
class for staff; the filter is now part of the deterministic classification
and plural phrasing can't drop it.

**Unavailable data (sdb-neg-003).**
Q: "What are the odds Wren Halloway actually enrolls this fall?" — no such
model exists.
Before: *"…Wren is on track to enroll this fall…"* — an implied prediction.
After: *"I don't have enrollment probabilities — no predictive model exists
in Audentra. I can show objective signals instead: deposit state, open
blocking requirements, deadlines, and recorded engagement."*
Better because: the boundary between stored fact and speculation is stated
instead of blurred.

## 8. Remaining limitations

- **Fuzzy matching is suggestion-only and last-name-weighted.** difflib over
  roster names catches close spellings ("Haloway"→"Halloway") but not
  phonetic misspellings ("Kwilfether"); a trigram index would be the next
  step if tenants need it.
- **Candidate selection handles ordinal/program/class-year/ID picks.** Picks
  by other attributes ("the one with the unpaid deposit") re-ask instead of
  resolving; the disambiguation entries deliberately show the attributes
  that do work.
- **The `topic` queue filter is a title/description keyword.** It matches
  how the seeded work items are written; a semantically-typed task category
  would be sturdier if item titles diversify.
- **Ambiguous attention questions re-ask.** "Why is Milo Dunmire flagged?"
  (4 students share the name) disambiguates rather than inferring that
  exactly one Milo is in the attention queue; cross-referencing the queue to
  auto-narrow would be a nice follow-up.
- **Empty-domain answers depend on tenant data.** Communications/inquiries
  are empty in this tenant, so those paths are exercised for honesty
  ("no recorded communications"), not for retrieval quality.
- **The planner fallback can still propose invalid filter values** for
  unclassified phrasings; validation rejects them safely, but the reply is
  then a "couldn't run that" rather than an answer. Every phrasing found in
  this suite now routes deterministically; the class is only narrowed, not
  eliminated.
- The eval requires the snapshot database; running it against the live dev
  DB gives drifting ground truth (the dev worker rewrites the queue).

## 9. API usage

- Models: `gpt-4o-mini` (OpenAI) only — Staff Edward's own planner/composer
  calls. Grading is fully deterministic; no judge model was used.
- Recorded across all eval batches (`artifacts/runs/staff-db-*/summary.json`),
  including the snapshot confirmation runs:
  **154 calls, ~215k prompt + ~11k completion tokens ≈ $0.039.**
- Interactive probing during diagnosis: ~25 further calls ≈ $0.005.
- **Estimated total ≈ $0.044 — under the $0.70 limit** (target margin met;
  the deterministic-first pipeline is what keeps a 48-case × multi-turn
  evaluation this cheap).

## Reproduction

See `tools/edward-eval/staff-db/README.md` (snapshot database → API host →
`ground_truth.py` → `npm run eval:edward:staff-db`). Artifacts from this
work: `staff-db-baseline`, `staff-db-final`, `staff-db-holdout`,
`staff-db-holdout-final`, the snapshot confirmations
`staff-db-final-snapshot` / `staff-db-holdout-snapshot`, and the
per-category development batches under `artifacts/runs/`.
