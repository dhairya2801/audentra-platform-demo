# `@vv/student-assistant-core`

Framework-neutral accepted-student onboarding assistant V1. The package is the
shared domain core used after a host application has authenticated the student
and loaded bounded conversation history.

```text
Edward text UI
  -> AssistantConversationService
  -> StudentAssistantGraph
  -> host-provided read-only adapters
  -> grounded structured response

Finalized voice turn
  -> existing internal voice-turn API
  -> AssistantConversationService
  -> the same StudentAssistantGraph
  -> TTS
```

## Architecture decision

The graph is implemented as explicit framework-neutral nodes. The repository
does not currently depend on LangGraph, and `AssistantConversationService`
already owns durable conversation history, leasing, and replay. Adding graph
persistence or a second history store here would create competing authority.

Every node is exported for focused tests:

1. `normalizeRequestNode`
2. deterministic mutation/sensitive-data safety gate
3. schema-constrained model intent and read-tool planning, with deterministic fallback
4. `executeToolReadsNode`
5. `deriveStudentStateNode`
6. `retrievePolicyNode`
7. `composeGroundedAnswerNode`
8. `validateGroundingNode`
9. response finalization in `StudentAssistantGraph`

Execution metadata contains node status, duration, tool names, and aggregate
counts only. It deliberately excludes message text, trusted identities, tool
records, provider errors, and credentials.

## Repository-native contracts and sources

The adapter boundary reuses existing public contracts and normalizes only data
that the repository currently exposes. Nest reads the authenticated tenant and
student from `PlatformStore`; preview reads the same account-scoped fictional
JSON state. Neither graph text nor model output can select an identity.

| Core tool | Current authoritative reads |
| --- | --- |
| `getStudentProfile` | `StudentProfile` |
| `getOnboardingChecklist` | `StudentRequirementList` |
| `getDocumentStatuses` | `StudentDocumentList` |
| `getEnrollmentHolds` | dashboard journey status; detailed requirement status/dependencies; academic-plan prerequisite gaps; financial-document action status |
| `getStudentDeadlines` | admission-offer `responseDeadline`; detailed requirement `dueAt`; financial-document `dueAt`; scheduled appointment `startsAt` |
| `getSupportOptions` | `StudentHelp` |
| `retrieveApprovedPolicy` | narrow `ApprovedPolicy` adapter result (no generic repository contract exists yet) |
| `getFinancialAidStatus` | `StudentFinancials.requiredDocuments`, amount-free `StudentFinancials.awards`, and the high-level `financial_aid_verification` requirement |
| `getFinancialAidSupportOptions` | tenant financial-aid support configuration with `/appointments`, `/financials`, and `/documents` fallbacks |
| `retrieveApprovedFinancialAidPolicy` | published, effective, tenant/offer-scoped policy excerpts from the financial-aid policy repository |
| `getStudentHousingStatus` | authenticated housing-plan preference, `housing_preference` requirement, and immediately redacted onboarding completion signals |
| `getHousingOptions` | tenant-listed residence preference options; preview equivalents are explicitly synthetic |

Tool results explicitly distinguish available data from unavailable,
conflicting, incomplete, timed-out, or failed reads. No tool can mutate state.
Each normalized record retains the current source receipt and preserves
`lastVerifiedAt` or source version only where its repository source provides
one. Reads and returned arrays are bounded.

The repository does **not** currently provide:

- an authoritative registration-eligibility decision or registration-blocking
  reason;
- an authoritative institution timezone (hosts must configure one; the
  repository default is the explicit `UTC` fallback);
- deadline hardness or consequence, so `hardOrRecommended` remains `null`;
- an official hold reason, owner, self-resolution flag, or hold-specific label;
- requirement-level onboarding verification timestamps or versions; or
- versions for appointment records.

The graph reports these gaps rather than synthesizing values. In particular,
“Why can't I register?” returns unknown registration eligibility alongside any
independently verified holds or derived blockers.

## Financial aid domain

Financial aid is a read-only domain in this same graph and conversation path.
Nest and preview use the shared `normalizeFinancialAidRead` mapper, then the
core deterministically derives completion, remaining work, missing documents,
review/verification state, award acceptance state, deadlines, blocked versus
ready work, and the next action. Completed items never remain outstanding, and
targeted worksheet or verification questions return only the matching item.

The structured `financialAid` result contains only current source-backed
fields: status, completed/remaining/blocked/ready requirements, missing
documents, verification status, amount-free award acceptance statuses,
deadlines, next action, support options, approved policy excerpt, unavailable
data, and receipt IDs. Award amounts, eligibility conclusions, filenames,
extracted fields, and document contents never cross this boundary.

