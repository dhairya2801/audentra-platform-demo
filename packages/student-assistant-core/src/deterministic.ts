import type {
  RequirementStatus,
  StudentDocument,
  StudentRequirementDetail,
  StudentRequirementSummary,
} from "@vv/contracts";
import { studentRequirementSlug } from "@vv/contracts";
import type {
  EnrollmentHoldsRead,
  GroundedRequirement,
  NormalizedEnrollmentBlocker,
  NormalizedStudentDeadlineSource,
  PriorityEvidence,
  PrioritizedStudentAction,
  RequestClassification,
  StudentDeadline,
  StudentDeadlineBucketCounts,
  StudentDeadlineUrgency,
  StudentDeadlineWindow,
  StudentAssistantEntity,
  FinancialAidEntity,
  HousingEntity,
  StudentBlockerQueryScope,
  StudentAssistantRequestType,
  StudentAssistantUnavailableData,
} from "./contracts";
import type { NormalizedStudentRequest } from "./state";

const completedStatuses = new Set<RequirementStatus>([
  "completed",
  "waived",
]);
const excludedStatuses = new Set<RequirementStatus>(["not_applicable"]);

/**
 * Outstanding, but waiting on the university rather than on the student. These
 * are deliberately kept out of "what do I still need to do?": telling someone
 * to submit a document they submitted yesterday is the failure this separation
 * exists to prevent. They are reported, just not as an action.
 */
const awaitingReviewStatuses = new Set<RequirementStatus>([
  "submitted",
  "under_review",
]);

/**
 * A message that is only a greeting. Anchored at both ends so "hi, why is my
 * aid incomplete?" is not treated as small talk -- the greeting is a courtesy
 * there, not the question.
 */
const greetingOnly =
  /^(?:hi|hii+|hiya|hey+|hello+|yo|howdy|greetings|good\s+(?:morning|afternoon|evening|day))(?:\s+(?:there|edward|everyone|folks))?[\s!.,?]*$/;

/** Asking what Edward is or what it can be asked. */
const capabilityQuestion =
  /^(?:who\s+(?:are|r)\s+(?:you|u)|what\s+are\s+you|what\s+(?:can|could)\s+(?:you|u)\s+(?:do|help)|what\s+(?:can|could)\s+(?:i|you)\s+ask|what\s+do\s+you\s+do|how\s+can\s+you\s+help|what\s+are\s+you\s+able\s+to\s+do|tell\s+me\s+(?:what|about)\s+(?:you|your)|what\s+kind\s+of\s+(?:things|questions))\b[^.?!]*[?.!]?$/;

/**
 * An open request for direction. This one *is* answered from the record: the
 * useful reply to "I'm not sure where to start" is where to start.
 */
