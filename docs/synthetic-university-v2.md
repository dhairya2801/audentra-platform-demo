# Synthetic university v2 — a coherent institution Edward can reason about

Branch `feat/synthetic-university-v1` (worktrees `synthetic-university/platform` and
`…/portals`), 2026-09-03/04. Database: `vv_enrollment_synthu`, a copy of the mock
university (`aster-demo`, 2,577 students / 88 staff) enriched in place; the
enrichment is reproducible from the repository, no database dump is committed.

## 1. What existed, and what was missing

The mock university was a population without an institution. The generator
(`tools/demo-api/src/synthetic-university/generate.js`) produced 3,000 students
with applications, checklists, documents, aid, housing answers and SAP status,
and the University Explorer produced 88 staff with caseloads, calendars, work
items and inquiries. Every *rule* those records implied lived nowhere:

- Python Edward's `policy_lookup` intent answered every institutional question
  with a refusal ("I can only answer from approved sources, which I don't have").
  The only policy text in the repository was ten paragraphs inside the legacy
  JavaScript demo API, never wired to the platform.
- The knowledge layer that existed (`help_article`, `staff_knowledge_card`,
  `staff_core_play`) was empty for the tenant, capped at 2,000 characters per
  card, and read wholesale rather than retrieved.
- The data contradicted itself where rules were implied: 433 international
  students held Federal Pell Grants and Direct Loans; the tenant listed three
  generic housing options while 1,200 students' onboarding answers named rooms
  in six halls ("FER-327B") that appeared nowhere; every student had the same
  180-credit SAP maximum regardless of program length; there were six catalog
  courses for fourteen programs, no clubs, three events, and no offices,
  calendar or directory of any kind.

## 2. The institutional world (what was built)

Everything is synthetic and lives under
`apps/api/assets/config/tenants/aster-demo/knowledge/` (plus the two managed
YAML files next to it). The fact sheet every document agrees with is that
directory's `README.md`; the numbers there come from the generator, so the
corpus *explains* the population rather than redefining it.

| Layer | Content | Runtime home |
| --- | --- | --- |
| **Offices** (`offices.yaml`) | 20 units: the 12 that have staff in the platform (mapped to `staff_member.component`) and 8 that a real university needs but the platform holds no people for (Accessibility Services, Student Conduct, Counseling, Career Center, Library, Dining, Campus Safety, Title IX) — each with location, hours, mailbox, head title, service level and the topics it handles. | `institution_office` |
| **Academic calendar** (`calendar.yaml`) | 75 dated entries for Fall 2026, Spring 2027 and Summer 2027: term dates, registration windows and add/drop, refund steps, aid disbursement and census, housing application/priority/final deadlines, move-in, orientation sessions, international check-in, hold sweeps, breaks, finals, grades, commencement — each with audience, owning office and the document that explains it. | `academic_calendar_entry` |
| **Documents** (`documents/*.md`, 88) | 27 policies, 8 procedures, 6 handbook chapters, 14 program guides, 12 service guides, 1 directory, 12 internal staff procedures. Each has YAML front matter: kind, owning office, audience (student / internal / all), version, effective dates, `applies_to` facets, related documents, curated keywords. Bodies are sectioned Markdown (376 sections). | `institution_knowledge_document` + `_section` |
| **Program plans** (`programs.yaml`) | Department and core requirements (195 rows with recommended terms) for all 14 programs, against a catalog expanded from 6 to 107 courses (`academics.yaml`, published through the managed-content path). | `program.department`, `program_requirement`, `catalog_course` |
| **Help articles** (`help-articles.yaml`) | 8 short guides for the student Help page (its four fixed categories), each pointing at the fuller document. | `help_article` |
| **Campus life** (`campus-life.yaml` + enrichment) | 13 events across the year; 12 registered clubs with contacts, schedules and next activities. | `campus_event`, `student_club` |
| **Data coherence** (`audentra-enrich-university`) | Six real residence halls replace the three generic options and 1,202 residence answers are re-pointed from their room labels; 812 federal/state (and 175 need-based Aster Access) awards held by international students are re-sourced to the Aster Global Scholarship, International Campus Employment and a partner-lender loan (amounts and statuses kept, so every balance figure is unchanged); SAP maxima follow 150% of program length; Spring 2027 offers get a Spring response deadline. | rows in place |