Policy explanations are a separate read after an exact current requirement is
selected. The response labels the student's authoritative status, the
institution's approved explanation, and the deterministic next action
separately. Missing, mismatched, expired, unpublished, or unsafe-citation
policy data produces an unavailable result and routes to Financial Aid; it is
never replaced with generated policy prose. Preview policy/support records are
explicitly synthetic.

Supported questions include financial-aid status and remaining steps, reasons
for incompleteness, missing requested documents, worksheet verification state,
matching deadlines, amount-free award acceptance state, approved requirement
explanations, next action, and support. Current sources cannot determine aid
eligibility, explain why a student was selected for verification, calculate or
promise an award amount, approve a record, expose document contents, or perform
award/checklist changes. Those requests receive a bounded limitation plus an
approved portal or appointment route.

Sensitive financial or identity content pasted into chat is detected before
tool or model selection and redirected to secure Documents or support. Raw
provider response bodies for financial-aid and identity document extraction
are not retained in the diagnostic journal; only bounded attempt metadata is
stored.

## Housing domain

Housing is a dedicated read-only domain in the same graph, conversation, and
canonical text/voice response. The core deterministically combines the current
housing-plan preference with the `housing_preference` checklist record. A plan
is confirmed only when both sources agree that a preference exists and the
requirement is completed. Mismatches are reported as `conflicting`; completed,
waived, and not-applicable states never appear as remaining work.

Supported answers cover the selected living-plan preference, residence
preference (never an assignment), remaining housing checklist work, its
deadline, deterministic next action, the responsible office/requirement route,
general enrollment support, and tenant-listed residence preference options.
Residence listings are not room inventory and never imply vacancy, eligibility,
price, likelihood, or assignment. Preview listing content is labeled synthetic.

The repository has no authoritative housing application, room/residence
assignment, waitlist, housing agreement, housing deposit, or actual meal-plan
record. Those structured fields remain `unavailable`; the enrollment deposit is
never treated as a housing deposit. The graph also cannot answer vacancy,
pricing, eligibility, move-in, or accommodation approval questions.
Accommodation questions route to the bounded housing/general support path and
must not place health details in model context.

The housing adapter discards the full onboarding payload immediately. Only a
redacted roommate-preference completion state may cross the boundary; roommate
names/emails, lifestyle answers, accommodation/health indicators, protected
preference fields, media URLs, and free-form housing records are excluded from
facts, receipts, traces, and logs. Housing writes, allocation, roommate matching,
agreement acceptance, payments, waitlist changes, and preference changes are
not graph capabilities.

## Deadline semantics

- Admission-offer response deadlines remain date-only values and are due for
  the entire calendar date in the configured institution timezone. They are
  never coerced to arbitrary UTC midnight instants.
- Requirement, financial-document, and appointment timestamps are compared as
  instants and assigned to the institution's local calendar day.
- Buckets are `overdue`, `due_today`, `due_within_7_days`, `upcoming`, and
  `unknown_date`. “This week” means Monday through Sunday in the institution
  timezone; “upcoming” excludes overdue items.
- Satisfied/completed source records contribute only to the aggregate completed
  count and never appear in outstanding deadlines.
- Conflicting values for one source identity produce an unknown date and a
  `conflicting_data` result. Invalid or missing dates remain visible as
  `unknown_date` when the question permits them.
- Ordering is deterministic and chronological within explicit urgency groups:
  overdue, due today, nearest future date, then unknown date. Date-only and
  instant values share a comparable epoch sort key. The response preserves
  `requestedEntity`, `deadlineScope`, and `matchedDeadlines`, so a transcript,
  financial-aid, identity, deposit, housing, immunization, or orientation query
  cannot expand into the full deadline collection.

## Holds, blockers, and priority

`journey.status === "on_hold"` is the only current official-hold signal. Its
safe result intentionally has no invented label or reason. Requirement
dependencies and missing academic prerequisites are derived blockers. Rejected
document review and incomplete financial actions are warnings; an ordinary
incomplete requirement is not called a hold and is not emitted as a blocker.
Normalized blocker sources carry a bounded typed domain. General enrollment
and orientation questions exclude course-registration prerequisites; explicit
course-registration questions can use them. An ambiguous “Why can't I
register?” asks whether the student means course or orientation registration.
Official holds, derived blockers, and incomplete non-blocking requirements are
separate response fields.