const openEndedHelp =
  /^(?:help|help\s+me|i\s+need\s+help|can\s+you\s+help(?:\s+me)?|i(?:'m|\s+am)\s+(?:not\s+sure|unsure|confused|lost|stuck)(?:\s+.*)?|where\s+(?:do|should)\s+i\s+(?:start|begin)|i\s+don'?t\s+know\s+where\s+to\s+(?:start|begin)|what\s+should\s+i\s+be\s+doing|not\s+sure\s+where\s+to\s+start)[\s!.,?]*$/;

export function isAwaitingReviewRequirementStatus(
  status: RequirementStatus,
): boolean {
  return awaitingReviewStatuses.has(status);
}

/**
 * "Where is my transcript?" is a different question from "what documents am I
 * missing?", and both are different from "when is my transcript due?". Each
 * alternative below therefore pairs a document noun with an explicit
 * state-of-that-document intent, rather than matching the noun on its own --
 * a looser rule swallowed the deadline question.
 */
const documentNoun =
  "(?:documents?|paperwork|transcripts?|records?|immuni[sz]ation|identity document|\\bid\\b|worksheet|forms?|upload)";

const documentStatusQuestion = new RegExp(
  [
    // "have I uploaded my transcript?", "did I already send the worksheet?"
    `(?:have|did|has)\\s+(?:i|my)\\b[^.?]{0,40}?\\b(?:upload(?:ed)?|submit(?:ted)?|sen[dt]|provide[d]?|turn(?:ed)? in|receive[d]?|gone through|been (?:reviewed|processed|accepted))\\b`,
    // "what's the status of my transcript", "any update on my documents"
    `(?:what(?:'s| is)|check|any|got any)\\s+(?:the\\s+)?(?:status|news|update|progress)[^.?]{0,40}${documentNoun}`,
    // "transcript status", "is my transcript under review / accepted / rejected"
    `${documentNoun}[^.?]{0,32}(?:status|under review|being reviewed|been (?:received|reviewed|accepted|rejected|processed))`,
    `(?:was|is|has)\\s+my\\s+[^.?]{0,24}?${documentNoun}[^.?]{0,16}(?:been\\s+)?(?:received|accepted|rejected|reviewed|processed|approved|uploaded|submitted|arrived)`,
    // "where is my transcript", "what happened to my immunization record"
    `(?:where(?:'s| is)|what happened to)\\s+my\\s+[^.?]{0,24}?${documentNoun}`,
  ].join("|"),
);

const conservativeStatusOrder: readonly RequirementStatus[] = [
  "blocked",
  "rejected",
  "expired",
  "in_progress",
  "ready",
  "submitted",
  "under_review",
  "not_applicable",
  "waived",
  "completed",
];

export interface RequirementEvidence {
  requirement: StudentRequirementSummary | StudentRequirementDetail;
  receiptId: string;
  source:
    | "getOnboardingChecklist"
    | "getEnrollmentHolds"
    | "getStudentDeadlines";
  order: number;
}

export interface DerivedRequirements {
  all: GroundedRequirement[];
  completed: GroundedRequirement[];
  /** Outstanding and the student's move. */
  remaining: GroundedRequirement[];
  /** Outstanding and the university's move. */
  awaitingReview: GroundedRequirement[];
  blocked: GroundedRequirement[];
  unavailableData: StudentAssistantUnavailableData[];
}

export function classifyRequestDeterministically(
  request: NormalizedStudentRequest,
): RequestClassification | null {
  const text = request.comparableText;

  // Conversational openers are answered before anything else looks at them, so
  // a greeting never drags a university-data read behind it. The three are kept
  // apart deliberately: "hi" wants a sentence, "what can you do?" wants the
  // list, and "I don't know where to start" wants this student's actual next
  // step. Collapsing them produced a capability dump in response to "hello".
  if (!request.isFollowUp) {
    if (greetingOnly.test(text)) return classification("greeting", 1);
    if (capabilityQuestion.test(text)) {
      return classification("capability_overview", 0.99);
    }
    if (openEndedHelp.test(text)) return classification("general_help", 0.98);
  }
  const aidEntity = financialAidEntityFromText(text);
  if (request.isMutationRequest) {
    const housingWrite = housingEntityFromText(text) !== null;
    return classification("unsupported_or_out_of_scope", 1, {
      financialAidEntity: aidEntity,
      requirementReference: housingWrite
        ? "housing_write_unavailable"
        : aidEntity
        ? "financial_aid_write_unavailable"
        : null,
    });
  }
  if (request.containsSensitiveFinancialData) {
    return classification("unsupported_or_out_of_scope", 1, {
      financialAidEntity: "financial_aid",
      requirementReference: "sensitive_financial_data",
    });
  }
  const housingEntity = housingEntityFromText(text);
  if (housingEntity) {
    if (/(?:deadline|due date|when .{0,30}due|due\b|overdue)/.test(text)) {
      return classification("housing_deadlines", 1, {
        deadlineWindow: deadlineWindow(text),
        requestedEntity: "housing_preference",
        housingEntity,
      });
    }
    if (
      /(?:what|which|show|list).{0,28}(?:housing|residence|dorm).{0,20}(?:option|choice)|(?:housing|residence|dorm).{0,20}(?:option|choice|available)/.test(
        text,
      )
    ) {
      return classification("housing_options", 1, { housingEntity });
    }
    if (
      /(?:who|where).{0,32}(?:contact|help)|contact.{0,24}housing|housing.{0,24}(?:support|office|help)|accommodation|accessible housing/.test(
        text,
      )
    ) {
      return classification("housing_support", 1, { housingEntity });
    }
    if (/(?:what|which).{0,20}(?:next|first)|next action|do next|priority/.test(text)) {
      return classification("housing_next_action", 1, { housingEntity });
    }
    if (/(?:left|remain|remaining|outstanding|still (?:have|need)|steps?|complete for)/.test(text)) {
      return classification("housing_remaining_steps", 1, { housingEntity });
    }
    return classification("housing_status", 1, { housingEntity });
  }
  if (aidEntity) {
    // Disbursement, coverage, and application status are checked before the
    // generic "how much" rule below. "How much will I still owe?" is a coverage
    // question with a definite answer on the record; routing it to the
    // can't-promise-an-amount refusal was correct only for questions about
    // amounts nobody has decided yet.
    if (
      /disburse|paid out|pay out|when.{0,32}(?:money|funds|aid).{0,24}(?:arrive|come|available|applied)|(?:money|funds|aid).{0,32}(?:hasn'?t|has not|not).{0,24}(?:arrive|come|been (?:paid|applied|disbursed))|refund check/.test(
        text,
      )
    ) {
      return classification("aid_disbursement", 1, {
        financialAidEntity: aidEntity,
      });
    }
    if (
      /(?:cover|covers|enough to (?:cover|pay))\b.{0,32}(?:tuition|cost|bill|charges|balance)|(?:tuition|cost of attendance|bill|balance).{0,32}(?:covered|after (?:aid|my aid))|how much.{0,24}(?:will i|do i|would i).{0,16}(?:still )?(?:owe|pay)|what.{0,16}(?:will|do) i (?:still )?owe|remaining balance|left to pay|out of pocket|refund/.test(
        text,
      )
    ) {
      return classification("aid_coverage", 1, { financialAidEntity: aidEntity });
    }
    if (
      /\bfafsa\b|(?:application|isir).{0,32}(?:received|status|processed|submitted)|(?:received|got).{0,24}my.{0,16}(?:fafsa|application)|selected for verification/.test(
        text,
      )
    ) {
      return classification("aid_application_status", 1, {
        financialAidEntity: /\bfafsa\b/.test(text) ? "fafsa" : aidEntity,
      });
    }
    // Guarded against "what aid *steps* do I have left", which is a question
    // about remaining work, not about the size of the package.
    if (
      !/\b(?:steps?|left|remaining|outstanding|still need|to do)\b/.test(text) &&
      /(?:what|which|how much).{0,24}(?:aid|award|grant|scholarship|loan|money|funding).{0,24}(?:do i have|am i (?:receiving|getting)|did i get|have i (?:got|been (?:offered|awarded)))|(?:summary|overview|breakdown).{0,24}(?:aid|award)|(?:my|all).{0,12}(?:financial[ -]?aid|awards?)\b\s*[?.]?$|total.{0,16}aid/.test(
        text,
      )
    ) {
      return classification("aid_summary", 1, { financialAidEntity: aidEntity });
    }
    if (
      /(?:qualif(?:y|ied)|eligib(?:le|ility)|will i get|how much|award amount|promise|guarantee|expected contribution|calculate)/.test(
        text,
      )
    ) {
      return classification("aid_status", 1, {
        financialAidEntity: aidEntity,
        requirementReference: /(?:how much|amount|promise|guarantee)/.test(text)
          ? "award_amount_unavailable"
          : "eligibility_unavailable",
      });
    }
    if (/(?:who|where).{0,32}(?:contact|help)|contact.{0,24}financial|financial.{0,24}(?:support|office|advisor)/.test(text)) {
      return classification("aid_support", 1, { financialAidEntity: aidEntity });
    }
    if (
      !/\bstatus\b|\bstate\b/.test(text) &&
      /(?:why|explain|what is|what does).{0,48}(?:worksheet|fafsa|form|verification|award acceptance)|why.{0,48}(?:need|required)/.test(
        text,
      )
    ) {
      return classification("aid_requirement_explanation", 1, {
        financialAidEntity: aidEntity,
        requirementReference: text,
      });
    }
    if (
      /(?:missing|still need|need|outstanding).{0,32}(?:document|paperwork|form|requirement|item)|(?:document|paperwork|form|requirement|item).{0,28}(?:missing|outstanding|still need)/.test(
        text,
      )
    ) {
      return classification("aid_missing_documents", 1, {
        financialAidEntity: aidEntity,
      });
    }
    if (/(?:verification|worksheet).{0,32}(?:pending|complete|completed|verified|status|review)|(?:pending|complete|verified|under review|status of|state of).{0,32}(?:verification|worksheet)/.test(text)) {
      return classification("aid_verification_status", 1, {
        financialAidEntity: aidEntity,
      });
    }
    if (/(?:award|loan|grant|scholarship|work[ -]?study).{0,40}(?:accepted|declined|pending|offered|status)|(?:accepted|declined).{0,32}(?:award|loan|grant|scholarship|work[ -]?study)/.test(text)) {
      return classification("aid_award_acceptance_status", 1, {
        financialAidEntity: "award_acceptance",
        requirementReference: text,
      });
    }
    if (/(?:deadline|due date|when .{0,30}due|due\b|overdue)/.test(text)) {
      return classification("aid_deadlines", 1, {
        deadlineWindow: deadlineWindow(text),
        financialAidEntity: aidEntity,
      });
    }
    if (/(?:what|which).{0,20}(?:next|first)|next action|do next|priority/.test(text)) {
      return classification("aid_next_action", 1, { financialAidEntity: aidEntity });
    }
    if (/(?:left|remain|remaining|outstanding|still (?:have|need)|steps?)/.test(text)) {
      return classification("aid_remaining_steps", 1, { financialAidEntity: aidEntity });
    }
    if (/(?:incomplete|not complete|status|where .{0,16}stand)/.test(text)) {
      return classification(
        /incomplete|not complete/.test(text) ? "aid_incomplete_reason" : "aid_status",
        1,
        { financialAidEntity: aidEntity },
      );
    }
  }
  if (
    request.history.length > 0 &&
    /^(?:why|how come).{0,40}(?:that|it).{0,20}first\??$/.test(text)
  ) {
    return classification("next_action", 0.99, {
      priorityExplanationRequested: true,
    });
  }
  // Asked before the "missing documents" rule, because "have I uploaded my
  // transcript?" and "what's the status of my transcript?" are questions about
  // a document's state, not requests for a list of what is absent. Answering
  // them from the missing-list is what produced silence about a document that
  // had in fact arrived.
  // "When do I lose my deposit?" is a question about the refund rule, not about
  // this student's payment due date. The planner routed it to deadlines on one
  // run in four and answered with the due date instead of the policy.
  if (
    /(?:lose|forfeit|get back|refund)\b[^.?]{0,32}\bdeposit\b|\bdeposit\b[^.?]{0,32}(?:refundable|non-?refundable|refund|forfeit)|when.{0,24}(?:can|do) i (?:get|lose)[^.?]{0,24}\bdeposit\b/.test(
      text,
    )
  ) {
    return classification("policy_lookup", 0.97);
  }
  if (documentStatusQuestion.test(text)) {
    return classification("document_status", 0.98, {
      requestedEntity: requestedEntityFromText(text),
    });
  }
  if (
    /(?:missing|need|still need|which|what about).{0,32}(?:document|paperwork|transcript|record)|(?:document|paperwork).{0,24}(?:missing|outstanding|need)/.test(
      text,
    )
  ) {
    return classification("missing_documents", 0.99);
  }
  if (
    /(?:how much|what).{0,24}(?:will|do|would) i still (?:owe|pay|have to pay)\b|(?:owe|pay|balance).{0,24}after (?:my )?(?:aid|financial[ -]?aid|scholarship|grant)|(?:still|left) to (?:owe|pay)\b|out of pocket|(?:am i|do i) get(?:ting)? a refund|refund (?:due|estimate|amount)/.test(
      text,
    )
  ) {
    return classification("aid_coverage", 0.96, {
      financialAidEntity: "financial_aid",
    });
  }
  if (
    /(?:hold|\bblock\b|blocker|blocked|blocking|preventing me|(?:can(?:not|'t)|unable to|why (?:can(?:not|'t)|won't)).{0,24}register)/.test(
      text,
    )
  ) {
    const blockerScope = blockerQueryScope(request);
    return classification("holds_and_blockers", 0.99, {
      blockerScope,
      blockerTarget:
        blockerScope === "orientation" ? "orientation_registration" : null,
      registrationQuestion: /(?:register|registration)/.test(text),
    });
  }
  if (
    /(?:deadline|due date|when (?:is|are).{0,30}due|due\b|what.{0,30}(?:missed|overdue)|(?:missed|overdue).{0,30}(?:task|requirement|item))/.test(
      text,
    )
  ) {
    const requestedEntity = requestedEntityFromText(text);
    return classification("deadlines", 0.99, {
      deadlineWindow: deadlineWindow(text),
      requestedEntity,
    });
  }
  if (
    /(?:human|advisor|counselor|contact support|support options|who can (?:help|i contact)|need help)/.test(
      text,
    )
  ) {
    return classification("request_support", 0.98);
  }
  if (
    /(?:explain|why (?:do|is|are)|what does .{0,50}(?:mean|require)|tell me about .{0,40}requirement)/.test(
      text,
    )
  ) {
    return classification("explain_requirement", 0.96, {
      requirementReference: text,
    });
  }
  if (/(?:what|which).{0,20}(?:next|first)|next action|do next|priority/.test(text)) {
    return classification("next_action", 0.98);
  }
  if (
    /(?:not (?:yet )?(?:done|complete|completed)|yet to do|left to do|\bremain(?:s|ing)?\b|outstanding|unfinished|still (?:have|need)|need to (?:do|finish|complete)|what(?:'s| is) left)/.test(
      text,
    )
  ) {
    return classification("remaining_steps", 0.99);
  }
  if (
    /(?:already (?:done|complete|completed)|have i (?:done|completed|finished)|completed steps|finished steps|what is complete)/.test(
      text,
    )
  ) {
    return classification("completed_steps", 0.99);
  }
  if (
    /(?:onboarding|enrollment).{0,30}(?:status|progress|how far)|where am i.{0,30}(?:onboarding|enrollment)|how is my onboarding/.test(
      text,
    )
  ) {
    return classification("onboarding_status", 0.97);
  }
  if (
    request.isFollowUp &&
    /^(?:what|how) about (?:documents|paperwork|transcripts?)\??$/.test(text)
  ) {
    return classification("missing_documents", 0.96);
  }
  return null;
}

export function deriveRequirements(
  evidence: readonly RequirementEvidence[],
): DerivedRequirements {
  const grouped = new Map<string, RequirementEvidence[]>();
  for (const item of evidence) {
    const key = item.requirement.code || item.requirement.id;
    const existing = grouped.get(key);
    if (existing) existing.push(item);
    else grouped.set(key, [item]);
  }

  const unavailableData: StudentAssistantUnavailableData[] = [];
  const all = [...grouped.values()].map((group) => {
    const first = group[0]!;
    const statuses = [...new Set(group.map((item) => item.requirement.status))];
    const dueDates = [
      ...new Set(
        group
          .map((item) => item.requirement.dueAt)
          .filter((value): value is string => value !== null),
      ),
    ];
    if (statuses.length > 1 || dueDates.length > 1) {
      unavailableData.push({
        source: first.source,
        reason: "conflicting_data",
        retryable: false,
      });
    }
    const status = conservativeStatus(statuses);
    const dueAt = earliestValidDate(dueDates);
    const requirement = first.requirement;
    const slug =
      "slug" in requirement
        ? requirement.slug
        : studentRequirementSlug(requirement.code);
    return {
      id: requirement.id,
      code: requirement.code,
      title: requirement.title,
      description: requirement.description,
      status,
      blocking: group.some((item) => item.requirement.blocking),
      dueAt,
      progressPercent: Math.min(
        ...group.map((item) => item.requirement.progressPercent),
      ),
      ...(requirement.reward ? { reward: requirement.reward } : {}),
      slug,
      contextReceiptIds: [
        ...new Set(group.map((item) => item.receiptId)),
      ],
      _order: Math.min(...group.map((item) => item.order)),
    } satisfies GroundedRequirement & { _order: number };
  });

  const sorted = all.sort(compareRequirements).map(stripOrder);
  const completed = sorted.filter((item) => completedStatuses.has(item.status));
  const outstanding = sorted.filter(
    (item) =>
      !completedStatuses.has(item.status) && !excludedStatuses.has(item.status),
  );
  const awaitingReview = outstanding.filter((item) =>
    awaitingReviewStatuses.has(item.status),
  );
  const remaining = outstanding.filter(
    (item) => !awaitingReviewStatuses.has(item.status),
  );
  const blocked = remaining.filter((item) => item.status === "blocked");
  return {
    all: sorted,
    completed,
    remaining,
    awaitingReview,
    blocked,
    unavailableData,
  };
}

export function isCompletedRequirementStatus(
  status: RequirementStatus,
): boolean {
  return completedStatuses.has(status);
}

export function hasCurrentDocument(
  documents: readonly StudentDocument[],
): boolean {
  return documents.some((document) =>
    [
      "uploaded",
      "processing",
      "needs_review",
      "under_review",
      "accepted",
    ].includes(document.status),
  );
}

export function mostRelevantDocumentStatus(
  documents: readonly StudentDocument[],
): StudentDocument["status"] | null {
  const order: readonly StudentDocument["status"][] = [
    "accepted",
    "under_review",
    "needs_review",
    "processing",
    "uploaded",
    "rejected",
    "placeholder",
  ];
  return order.find((status) =>
    documents.some((document) => document.status === status),
  ) ?? null;
}

export function validIsoDate(value: string): boolean {
  return Number.isFinite(Date.parse(value));
}

export function validIanaTimeZone(value: string | null | undefined): value is string {
  if (!value) return false;
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: value }).format(new Date(0));
    return true;
  } catch {
    return false;
  }
}

