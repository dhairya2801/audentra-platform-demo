# Edward experience evaluation — baseline audit

Baseline revisions: platform `cdfcc44`; portals `5e17c12`, with pre-existing
uncommitted portal work preserved. Evaluation started 2026-09-12. The university
is a synthetic, PostgreSQL-backed 3,000-student world with clock 2026-09-08.

## Method and boundaries

The new runner uses real student/staff HTTP endpoints, authenticated demo actors,
an isolated copy of `audentra_university_v3`, durable conversations, and the same
tools, provider gateway, guards and persistence as the portal. Rubrics are kept
outside model context. The original university development scenarios are paired
with new informal, compound, corrective and adversarial conversations. Existing
holdouts are reserved; additional holdouts will be authored after implementation.

The evaluator observes full product answers and traces, then checks canonical
records. Keyword matches are diagnostic, not proof of successful resolution.
Record correctness, scope, grounding, useful next steps, unnecessary questions,
task completion and presentation are assessed separately. A safe refusal is not
automatically a useful response, and no answer counts as a successful write
without a server receipt and a corresponding state change.

All OpenAI requests on the evaluation API, including browser use, reserve spend
before transmission in `artifacts/edward-experience/spend.json`. GPT-5.6 Luna is
priced conservatively at $0.20 input/$1.20 output per million tokens (cached input
is counted at full price); uncertain calls retain their reservations. The tool
stops admitting calls at $9.80, leaving $0.20 headroom below the user's cap.
Pricing verified at https://developers.openai.com/api/docs/models/gpt-5.6-luna.

Raw transcripts/traces remain in ignored artifacts, consistent with repository
rules against committing captured provider responses. Commit evaluation inputs,
checks, aggregate results, and selected synthetic examples in the report.

## Actual request flow

HTTP auth and tenant binding → service-level safety/action recognition → durable
conversation and active staff referent → actor-bound tool host → pipeline.

Actions: deterministic patterns, pending-intent continuation, optional model
recognizer; server capability check, canonical target resolution, persisted
preview, explicit version/hash confirmation, transactional write, receipt.
Existing capabilities include student preferences/support and staff follow-ups,
work-item updates and email preparation. Hold release is explicitly unavailable.
Email preparation is not delivery. The read loop cannot execute writes.

Reads: legacy tenants retain deterministic classification and optional hybrid
fallback. University-backed tenants send substantive reads through the model
loop even when classification matched. Both student and staff loops allow three
planning rounds plus a final answer round. The model selects from bounded reads;
identity handles, argument validation, authorization and execution are code owned.
University tools read overview, academics, account, relationships, documents and
history. Policy retrieval uses tenant/audience/version/applicability constraints.
Other tools still expose portal checklist/advising/operations state.

The final answer is checked for evidence-backed numbers, dates, contacts, action
claims and some contradictions. Legacy structured blocks survive deterministic
composition; the university loop emits only a text block and a second text block
listing up to five retrieved policy citations. The prompt explicitly forbids
bullets/headings. Staff identity, entity/referent resolution and legacy scope
classification still gate which handles the loop receives.

History is durable, but the loop sees only six messages truncated to 1,200
characters each. Prior assistant claims are not part of the current evidence
corpus; a model answering from conversation alone can fail grounding. Traces
include observable tool inputs/results, timing, model usage, classification and
guard outcomes. They also expose model-generated `reasoning` strings; these
should become bounded decision summaries, not hidden chain-of-thought.

Student floating UI: narrow non-modal desktop window, modal mobile sheet,
conversation history, text/voice composer, static enrollment suggestions,
paragraphs and appended citations, links and confirmation cards. Staff has a
separate floating panel and embedded workspace; retry/new-conversation/focus
behavior is less complete. Main-page Ask Edward can navigate to the embedded
staff view. Neither current presentation prioritizes the answer and next step
independently of paragraph ordering.

## Observed failures before implementation

- **Contradictory progress:** verification-chain says “the shortest safe path is
  already complete,” then says verification is holding disbursement. The portal
  checklist marks verification completed, while the university workflow has five
  incomplete dependent steps. The student's relationship tool hides workflow
  stages entirely. A completed submission is mistaken for downstream completion.
- **Wrong help:** adviser-coverage recommends Admissions, Financial Aid and
  Housing instead of the designated academic cover. `getStudentAdvising` returns
  the absent primary adviser and other counselors; `getUniversityRelationships`
  has the actual cover. It also renders a 13:00 UTC slot as 1 p.m. without
  converting to New York time. The useful answer arrives only after a follow-up.
- **Lost follow-up:** “my friend said it clears instantly” produces a good-looking
  draft from history but no current read; the guard rejects $1,000/$250 as
  ungrounded, replacing everything with “Please narrow the question.”
- **Unnecessary support question:** “who do i talk to then” is classified as a
  support creation request and asks what the student needs help with despite the
  preceding payment discussion. An informational contact question is treated as
  an instruction to create a ticket.
- **Ignored explicit identity:** a staff housing question supplies a student UUID
  but department classification prevents binding it; the loop asks for the ID
  already supplied. A parent name in an explicit student question triggers a
  fuzzy student lookup that preempts the real question.
- **Overbroad injection refusal:** staff asking whether a quoted payment claim is
  reliable gets a refusal to act on quoted instructions. Student already has a
  narrow exception for this exact kind of fact-check; staff does not.
- **Guard representation mismatch:** a staff transcript response with canonical
  `progressPercent: 0` is entirely discarded because `0%` is absent from flattened
  evidence. Correct formatting is mistaken for an invented number.
- **Guessed alternative:** “and the other class instead?” after a CS 101 drop
  discussion selects MATH 151 despite multiple other enrolled classes.
- **Missing read:** a compound immunization/residency question says residency
  cannot be verified although university overview contains it.
- **UX:** long source codes and paragraph text compete with the next step;
  suggestions offer deposit payment even to a student whose deposit is paid.
  Several source/footer cautions repeat without making actual evidence easier
  to inspect. Record snapshot time is not consistently visible.

Positive evidence: pending/failed/reversed payments are generally distinguished;
profile proposals/corrections/cancellation preserve confirmation; simple named
cross-student access is refused; course-drop counterfactuals do not mutate state.
The targeted existing deterministic suite passed 168 tests (3 integration tests
skipped pending isolated test DB configuration). These passes did not reveal the
above live conversational failures.
