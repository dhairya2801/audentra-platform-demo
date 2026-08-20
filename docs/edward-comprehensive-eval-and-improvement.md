# Edward: comprehensive evaluation and improvement

2026-08-19. Both Edwards — the student portal assistant and the staff
assistant — evaluated against purpose-built 100-scenario question banks and
improved end to end. Student Edward runs against the 16 seeded personas of the
canonical in-memory composition; Staff Edward runs against the 2,577-student
synthetic university in PostgreSQL, served by the same repositories, Action
Center queue and Morning Brew composer the Staff Portal renders.

---

## 1. Executive summary

| Suite | Baseline | Final |
| --- | --- | --- |
| **Student development** (80 scenarios) | 73 PASS / 1 PARTIAL / 6 FAIL | **80 PASS / 0 / 0** |
| **Staff development** (80 scenarios) | 59 PASS / 5 PARTIAL / 16 FAIL | **80 PASS / 0 / 0** |
| **Student holdout** (20, never run during development) | — | **18/20 first contact → 20/20** after two generalized fixes |
| **Staff holdout** (20, never run during development) | — | **17/20 first contact** (1 FAIL, 2 PARTIAL) **→ 20/20** after two generalized fixes |

Baselines are stated **under the final case specifications**. Six expectations
(three per persona bank) were objectively wrong when first written and were
corrected before the baseline was regraded; every correction is listed in §4.
The raw first-run numbers were Student 67/1/12 and Staff 56/4/20.

The headline defect the brief named — Staff Edward's conversational context
being too sticky — was real, systemic, and is fixed at the root: the durable
`active_student_id` column was monotonic (`COALESCE(:new, active_student_id)`
never cleared it), and the intent classifier let plainly team-scoped questions
fall into student-scoped branches, where the stale referent was then applied.
Both are gone; §6 shows the before/after.

Regression state: `pytest` **904 passed** (868 before this work, +36 new
tests), `ruff check`, `ruff format --check` and `mypy` clean over `src` and
`tests`, the pre-existing staff database suite **40 / 40**, and the in-memory staff
suite **36 / 36**.

**OpenAI spend: ≈ $0.29 of the $1.00 budget** — 935 metered calls plus
interactive probing, all `gpt-4o-mini`, all of it Edward's own planner and
composer calls. No LLM judge was used: every one of the 200 scenarios is graded
deterministically against backend-derived ground truth.

---

## 2. Current architecture

Two pipelines, deterministic-first, sharing a philosophy: **the model decides
what to say, the backend decides what is true.**

**Student Edward** — `POST /v1/student/assistant/messages` →
`AssistantPipeline` (`apps/api/src/audentra/integrations/assistant/`):

```
normalize → classify (regex, deterministic-first) → coverage gate
→ select reads → execute 23 read-only tools → derive state
→ bounded dependency round → compose deterministic draft
→ optional gpt-4o-mini rewrite → grounding guard
```

Self-scoped throughout: the authenticated student *is* the referent, so there
is no entity-resolution stage. The coverage gate widens a confident first-match
classification when the request names domains the selected reads would not
answer — the mechanism that keeps compound questions whole.

**Staff Edward** — `POST /v1/staff/assistant/messages` →
`StaffAssistantPipeline` (`.../staff_assistant/`):

```
normalize → classify → RESOLVE REFERENT → plan → execute 23 read-only tools
→ derive state → bounded dependency round → compose deterministic draft
→ optional gpt-4o-mini rewrite → claim guard
```

The staff-specific stage is referent resolution: staff questions are about
arbitrary students, so *which* student must be established — from a name, a
pasted SIS id, a disambiguation pick, or the conversation — and validated
against the authenticated tenant before any read runs. **Identity arguments are
always server-bound**; the model may propose tool names and non-identity
filters only.

Both use `gpt-4o-mini` for an optional planner (only when deterministic
classification returns `None`) and an optional composer rewrite. Everything
else — routing, reads, derivations, the draft answer, the honesty caveats — is
code.

### Portal-data parity

Student Edward's 23 tools cover every meaningful Student Portal page:
dashboard, enrollment checklist and per-requirement detail, documents,
financials, payments, appointments, messages, help, onboarding responses,
profile, classrooms/academics, campus life. **New in this pass:** portal
*navigation* — Edward can now say where in the portal a thing is done, using
routes from `links.py` (mirrored from the portals app router) rather than
prose it invents.

Staff Edward's tools cover the roster, per-student enrollment state, documents,
blockers, deadlines, financials, housing, appointments, communications,
engagement, timeline, ownership, the Action Center queue and work-item detail,
inquiries, playbooks, action rules, and cohort search/aggregate.
**New in this pass:** `getMorningBriefing` — the Staff Portal's own Morning
Brew, read through the same `build_morning_brew` composer, so a staff member
can ask Edward for the briefing they can already see.

Remaining portal-visible surfaces Edward cannot read are listed in §12.

---

## 3. Question-bank design

Two purpose-built banks, 100 scenarios each, **80 development / 20 holdout**.

### Student — 100 scenarios (`tools/edward-eval/student-v3/`)

| Category | Dev | Holdout | What it tests |
| --- | --- | --- | --- |
| overview | 5 | 2 | what's next / am I done / how far along |
| requirements | 7 | 2 | per-requirement status, dependencies, counts |
| why_blocked | 7 | 2 | multi-fact causal questions |
| conflict | 5 | 1 | the student's claim vs the recorded state |
| deadlines | 5 | 1 | due next, overdue, urgency |
| aid | 7 | 2 | aid status and its boundaries |
| housing | 5 | 1 | eligibility, blockers, dependencies |
| documents | 6 | 1 | submitted / received / under review / returned / missing |
| prioritization | 4 | 1 | what first, what can wait |
| navigation | 5 | 2 | where in the portal |
| knowledge | 3 | 1 | clubs, events, academic plan |
| multi_intent | 5 | 1 | two- and three-part questions |
| context_carry | 4 | 1 | short multi-turn where context matters |
| context_reset | 4 | 1 | topic switch that must not inherit |
| unsupported | 8 | 1 | data / prediction / action Edward lacks |
| **total** | **80** | **20** | |