export interface DeadlineDerivationResult {
  allOutstanding: StudentDeadline[];
  visible: StudentDeadline[];
  bucketCounts: StudentDeadlineBucketCounts;
  unavailableData: StudentAssistantUnavailableData[];
}

export function deriveStudentDeadlines(input: {
  items: readonly NormalizedStudentDeadlineSource[];
  receiptId: string;
  receiptObservedAt: string | null;
  receiptSourceVersion: string | null;
  institutionalTimeZone: string | null | undefined;
  now: Date;
  window: StudentDeadlineWindow;
  requestedEntity?: StudentAssistantEntity | null;
}): DeadlineDerivationResult {
  const counts = emptyDeadlineBucketCounts();
  const unavailableData: StudentAssistantUnavailableData[] = [];
  const grouped = new Map<string, NormalizedStudentDeadlineSource[]>();
  for (const item of input.items) {
    const key = `${item.source}:${item.id}`;
    const group = grouped.get(key);
    if (group) group.push(item);
    else grouped.set(key, [item]);
  }

  const timeZoneIsValid = validIanaTimeZone(input.institutionalTimeZone);
  const institutionalTimeZone: string =
    timeZoneIsValid && input.institutionalTimeZone
      ? input.institutionalTimeZone
      : "UTC";
  if (!timeZoneIsValid && input.items.length > 0) {
    unavailableData.push(deadlineUnavailable("incomplete"));
  }
  const today = timeZoneIsValid
    ? localCalendarDate(input.now, institutionalTimeZone)
    : null;
  const results: Array<StudentDeadline & { sourceOrder: number; dueSort: number }> = [];

  for (const group of grouped.values()) {
    const first = group[0]!;
    const completionStates = new Set(group.map((item) => item.completionState));
    if (completionStates.size > 1) {
      unavailableData.push(deadlineUnavailable("conflicting_data"));
      continue;
    }
    if (first.completionState === "satisfied") {
      counts.completedOrSatisfied += 1;
      continue;
    }

    const dueValues = [...new Set(group.map((item) => item.dueAt))];
    const conflictingDate = dueValues.length > 1;
    if (conflictingDate) unavailableData.push(deadlineUnavailable("conflicting_data"));
    const dueAt = conflictingDate ? null : (first.dueAt ?? null);
    const classified =
      timeZoneIsValid && today && !conflictingDate
        ? classifyDeadlineUrgency(
            dueAt,
            first.duePrecision,
            input.now,
            institutionalTimeZone,
            today,
          )
        : { urgency: "unknown_date" as const, dueSort: Number.POSITIVE_INFINITY };
    if (classified.urgency === "unknown_date" && !conflictingDate) {
      unavailableData.push(deadlineUnavailable("incomplete"));
    }
    incrementDeadlineBucket(counts, classified.urgency);
    results.push({
      id: first.id,
      kind: first.kind,
      title: first.label,
      dueAt,
      duePrecision: first.duePrecision,
      institutionalTimeZone,
      urgency: classified.urgency,
      sourceStatus: first.sourceStatus,
      requirementId: first.requirementId,
      requirementCode: first.requirementCode,
      source: first.source,
      currentlyBlocking: group.some((item) => item.currentlyBlocking),
      blockingRequirement: group.some((item) => item.blockingRequirement),
      hardOrRecommended: consistentHardness(group),
      lastVerifiedAt: first.lastVerifiedAt ?? input.receiptObservedAt,
      sourceVersion: first.sourceVersion,
      navigationRoute: first.navigationRoute,
      contextReceiptIds: [input.receiptId],
      sourceOrder: Math.min(...group.map((item) => item.sourceOrder)),
      dueSort: classified.dueSort,
    });
  }

  const sorted = results.sort(compareDeadlines).map(stripDeadlineSort);
  return {
    allOutstanding: sorted,
    visible: sorted.filter(
      (deadline) =>
        deadlineMatchesWindow(deadline, input.window, input.now) &&
        deadlineMatchesEntity(deadline, input.requestedEntity ?? null),
    ),
    bucketCounts: counts,
    unavailableData: deduplicateDeadlineUnavailable(unavailableData),
  };
}