The corpus covers admissions and the deposit; the enrollment checklist and
document standards; registration, course load, withdrawal and refunds, grading,
academic standing, transfer credit, transcripts, FERPA, residency for tuition,
name changes and the five holds; financial aid (application and verification,
awards and disbursement, SAP, scholarships, loans and work-study, international
aid, appeals); billing, tuition and fees, refunds, sponsors, insurance, 1098-T;
housing (residency requirement, application and waitlist, the six halls,
contract and cancellation, move-in, breaks and guests, roommates, meal plans);
immunization, exemptions, TB screening, health and counseling; international
check-in, I-20 and visa, F-1 enrollment, employment, travel and reporting;
orientation; advising, appointments and no-shows, change of major; the
general-education core and every program; conduct and academic integrity;
accessibility; campus directory, student services, clubs, ID and accounts; and
twelve staff-only procedures (document review with the tenant's six rejection
codes, deposit extensions, service levels and escalation, adviser leave and
departure coverage, help-request triage, verification processing, housing
waitlist and exemption review, international check-in, immunization review,
FERPA verification, the SAP appeals committee, hold placement and release).

Deliberate realism choices: deadlines are counted from the day the offer was
accepted exactly as the requirement engine counts them; the six document
rejection codes in the procedures are the tenant's real
`tenant_document_rejection_reason` rows; departmental service levels are the
University Explorer's; staff who hold roles in the population (the Assistant
Directors who cover leave, the Health Compliance Manager, the DSOs) are the
people the procedures name by title; no telephone numbers exist anywhere,
because none exist in the data and the frozen evaluation banks treat an
invented number as a hallucination.

## 3. How Edward uses it (design decisions)

**Storage and import.** Migration `0051_institution_knowledge.sql` adds four
tenant-scoped tables and `program.department`. `audentra-seed-knowledge
--tenant aster-demo` validates the directory (unique codes, every related
document and office resolves, facet vocabulary, dates, sections) and upserts
idempotently by content hash — an unchanged document is untouched, a removed
one is retired, never deleted. PostgreSQL stays canonical; YAML/Markdown is
seed input, exactly like the existing managed configuration.

**Retrieval policy** (`infrastructure/postgres/knowledge_repository.py`).
This is the "separately reviewed retrieval policy" the contributor guide
requires before institutional content enters model context:

- only `published` documents in their effective window; students see the
  `student`/`all` audiences, staff see everything;
- lexical and explainable: PostgreSQL full-text rank over sections (generated
  `tsvector` columns, GIN), a curated synonym map from student language to
  institutional language, bonuses for curated keyword phrases, title overlap and
  document kind — no embedding service in the request path, so a policy answer
  never depends on a second provider;
- **applicability from the record, not the model**: the student's residency,
  citizenship, first-year/transfer standing (inferred from class year vs admit
  term), admit term, housing plan, program and department are reduced to facets
  in one query; each document's `applies_to` is evaluated against them and the
  verdict (applies / does not apply / unknown, with the basis) travels with the
  hit;
- **situation-aware sections**: a document's section whose heading names the
  student's situation ("International students", "Transfer") is surfaced
  first; every section carries a highlight (the rows or sentences with the
  question's terms or the student's facet terms); Markdown tables are
  linearised into "header: cell" lines because composers misread pipes;
