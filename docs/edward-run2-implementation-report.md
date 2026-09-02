# Edward run 2: making the real Student and Staff Edward reliable (2026-09-02)

Follow-on to `docs/edward-luna-architecture-evaluation.md` (run 1). Run 1 built
the measurement (unseen `read-gen` bank, model/planner A/B controls) and found
that the regex-first architecture, not the model, was the ceiling. This run
changed the architecture and the tools so that the Edward the portals actually
call answers natural questions from canonical data, then re-measured.

Every figure below is a deterministic grade from a batch under
`artifacts/runs/` (name given). No LLM judge. OpenAI spend for this run:
**≈ $1.33** (itemised at the end).

## 1. What changed

### Read architecture: hybrid by default, for the real portal

`EDWARD_READ_PLANNER` now defaults to **hybrid** (`GatewaySettings.read_planner`,
`bootstrap/settings.py`), so the production `POST /v1/student|staff/assistant/messages`
path — the one both portals call — takes it. Hybrid means:

- the deterministic classifier keeps every confident, well-covered intent
  (fast, wording-stable, cheap);
- the **model read loop** (`integrations/assistant/read_loop.py`, from run 1)
  takes every turn the classifier cannot place, every safe-fallback turn,
  and — new this run — every staff question that spans two or more domains
  (appointments + requirements, queue + adviser, documents + ownership), which
  a single-intent classifier can never answer fully;
- identity, authorization, writes and the claim guard stay deterministic.