export interface EnrollmentBlockerDerivationResult {
  officialHolds: NormalizedEnrollmentBlocker[];
  derivedBlockers: NormalizedEnrollmentBlocker[];
  nonBlockingActions: NormalizedEnrollmentBlocker[];
}

export function deriveEnrollmentBlockers(
  read: EnrollmentHoldsRead,
  receiptId: string,
): EnrollmentBlockerDerivationResult {
  const officialHolds: NormalizedEnrollmentBlocker[] = [];
  const derivedBlockers: NormalizedEnrollmentBlocker[] = [];
  const nonBlockingActions: NormalizedEnrollmentBlocker[] = [];
  if (read.journey?.status === "on_hold") {
    officialHolds.push({
      id: read.journey.id,
      label: null,
      type: "official_enrollment_hold",
      officialOrDerived: "official",
      severity: "blocking",
      blockingScope: "enrollment_journey",
      reasonCode: null,
      studentSafeReason: null,
      relatedRequirementId: null,
      relatedRequirementCode: null,
      resolutionOwner: null,
      selfResolvable: null,
      supportRoute: read.journey.supportRoute,
      contextReceiptIds: [receiptId],
      domain: read.journey.domain,
      blockerTarget: null,
    });
  }

  const requirementsByCode = new Map(
    read.requirements.map((requirement) => [requirement.code, requirement]),
  );
  const satisfiedCodes = new Set(
    read.requirements
      .filter((requirement) => isSatisfiedRequirementStatus(requirement.status))
      .map((requirement) => requirement.code),
  );
  for (const requirement of read.requirements) {
    const unsatisfiedDependencies = requirement.dependencyCodes.filter(
      (code) => !satisfiedCodes.has(code),
    );
    if (requirement.status === "blocked") {
      const labels = unsatisfiedDependencies.map(
        (code) => requirementsByCode.get(code)?.label ?? safeCodeLabel(code),
      );
      derivedBlockers.push({
        id: requirement.id,
        label: requirement.label,
        type: "requirement_dependency",
        officialOrDerived: "derived",
        severity: "blocking",
        blockingScope: "onboarding_requirement",
        reasonCode: unsatisfiedDependencies[0] ?? null,
        studentSafeReason:
          labels.length > 0
            ? dependencyBlockerReason(requirement.code, requirement.label, labels)
            : null,
        relatedRequirementId: requirement.id,
        relatedRequirementCode: requirement.code,
        resolutionOwner: requirement.resolutionOwner,
        selfResolvable: null,
        supportRoute: requirement.supportRoute,
        contextReceiptIds: [receiptId],
        domain: requirement.domain,
        blockerTarget: requirement.code,
      });
    } else if (
      requirement.status === "rejected" &&
      requirement.submissionType === "document"
    ) {
      nonBlockingActions.push({
        id: requirement.id,
        label: requirement.label,
        type: "document_review",
        officialOrDerived: "derived",
        severity: "warning",
        blockingScope: "document_submission",
        reasonCode: "rejected",
        studentSafeReason: `${requirement.label} needs another document submission or review.`,
        relatedRequirementId: requirement.id,
        relatedRequirementCode: requirement.code,
        resolutionOwner: requirement.resolutionOwner,
        selfResolvable: null,
        supportRoute: requirement.supportRoute,
        contextReceiptIds: [receiptId],
        domain: requirement.domain,
        blockerTarget: requirement.code,
      });
    }
  }

  for (const item of read.academicPlan) {
    if (item.status !== "blocked" || item.missingPrerequisiteCodes.length === 0) {
      continue;
    }
    derivedBlockers.push({
      id: item.id,
      label: item.label,
      type: "academic_prerequisite",
      officialOrDerived: "derived",
      severity: "blocking",
      blockingScope: "course_eligibility",
      reasonCode: item.missingPrerequisiteCodes[0] ?? null,
      studentSafeReason: `${item.label} requires ${formatList(item.missingPrerequisiteCodes)} first.`,
      relatedRequirementId: null,
      relatedRequirementCode: item.courseCode,
      resolutionOwner: null,
      selfResolvable: null,
      supportRoute: item.supportRoute,
      contextReceiptIds: [receiptId],
      domain: item.domain,
      blockerTarget: item.courseCode,
    });
  }

  for (const item of read.financialActions) {
    if (item.status !== "action_required") continue;
    nonBlockingActions.push({
      id: item.id,
      label: item.label,
      type: "financial_action",
      officialOrDerived: "derived",
      severity: "warning",
      blockingScope: null,
      reasonCode: item.status,
      studentSafeReason: `${item.label} requires action, but the current records do not identify it as an enrollment or registration hold.`,
      relatedRequirementId: null,
      relatedRequirementCode: item.code,
      resolutionOwner: null,
      selfResolvable: null,
      supportRoute: item.supportRoute,
      contextReceiptIds: [receiptId],
      domain: item.domain,
      blockerTarget: null,
    });
  }
  return { officialHolds, derivedBlockers, nonBlockingActions };
}