“What should I handle first?” is computed without a language model. Official
hold support wins first; otherwise the dependency graph is followed to an
actionable prerequisite, then deadline urgency and blocking status are applied.
A dependency cycle produces a typed no-action result instead of a guessed
priority. The model may select from grounded facts for composition, but cannot
create or reorder the domain priority.

Priority responses also carry `priorityReasonCode` and receipt-grounded
`priorityEvidence` (deadline, local-day distance, unlocked dependencies, and
ranking basis). The hosts pass the last persisted structured priority back to
the same graph for “Why should I do that first?” and the graph refreshes its
evidence from current reads before explaining it.

## Trust and grounding boundary

- `tenantId` and `studentId` exist only in
  `TrustedStudentAssistantContext`, which the authenticated host creates. User
  text and model output have no identity selector.
- Conversation history is length/character bounded and is labeled as untrusted
  conversation text for model classification. It is never promoted to system
  instructions.
- When a model is configured, normal turns use a schema-constrained semantic
  plan containing one primary intent, up to two secondary intents, and at most
  eight allowlisted read tools. Cross-domain or unknown tool plans are rejected.
- Mutation requests and pasted sensitive financial data are stopped by
  deterministic safety gates before any model call. Missing, unavailable, or
  invalid planner output falls back to the deterministic intent/tool table.
- `graphExecution.toolSelectionSource` records `model_plan`,
  `deterministic_fallback`, or `safety_gate` without retaining prompt text.
- Completion, remaining work, blocker detection, document presence, priority,
  and deadline ordering are deterministic.
- Policy is read only for `explain_requirement`, and only after a requirement
  code has been matched to the current checklist.
- For explicit multi-part requests, the response combines grounded facts for
  the planned intents while preserving one bounded tool execution set.
- The composition model may select and order pre-grounded fact IDs. It cannot
  emit prose, links, tool arguments, or record mutations. Unknown fact IDs are
  removed before rendering.
- Every factual model-selected segment must resolve to an available context
  receipt. Model and tool failures use bounded deterministic responses.

## Host integration

```ts
import { createStudentAssistantGraph } from "@vv/student-assistant-core";

const graph = createStudentAssistantGraph({
  tools: backendReadOnlyStudentTools,
  model: configuredStudentAssistantModel,
  toolTimeoutMs: 2_500,
  institutionalTimeZone: "America/New_York",
  now: () => new Date(),
});

const result = await graph.execute({
  message: finalizedTurnText,
  context: {
    tenantId: auth.tenantId,
    studentId: auth.studentId,
    conversationId,
    inputMode: "text",
    history: authoritativeBoundedHistory,
    pageContext,
  },
});
```

The hosting service remains responsible for authorization, durable conversation
history, provider configuration, observability export, and transport-specific
rendering. Nest passes `STUDENT_ASSISTANT_TIMEZONE` (validated as an IANA name,
default `UTC`). Tests pass fake clocks directly to the graph.

The included Nest and preview adapters prefer direct OpenAI when
`OPENAI_API_KEY` is set and default to `OPENAI_MODEL=gpt-4o-mini`. If no OpenAI
key is configured, they retain the existing OpenRouter configuration and then
the deterministic fallback when no model provider is available.

Both Nest and preview persist/return the same `StudentAssistantResponse` for
text and finalized voice turns. Its canonical fields include deadlines,
official holds, derived blockers, non-blocking warnings, deterministic priority,
receipts, unavailable data, and safe aggregate capability counts. Existing
provider-attempt telemetry and conversation idempotency remain owned by the
host. The preview response stores the canonical result in assistant-message
metadata so current website state is re-read on the next turn.

Capability observability is aggregate-only: deadline bucket counts, official
hold count, derived blocker count, priority result code, unavailable-source
count, financial-aid request type, completed/remaining/missing counts,
verification state code, unavailable-source count, and next-action reason
code; plus housing request type, state result code, remaining-step/deadline/
unavailable-source counts, and next-action reason code. Student records,
sensitive labels, amounts, preferences, and response text are excluded.

## Evaluation fixtures

`fixtures/evaluation-fixtures.ts` contains compact representative questions and
expected structured outcomes for intent and domain-result evaluation.

## Known limitations and next source capability

The next useful source capability is a tenant-scoped, authoritative
registration-eligibility read with safe reason codes, blocking scope,
resolution owner, support route, verification timestamp, and version. It would
allow registration questions to explain a verified cause without broadening
this graph into registration writes or autonomous hold removal.