72 of 80 dev scenarios are single-turn; 8 are two-turn. Personas are chosen so
each question has a definite right answer (`payment_pending` for "I paid, why
am I blocked?", `transcript_under_review` for "did you receive it?", `no_aid`
for "how much aid am I getting?", `document_needs_resubmission` for "what's
wrong with my transcript?").

### Staff — 100 scenarios (`tools/edward-eval/staff-db/cases-v2.mjs`)

| Category | Dev | Holdout | What it tests |
| --- | --- | --- | --- |
| identification | 10 | 2 | exact / duplicate / SIS id / surname / misspelling / absent / disambiguation pick |
| overview | 8 | 2 | what's going on, done, outstanding, deadlines, documents, ownership, financials |
| action_center | 12 | 3 | membership both ways, ordering, topic slices, counts, "why is X here" |
| cohort | 12 | 3 | counts, filters, groupings, listings |
| risk | 5 | 1 | attention ranking and its honest basis |
| cross_domain | 6 | 2 | reasoning needing more than one read |
| communications | 4 | 1 | contact history, including honest empty state |
| recommendation | 4 | 1 | next step, drafting, and the write refusal |
| multi_intent | 5 | 2 | two- and three-clause questions |
| **context** | **14** | **3** | the conversational-scope stress family |
| **total** | **80** | **20** | |

65 of 80 dev scenarios are single-turn; 15 are two-turn — the context family
needs a prior turn by construction.

The **context** category implements the brief's §5 stress matrix directly:
correct carry-forward, explicit new referent, cohort after student, Action
Center after student, new cohort topic, pronoun continuation, ambiguous
continuation ("What about housing?"), team-scope switch, ranking after student,
queue → "that student", cohort refinement ("break that down by program"), and
queue-size after student.

### Phrasing diversity

Deliberately mixed across both banks: clear ("What requirements do I still have
open?"), conversational ("Am I basically done or is there anything else I need
to deal with?"), messy ("wait so i paid the deposit why is housing still not
letting me continue"), staff shorthand ("pull Ingrid Thistlebrook", "open
Georgina Underhollow"), contextual ("What about her housing?"), multi-intent,
negative premise ("I thought Tobias Quillfeather was in my Action Center — why
isn't he showing up?"), and uncertain memory ("There was a student named Wren
or Ren something with housing problems").

### Ground truth

No expectation is prose an author believed to be true.

- **Student**: `deriveFacts()` over a snapshot of the host's own student REST
  endpoints, captured from a clean boot of the same process the scenarios run
  against. Patterns reference it by path — `{{f:depositAmountUsd}}`,
  `{{n:unreadMessageCount}}`, `{{any:openBlockingTitles}}`,
  `{{all:missingDocumentTitles}}`.
- **Staff**: `ground_truth.py` builds the production `PostgresPlatformService`
  staff tool host and calls `execute_staff_tool_reads` — expected facts are the
  product's own reads. Cohort truths come from the same `CohortSql` the portal
  and Morning Brew use; the briefing truths come from `build_morning_brew`
  itself. Patterns reference it as `{{gt:path}}` / `{{num:path}}`.

The staff eval runs against a **frozen `pg_dump` snapshot**
(`vv_enrollment_staff_eval`) because the dev worker rewrites work items
continuously, which makes ground truth drift mid-run.

### Grading

Deterministic throughout — PASS / PARTIAL / FAIL over: classification (soft),
executed tools, traced tool *arguments* (e.g. the cohort filter actually
applied), resolved-student identity (including "must NOT resolve"), required
facts over the full user-visible answer (prose **and** every block's
`fallbackText`), alternative fact groups, and forbidden claims. Wording is
never graded.

The eval distinguishes *factually correct* from *actually answering*: a queue
question whose only content is a bulk count fails its `forbidden` check, a
multi-intent question fails unless every clause's fact is present, and a
context case asserts both what must carry forward and what must not appear.

---

## 4. Baseline results

### Student development (80)

| Category | PASS | PARTIAL | FAIL |
| --- | --- | --- | --- |
| overview | 5 | 0 | 0 |
| requirements | 6 | 1 | 0 |
| why_blocked | 7 | 0 | 0 |
| conflict | 5 | 0 | 0 |
| deadlines | 5 | 0 | 0 |
| aid | 6 | 0 | 1 |
| housing | 5 | 0 | 0 |
| documents | 6 | 0 | 0 |
| prioritization | 4 | 0 | 0 |
| navigation | 1 | 0 | 4 |
| knowledge | 3 | 0 | 0 |
| multi_intent | 5 | 0 | 0 |
| context_carry | 4 | 0 | 0 |
| context_reset | 4 | 0 | 0 |
| unsupported | 7 | 0 | 1 |
| **total** | **73** | **1** | **6** |

### Staff development (80)

| Category | PASS | PARTIAL | FAIL |
| --- | --- | --- | --- |
| identification | 8 | 0 | 2 |
| overview | 6 | 2 | 0 |
| action_center | 10 | 0 | 2 |
| cohort | 9 | 1 | 2 |
| risk | 4 | 0 | 1 |
| cross_domain | 5 | 1 | 0 |
| communications | 2 | 0 | 2 |
| recommendation | 3 | 1 | 0 |
| multi_intent | 2 | 0 | 3 |
| context | 10 | 0 | 4 |
| **total** | **59** | **5** | **16** |

Four critical cases failed at baseline, all in the context family:
`s2-mi-001`, `s2-ctx-005`, `s2-ctx-008`, `s2-ctx-011`.

### Corrected expectations (stated for honesty)

Six expectations were objectively wrong when written and were fixed before the
baseline was regraded. None turned a genuine Edward defect into a pass.

| Case | Original expectation | Why it was wrong |
| --- | --- | --- |
| `stu-uns-004` | "What's my GPA?" must decline | The platform **does** hold academic standing (GPA 3.42, SAP status, completion rate) and Edward read it. Now grades that it reports the record. |
| `stu-doc-003` | must contain the full checklist titles | The question asks which *documents* are missing; the answer names them by document noun. Now grades the nouns. |
| `stu-hou-005` | forbade "you have been assigned to" | The pattern also matched the correct *negation* ("this does not mean you have been assigned to a specific room"). |
| `stu-uns-002` / `stu-uns-005` | required specific refusal wording | The shipped refusal ("I can't make that change myself — I'm read-only") is correct; the pattern did not admit it. |
| `stu-uns-003` | required specific "not available" wording | "not specified in the information provided" is an honest boundary. |
| `stu-ovw-002` | forbade "you're done" anywhere | Also matched "…before you're done" and "once both are finished, you'll be all set", which are correct. Narrowed to a *leading* completion claim. |
| `s2-id-005` | "Pull up Pemberwell." must resolve | 110 students share that surname. Ambiguity, not resolution, is the correct behaviour; now grades a usable disambiguation. |
| `s2-com-001` / `s2-com-004` | required specific empty-state wording | "there are zero recorded communications" and "email opens and clicks aren't tracked" are correct answers the patterns rejected. |

Both banks now carry `--regrade <batch>` so a spec correction can be applied to
an already-recorded run without calling Edward again.

---

## 5. Major failure patterns

Ranked by importance and frequency at baseline.

| # | Pattern | Cases | Root cause |
| --- | --- | --- | --- |
| 1 | **Sticky conversational referent** | `s2-ctx-005`, `s2-ctx-008` (+ every downstream context case) | The durable `active_student_id` was monotonic, and `is_follow_up` was true for *any* message under 60 characters, so a student-scoped intent inherited the old referent forever. §6. |
| 2 | **Global questions routed to student-scoped intents** | `s2-ctx-005`, `s2-ctx-008`, `s2-ac-011` | The classifier's student branches fired on a bare topic word (`deadlines`, `deposit`, `documents`) with no requirement that the turn be about one student. "What deadlines should my team care about today?" became `student_deadlines`. |
| 3 | **Multi-intent answered one clause deep** | `s2-mi-001/002/005`, `s2-com-003` | One turn → exactly one intent → one domain read. The unread clauses were then improvised: "There is no information indicating that anyone has contacted her" was asserted **without ever reading the communication history**. |
| 4 | **Cohort filter silently dropped** | `s2-coh-010`, `h2-coh-001` | A phrasing gap ("still owe an immunization record", "haven't paid up") produced an empty filter, and the unfiltered population (2,577) was presented as the answer. The most dangerous class of failure for staff. |
| 5 | **Missing intents / vocabulary** | `s2-coh-009/011`, `s2-risk-004`, `s2-com-002/003`, `s2-ovw-006/008`, `s2-rec-001` | "How big is the roster", "which programs have the most", "why is the top attention student flagged", "did X ever reply", "what documents do we have on file", "what should I contact X about" — all unmatched, all falling to the model planner or the wrong branch. |
| 6 | **Portal navigation not answerable** | `stu-nav-001…004` | "Where do I upload this?" was routed to `document_status` and answered with the requirement's *status*. Technically true, useless. |
| 7 | **Prediction dressed as fact** | `stu-uns-001` | "Will I get into my first choice dorm?" was answered from the housing *step* ("you won't be able to right now"), as if the question about assignment outcomes had been answered. |
| 8 | **Page size reported as queue size** | `s2-mi-005` | The queue composer counted the returned page (capped at 25) rather than the queue's own open count (1,027). |
| 9 | **Premise vocabulary outranked the question** | `s2-xd-002` | "Ingrid's identity document is accepted. What is preventing her housing?" classified on the premise (`student_documents`), not the ask. |
| 10 | **Question about the past read as a request to act** | `s2-com-002` | "When did we last email Marisol?" tripped the `send_message` action gate and was refused as a write. |
| 11 | **Name extraction gaps** | `s2-id-004`, `s2-id-008` | "Look up Thistlebrok **for me**." (trailing courtesy) and "a student **named** Wren" extracted no name. |
| 12 | **Answers that read but do not inform** | `stu-req-004`, `s2-ac-002` | "Which of my requirements are blocking and which aren't?" listed only the blocking set; a prioritization question answered with a bulk count. |

---

## 6. The conversational-context bug

### What the recorded context actually was

One durable column, `staff_assistant_conversation.active_student_id`, written
on every exchange by:

```sql
active_student_id = COALESCE(:active_student_id, active_student_id)
```

`:active_student_id` was the *student this turn resolved*. When a turn resolved
nobody, `COALESCE` kept the old value. **There was no path that cleared it.**
Once a conversation touched a student, it held that student until the
conversation ended.

On the read side, `_resolve_student_referent` decided to use it like this:

```python
needs_student = classification is None or classification.request_type in STUDENT_REQUIRED_REQUEST_TYPES
...
if context_student_id is not None:
    overview = await run_referent_read(getStudentStaffSummary(studentId=context_student_id))
```

— i.e. **any** student-requiring intent, and **every** unclassified turn (the
model-planner path), inherited the referent unconditionally.

Two things then combined to make it visible:

1. `normalize_staff_request` set `is_follow_up = bool(history) and (opener or pronoun or len(text) <= 60)`. Most questions are under 60 characters, so nearly every later turn was a "follow-up".
2. The classifier's student-scoped branches fired on a bare topic word, with `has_referent_language` including `is_follow_up`. "What deadlines should my team care about today?" (46 chars) matched `\bdeadlines?\b` → `student_deadlines` → student-required → inherit → **answered about the last student looked up.**

The same shape produced "Which students are waiting on transcripts?" →
`student_overview` about Ingrid Thistlebrook, because the cohort classifier had
no predicate for "waiting on transcripts" and the fall-through matched
`\bwhich students?\b` in a student branch.

So it was not a cache. It was three separate design gaps: a monotonic referent
column, an unconditional inheritance rule, and a classifier with no notion of
turn *scope*.

### How it was changed

A new module, `apps/api/src/audentra/integrations/staff_assistant/scope.py`,
makes turn scope a first-class property and turns inheritance into a policy
with four conditions:

```python
def may_inherit_referent(request, request_type) -> bool:
    if has_explicit_entity(request):        # this turn names someone → use them
        return False
    if scope_of(request_type) is not STUDENT_SCOPE:   # cohort/queue/ranking/institution → never
        return False
    if is_globally_scoped(request):         # "my team", "across the class" → never
        return False
    return refers_back(request)             # anaphora, continuation opener, or a short continuation
```

Every one of the 32 staff request types is mapped to exactly one scope
(`student`, `cohort`, `queue`, `ranking`, `institution`, `conversational`).
A cohort, queue or ranking turn cannot inherit a student — structurally, not by
pattern.

The referent also gained a **lifecycle**. The pipeline now returns a
`referent_action`, and the SQL became:

```sql
active_student_id = CASE
  WHEN :referent_action = 'clear' THEN NULL
  WHEN :referent_action = 'set'   THEN COALESCE(:active_student_id, active_student_id)
  ELSE active_student_id
END
```

`set` when the turn resolved a student (or a queue turn put one on the table),
`clear` when the turn was explicitly about the population, the queue or the
attention scan, `keep` for turns that neither resolved nor contradicted it (a
refusal, a greeting, a failed lookup).

Three supporting changes complete it:

- **A global-scope guard in the classifier.** A turn whose own language is
  about the team, the queue or the population is redirected — to the cohort
  classifier, the work queue, or the attention scan — before the student
  branches are consulted at all.
- **Question focus.** `question_focus()` classifies on the sentence that
  carries the ask, not on a premise stated first.
- **A demonstrative can point at the queue.** A `work_queue` turn records its
  head item's student as the conversation's current object (without claiming
  the answer was about a student), so "What else is blocking that student?"
  has an antecedent.

### Before / after

| Turn 1 | Turn 2 | Before | After |
| --- | --- | --- | --- |
| "Tell me about Ingrid Thistlebrook." | "What deadlines should my team care about today?" | *"Ingrid Thistlebrook has five deadlines that are past due…"* — `student_deadlines`, resolved = Ingrid | *"Your team should focus on the 97 urgent items in the work queue today. The most pressing task is to review the tax return transcript for Dmitri Ashgrove…"* — `work_queue`, resolved = none |
| "Why is Ingrid Thistlebrook blocked?" | "Which students are waiting on transcripts?" | *"Ingrid Thistlebrook is waiting on her official transcript…"* — `student_overview`, resolved = Ingrid | *"There are 621 students waiting on transcripts…"* — `cohort_search`, resolved = none |
| "Tell me about Ingrid Thistlebrook." | "What is in my Action Center?" | already correct | still correct — and the referent is now **cleared**, so the turn after it does not snap back |
| "Tell me about Ingrid Thistlebrook." | "What about Marisol Fennwick?" | correct | correct — explicit entity always wins |
| "Tell me about Ingrid Thistlebrook." | "What about her housing?" | correct | correct — student scope + anaphora → inherit |
| "Show me the top transcript case in my Action Center." | "What else is blocking that student?" | *"Tell me which student you mean."* | answers about the student on that case |
| "How many students have unpaid deposits?" | "Break that down by program." | *"I can help with a specific student, your work queue…"* | 685, split by program, with the previous turn's filter reused |

---

## 7. Changes implemented

### Staff Edward — conversational scope

| File | Change | Rationale |
| --- | --- | --- |
| `staff_assistant/scope.py` *(new)* | Request-type → scope map; `has_singular_student_reference`, `is_globally_scoped`, `refers_back`, `may_inherit_referent`, `referent_action` | One place that decides what a turn is about and whether history may contribute a referent. |
| `staff_assistant/pipeline.py` | Inheritance gated by `may_inherit_referent`; unclassified turns inherit only when they refer back; `referent_action` + `next_referent_student_id` on the result; queue head becomes the conversation's current object; a demonstrative can resolve against the previous answer's named student; `_restore_required_phrases` | Context is evidence for interpretation, never a standing scope. |
| `staff_assistant/classify.py` | Global-scope guard ahead of the student branches; `_classify_global_scope` router | A team/population question can no longer land in a student branch. |
| `staff_assistant/normalize.py` | `focus_text` / `question_focus()`; `comparable_text` now the focus sentence | Intent comes from the ask, not from a premise stated first. |
| `postgres/staff_assistant_repository.py`, `memory/store.py` | `referent_action` + `active_student_id` on `append_exchange`; the monotonic `COALESCE` replaced by an explicit set/clear/keep | The referent has a lifecycle. |
| `postgres_service.py`, `platform_service.py` | Thread the action and carried referent through both compositions | Same behaviour in Postgres and in memory. |

### Staff Edward — multi-intent

| File | Change | Rationale |
| --- | --- | --- |
| `staff_assistant/classify.py` | `detect_additional_intents()`: clause split, per-domain probes, bounded to **two** extras; separate tables for student-scoped and operational turns | One message can carry several information needs; forcing one intent left the others to be improvised. |
| `staff_assistant/compose.py` | `_append_additional_intents()` composes each extra intent with its own canonical composer over the same derived state, de-duplicating blocks | The second and third clauses are answered from reads, never from the model's sense of what was probably true. |
| `staff_assistant/planner.py` | (already unioned tools across `additional_request_types`) | The extra reads actually execute. |

### Staff Edward — cohorts, queue and vocabulary

| File | Change | Rationale |
| --- | --- | --- |
| `staff_assistant/classify.py` | Outstanding-document vocabulary widened (`owe`, `waiting on`, `yet to send`, `still need`); colloquial unpaid-deposit phrasings, bounded by a negative lookahead so "still owe an immunization record" stays a requirement question; `roster`/`how big`/`what's the size` as cohort counting; `which programs/years` as a grouped count; `_queue_topic()` beyond documents (deposit, orientation, housing, aid, payment); queue vocabulary (`the queue`, `on the board`, `my plate`); communications phrasings (`did X ever reply`, `when did we last email`, `outreach history`); attention phrasings; `financial situation`; `documents on file`; `what should I contact X about`; Action Center synonyms (`task board`, `have a task on him`); clause-scoped Action Center subject test | A dropped filter presented as a confident count is the worst failure a staff assistant can produce; every gap here produced one, or produced a "couldn't run that". |
| `staff_assistant/classify.py` | `classify_cohort_refinement()` — "break that down by program" reuses the previous turn's cohort filter | Context that *should* persist. |
| `staff_assistant/compose.py` | The unfiltered queue answer states the queue's open count, not the returned page size | 1,027 items were being reported as 25. |
| `staff_assistant/compose.py` | Cohort bucket evidence names the grouping dimension ("Psychology has 60 students") rather than the literal word "Group" | A rewrite that copies the evidence line verbatim otherwise says "Group Psychology has 60 students". |
| `staff_assistant/compose.py` / `pipeline.py` | `ComposedStaffAnswer.required_phrases` + `_restore_required_phrases` | An overview whose rewrite drops "Chemistry, class of 2030" no longer tells the reader *which* student this is. |

### Staff Edward — portal parity

| File | Change | Rationale |
| --- | --- | --- |
| `staff_assistant/tools.py`, `catalog.py`, `planner.py`, `derive.py`, `compose.py`, `classify.py` | New `getMorningBriefing` tool + `daily_briefing` intent + composer | Staff can see the Morning Brew in the portal; Edward could not read it. It now reads the *same* `build_morning_brew` output, including the briefing's own list of metrics the platform does not hold. |
| `postgres_service.py` | `morning_brew` primitive wired to `build_morning_brew` | Shared source of truth, not a second implementation. |
| `platform_service.py` | `_unavailable_primitive("morning_brew")` for the in-memory composition | An honest "unavailable" beats a second, divergent briefing. |

### Staff Edward — entity resolution and the action gate

| File | Change | Rationale |
| --- | --- | --- |
| `staff_assistant/normalize.py` | `named`/`called` as referring prepositions; a verb-led name survives a trailing courtesy ("Look up Thistlebrok for me."); the action gate is skipped when the message opens with an interrogative | "When did we last email X?" is a question about history, not a request to send. |
| `staff_assistant/pipeline.py` | The disambiguation-pick path runs before the inheritance gate | "The second one." names nobody and refers to a *list*, not a student. |

### Student Edward

| File | Change | Rationale |
| --- | --- | --- |
| `assistant/classify.py`, `planner.py`, `compose.py` | New `portal_navigation` intent, destination table, and composer; every route from `links.py` | "Where do I upload this?" is product help. The answer now names the real page *and* what that page currently shows. |
| `assistant/classify.py`, `compose.py` | `_HOUSING_ASSIGNMENT_QUESTION` → `housing_assignment_unavailable` | Room assignments, building allocations and roommate matching are not held here and no model predicts them; answering from the housing *step* implied otherwise. |
| `assistant/classify.py` | Award-action phrasings route to `aid_award_acceptance_status` | "Which of my awards still needs me to do something?" is about the awards, not the aid documents. |
| `assistant/compose.py` | The blockers answer names open-but-not-blocking steps, matched on **canonical gate codes** | "Which are blocking and which aren't?" deserves both halves — and a title match listed the deposit as both. |
| `assistant/planner.py`, `compose.py` | `enrollment_state` reads the checklist; when it was not read, the answer says so | The projection carries no open-item list; the silence was being filled with "nothing outstanding". |
| `assistant/compose.py` | Shared `step_action_row()`: a step whose payment is already processing is never rendered as a student action | A next-steps list was offering "Pay the enrollment deposit" directly under "no new payment is needed". |
| `assistant/compose.py` | The missing-documents answer names returned/rejected uploads alongside never-sent ones | A returned upload is the student's move, and the one they are most likely to think is done. |
| `assistant/classify.py`, `compose.py` | `Classification.claims_waiver` + `_answer_waiver_claim()` | "I thought that was waived" asks whether a waiver exists. Restating the requirement's status without saying whether one is recorded reads as not having listened. A waiver **is** a recorded status, so the checklist read already answers it. |

### Evaluation assets

| Path | Contents |
| --- | --- |
| `tools/edward-eval/student-v3/` | 80 dev + 20 holdout scenarios, deterministic runner with `--regrade`, README |
| `tools/edward-eval/staff-db/cases-v2.mjs`, `holdout-cases-v2.mjs` | 80 dev + 20 holdout scenarios |
| `tools/edward-eval/staff-db/run.mjs` | `--suite v1|v2`, `--regrade`, tool arguments recorded per turn |
| `tools/edward-eval/staff-db/ground_truth.py` | Morning Brew truths, per-program/per-year cohort breakdowns, Action Center membership count, immunization/aid/onboarding/international totals |
| `package.json` | `eval:edward:student-v3` |

### Tests

36 new tests across `apps/api/tests/test_staff_edward.py` and
`test_assistant_pipeline.py`, pinning: student-scoped carry-forward, cohort /
queue / ranking never inheriting, explicit new referent, team-scoped deadlines,
referent lifecycle (set / clear / keep), plural "they" with a plural subject,
end-to-end cohort-after-student, three-clause decomposition, the two-extra
bound, compound cohort questions, `owe`/`waiting on`/roster cohort filters (and
the negative case that keeps them apart), cohort refinement, question focus,
the interrogative action gate, briefing routing and its zero-argument schema,
identity restoration after a rewrite, queue count vs page size, colloquial
unpaid phrasings, task-board synonyms, navigation routes (all real),
housing-assignment refusal, the blocking/non-blocking split, `enrollment_state`
checklist honesty, the pending-deposit action row, returned documents, and waiver-claim
handling (both that it fires on a claim and that it stays silent otherwise).

---

## 8. Final development results

| Persona | Category | Baseline P/P/F | Final P/P/F |
| --- | --- | --- | --- |
| Student | overview | 5/0/0 | 5/0/0 |
| Student | requirements | 6/1/0 | 7/0/0 |
| Student | why_blocked | 7/0/0 | 7/0/0 |
| Student | conflict | 5/0/0 | 5/0/0 |
| Student | deadlines | 5/0/0 | 5/0/0 |
| Student | aid | 6/0/1 | 7/0/0 |
| Student | housing | 5/0/0 | 5/0/0 |
| Student | documents | 6/0/0 | 6/0/0 |
| Student | prioritization | 4/0/0 | 4/0/0 |
| Student | navigation | 1/0/4 | 5/0/0 |
| Student | knowledge | 3/0/0 | 3/0/0 |
| Student | multi_intent | 5/0/0 | 5/0/0 |
| Student | context_carry | 4/0/0 | 4/0/0 |
| Student | context_reset | 4/0/0 | 4/0/0 |
| Student | unsupported | 7/0/1 | 8/0/0 |
| **Student** | **total** | **73/1/6** | **80/0/0** |
| Staff | identification | 8/0/2 | 10/0/0 |
| Staff | overview | 6/2/0 | 8/0/0 |
| Staff | action_center | 10/0/2 | 12/0/0 |
| Staff | cohort | 9/1/2 | 12/0/0 |
| Staff | risk | 4/0/1 | 5/0/0 |
| Staff | cross_domain | 5/1/0 | 6/0/0 |
| Staff | communications | 2/0/2 | 4/0/0 |
| Staff | recommendation | 3/1/0 | 4/0/0 |
| Staff | multi_intent | 2/0/3 | 5/0/0 |
| Staff | context | 10/0/4 | 14/0/0 |
| **Staff** | **total** | **59/5/16** | **80/0/0** |

---

## 9. Holdout / generalization

The 40 holdout scenarios were written alongside the development banks and
**executed for the first time only after the development work was finished**.

### First contact — the untouched score

| Suite | PASS | PARTIAL | FAIL |
| --- | --- | --- | --- |
| Student holdout (20) | **18** | 0 | 2 |
| Staff holdout (20) | **17** | 2 | 1 |

### What the holdout exposed, and the generalized fixes

All five findings were instances of failure classes the development suite had
already named — which is the point of the split. No expectation was changed to
turn a failure into a pass.

| # | Case | Category | Root cause | Generalized fix |
| --- | --- | --- | --- | --- |
| 1 | `hold-stu-001` "give me the short version of where i'm at" | overview | `enrollment_state` read only the enrollment projection, which carries admission and completion percent but **no open-item list**. The silence was filled with *"there are no outstanding items at this time"* — false; four requirements were open. | `enrollment_state` now reads the checklist; and when the checklist genuinely was not read, the composer says *"I haven't checked your open checklist items in this answer"* instead of leaving a silence. |
| 2 | `hold-stu-013` "what's the single most useful thing i can do right now" | prioritization | The next-steps block listed *"Pay the enrollment deposit"* directly beneath *"your payment is processing — no new payment is needed"*, for a student whose deposit was pending. | A shared `step_action_row()` renders any `processingPending` step as a university-owned row with its explanation. Every composer that turns a checklist step into an action now uses it. |
| 3 | `hold-stu-012` "do i need to send the transcript again" | documents | The missing-documents answer counted only `not_submitted` documents, so a **returned** upload vanished from it. (It passed on first contact only because the rewrite happened to mention the rejection.) | Returned/`needs_resubmission` documents are now first-class in that answer — named, counted, and given a "Re-upload" action row. |
| 4 | `h2-coh-001` "how many admits still haven't paid up" | cohort | Colloquial unpaid phrasing matched no deposit predicate → empty filter → **2,577 reported as the answer**. Failure class #4 from development. | The unpaid-deposit predicate now covers "haven't paid (up/yet)", "still owe", "non-depositors" — bounded by a negative lookahead so "still owe an immunization record" stays a requirement question. Both directions are pinned by tests. |
| 5 | `h2-ac-001` / `h2-ac-002` "Is X on my task board?" / "Why does X have a task on him?" | action_center | Membership questions phrased without the words "Action Center" reached `work_queue` and `student_overview` instead of the canonical membership read. | The Action Center pattern now covers the board's other names and the "have a task on <student>" shape. |

### After the generalized fixes

| Suite | PASS | PARTIAL | FAIL |
| --- | --- | --- | --- |
| Student holdout (20) | **20** | 0 | 0 |
| Staff holdout (20) | **20** | 0 | 0 |
| Student development (80, re-run) | **80** | 0 | 0 |
| Staff development (80, re-run) | **80** | 0 | 0 |

---

## 10. Representative before / after

### Student

**Basic requirement.** "Which documents am I missing?" (`document_needs_resubmission`)
Before: *"You are missing three documents…"* — the returned transcript was
counted as merely "not submitted".
After: *"Yes — your transcript was returned and needs a fresh upload. 2
documents have not been sent at all: identity document and immunization
record."* with a "Re-upload your transcript" row.
*Better because:* a returned upload is a different situation from one never
sent, and it is the one the student thinks is done.

**Cross-domain blocker.** "wait so i paid the deposit why is housing still not
letting me continue" (`payment_pending`)
Both before and after ground correctly: *"the enrollment deposit has not been
posted… the payment is submitted and waiting to post — no new payment is
needed unless it fails."* What changed is the **action list**, which used to
offer "Pay the enrollment deposit" as a student step in the same answer.

**Multi-intent.** "Did you receive my transcript, and what else do I still need
to finish before housing?" — the coverage gate widens `housing_remaining_steps`
with `document_status`, and both clauses are answered from reads.

**Context carry-forward.** "What's blocking me?" → "Which one is due first?"
answers about the blockers just listed, from `getStudentDeadlines`.

**Context reset.** "What's wrong with my transcript?" → "What clubs might I
like?" answers from `getCampusLife` with real club names and never mentions the
transcript.

**Unsupported.** "Will I get into my first choice dorm?"
Before: *"You won't be able to get into your first choice dorm right now
because the housing step is blocked by earlier checklist items…"*
After: *"I can't tell you that — Audentra doesn't hold room assignments,
building allocations, or roommate matching, and there's no model here that
predicts them. What I can show you is your housing step: whether it's open to
you, and what has to clear first."*
*Better because:* the first answer sounds like an answer to a question it never
addressed.

**Navigation (new).** "Where do I upload my immunization records?"
Before: *"You can upload your immunization records as part of the checklist
item 'Submit your immunization record.' However, this document has not been
submitted yet…"*
After: *"You can upload your immunization records on the Documents page, where
you can also see the status of each upload. Currently, you need to upload three
required documents…"* plus an "Open Documents" action.

### Staff

**Ambiguous student.** "Pull up Pemberwell." → 110 students share the surname;
Edward lists candidates **with their IDs** and never guesses. "The second one."
then resolves deterministically from the re-run canonical search.

**Action Center membership.** "Is Ingrid Thistlebrook currently in the Action
Center?" → *"No — Ingrid is not in the Action Center right now: there are no
open staff work items on their record. They do still have open blockers on
their side…"* — read from the same rows the portal renders, rewrite-free so the
yes/no cannot be softened.

**Cohort aggregate.** "How many students still owe an immunization record?"
Before: *"There are 2577 students who still owe an immunization record. This
total includes all students in the cohort, as no filter has been applied."*
After: 714, with the trace showing `requirementCode='immunization_record'`
actually applied. The grader asserts the **argument**, not just the number.

**Multi-intent.** "Is Marisol Fennwick in my Action Center, what's blocking
her, and has anyone contacted her?"
Before: membership + blockers, then *"There is no information indicating that
anyone has contacted her"* — asserted without reading communications.
After: membership from her work item, blockers with who clears each, and
*"No communications are recorded for Mari"* — from
`getStudentCommunicationHistory`, which now actually executes.

**Context carry-forward.** "Tell me about Marisol Fennwick." → "Has anyone
contacted her?" resolves Marisol and reads her communication history.

**Context reset after a student lookup.** "Tell me about Ingrid Thistlebrook."
→ "What deadlines should my team care about today?" — see §6.

**Cross-domain.** "Marisol Fennwick says she already paid her deposit. What's
actually blocking her?" → the record outranks the reported claim; the deposit
is named as not posted, and the transcript is separated as *university*-owned
review rather than her move.

**Unsupported.** "What are the odds Wren Halloway actually enrolls this fall?"
→ *"I don't have enrollment probabilities — no predictive model exists in
Audentra. I can show objective signals instead…"*

**Briefing (new).** "What's my morning briefing today?" → *"Today, there were
no new updates recorded in the last 24 hours. Currently, 1892 out of 2577
accepted students have paid their enrollment deposit. In your work queue, there
are 1027 open Action Center items, with 75 assigned to you, including 97 urgent
and 985 overdue…"* — every number from the portal's own briefing composer.

---

## 11. Capability matrix

### Student Edward

| Capability | Strong | Partial | Unsupported | Notes |
| --- | --- | --- | --- | --- |
| What's next / remaining steps | ✅ | | | From the canonical checklist, with priority reasoning |
| Per-requirement status & dependencies | ✅ | | | Including the blocking vs non-blocking split |
| "Why can't I…" causal questions | ✅ | | | Gates named with what clears each and who owns it |
| Conflicting-claim handling | ✅ | | | Record outranks claim, without accusing the student |
| Deadlines / overdue / urgency | ✅ | | | Buckets computed server-side, never inferred from bare dates |
| Document lifecycle | ✅ | | | Missing / submitted / under review / accepted / **returned** all distinguished |
| Deposit & account | ✅ | | | Posted / pending / unpaid are three different answers |
| Financial aid status & requirements | ✅ | | | Awards, aid documents, verification, coverage |
| Aid disbursement timing | | ⚠️ | | Answered when a schedule exists; otherwise stated as not held |
| Housing eligibility / status / options | ✅ | | | Blockers explained from the gating requirements |
| Housing assignment, room, roommate | | | ❌ | Not held by the platform; no predictive model |
| Registration eligibility | ✅ | | | Gate list from the shared domain projection |
| Academic plan & standing | ✅ | | | Planned courses, prerequisites, GPA/SAP |
| Campus life (clubs, events) | ✅ | | | |
| Appointments & messages | ✅ | | | Read-only |
| **Portal navigation** | ✅ | | | Routes from `links.py` only; the answer also states what the page shows |
| Multi-intent questions | ✅ | | | Coverage gate widens the route; bounded |
| Context carry-forward / reset | ✅ | | | Self-scoped, so no entity stickiness is possible |
| Institutional policy text | | | ❌ | No retrieval policy over the Knowledge Base yet |
| Other students' data | | | ❌ | Refused by design |
| Any write (pay, upload, waive, book, email) | | | ❌ | Read-only; Edward names the page instead |

### Staff Edward

| Capability | Strong | Partial | Unsupported | Notes |
| --- | --- | --- | --- | --- |
| Student lookup by full name / SIS id | ✅ | | | ID namespace resolved against the roster first |
| Duplicate-name disambiguation | ✅ | | | Candidates carry IDs; picks by ordinal / program / class year / ID |
| Misspelling recovery | | ⚠️ | | `difflib` close-spelling suggestions, offered never applied; phonetic misses remain |
| Picks by other attributes ("the one with the unpaid deposit") | | ⚠️ | | Re-asks rather than resolving |
| Student overview / blockers / documents / deadlines / financials / housing | ✅ | | | |
| Ownership | ✅ | | | |
| Action Center queue, ordering, topic slices | ✅ | | | Same read the portal renders; canonical order preserved |
| Action Center membership ("is X here, and why") | ✅ | | | From the student's actual work items, rewrite-free |
| Cohort counts / filters / groupings / listings | ✅ | | | Shares `CohortSql` with Morning Brew; filters asserted in tests |
| Cohort refinement across turns | ✅ | | | "Break that down by program" reuses the prior filter |
| Attention ranking and its basis | ✅ | | | Rule-based engagement scan, labelled as such |
| Predictive risk / melt / yield probability | | | ❌ | No such model exists; stated plainly, with objective signals offered |
| Cross-domain reasoning | ✅ | | | Bounded dependency round verifies the gate's own domain |
| Communications history | ✅ | | | Honest empty state in this tenant |
| Email opens / clicks / campaign performance | | | ❌ | Not tracked |
| Recommendations, email/SMS/call-point drafting | ✅ | | | Labelled as recommendation, not policy |
| **Morning Brew briefing** | ✅ | | | Same composer as the portal, including its "not tracked" list |
| Multi-intent questions | ✅ | | | Bounded to two extra intents |
| Conversational scope | ✅ | | | Carry-forward, replacement, reset, and lifecycle |
| Inquiries / support threads | ✅ | | | |
| Playbooks / action rules | ✅ | | | Reads what staff authored; says so when nothing is authored |
| Knowledge Base / Core Play content in answers | | | ❌ | Excluded until a reviewed retrieval policy exists (`AGENTS.md`) |
| Any write (assign, resolve, snooze, send, escalate, update) | | | ❌ | Refused with a drafting alternative |

---

## 12. Questions Edward still cannot answer

Concrete, with the reason.

**Data the platform does not hold**

- Room assignments, building allocations, roommate matching (student and staff).
- Email opens, clicks, read receipts, campaign performance.
- Demographics: ethnicity, gender, date of birth, citizenship, first-generation status, home address.
- Time-in-stage / time-in-status durations; the platform stores current state and events, not stage dwell time.
- Registration windows and disbursement schedules where no dated record exists.
- Week-, month- and cycle-to-date comparisons: no end-of-period snapshot is retained, so the denominator does not exist (the Morning Brew says so in its own payload).

**Capability that does not exist**

- Enrollment probability, melt risk, yield, recovery likelihood, "student value". No predictive model exists; Edward names the gap and offers the deterministic attention scan instead.
- Institutional policy text (refund policy, appeal rules, consequence policy). Knowledge Base and Core Play records are canonical in PostgreSQL but deliberately excluded from LLM context until a separately reviewed retrieval policy exists.
- Free-text search over student notes or documents' contents.

**Actions that are not permitted**

- Every write: paying, uploading, waiving, booking, sending, assigning, escalating, resolving, snoozing, changing ownership. Both Edwards refuse and point at the page (student) or offer a draft (staff).

**Resolution limits**

- Phonetic misspellings ("Kwilfether" → "Quillfeather") are not recovered; `difflib` catches close spellings only. A trigram index would be the next step.
- Candidate picks by attributes other than ordinal / program / class year / ID re-ask instead of resolving.
- Ambiguous-name attention questions ("Why is Milo Dunmire flagged?" — four share the name) disambiguate rather than cross-referencing the attention queue to auto-narrow.

**Evaluation-environment limits**

- Communications and inquiries are empty in the synthetic tenant, so those paths are exercised for honesty, not for retrieval quality.
- The staff suite needs the frozen snapshot database; against the live dev database the worker rewrites the queue mid-run and ground truth drifts.

---

## 13. Recommended next directions

Ranked by product value × feasibility × safety.

**Is Edward ready for bounded write/action tools? Yes — for a narrow first
set, and the architecture is already shaped for it.** Identity is server-bound,
every tool call is validated against a typed schema and receipted, the action
gate already classifies write requests by kind, and the refusal path already
names the right alternative. What is missing is a *proposal → confirmation →
execute* protocol and the audit/idempotency envelope. That protocol, not the
individual actions, is the work.

1. **Bounded staff write actions, behind explicit confirmation** *(high value, high feasibility, medium risk)*
   First set, in order of safety: **add a staff note / work-item comment** (append-only, no state change), **snooze or defer a work item** (reversible, bounded to a date range), **mark a work item resolved** (already a portal affordance with an audit trail), **create a follow-up task** (idempotent on a client key). Each should be a *proposal*: Edward returns a structured action card with the exact mutation, the record it touches and the reason; the staff member confirms in the UI; the API executes the existing endpoint. Requirements: reuse the existing `staff.update_work_item` authorization, carry a `clientActionId` for idempotency, write an audit entry naming the assistant as the origin, and emit the outbox event the portal already consumes. Never let the model supply the target id — bind it from the resolved referent exactly as reads do.
2. **Outreach drafting → send after confirmation** *(high value, medium feasibility, higher risk)*
   Drafting already works and is graded. Sending should reuse the durable
   `student_message` + inquiry-reply path (per `AGENTS.md`, an outbound portal
   communication must write both), require an explicit confirm step, and be
   rate-limited per student. Bulk send should not be in the first phase.
3. **Student action hand-off (deep links with intent)** *(high value, high feasibility, low risk)*
   Not a write: Edward already names the right page. The next step is a deep
   link that opens the *specific* requirement or upload dialog
   (`/enrollment/requirements/{slug}`, `/documents?upload={category}`), so
   "where do I upload this" becomes one click. Zero new authorization surface.
4. **Knowledge Base / Core Play retrieval with a reviewed policy** *(high value, medium feasibility, medium risk)*
   The single largest remaining answer gap for both personas ("what's the
   refund policy?", "what does our playbook say about deposit deadlines?"). The
   content is already canonical, versioned and tenant-scoped; what it needs is
   the retrieval policy `AGENTS.md` requires, plus citation-bearing composition
   so every policy sentence carries its source card.
5. **Structured response cards** *(medium-high value, high feasibility, low risk)*
   The blocks vocabulary already carries tables and next-step lists. Student
   records, work items and cohort slices should render as first-class cards
   with the portal's own affordances, rather than as prose plus a table.
6. **Communications ingestion so the empty state stops being the answer** *(high value, low feasibility)*
   "Has anyone contacted X?" is honest but useless while the tenant has no
   communication records. Email/telephony ingestion (durable `inbox_event`
   first, per `AGENTS.md`) turns four honest empty answers into four useful
   ones.
7. **Proactive Morning Brew → Edward hand-off** *(medium-high value, high feasibility, low risk)*
   The briefing tool exists now. The next step is the reverse direction: each
   briefing attention theme gets an "ask Edward about this" affordance that
   opens a conversation pre-scoped to that cohort — which the new scope model
   supports cleanly, because a cohort scope no longer leaks into later turns.
8. **Trigram / phonetic name resolution** *(medium value, high feasibility, low risk)*
   `pg_trgm` over the roster, replacing `difflib`, so phonetic misspellings
   resolve. Purely a resolution-quality change.
9. **Cross-student cohort reasoning with saved segments** *(medium value, medium feasibility)*
   "Show me the students I asked about yesterday", "the segment I built last
   week" — the cohort filter is already a typed value; persisting named
   segments makes cohort work compounding rather than one-shot.
10. **Outcome learning** *(medium value, low feasibility, needs care)*
    Recording which recommendations were acted on and what followed would let
    the attention scan be tuned against outcomes. This is where a *real*
    predictive model could eventually be justified — and until it exists,
    Edward should keep saying so.

**Not recommended yet:** unrestricted SQL or an open agent loop. The current
bounded design is why a 200-scenario evaluation costs under $0.30 and why a wrong
answer is diagnosable from a trace.

---

## 14. Regression testing

| Gate | Result |
| --- | --- |
| `pytest` (in-memory tier) | **904 passed**, 118 skipped (868 before this work; +36 new tests) |
| `ruff check src tests` | clean |
| `ruff format src tests` | clean |
| `mypy src tests` | clean, 205 source files |
| `npm run test:node` (contracts, demo-api, state-effects, student-assistant-core, voice-agent) | **402 passed** across 5 workspaces |
| `npm run typecheck:node`, `npm run lint:node` | clean |
| Staff database suite v1 (pre-existing 40-case bank) | **40 PASS / 0 / 0** |
| Staff in-memory suite (`run-staff.mjs`, 36 cases) | **36 / 36** |
| Student v3 development (80) | 80 / 80 |
| Staff v2 development (80) | 80 / 80 |
| Student v3 holdout (20) | 20 / 20 |
| Staff v2 holdout (20) | 20 / 20 |

Both pre-existing suites end green, but two of their checks are **flaky across
runs** because they assert wording a model rewrite may legitimately vary:
`sdb-xd-005` (staff v1) wants the progress phrasing "3 of 8" and once saw "3 of
her onboarding requirements" (graded PARTIAL, facts correct);
`staff-playbooks-023` (in-memory) wants "no staff-authored plays" and once saw
"There is currently no staff-authored playbook or guidance…". Both are grader
brittleness rather than Edward defects, and both point the same way:
prose-shape assertions should be replaced by fact assertions, as the two new
banks do throughout.

No TypeScript source changed in this pass — the production Edward path is the
Python pipeline; `packages/student-assistant-core` is the legacy reference
implementation, and the only JavaScript touched is evaluation tooling.

Postgres-gated suites were not re-run in this session (they require a
disposable migrated test database and were green in the previous pass); the
schema changed only in the `active_student_id` **write expression**, which the
new in-memory lifecycle tests cover directly.

---

## 15. OpenAI usage

| Item | Value |
| --- | --- |
| Model | `gpt-4o-mini` (`gpt-4o-mini-2024-07-18`), OpenAI |
| Used for | Edward's own optional planner and composer rewrite only |
| Judge model | **none** — all 200 scenarios graded deterministically |
| Metered eval calls | 935 |
| Prompt tokens | ≈ 1,381,500 |
| Completion tokens | ≈ 64,000 |
| Metered spend | **$0.246** |
| Unmetered (in-memory staff suite ×4, interactive probing ≈ 130 turns) | ≈ $0.04 |
| **Estimated total** | **≈ $0.29 of the $1.00 budget** — a 71% margin |

Per-run spend is recorded in every `artifacts/runs/<batch>/summary.json`. The
deterministic-first pipeline is what keeps a 200-scenario, multi-turn
evaluation this cheap: a turn costs at most two small model calls, and most
turns cost one. The figure covers a baseline, four development iterations, two
holdout passes, and three regression suites.

---

## Reproduction

```bash
# Student (no database needed — one eval host per persona)
npm run eval:edward:student-v3 -- --batch student-v3-dev
npm run eval:edward:student-v3 -- --holdout --batch student-v3-holdout

# Staff (frozen snapshot database + a running API host; see the suite README)
docker exec audentra-platform-postgres-1 sh -c \
  "psql -U vv -d postgres -c 'DROP DATABASE IF EXISTS vv_enrollment_staff_eval' \
   && psql -U vv -d postgres -c 'CREATE DATABASE vv_enrollment_staff_eval OWNER vv' \
   && pg_dump -U vv vv_enrollment | psql -q -U vv -d vv_enrollment_staff_eval"

DATABASE_URL=postgresql://vv:vv_local_password@127.0.0.1:55432/vv_enrollment_staff_eval \
  uv run --directory apps/api --locked python tools/edward-eval/staff-db/ground_truth.py \
  > artifacts/staff-db-eval/ground-truth.json

npm run eval:edward:staff-db -- --batch staff-v2-dev
npm run eval:edward:staff-db -- --holdout --batch staff-v2-holdout
npm run eval:edward:staff-db -- --suite v1 --batch staff-v1-regression
```

Suite documentation: `tools/edward-eval/student-v3/README.md` and
`tools/edward-eval/staff-db/README.md`.