export function filterEnrollmentBlockers(
  result: EnrollmentBlockerDerivationResult,
  scope: StudentBlockerQueryScope | null,
): EnrollmentBlockerDerivationResult {
  if (!scope || scope === "enrollment") {
    return {
      officialHolds: result.officialHolds.filter(
        (hold) => hold.domain === "enrollment",
      ),
      derivedBlockers: result.derivedBlockers.filter(
        (blocker) => blocker.domain === "enrollment",
      ),
      nonBlockingActions: [],
    };
  }
  if (scope === "official_holds") {
    return {
      officialHolds: result.officialHolds,
      derivedBlockers: [],
      nonBlockingActions: [],
    };
  }
  if (scope === "orientation") {
    return {
      officialHolds: [],
      derivedBlockers: result.derivedBlockers.filter(
        (blocker) => blocker.blockerTarget === "orientation_registration",
      ),
      nonBlockingActions: [],
    };
  }
  if (scope === "course_registration") {
    return {
      officialHolds: [],
      derivedBlockers: result.derivedBlockers.filter(
        (blocker) => blocker.domain === "course_registration",
      ),
      nonBlockingActions: [],
    };
  }
  return { officialHolds: [], derivedBlockers: [], nonBlockingActions: [] };
}

export interface PriorityDerivationResult {
  action: PrioritizedStudentAction | null;
  resultCode: PrioritizedStudentAction["reasonCode"];
  dependencyCycle: boolean;
}

export function derivePrioritizedAction(input: {
  requirements: readonly GroundedRequirement[];
  holdRead: EnrollmentHoldsRead | null;
  officialHolds: readonly NormalizedEnrollmentBlocker[];
  deadlines: readonly StudentDeadline[];
}): PriorityDerivationResult {
  const officialHold = input.officialHolds[0];
  if (officialHold) {
    return {
      action: {
        id: `priority:${officialHold.id}`,
        label: "Contact enrollment support about the official hold",
        kind: "contact_support",
        reasonCode: "official_hold_support",
        relatedRequirementId: null,
        relatedRequirementCode: null,
        navigationRoute: officialHold.supportRoute ?? "/help",
        contextReceiptIds: officialHold.contextReceiptIds,
      },
      resultCode: "official_hold_support",
      dependencyCycle: false,
    };
  }

  const details = new Map(
    (input.holdRead?.requirements ?? []).map((requirement) => [
      requirement.code,
      requirement,
    ]),
  );
  const requirements = new Map(
    input.requirements.map((requirement) => [requirement.code, requirement]),
  );
  const deadlinesByCode = new Map(
    input.deadlines.flatMap((deadline) =>
      deadline.requirementCode ? [[deadline.requirementCode, deadline] as const] : [],
    ),
  );
  let dependencyCycle = false;
  const candidates = new Map<
    string,
    {
      requirement: GroundedRequirement;
      beneficiary: GroundedRequirement;
      deadline: StudentDeadline | null;
      dependency: boolean;
      sourceOrder: number;
    }
  >();

  const actionableLeaves = (
    requirement: GroundedRequirement,
    path: Set<string>,
  ): GroundedRequirement[] => {
    if (path.has(requirement.code)) {
      dependencyCycle = true;
      return [];
    }
    const dependencyCodes = details.get(requirement.code)?.dependencyCodes ?? [];
    const unsatisfied = dependencyCodes
      .map((code) => requirements.get(code))
      .filter(
        (candidate): candidate is GroundedRequirement =>
          candidate !== undefined && !isSatisfiedRequirementStatus(candidate.status),
      );
    if (unsatisfied.length === 0) {
      return isActionableRequirementStatus(requirement.status) ? [requirement] : [];
    }
    const nextPath = new Set(path).add(requirement.code);
    return unsatisfied.flatMap((dependency) => actionableLeaves(dependency, nextPath));
  };

  input.requirements.forEach((beneficiary, sourceOrder) => {
    if (isSatisfiedRequirementStatus(beneficiary.status)) return;
    const leaves = actionableLeaves(beneficiary, new Set());
    for (const requirement of leaves) {
      const ownDeadline = deadlinesByCode.get(requirement.code) ?? null;
      const beneficiaryDeadline = deadlinesByCode.get(beneficiary.code) ?? null;
      const effectiveDeadline = earlierPriorityDeadline(
        ownDeadline,
        beneficiaryDeadline,
      );
      const existing = candidates.get(requirement.code);
      const candidate = {
        requirement,
        beneficiary,
        deadline: effectiveDeadline,
        dependency: beneficiary.code !== requirement.code,
        sourceOrder,
      };
      if (!existing || comparePriorityCandidates(candidate, existing) < 0) {
        candidates.set(requirement.code, candidate);
      }
    }
  });

  if (dependencyCycle) {
    return { action: null, resultCode: "dependency_cycle", dependencyCycle: true };
  }
  const selected = [...candidates.values()].sort(comparePriorityCandidates)[0];
  if (selected) {
    const reasonCode = selected.dependency
      ? "dependency_prerequisite"
      : selected.deadline?.urgency !== "unknown_date" && selected.deadline
        ? "urgent_deadline"
        : selected.requirement.blocking
          ? "required_step"
          : "recommended_step";
    return {
      action: {
        id: `priority:${selected.requirement.id}`,
        label: selected.requirement.title,
        kind: "requirement",
        reasonCode,
        relatedRequirementId: selected.requirement.id,
        relatedRequirementCode: selected.requirement.code,
        navigationRoute: `/enrollment/requirements/${selected.requirement.slug}`,
        contextReceiptIds: [
          ...new Set([
            ...selected.requirement.contextReceiptIds,
            ...(selected.deadline?.contextReceiptIds ?? []),
          ]),
        ],
      },
      resultCode: reasonCode,
      dependencyCycle: false,
    };
  }

  const deadline = input.deadlines.find(isActionableStandaloneDeadline);
  if (deadline) {
    return {
      action: {
        id: `priority:${deadline.source}:${deadline.id}`,
        label: deadline.title,
        kind: "deadline",
        reasonCode: "urgent_deadline",
        relatedRequirementId: deadline.requirementId,
        relatedRequirementCode: deadline.requirementCode,
        navigationRoute: deadline.navigationRoute,
        contextReceiptIds: deadline.contextReceiptIds,
      },
      resultCode: "urgent_deadline",
      dependencyCycle: false,
    };
  }
  return { action: null, resultCode: "no_action", dependencyCycle: false };
}