The compound student turn ("what's my preferred name? change it to Lucy and
set my pronouns") now gives its read half the same hybrid hooks as a plain
read turn instead of a hook-less pipeline.

### Entity and referent resolution (staff)

- **Lower-case and partial names resolve.** "which of petra oakenshaw's items
  are overdue", "pull up caleb dunmire", "who owns hana mossbank's next
  action" used to fall through ("tell me which student you mean") because
  mention extraction required capitalised names. Lower-case word pairs and
  possessive single words are now *speculative* mentions: they resolve only
  on an exact roster/directory hit and are dropped silently otherwise, so
  ordinary prose never becomes a "nobody named X" answer
  (`entities.py: _speculative_name_candidates`, a 248-word common-word filter,
  three candidates per turn).
- **A pasted student ID is a mention** (`SYN-001278` → roster lookup by
  external reference before the work-item namespace), and it wins over an
  ambiguous name in the same message.
- **Inline qualifiers pick among namesakes**: "Gustav Fennwick … the one in
  Economics", "(the Economics one)", a class year or an ID in the same message
  selects the candidate from the candidates' own canonical fields.
- **Staff-vs-student ties** ("Hana Dunmire" is both a Senior Academic Adviser
  and two students): a student-side fact in the question (enrollment status,
  academic adviser, deposit) now settles the tie for the roster; a staff-side
  noun after the name (Wednesday, schedule, phone, office, calendar, caseload)
  settles it for the directory.
- **Pronoun follow-ups on queue-scoped turns** ("who owns her housing item?"
  after a turn about Noor) are re-scoped to the active student instead of the
  tenant queue (`_rescope_pronoun_follow_up`).
- An honest refusal about a named student ("what's Petra's melt risk score?")
  keeps Petra as the conversation's referent, so the next question does not
  have to name her again.

### Student routing and evidence

- The acknowledgement gate no longer swallows questions that open like one
  ("ok whats overdue for me rn" → deadlines, not "Any time!").
- Reaching, seeing, booking or asking about an adviser/counsellor routes to
  the advising read whatever the verb ("how do i reach my international
  adviser", "any chance i can get in with my adviser this week??", "who's my
  adviser", "have I ever no-showed on anything?").
- The coverage gate learned two domains, **advising** and **appointments**, so
  "is my adviser available before my deposit deadline?" reads both.
- Support-style routes ("who do I talk to about housing stuff?") carry the
  adviser assignments as context (the housing coordinator is an assignment),
  and advising unavailability on those routes is no longer material.
- Evidence now carries what the reads already held but the composer never
  said: appointment staff names, titles and statuses (no-show, cancelled,
  completed, still to come), "no appointments on record", the distinction
  between an adviser's bookable slot and an appointment, the reviewer's note
  on a rejected document, and **today's date** ("Today is Wednesday 02
  September 2026 … dates before this have already passed") so "this week" and
  "before Friday" are reasoned correctly. A rejected document is now reported
  as rejected (it was folded into "under review").
- Loop turns report a real request type (the first read's domain) instead of
  `general_question`, so traces and evals stay comparable.

### Staff evidence and tools

- Open work items are listed with their **owner** in the student evidence
  bundle (`Open work item AST-…: … owner Yusuf Crane, due …`).
- `getStudentOwnership` is described as the adviser/counsellor read with
  employment status and leave dates; `getStaffCaseload` reads every
  assignment role by default (a financial-aid counsellor's "my students" are
  their aid assignments) and says that its per-student work counts include
  every owner.
- "oldest thing in my queue" sorts by creation, not due date.

### Write plane (unchanged design, four recognition fixes)

- "wait did that go thru", "is it saved now", "did the change stick" are
  recall questions and are answered from receipts (the casual spelling had
  fallen into the read plane, which answered "No, the change has not gone
  through yet" about a committed change).
- "open items", "tasks on my board", "her open item count" are reads:
  `open` creates only as a verb, and a noun-led "task for X" creates only at
  the start of a sentence.
- A partially parsed preference change ("change it to Lucy and set my
  pronouns") consults tier 1 for the fields tier 0 could not anchor, and the
  receipt wording no longer reads "your your".
- A named follow-up subject binds to a requirement under review ("about her
  transcript") instead of the most urgent unrelated item.

### Model strategy controls

`EDWARD_MODEL_OVERRIDES="edward_action_recognizer=gpt-4o-mini"` pins a cheap
model to one operation while the host runs luna; `EDWARD_REASONING_EFFORT`
and `_OVERRIDES` set effort per operation. The recommended luna host:
`OPENAI_MODEL=gpt-5.6-luna EDWARD_REASONING_EFFORT=low
EDWARD_REASONING_EFFORT_OVERRIDES=edward_action_recognizer=none
EDWARD_MODEL_OVERRIDES=edward_action_recognizer=gpt-4o-mini`.

### Portal: Action Center and Task Board

See section 4.

## 2. Root causes found

| # | Symptom | Root cause | Fix |
|---|---|---|---|
| 1 | Lower-case names → "which student do you mean" | mention extraction required capitalised names | speculative lower-case mentions resolved against the roster |
| 2 | "who owns the open items for SYN-001278" → tenant queue | pasted IDs were scrubbed out of mentions; the intent stayed queue-scoped | ID tokens are mentions; resolved student re-scopes the turn |
| 3 | "who owns her housing item?" → tenant housing stats | queue classifier + student-only inheritance policy | pronoun re-scope to the active student |
| 4 | Staff+student namesakes always ask "which do you mean" | tie-break ignored the question's own domain | student-side facts vs staff-side nouns decide |
| 5 | "ok whats overdue for me rn" → "Any time!" | closing/ack regex accepted trailing text | ask markers and domain hints veto the ack |
| 6 | Adviser questions → help page / support | adviser phrasings only matched "advisor" and "appointment" | adviser-contact route; advising + appointments coverage domains |
| 7 | Immunization "under review" though rejected | document-state join had no rejected branch | `rejected` state with reviewer note |
| 8 | "did that go thru" → "No, not gone through" | recall regex missed casual spellings | widened recall matcher (tested) |
| 9 | "Sept 4 … not this week" (today was Sept 2) | composer never saw today's date | today line in evidence |
| 10 | "no advisees with overdue tasks" (55 existed) | caseload tool defaulted to primary-adviser role | all roles by default; description states count semantics |
| 11 | "Count the escalated items…" → follow-up creation | `open items`, `tasks on` matched create patterns | verb-gated create patterns |
| 12 | Action Center showed one task | personal queue derived from the roster's top-item owner | queue read `assignee=me`, attention order, server counts |
| 13 | Task Board badge 2,330 for everyone | tenant-wide todo+in_progress | Mine / My team / Everyone scopes, badge = mine |

## 3. Final Edward architecture

```mermaid
flowchart TD
  M[message + durable conversation] --> N[normalize · safety gates · injection prefilter]
  N --> W{write recognition<br/>tier 0 regex → continuation → tier 1 enum (gpt-4o-mini)<br/>partial parse → tier 1 fills fields · recall questions → receipts}
  W -- action --> AG[Action Gateway (deterministic)<br/>resolve target · authorize · preview · confirm · execute · receipt]
  AG --> RH[read half of a compound turn → hybrid route]
  W -- read --> ID[identity · entity + referent resolution<br/>capitalised, lower-case (speculative), pasted IDs, inline qualifiers,<br/>staff/student tie-break by question domain, pronoun re-scope]
  ID --> C{regex classifier + coverage gate}
  C -- confident, single-domain --> T[static tool table · bounded dependency round]
  T --> K[deterministic derive + compose<br/>evidence incl. today, appointment staff/status,<br/>document reviewer notes, item owners]
  K --> RW[luna prose rewrite]
  C -- unplaceable · fallback · multi-domain --> L[luna read loop ≤3 rounds<br/>handles bound server-side · args validated · results humanised]
  L --> G[claim guard over evidence / flattened results]
  RW --> G
  G -- reject --> K
  G -- accept --> OUT[answer · receipts · trace (readPlanner, readLoop)]
```

Deterministic: identity, authorization, entity binding, write recognition
tier 0, the Action Gateway, the claim guard, refusals. Model: routing of the
long tail, reasoning over results, composition. luna at `low` for the loop
and composer; gpt-4o-mini (or luna at `none`) for the enum recognizer.

## 4. Action Center / Task Board findings and fixes

Findings (DB `vv_enrollment_manual`, tenant aster-demo):

- The **Action Center showed a single card per student and only some
  students** because `personalActionCenter` was derived from the 1,000-row
  workspace roster filtered to students whose *single top-priority open item*
  was owned by the viewer. Greta Radcliffe owns 58 open items on 55
  students; 8 students were shown. Matthias Gunnarsson owns 276; 53 shown.
  Priya Shah owns 0; nothing shown.
- The **Task Board badge read 2,330** = tenant-wide `todo + in_progress`
  (1,752 + 578; blocked 200 not counted; open total 2,530; unassigned 158).
  The board itself is a shared tenant queue with assignee/component filters
  defaulting to everyone, so institution-wide work looked personal.

Fixes (backend + portal, all server-computed):

- `GET /v1/staff/action-center` returns `scopes {mine, myComponent, all}`
  (14 measures each, one SQL aggregate) and every item carries
  `viewerAssignmentRoles` (the viewer's caseload role for that student); a new
  `sort=attention` (escalated → overdue → priority → due).
- `personalActionCenter` is the first page of the viewer's own queue in
  attention order with `queue {total, hasMore}` and counts from
  `scopes.mine`.
- Task Board: **Mine / My team (component) / Everyone** switch with server
  counts; defaults to Mine when the viewer owns anything (else team, else
  everyone); column headers use the active scope; cards show owner
  ("You · name" / Unassigned), component, student, due/overdue and "Your
  advisee (financial aid)". Sidebar badge = the viewer's own open count, with
  the institution total as secondary text.
- Action Center: header metrics open / overdue / due today / awaiting
  outcome for Mine; the full personal queue with "Start here" on the top
  item and load-more paging; an explicit empty state for members who own
  nothing with links to their team's and the unassigned queues.
- Students view: the viewer's own role for the student and the owners of the
  student's open items.

Verified live (Greta: "Mine · 58", "Financial Aid · 475", "Everyone · 2,530";
"Open · Mine 58 across 55 students"; screenshots in `portals/.ua/shots/`).

## 5. Results

### Unseen read bank (`read-gen`, 128 dev cases, SQL ground truth)

| Configuration | pass / partial / fail | pass % | halluc | entity | USD | p50 ms |
|---|---|---|---|---|---|---|
| run-1 start: gpt-4o-mini, deterministic (`rg-dev-4o-deterministic`) | 58 / 3 / 67 | 45.3 | 5 | 13 | 0.045 | 1342 |
| run-1 end: luna, hybrid (`rg-dev-luna-hybrid`) | 70 / 4 / 54 | 54.7 | 5 | 10 | 0.111 | 2154 |
| run-2 after entity/referent/evidence fixes (`r2-rg-dev-luna-hybrid`) | 83 / 6 / 39 | 64.8 | 6 | 1 | 0.096 | 2775 |
| run-2 after routing/coverage/multi-domain fixes (`r3-…`) | 91 / 6 / 31 | 71.1 | 4 | 2 | 0.126 | 3023 |
| run-2 after sort/tie-break fixes (`r4-…`) | 93 / 7 / 28 | 72.7 | 5 | 2 | 0.135 | 3029 |
| **run-2 final code, luna hybrid (`def-luna-hybrid-rg-dev`)** | **94 / 6 / 28** | **73.4** | 3 | 2 | 0.125 | 2630 |
| run-2 final code, gpt-4o-mini hybrid (`final-4o-hybrid-rg-dev`) | 85 / 7 / 36 | 66.4 | 3 | 2 | 0.078 | 1336 |
| holdout (29), luna hybrid, final code (`last-luna-hybrid-rg-holdout`; an earlier final-code run scored 17 / 2 / 10) | 16 / 2 / 11 | 55.2 | 0 | 2 | 0.021 | 2045 |
| holdout (29), run-1 luna deterministic | 15 / 1 / 13 | 51.7 | 0 | 4 | 0.016 | 2290 |

By category, final code (pass / cases; run-1 gpt-4o-mini deterministic in
parentheses): adviser contact 9/10 (4) · availability 7/8 (4) · deadlines &
blockers 9/12 (8) · documents 6/8 (6) · status overview 4/8 (3) ·
appointments 8/8 (5) · Action Center & Task Board 10/12 (5) · ownership 5/6
(3) · recent changes 5/5 (3) · cross-entity 7/8 (3) · multi-intent 6/8 (4) ·
multi-turn 7/10 (3) · ambiguous/incomplete 5/8 (3) · honesty 3/6 (0) ·
authorization 2/6 (3) · unsupported actions 1/5 (1). Students 35/52 (was 18),
staff 59/76 (was 40). Failure classes: query 13, composition 8, hallucination
3, entity 2, tool 1, action 1 (was query 22 / composition 16 / tool 13 /
entity 13 / hallucination 5).

### Tuned banks and write suites (final code, luna hybrid)

| Suite | run-1 best (luna, deterministic route) | run-2 final (luna, hybrid) |
|---|---|---|
| student-v3 dev / holdout | 75 / 0 / 5 · 19 / 0 / 1 | 72 / 0 / 8 · **20 / 0 / 0** (`def-luna-hybrid-student-v3-*`) |
| staff-db v2 dev / holdout | 66 / 1 / 13 · 16 / 0 / 4 | 62 / 1 / 17 · 16 / 0 / 4 (`last-luna-hybrid-staffdb-dev`, `final-…-holdout`) |
| university dev / holdout | 133 / 0 / 8 · 20 / 0 / 3 | 131 / 0 / 10 · **21 / 0 / 2** (`last-luna-hybrid-univ-dev`, `final-…-holdout`) |
| write dev / holdout | 118 / 0 / 0 · 38 / 0 / 0 | 117 / 0 / 1 · 38 / 0 / 0 (`final-luna-write-*`; the one failure — a bare "change my name" previewing the value "my name" through the new tier-1 completion — was fixed after the batch and is pinned by a unit test) |
| write-gen (frozen, 201) | 182 / 18 / 1 | **184 / 17 / 0** (`final-luna-write-gen`) |

Reading the tuned-bank deltas: every case that moved is listed in the
transcripts. student-v3's eight dev failures are six correct answers in
wording the bank does not accept ("not shown as paid", "$0 in accepted aid",
"has not been started"), one negation false positive ("there is *no*
registrar hold") and one clarifying question on a seeded-history follow-up.
staff-db's four new dev failures are two wording mismatches, one luna loop
answer that covered two of three asks, and one luna context-bleed (a prior
student named in a cohort answer). The university bank moved −2 dev / +1
holdout: the multi-domain trigger sends a few precise queue-aggregate
questions to the loop, which the exemption for aggregate intents (added
after `last-*` ran) narrows further. Write behaviour is unchanged except the
recognition fixes above; the tier-1 field-completion regression was caught
by this pass and fixed. The final tie-break for possessive lower-case names
("greta everlyn's academic adviser") landed after the last batch; it is
covered by unit tests and a live probe (section 6).

### What still fails on the unseen bank, and why

Of the 28 dev failures on the final code, 17 are correct answers the bank's
regexes reject (refusals phrased "I can't access or reveal another student's
information", "no academic adviser is currently assigned", "not shown as
paid", "$0 in accepted aid", a draft email correctly labelled draft-only,
a caseload-scope denial the bank expected as a proposal, a GPA that the
snapshot does hold). The remaining eleven are real:

- **Namesake tie on a possessive lower-case name** (`rg-auth-005`,
  `rg-sts-006`): fixed after the batch (pattern required "who is", which the
  generic-lookup scrubber removes); verified live.
- **Multi-part answers that drop a clause** (`rg-mi-006`, `rg-mt-010`): luna
  in the loop sometimes answers two of three asks; the prompt now demands one
  read per part.
- **Misspelled staff names** (`rg-amb-008`, "Gunnarson"): the staff directory
  search has no fuzzy suggestion path (students do).
- **Ownership by topic** (`rg-uns-005`, "reassign Kwame's housing item to
  me"): the update action still needs a work-item key.
- **Ordinal follow-ups over a mixed past/upcoming list** (`rg-mt-003`).
- **Owner names in the student Action Center composer** (`rg-act-005`):
  evidence carries them, the deterministic draft's sentence does not.
- **Bookable-slot dates in a "no appointments" answer** (`rg-amb-005`): honest
  but the bank forbids any date there.

## 6. Examples (all verified against the database)

**Student, read** — "who's my adviser" (British spelling): before, "I cannot
find your adviser"; after, "Your adviser is Caleb Mossbank …
caleb.mossbank.adv4@synthetic.aster.example" (`student_staff_assignment`).

**Student, read, follow-up with relative dates** — "who's my adviser and can
i see them this week" (asked Wednesday 2 Sept): before, "the next available
open slot … September 4 … which is not this week"; after, "You can see him
this week; he has an open slot on September 4 at 6:30 PM that you can book."

**Student, compound read + write** — "whats my name on file rn? change it to
Lucy pls, pronouns she/her": reply answers the name (Lucia) and previews
both fields; after confirm `student_profile` reads Lucy / she/her; "wait did
that go thru" → "Yes — I changed your pronouns to she/her" (before: "No, the
change has not gone through yet").

**Staff, read, lower-case name + cross-domain** — "does petra oakenshaw have
an appointment with her adviser before her earliest overdue requirement":
before, "Tell me which student you mean"; after, resolved SYN-… and answered
from appointments + requirements through the loop.

**Staff, read, namesake with qualifier** — "Who's handling Gustav Fennwick's
transcript review? The one in Economics.": before, "I found 2 students …
which one?"; after, resolves the Economics Gustav and names the reviewer.

**Staff, read, pronoun after a queue turn** — "who owns her housing item?":
before, tenant-wide housing statistics; after, the item's owner on the active
student.

**Staff, write** — "Add a follow-up for Lucia Zephyrine SYN-001278 about her
transcript, due Friday, and assign it to me": preview targets "Submit your
official transcript" (was: financial-aid verification); confirm creates
`MAN-…` in `staff_work_item` (todo, high, due Friday, assignee Greta); the
Task Board search shows it; "did that go through?" answers with the key.

**Staff, read, false write** — "tasks on my board that are in progress":
before, "Which student is the follow-up for?"; after, the in-progress items.

## 7. Model strategy conclusions

- **Recognizer (closed enum):** gpt-4o-mini and luna@none tie (35/38);
  luna@low is worse (33/38) and slower. Pinned to gpt-4o-mini via
  `EDWARD_MODEL_OVERRIDES`.
- **Deterministic route composer:** luna and gpt-4o-mini are within a few
  cases on the tuned banks; luna is more specific and follows instructions
  literally (which is why stale prompt lines had to go).
- **Read loop:** once the architecture lets the model see results, the model
  matters: luna hybrid 93 vs gpt-4o-mini hybrid 85 on the unseen bank (same
  code, same day), with luna better on cross-entity, multi-turn and
  ambiguous cases. Cost ≈ 1.7×, p50 ≈ 2.3×.
- **Full model planning of every turn** remained worse than hybrid for both
  models (run 1), so it is not the default.

## 8. Remaining failures and next steps

What Edward still cannot do reliably, and what to do next:

1. **Fuzzy staff names** — add close-spelling suggestions to `searchStaff` as
   the roster already has.
2. **Work-item targeting by student + topic** for updates ("her housing
   item") — resolve the unique open item in the gateway before asking for a
   key.
3. **Multi-part staff questions through the loop** — luna occasionally
   answers two of three asks; a per-ask completeness check against the plan
   (deterministic) would catch it before the answer is sent.
4. **Context bleed on cohort answers** — hand the composer scope-filtered
   evidence (drop the prior student's lines on cohort/queue turns).
5. **Bank wording** — 17 of the 28 remaining dev "failures" are correct
   answers; the read-gen bank should accept the platform's canonical
   vocabulary before it is used as a regression gate.
6. **Holdout is small (29) and noisy** (16–17 pass across two runs of the
   same code); grow it before reading it as a trend.
7. Roster `assignedStaffId` / `recommendedAction` still mean "owner of the
   top item"; only their use in the personal queue was removed.

## 9. OpenAI spend (this run)

| Purpose | Batches | USD |
|---|---|---|
| Iterations on the unseen bank (luna hybrid, dev) | `r2-`, `r3-`, `r4-rg-dev-luna-hybrid` | 0.357 |
| Final pass, luna hybrid: read-gen dev + holdout, staff-db, university, student-v3 | `final-luna-hybrid-*` | 0.407 |
| Final pass, write suites (luna) | `final-luna-write-*` | 0.068 |
| gpt-4o-mini hybrid on the unseen bank (model comparison) | `final-4o-hybrid-rg-dev` | 0.078 |
| Definitive reruns on final code (read-gen dev, student-v3) | `def-luna-hybrid-*` | 0.196 |
| Re-measurement after the aggregate exemption (university, staff-db, read-gen holdout) | `last-luna-hybrid-*` | 0.178 |
| Live probes on the manual stack (write flows, entity checks) | — | ≈ 0.05 |
| **Total** | | **≈ 1.33** (cap $2.50; stop line $2.00) |