- bounded: at most 4 documents, 3 sections for the top one and 2 for the rest,
  clipped, plus up to 6 calendar entries (the student's own admit term first)
  and 3 matching offices.

**Tools.** Student `getInstitutionalPolicies` takes no identity argument (the
host binds the student; the question is the search text, a model read loop may
narrow it with `query`); staff `searchInstitutionalKnowledge` takes text
filters and a server-bound optional `studentId` so "does this apply to Petra?"
is answered with her facets. Both appear in the Lab's tool catalogue.

**Routing.** `policy_lookup` now reads the corpus plus enough record to apply
it. On every other intent a **policy augment** adds the read when the message
carries institutional language (consequences, permissions, exemptions,
amounts, dates, "who handles"), so "what happens if I miss the deposit
deadline" reads the deposit's state *and* the deposit policy. The coverage
gate no longer exempts policy questions, so "why can't I register and what are
the rules" also reads registration status. Staff-side, institutional
questions with no student in them route to the corpus (including definitional
"what is the SLA / how long does review take" questions the unsupported-metric
gate used to refuse), and the same augment applies to student-scoped turns.
Several pre-existing gates were narrowed because they swallowed policy
questions: the payment boundary ("how much is tuition for me" is not a
payment), the mutation detector ("if I change my mind" is an idiom), the
foreign-institution guard ("withdraw from the university" is this university),
and the course-grade refusal ("minimum grade for transfer credit").

**Composition.** Institutional evidence leads the evidence bundle (the
composer sees a bounded window; a rule at the end fell off), every line
carries provenance (code, version, effective date, owning office with location
and mailbox) and the applicability verdict, and the deterministic draft of a
record answer now carries a "Policy — <title> (v…)" sentence from the top
document's best highlight, placed after the first sentence so the 1,200-
character cap cannot trim it. The composer prompts tell both assistants to
cite the document, quote dates and amounts exactly, state applicability only
as the facts say, and name offices and options when asked. The read loop
accepts the bare `query: …` argument shape smaller models produce, and a bare
verdict ("No, you cannot.") after a policy read falls back to the
deterministic route.

## 4. Evaluation

Suite: `tools/edward-eval/knowledge/` — 59 development cases (62 turns), 18
holdout cases, and 114 model-paraphrased variants of the development
questions. Every case combines a real persona's record with the corpus and is
graded deterministically (facts as regexes or `{{date:…}}`/`{{gt:…}}`
templates over `ground_truth.py`, forbidden claims, required reads, entity
resolution); a second-opinion judge model scores groundedness and helpfulness
over the stored transcripts. Host: this branch on `vv_enrollment_synthu`,
hybrid read planner, gpt-4o-mini unless stated.

| Batch | Suite | Model | Cases | PASS | PARTIAL | FAIL | Pass rate | Forbidden claims | p50 / p90 ms | Edward spend | Judge g / h |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: | --- |
| kn-4o-dev-r1 | dev | gpt-4o-mini | 59 | 17 | 4 | 38 | 29% | 4 | 1484 / - | $0.038 | - |
| kn-4o-dev-r2 | dev | gpt-4o-mini | 59 | 33 | 6 | 20 | 56% | 2 | 1796 / - | $0.047 | - |
| kn-4o-dev-r3 | dev | gpt-4o-mini | 59 | 38 | 7 | 14 | 64% | 2 | 1939 / - | $0.047 | - |
| kn-4o-dev-r4 | dev | gpt-4o-mini | 59 | 38 | 10 | 11 | 64% | 4 | 1791 / - | $0.047 | - |
| kn-4o-dev-r5 | dev | gpt-4o-mini | 59 | 44 | 7 | 8 | 75% | 2 | 1947 / - | $0.055 | - |
| kn-4o-dev-r6 | dev | gpt-4o-mini | 59 | 48 | 8 | 3 | 81% | 1 | 2034 / - | $0.055 | - |
| kn-4o-dev-r7 | dev | gpt-4o-mini | 59 | 47 | 8 | 4 | 80% | 1 | 1845 / - | $0.058 | 0.92 / 1.24 ($0.049) |
| kn-4o-holdout | holdout | gpt-4o-mini | 18 | 8 | 2 | 8 | 44% | 1 | 1659 / - | $0.020 | - |
| kn-4o-holdout-r2 | holdout | gpt-4o-mini | 18 | 11 | 3 | 4 | 61% | 0 | 2128 / - | $0.022 | 0.74 / 1.00 ($0.015) |
| kn-4o-paraphrase | paraphrase | gpt-4o-mini | 114 | 64 | 10 | 40 | 56% | 9 | 1839 / - | $0.098 | 0.93 / 1.19 ($0.079) |
| kn-luna-dev | dev | gpt-5.6-luna | 59 | 46 | 5 | 8 | 78% | 1 | 3700 / - | $0.100 | 1.13 / 1.47 ($0.048) |
| kn-luna-holdout | holdout | gpt-5.6-luna | 18 | 8 | 1 | 9 | 44% | 1 | 4149 / - | $0.035 | - |
| kn-luna-holdout-r2 | holdout | gpt-5.6-luna | 18 | 12 | 3 | 3 | 67% | 0 | 4118 / - | $0.035 | 1.42 / 1.58 ($0.013) |
| kn-luna-paraphrase | paraphrase | gpt-5.6-luna | 114 | 66 | 7 | 41 | 58% | 7 | 3469 / - | $0.167 | - |

The judge pass over the gpt-5.6-luna paraphrase batch was stopped at 94/114 turns when the work was wrapped up, so that cell is empty; re-run `judge.mjs --batch kn-luna-paraphrase` to complete it.

Reading the table:

- **Development bank, gpt-4o-mini: 29% → 80%** over seven iterations
  (`r1`…`r7`), with forbidden claims falling from 4 to 1. The final code is
  `r7`; `r6` (81%) differs by one case that flips between runs.
- **Holdout bank (unseen phrasings and personas): 44% on first contact for
  both models**, 61% (gpt-4o-mini) and 67% (gpt-5.6-luna) after the marker
  fixes the first contact motivated, with zero forbidden claims. The three to
  four remaining failures are the same for both models: a summary sentence
  that carries the neighbouring deadline, a rule quoted without its
  documentary prerequisite, and a staff/student name collision.
- **Paraphrase bank (114 model-reworded development questions,
  expectations inherited): 56% (gpt-4o-mini) and 58% (gpt-5.6-luna)**,
  against 80%/78% on the originals. 22 of the 40 (4o) and 23 of the 41 (luna)
  failing turns never read the corpus: the rewording missed the lexical
  markers. The two models fail the same questions, which places the loss in
  routing rather than in the model.
- **gpt-5.6-luna is not a better composer here**: within two points of
  gpt-4o-mini on every bank at roughly twice the latency and cost.
- **The judge is stricter than the grader, and mostly right.** Over the final
  development transcripts it scores 24/62 turns fully grounded for gpt-4o-mini
  (29/62 for luna) and flags a third of the PASS turns. Reading its notes: the
  composer over-generalises a rule by dropping its exceptions ("freshmen
  cannot live off campus"), quotes the Fall date to a Spring entrant when one
  sentence carries both cohorts' dates (the immunization hold: 1 October 2026
  versus 1 March 2027 — the deterministic bank accepted the answer because
  its "hold" fact was soft), mislabels award types ("two scholarships" for a
  grant and work-study), and answers "who do I ask" questions from the write
  plane with no evidence recorded at all. Three of the 29 zero scores are that
  last artefact. The deterministic pass rate therefore overstates answer
  quality; the judge column is the honest one, and the next round of fixes
  should be driven by its notes, not by the grader.

Spend: every model call is metered in the transcripts. Edward calls across
all batches, the paraphrase generation, the judge passes and ad-hoc probes
come to about $1.16 (metered $1.027, plus about $0.13 for paraphrase generation, ad-hoc probes and the unfinished luna paraphrase judge pass) of the $2 cap (≥ $1 was required).

Iteration findings that changed the code or the data (all reproducible in
`artifacts/runs/kn-*`):

- **First contact was 17/59 (29%).** Two thirds of the failures were routing:
  policy questions never reached the corpus because pre-existing gates claimed
  them first (payment boundary, mutation detector, foreign-institution guard,
  course-grade refusal, unsupported-metric refusal) or because a domain intent
  read only the record. Each gate was narrowed to the request it was written
  for; the policy augment and the coverage-gate change closed the rest.
- **A model-planned turn passed `query: …` as the argument** rather than a
  JSON object, rejecting the read; the loop now accepts the bare pair.
- **The staff regexes were silently corrupted**: an earlier source edit through
  `re.sub` turned `\b` into backspace bytes, so "when is the add/drop
  deadline?" never matched the institutional pattern. Caught by the routing
  test; repaired byte-for-byte.
- **The composer's evidence window is 40 lines.** Long record answers pushed
  the policy lines off the end; institutional evidence now leads the bundle,
  and the deterministic draft carries a versioned "Policy —" sentence after
  its first sentence so the rewrite cannot lose it and the 1,200-character
  cap cannot trim it.
- **The claim guard rejected "75%"** because the corpus wrote "75 %"; the guard
  is right to be strict, so the corpus was normalised.
- **Markdown tables were misread** (a status mapped to the row below it);
  sections are now linearised into "header: cell" lines, every section carries
  a highlight (the rows or sentences with the question's rarer words or the
  student's facet terms), rate tables are scoped to the student's residency,
  and a section whose heading names the student's situation is surfaced
  first. "How much is tuition for me?" went from the cost of attendance
  ($47,400), to the in-state row ($18,400), to the right answer ($31,200).
- **Terse loop verdicts** ("No, you cannot.") after a policy read now fall back
  to the deterministic route, which carries the rule.
- **The staff member's own office** now weights table rows (Financial Aid's
  service level, not Enrollment Services'), and "left" expands to
  "departed/reassign" so a departed adviser's ten-day reassignment rule is the
  highlighted line.
- **Data, not code:** Spring 2027 offers carried the Fall response deadline (1
  September 2026), so a Spring admit was told his deposit was overdue; the
  enrichment gives Spring offers a 15 December deadline.
- **Same-name landmines** (four Omar Vellacourts, two Ivo Ravensworths) hit
  the staff cases exactly as the earlier banks warned; the honest
  disambiguation is correct behaviour, so those cases moved to unique names
  and one keeps the disambiguation as its expected answer.
- **Paraphrases expose the lexical markers.** 22 of the 40 failing paraphrased
  turns never read the corpus: rewordings such as "is my offer gone?" or "last
  day to drop a class without it showing up" miss the marker regexes. Both
  models fail the same shapes, which points at the routing layer, not the
  model; the next step is a learned or embedding-based "institutional
  question" classifier ahead of the deterministic markers.
- **gpt-5.6-luna ≈ gpt-4o-mini on this bank** (78% vs 80% dev, identical
  holdout first contact) at roughly twice the cost and latency; luna's
  remaining failures are the same routing shapes plus the write plane
  claiming "who do I ask" turns.

## 5. Example questions Edward now answers

Answers below are verbatim from the final development batch (gpt-4o-mini,
hybrid planner); every fact in them is traceable to a record read or a
versioned document.

**Student, Petra Oakenshaw (first-year, deposit unpaid and overdue):**
*what happens if I miss the deposit deadline?* → "If you miss the deposit
deadline, you will still keep your admission offer, but your housing
application will remain closed. This means that you will be placed behind other
students who applied for housing on time. According to the policy 'Enrollment
Deposit — amount, deadline, refund and forfeiture', the deposit deadline is an
institutional deadline, and missing it does not cancel your offer but does
affect your housing status. To proceed, you should focus on paying your
enrollment deposit as soon as possible." A follow-up *who do I ask for an
extension?* names the Office of Admissions.

**Student, Ivo Ravensworth (transfer):** *do I have to live on campus?* → "You
do not have to live on campus. According to the First-Year Residency
Requirement policy, this requirement does not apply to you as a transfer
student. Transfer students are not subject to the requirement and can apply for
housing as space allows after first-year students." The same question from a
first-year student is answered "yes", with the exemption grounds.

**Student, Lucia Zephyrine (international):** *how much is tuition for me?* →
"As an international student, your annual tuition is $31,200, which is billed
at $15,600 per term for 12–18 credits. Additionally, there is a mandatory fee
of $1,450…" — and *I'm international, am I eligible for a Pell grant?* (Kwame
Oakenshaw) → not eligible, cannot file the FAFSA, with the Aster Global and
Merit scholarships offered as the alternatives.

**Student, Omar Vellacourt (Spring 2027 admit):** *when does registration open
for me and when do classes start?* → "Registration for you opens on Monday,
January 4, 2027, at 09:00, as you are a new Spring admit. Classes for the Spring
2027 term start on January 19, 2027. Currently, your registration is blocked
because your enrollment deposit has not been posted…"

**Student, Noor Zephyrine (adviser on leave):** *my adviser is on leave — who
covers for her and how do I book?* → "Your adviser, Junia Pemberwell, is
currently on leave until 21 September 2026. During her absence, the Assistant
Director of First-Year Advising will cover her caseload. You can book an
appointment with this covering adviser through the Appointments page…"

**Student, Ada Kettleby:** *which office do I contact about a hold on my
account, and where is it?* → the Office of Student Accounts, Larkin Hall 120,
hours, mailbox, then her two open blockers. *what's the phone number for the
registrar?* → "…Aster offices do not publish telephone numbers in the portal
directory. You can contact the Office of the Registrar via email at
registrar@synthetic.aster.example, and their office hours are Monday to Friday
from 8:30 AM to 4:30 PM."

**Staff, Priya Shah (Admissions):** *can we extend Petra Oakenshaw's deposit
deadline?* → "Yes… Admissions counselors may grant one extension of up to 30
days for reasons such as financial hardship or pending external decisions, as
outlined in the 'Deposit Extension and Waiver Procedure'. However, this
requires a written request from Petra. Her deposit was due on September 1,
2026, and is currently overdue."

**Staff, Hana Dunmire (adviser):** *does the first-year residency requirement
apply to Petra Oakenshaw?* → "Yes… As a first-year undergraduate student
entering in Fall 2026, she is required to live in university housing for both
semesters… Since the exemption request deadline of August 22, 2026, has
passed…" The same question about *Ivo Ravensworth* is answered with "I found
2 students matching 'Ivo Ravensworth' — which one do you mean?"

**Staff, Aurelio Abernathy (Registrar):** *what is the procedure when a
transcript is rejected twice?* → escalation to the office director and a call
or virtual meeting within three business days, citing the Document Review
Procedure (staff) v2026.1. **Ulysses Abernathy (Housing):** *is financial
hardship a ground for a residency exemption?* → no, with the four grounds
listed and Financial Aid's special-circumstances review as the route.

## 6. Remaining realism and data gaps

- **No telephone numbers, no building maps, no room-level schedules.** Offices
  are reached by portal, email and visit; that is stated as policy.
- **Course sections and enrollments are not modelled.** Students have program
  plans and a catalog, but no registrations, grades or timetable; the
  registration window and add/drop exist as dates and gates only.
- **Payments are all `succeeded`.** "Submitted but not posted" is represented
  only by the requirement engine's `in_progress`; the deposit policy describes
  pending payments the data cannot show.
- **Offer response deadlines for Fall 2026 admits stay at 1 September 2026**
  (after classes begin) because the frozen read/write banks derive facts from
  them; Spring admits were corrected to 15 December.
- **Award amounts are the generator's** (a Pell of $29,000 exists). The
  scholarship handbook therefore describes eligibility and renewal, not maxima.
- **The corpus is one voice and one version.** Real institutions carry
  superseded versions, exceptions by college, and contradictions between
  offices; only `supersedes`/`effective_until` exist to model that.
- **No staff records for eight offices** (Accessibility, Conduct, Counseling,
  Career, Library, Dining, Safety, Title IX): Edward names the office and says
  the platform holds no people for it.
- **The action plane does not read the corpus.** A question the write plane
  claims ("who do I ask…" becomes a support-request offer) is answered by its
  own composer; those turns record no reads in the trace.
- **A summary can carry a neighbouring deadline.** The withdrawal policy's
  front-matter summary names the W deadline (2 April 2027); asked for the last
  day to drop *without a record*, Edward quoted that date instead of the
  add/drop deadline (29 January 2027) two lines below. The corpus is right; the
  highlight chose the sentence with the rarer words. Confusable date pairs
  need the calendar entry, not the summary, to lead.
- **Same names cross the staff/student line.** Greta Everlyn is both a Faculty
  Adviser and a student; the staff holdout case about her aid is answered
  with a disambiguation, which is correct behaviour and a failed case. The
  personas file should be checked against `staff_member` as well as
  `student`.
- **The write-plane recogniser still costs one model call per student turn**
  even on pure policy questions (visible in every trace as
  `action_recognizer: no_action`).

## 7. Recommended next steps

1. **Hybrid retrieval.** Add an embedding column (pgvector is not installed;
   an in-process cosine over ~400 section vectors is enough) behind the same
   policy, and measure on the paraphrase bank where lexical retrieval is
   weakest.
2. **Let the action plane read the corpus** before offering a support request,
   so "who do I ask" turns are grounded and traced like every other turn.
3. **Section-level citations in the portal.** The Lab already shows the tool
   result; the student answer should render "From *Enrollment Deposit* (v2026.2,
   Admissions)" as a block with a link to a Help-page reader over
   `institution_knowledge_document`.
4. **Model course sections and enrollments**, so registration gates, credit
   loads, F-1 full-time status and SAP pace are computed from real rows rather
   than stated.
5. **Version the corpus in the portal**: a staff editor over the same tables
   (the managed-content pattern), with `supersedes` and effective windows
   surfaced, and the Lab trace showing which version answered.
6. **Extend the bank** with the failure shapes the judge flagged: multi-office
   questions, exception paths ("I'm 21, can I live off campus?"), and Spring-
   admit calendar questions across every category.