export function derivePriorityEvidence(input: {
  action: PrioritizedStudentAction;
  holdRead: EnrollmentHoldsRead | null;
  deadlines: readonly StudentDeadline[];
  now: Date;
  currentTopActionId: string | null;
}): PriorityEvidence {
  const deadline = input.deadlines.find(
    (item) =>
      (input.action.relatedRequirementCode !== null &&
        item.requirementCode === input.action.relatedRequirementCode) ||
      (input.action.kind === "deadline" && item.title === input.action.label),
  );
  const blockedRequirements = (input.holdRead?.requirements ?? []).filter(
    (requirement) =>
      input.action.relatedRequirementCode !== null &&
      requirement.status === "blocked" &&
      requirement.dependencyCodes.includes(
        input.action.relatedRequirementCode,
      ),
  );
  return {
    actionId: input.action.id,
    deadlineAt: deadline?.dueAt ?? null,
    deadlinePrecision: deadline?.duePrecision ?? null,
    institutionalTimeZone: deadline?.institutionalTimeZone ?? null,
    urgency: deadline?.urgency ?? null,
    daysRemaining: deadline ? deadlineDaysRemaining(deadline, input.now) : null,
    blocksRequirementCodes: blockedRequirements.map((item) => item.code),
    blocksRequirementLabels: blockedRequirements.map((item) => item.label),
    rankingBasis: input.action.reasonCode,
    isCurrentTopPriority: input.currentTopActionId === input.action.id,
    contextReceiptIds: [
      ...new Set([
        ...input.action.contextReceiptIds,
        ...(deadline?.contextReceiptIds ?? []),
      ]),
    ],
  };
}

function deadlineDaysRemaining(
  deadline: StudentDeadline,
  now: Date,
): number | null {
  if (!deadline.dueAt || deadline.urgency === "unknown_date") return null;
  const dueDate =
    deadline.duePrecision === "date"
      ? parseDateOnly(deadline.dueAt)
      : localCalendarDate(new Date(deadline.dueAt), deadline.institutionalTimeZone);
  if (!dueDate) return null;
  const today = localCalendarDate(now, deadline.institutionalTimeZone);
  const difference = calendarOrdinal(dueDate) - calendarOrdinal(today);
  return difference >= 0 ? difference : null;
}

function emptyDeadlineBucketCounts(): StudentDeadlineBucketCounts {
  return {
    overdue: 0,
    dueToday: 0,
    dueWithinSevenDays: 0,
    upcoming: 0,
    completedOrSatisfied: 0,
    unknownDate: 0,
  };
}

function classifyDeadlineUrgency(
  dueAt: string | null,
  precision: "date" | "instant",
  now: Date,
  timeZone: string,
  today: CalendarDate,
): { urgency: StudentDeadlineUrgency; dueSort: number } {
  if (!dueAt) return { urgency: "unknown_date", dueSort: Number.POSITIVE_INFINITY };
  if (precision === "date") {
    const dueDate = parseDateOnly(dueAt);
    if (!dueDate) return { urgency: "unknown_date", dueSort: Number.POSITIVE_INFINITY };
    const difference = calendarOrdinal(dueDate) - calendarOrdinal(today);
    const dueSort = calendarOrdinal(dueDate) * 86_400_000;
    if (difference < 0) return { urgency: "overdue", dueSort };
    if (difference === 0) return { urgency: "due_today", dueSort };
    if (difference <= 7) return { urgency: "due_within_7_days", dueSort };
    return { urgency: "upcoming", dueSort };
  }
  const instant = new Date(dueAt);
  if (!Number.isFinite(instant.getTime())) {
    return { urgency: "unknown_date", dueSort: Number.POSITIVE_INFINITY };
  }
  if (instant.getTime() < now.getTime()) {
    return { urgency: "overdue", dueSort: instant.getTime() };
  }
  const localDueDate = localCalendarDate(instant, timeZone);
  const difference = calendarOrdinal(localDueDate) - calendarOrdinal(today);
  if (difference === 0) return { urgency: "due_today", dueSort: instant.getTime() };
  if (difference <= 7) return { urgency: "due_within_7_days", dueSort: instant.getTime() };
  return { urgency: "upcoming", dueSort: instant.getTime() };
}

interface CalendarDate {
  year: number;
  month: number;
  day: number;
}

function parseDateOnly(value: string): CalendarDate | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return null;
  const result = { year: Number(match[1]), month: Number(match[2]), day: Number(match[3]) };
  const date = new Date(Date.UTC(result.year, result.month - 1, result.day));
  return date.getUTCFullYear() === result.year &&
    date.getUTCMonth() + 1 === result.month &&
    date.getUTCDate() === result.day
    ? result
    : null;
}

function localCalendarDate(date: Date, timeZone: string): CalendarDate {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);
  const value = (type: Intl.DateTimeFormatPartTypes) =>
    Number(parts.find((part) => part.type === type)?.value);
  return { year: value("year"), month: value("month"), day: value("day") };
}

function calendarOrdinal(date: CalendarDate): number {
  return Math.floor(Date.UTC(date.year, date.month - 1, date.day) / 86_400_000);
}

function incrementDeadlineBucket(
  counts: StudentDeadlineBucketCounts,
  urgency: StudentDeadlineUrgency,
): void {
  if (urgency === "overdue") counts.overdue += 1;
  else if (urgency === "due_today") counts.dueToday += 1;
  else if (urgency === "due_within_7_days") counts.dueWithinSevenDays += 1;
  else if (urgency === "upcoming") counts.upcoming += 1;
  else counts.unknownDate += 1;
}

function consistentHardness(
  items: readonly NormalizedStudentDeadlineSource[],
): "hard" | "recommended" | null {
  const values = [...new Set(items.map((item) => item.hardOrRecommended))];
  return values.length === 1 ? (values[0] ?? null) : null;
}

function compareDeadlines(
  left: StudentDeadline & { sourceOrder: number; dueSort: number },
  right: StudentDeadline & { sourceOrder: number; dueSort: number },
): number {
  const rank = (deadline: typeof left) =>
    deadline.urgency === "overdue"
      ? 0
      : deadline.urgency === "due_today"
        ? 1
        : deadline.urgency === "unknown_date"
          ? 3
          : 2;
  return (
    rank(left) - rank(right) ||
    left.dueSort - right.dueSort ||
    left.sourceOrder - right.sourceOrder ||
    left.title.localeCompare(right.title) ||
    left.id.localeCompare(right.id)
  );
}

function stripDeadlineSort(
  deadline: StudentDeadline & { sourceOrder: number; dueSort: number },
): StudentDeadline {
  const { sourceOrder: _sourceOrder, dueSort: _dueSort, ...result } = deadline;
  return result;
}

function deadlineMatchesWindow(
  deadline: StudentDeadline,
  window: StudentDeadlineWindow,
  now: Date,
): boolean {
  if (deadline.urgency === "unknown_date") return true;
  if (window === "all") return true;
  if (window === "today") return deadline.urgency === "due_today";
  if (window === "overdue") return deadline.urgency === "overdue";
  if (window === "upcoming") return deadline.urgency !== "overdue";
  if (!deadline.dueAt) return false;
  const today = localCalendarDate(now, deadline.institutionalTimeZone);
  const dueDate =
    deadline.duePrecision === "date"
      ? parseDateOnly(deadline.dueAt)
      : localCalendarDate(new Date(deadline.dueAt), deadline.institutionalTimeZone);
  if (!dueDate) return false;
  const weekday = new Date(
    Date.UTC(today.year, today.month - 1, today.day),
  ).getUTCDay();
  const mondayOffset = weekday === 0 ? 6 : weekday - 1;
  const todayOrdinal = calendarOrdinal(today);
  const dueOrdinal = calendarOrdinal(dueDate);
  return dueOrdinal >= todayOrdinal - mondayOffset && dueOrdinal <= todayOrdinal + (6 - mondayOffset);
}

function deadlineMatchesEntity(
  deadline: StudentDeadline,
  requestedEntity: StudentAssistantEntity | null,
): boolean {
  if (!requestedEntity) return true;
  return deadline.requirementCode === requestedEntity;
}

function deadlineUnavailable(
  reason: "incomplete" | "conflicting_data",
): StudentAssistantUnavailableData {
  return { source: "getStudentDeadlines", reason, retryable: false };
}

function deduplicateDeadlineUnavailable(
  values: readonly StudentAssistantUnavailableData[],
): StudentAssistantUnavailableData[] {
  return values.filter(
    (value, index) =>
      values.findIndex(
        (candidate) =>
          candidate.source === value.source && candidate.reason === value.reason,
      ) === index,
  );
}

function isSatisfiedRequirementStatus(status: RequirementStatus): boolean {
  return completedStatuses.has(status) || excludedStatuses.has(status);
}

function isActionableRequirementStatus(status: RequirementStatus): boolean {
  return ![
    "blocked",
    "submitted",
    "under_review",
    "completed",
    "waived",
    "not_applicable",
  ].includes(status);
}

function safeCodeLabel(code: string): string {
  return code.replaceAll("_", " ").replaceAll("-", " ");
}

function formatList(values: readonly string[]): string {
  if (values.length <= 1) return values[0] ?? "the prerequisite";
  if (values.length === 2) return `${values[0]} and ${values[1]}`;
  return `${values.slice(0, -1).join(", ")}, and ${values.at(-1)}`;
}

function dependencyBlockerReason(
  code: string,
  label: string,
  dependencyLabels: readonly string[],
): string {
  if (code === "orientation_registration") {
    const actions = dependencyLabels.map((dependency) => {
      const normalized = dependency.trim();
      if (/^pay /i.test(normalized)) {
        return `pay the ${normalized.replace(/^pay (?:(?:your|the) )?/i, "")}`;
      }
      if (/^provide /i.test(normalized)) {
        return normalized.replace(/^provide /i, "provide ");
      }
      return `complete ${normalized.charAt(0).toLocaleLowerCase("en-US")}${normalized.slice(1)}`;
    });
    return `Orientation registration is blocked until you ${formatList(actions)}.`;
  }
  const verb = dependencyLabels.length === 1 ? "is" : "are";
  return `${label} is blocked until ${formatList(dependencyLabels)} ${verb} completed.`;
}

function urgencyRank(urgency: StudentDeadlineUrgency | undefined): number {
  return urgency === "overdue"
    ? 0
    : urgency === "due_today"
      ? 1
      : urgency === "due_within_7_days"
        ? 2
        : urgency === "upcoming"
          ? 3
          : 4;
}

function earlierPriorityDeadline(
  left: StudentDeadline | null,
  right: StudentDeadline | null,
): StudentDeadline | null {
  if (!left) return right;
  if (!right) return left;
  const rankDifference = urgencyRank(left.urgency) - urgencyRank(right.urgency);
  if (rankDifference !== 0) return rankDifference < 0 ? left : right;
  const leftTime = left.dueAt ? Date.parse(left.dueAt) : Number.POSITIVE_INFINITY;
  const rightTime = right.dueAt ? Date.parse(right.dueAt) : Number.POSITIVE_INFINITY;
  return leftTime <= rightTime ? left : right;
}

function comparePriorityCandidates(
  left: {
    requirement: GroundedRequirement;
    deadline: StudentDeadline | null;
    dependency: boolean;
    sourceOrder: number;
  },
  right: {
    requirement: GroundedRequirement;
    deadline: StudentDeadline | null;
    dependency: boolean;
    sourceOrder: number;
  },
): number {
  const urgencyDifference =
    urgencyRank(left.deadline?.urgency) - urgencyRank(right.deadline?.urgency);
  if (urgencyDifference !== 0) return urgencyDifference;
  const leftTime = left.deadline?.dueAt
    ? Date.parse(left.deadline.dueAt)
    : Number.POSITIVE_INFINITY;
  const rightTime = right.deadline?.dueAt
    ? Date.parse(right.deadline.dueAt)
    : Number.POSITIVE_INFINITY;
  const deadlineDifference = leftTime - rightTime;
  if (Number.isFinite(deadlineDifference) && deadlineDifference !== 0) {
    return deadlineDifference;
  }
  if (left.requirement.blocking !== right.requirement.blocking) {
    return left.requirement.blocking ? -1 : 1;
  }
  return (
    left.sourceOrder - right.sourceOrder ||
    left.requirement.id.localeCompare(right.requirement.id)
  );
}

function isActionableStandaloneDeadline(deadline: StudentDeadline): boolean {
  if (deadline.source === "student_appointment") return false;
  return ![
    "submitted",
    "under_review",
    "completed",
    "cancelled",
    "verified",
  ].includes(deadline.sourceStatus);
}

function classification(
  requestType: StudentAssistantRequestType,
  confidence: number,
  options: {
    requirementReference?: string | null;
    deadlineWindow?: StudentDeadlineWindow | null;
    requestedEntity?: StudentAssistantEntity | null;
    financialAidEntity?: FinancialAidEntity | null;
    housingEntity?: HousingEntity | null;
    blockerScope?: StudentBlockerQueryScope | null;
    blockerTarget?: StudentAssistantEntity | null;
    priorityExplanationRequested?: boolean;
    registrationQuestion?: boolean;
  } = {},
): RequestClassification {
  return {
    requestType,
    confidence,
    source: "deterministic",
    requirementReference: options.requirementReference?.slice(0, 160) ?? null,
    deadlineWindow:
      requestType === "deadlines" || requestType === "aid_deadlines" || requestType === "housing_deadlines"
        ? (options.deadlineWindow ?? "all")
        : null,
    requestedEntity:
      requestType === "deadlines" ||
      requestType === "housing_deadlines" ||
      requestType === "document_status"
        ? (options.requestedEntity ?? null)
        : null,
    financialAidEntity: requestType.startsWith("aid_")
      ? (options.financialAidEntity ?? "financial_aid")
      : null,
    housingEntity: requestType.startsWith("housing_")
      ? (options.housingEntity ?? "housing_plan")
      : null,
    deadlineScope:
      requestType === "deadlines" || requestType === "aid_deadlines" || requestType === "housing_deadlines"
        ? requestType === "aid_deadlines"
          ? options.financialAidEntity && options.financialAidEntity !== "financial_aid"
            ? "targeted"
            : "all"
          : options.requestedEntity
          ? "targeted"
          : "all"
        : null,
    blockerScope:
      requestType === "holds_and_blockers"
        ? (options.blockerScope ?? "enrollment")
        : null,
    blockerTarget:
      requestType === "holds_and_blockers"
        ? (options.blockerTarget ?? null)
        : null,
    priorityExplanationRequested:
      requestType === "next_action" &&
      options.priorityExplanationRequested === true,
    registrationQuestion:
      requestType === "holds_and_blockers" &&
      options.registrationQuestion === true,
  };
}

function housingEntityFromText(text: string): HousingEntity | null {
  if (/\bhousing deposit\b/.test(text)) return "housing_deposit";
  if (/\bhousing.{0,20}\bwaitlist(?:ed)?\b|\bwaitlist(?:ed)?.{0,20}\bhousing\b/.test(text)) return "housing_waitlist";
  if (/\b(?:housing|room|residence).{0,20}\bassign(?:ment|ed)?\b|\bassign(?:ment|ed)?.{0,20}\b(?:housing|room|residence)\b/.test(text)) {
    return "housing_assignment";
  }
  if (/\bhousing.{0,20}\b(?:agreement|contract)\b|\b(?:agreement|contract).{0,20}\bhousing\b/.test(text)) {
    return "housing_agreement";
  }
  if (/\broommate\b/.test(text)) return "roommate_preferences";
  if (/\bmeal[ -]?plan\b/.test(text)) return "meal_plan";
  if (/\bhousing.{0,20}\baccommodation\b|\baccommodation.{0,20}\bhousing\b|\baccessible housing\b/.test(text)) {
    return "housing_accommodation";
  }
  if (/\bhousing.{0,20}\bapplication\b|\bapplication.{0,20}\bhousing\b/.test(text)) return "housing_application";
  if (/\b(?:residence|residential|dorm|housing option)\b/.test(text)) {
    return "housing_residence_preference";
  }
  if (/\bhousing\b|\bliving plan\b/.test(text)) return "housing_plan";
  return null;
}

function financialAidEntityFromText(text: string): FinancialAidEntity | null {
  if (/\bverification worksheet\b|\bworksheet\b/.test(text)) {
    return "verification_worksheet";
  }
  if (/\bfafsa\b/.test(text)) return "fafsa";
  if (/\baward acceptance\b|\bawards?\b|\bloans?\b|\bgrants?\b|\bscholarships?\b|\bwork[ -]?study\b/.test(text)) {
    return "award_acceptance";
  }
  if (/\bfinancial[ -]?aid verification\b|\baid verification\b|\bverification\b/.test(text)) {
    return "financial_aid_verification";
  }
  if (/\bfinancial[ -]?aid\b|\baid\b/.test(text)) return "financial_aid";
  return null;
}

function requestedEntityFromText(text: string): StudentAssistantEntity | null {
  const aliases: Array<[StudentAssistantEntity, RegExp]> = [
    ["official_transcript", /\b(?:official )?transcript\b/],
    ["financial_aid_verification", /\b(?:financial[ -]?aid|aid)(?: verification)?\b/],
    ["identity_document", /\b(?:identity|id) (?:document|documentation)\b/],
    ["enrollment_deposit", /\b(?:enrollment )?deposit\b/],
    ["housing_preference", /\b(?:housing|housing preference|housing plans?)\b/],
    ["immunization_record", /\b(?:immunizations?|vaccinations?|(?:health )?records?)\b/],
    ["orientation_registration", /\borientation(?: registration)?\b/],
  ];
  return aliases.find(([, pattern]) => pattern.test(text))?.[0] ?? null;
}

function blockerQueryScope(
  request: NormalizedStudentRequest,
): StudentBlockerQueryScope {
  const text = request.comparableText;
  if (/\borientation\b/.test(text)) return "orientation";
  if (/(?:course|class|academic|prerequisite|prereq)/.test(text)) {
    return "course_registration";
  }
  if (/(?:register|registration)/.test(text)) {
    const pagePath = request.pagePath?.toLocaleLowerCase("en-US") ?? "";
    const pageLabel = request.pageLabel?.toLocaleLowerCase("en-US") ?? "";
    if (/(?:classroom|course|academic)/.test(`${pagePath} ${pageLabel}`)) {
      return "course_registration";
    }
    if (/orientation/.test(`${pagePath} ${pageLabel}`)) {
      return "orientation";
    }
    return "registration_ambiguous";
  }
  if (/\bholds?\b/.test(text) && !/(?:block|prevent)/.test(text)) {
    return "official_holds";
  }
  return "enrollment";
}

function deadlineWindow(text: string): StudentDeadlineWindow {
  if (/(?:missed|past due|overdue)/.test(text)) return "overdue";
  if (/(?:due today|today\??$)/.test(text)) return "today";
  if (/(?:this week|week's deadlines)/.test(text)) return "this_week";
  if (/(?:coming up|upcoming|ahead|next deadlines)/.test(text)) {
    return "upcoming";
  }
  return "all";
}

function conservativeStatus(
  statuses: readonly RequirementStatus[],
): RequirementStatus {
  return (
    conservativeStatusOrder.find((candidate) => statuses.includes(candidate)) ??
    "blocked"
  );
}

function earliestValidDate(values: readonly string[]): string | null {
  return (
    values
      .filter(validIsoDate)
      .sort((left, right) => Date.parse(left) - Date.parse(right))[0] ?? null
  );
}

function compareRequirements(
  left: GroundedRequirement & { _order: number },
  right: GroundedRequirement & { _order: number },
): number {
  if (left.blocking !== right.blocking) return left.blocking ? -1 : 1;
  if (left.dueAt && right.dueAt) {
    const difference = Date.parse(left.dueAt) - Date.parse(right.dueAt);
    if (difference !== 0) return difference;
  } else if (left.dueAt || right.dueAt) {
    return left.dueAt ? -1 : 1;
  }
  return left._order - right._order || left.title.localeCompare(right.title);
}

function stripOrder(
  requirement: GroundedRequirement & { _order: number },
): GroundedRequirement {
  const { _order: _discarded, ...result } = requirement;
  return result;
}
