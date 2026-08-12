import type {
  StudentDocument,
  StudentRequirementDetail,
  StudentRequirementSummary,
} from "@vv/contracts";
import {
  classifyRequestDeterministically,
  deriveEnrollmentBlockers,
  derivePrioritizedAction,
  derivePriorityEvidence,
  deriveRequirements,
  deriveStudentDeadlines,
  hasCurrentDocument,
  filterEnrollmentBlockers,
  isCompletedRequirementStatus,
  mostRelevantDocumentStatus,
  type RequirementEvidence,
} from "./deterministic";
import {
  documentSubmissionState,
  isAwaitingReviewSubmissionState,
  isSettledSubmissionState,
  strongestSubmissionState,
} from "./document-lifecycle";
import {
  deriveFinancialAidResult,
  financialAidRequirementMatchesEntity,
} from "./financial-aid";
import { deriveHousingResult } from "./housing";
import type {
  AcademicCalendarRead,
  AidDisbursementsRead,
  FinancialAidSummaryRead,
  ApprovedPolicy,
  ApprovedPolicySearchRead,
  GroundedRequirement,
  RegistrationStatusRead,
  StudentAccountSummaryRead,
  StudentAppointmentsRead,
  StudentHousingEligibilityRead,
  PrioritizedStudentAction,
  RequestClassification,
  StudentAssistantContextReceipt,
  StudentAssistantRequestType,
  StudentAssistantToolName,
  StudentAssistantTools,
  StudentAssistantUnavailableData,
  StudentToolContext,
  ToolReadResult,
  StudentAssistantPriorPriorityContext,
  StudentDeadline,
  StudentDocumentState,
} from "./contracts";
import type {
  DerivedStudentState,
  ExecutedToolRead,
  NormalizedStudentRequest,
  StudentAssistantToolExecutions,
  ToolExecutionNodeResult,
} from "./state";

const defaultHistoryLimit = 6;
const maximumMessageCharacters = 2_000;
const maximumHistoryCharacters = 1_200;
const maximumPageContextCharacters = 240;

export interface NormalizeRequestNodeOptions {
  historyLimit?: number;
}

export interface ExecuteToolReadsNodeOptions {
  timeoutMs?: number;
}

export interface RetrievePolicyNodeResult {
  derived: DerivedStudentState;
  read: ExecutedToolRead<ApprovedPolicy> | null;
  aidRead: ExecutedToolRead<import("./contracts").ApprovedAidPolicyExcerpt> | null;
  unavailableData: StudentAssistantUnavailableData[];
  executed: boolean;
}

export interface DeriveStudentStateNodeOptions {
  now: Date;
  institutionalTimeZone: string | null;
  priorPriority?: StudentAssistantPriorPriorityContext | null;
}

export function normalizeRequestNode(
  message: string,
  context: {
    history: readonly { role: "user" | "assistant"; content: string }[];
    pageContext?: { path: string; label: string };
  },
  options: NormalizeRequestNodeOptions = {},
): NormalizedStudentRequest {
  const historyLimit = Math.max(
    0,
    Math.min(options.historyLimit ?? defaultHistoryLimit, 12),
  );
  const text = normalizeText(message, maximumMessageCharacters);
  const history = context.history.slice(-historyLimit).map((item) => ({
    role: item.role,
    content: normalizeText(item.content, maximumHistoryCharacters),
  }));
  const isFollowUp =
    history.length > 0 &&
    (/^(?:and |also |so |then |what about|how about|those|them|it\b)/i.test(text) ||
      isReferential(text));
  return {
    text,
    resolvedText: resolveReferent(text, history, isFollowUp),
    comparableText: text.toLocaleLowerCase("en-US"),
    history,
    pagePath: context.pageContext
      ? normalizeText(context.pageContext.path, maximumPageContextCharacters)
      : null,
    pageLabel: context.pageContext
      ? normalizeText(context.pageContext.label, maximumPageContextCharacters)
      : null,
    isFollowUp,
    isMutationRequest: detectsMutationRequest(text),
    containsSensitiveFinancialData: detectsSensitiveFinancialData(text),
  };
}

/**
 * A short question whose subject is a pronoun. "How do I fix that?" names
 * nothing, so a planner asked to route it from the words alone has to guess --
 * and it guessed wrong, answering about a balance when the previous turn had
 * named an immunisation record and an advising meeting.
 */
function isReferential(text: string): boolean {
  return (
    text.length <= 80 &&
    /\b(?:that|this|those|these|it|them|the first one|the other one)\b\s*[?.!]?$|^(?:how|what|why|when|who)\b[^?]{0,48}\b(?:that|this|those|these|it|them)\b/i.test(
      text,
    )
  );
}

/**
 * Resolve the referent deterministically rather than with a second model call.
 *
 * Both halves of the previous turn matter, and the assistant's half matters
 * more: after "why can't I register?" answered with "your immunisation record
 * and your advising meeting", the "that" in "how do I fix that?" points at
 * those two items, not at the whole question. Quoting only the question sent
 * the follow-up back to re-enumerate every blocker.
 */
function resolveReferent(
  text: string,
  history: readonly { role: "user" | "assistant"; content: string }[],
  isFollowUp: boolean,
): string {
  if (!isFollowUp) return text;
  const recent = [...history].reverse();
  const priorQuestion = recent.find(
    (message) => message.role === "user" && message.content.trim().length > 0,
  );
  const priorAnswer = recent.find(
    (message) => message.role === "assistant" && message.content.trim().length > 0,
  );
  if (!priorQuestion && !priorAnswer) return text;
  return [
    text,
    "",
    "This is a follow-up. Resolve what it refers to from the turn before it, and answer only that rather than restating everything.",
    ...(priorQuestion
      ? [`Their previous question: "${priorQuestion.content.slice(0, 240)}"`]
      : []),
    ...(priorAnswer
      ? [`Your previous answer: "${priorAnswer.content.slice(0, 400)}"`]
      : []),
  ].join("\n");
}

export function deterministicClassificationNode(
  request: NormalizedStudentRequest,
): RequestClassification | null {
  return classifyRequestDeterministically(request);
}

/**
 * These decisions stay ahead of every model call. They protect a deliberately
 * read-only assistant from mutation requests and keep pasted high-risk
 * financial data out of provider prompts.
 */
export function deterministicSafetyClassificationNode(
  request: NormalizedStudentRequest,
): RequestClassification | null {
  // Another person's contact details are a privacy boundary, not a housing
  // question. Routed as one, it produced a plausible-sounding answer about the
  // student's own profile instead of a refusal.
  if (requestsAnotherPersonsContactDetails(request.comparableText)) {
    return {
      requestType: "unsupported_or_out_of_scope",
      confidence: 1,
      source: "deterministic",
      requirementReference: "other_person_contact_details",
      deadlineWindow: null,
      requestedEntity: null,
      financialAidEntity: null,
      housingEntity: null,
      deadlineScope: null,
      blockerScope: null,
      blockerTarget: null,
      priorityExplanationRequested: false,
      registrationQuestion: false,
    };
  }
  if (!request.isMutationRequest && !request.containsSensitiveFinancialData) {
    return null;
  }
  return classifyRequestDeterministically(request);
}

/**
 * Deliberately narrow: it matches a *person* plus a contact detail. Asking who
 * an adviser is, or how to reach an office, stays a perfectly good question.
 */
function requestsAnotherPersonsContactDetails(text: string): boolean {
  return /\b(?:roommate|room-?mate|classmate|another student|other students?|someone else|my friend|another person)(?:'|’)?s?\b[^.?]{0,40}\b(?:phone|number|mobile|cell|email|e-mail|address|contact details?|contact info\w*)\b|\b(?:phone|number|email|address|contact details?)\b[^.?]{0,32}\b(?:roommate|room-?mate|classmate|another student)\b/.test(
    text,
  );
}

/**
 * Conversational openers, settled ahead of every model call. These have one
 * right answer each and no record behind them, so routing them through a
 * planner costs money to make the answer worse.
 */
export function deterministicConversationalClassificationNode(
  request: NormalizedStudentRequest,
): RequestClassification | null {
  const classification = classifyRequestDeterministically(request);
  return classification &&
    ["greeting", "capability_overview", "general_help"].includes(
      classification.requestType,
    )
    ? classification
    : null;
}

export function selectToolReadsNode(
  requestType: StudentAssistantRequestType,
): StudentAssistantToolName[] {
  const rules: Record<
    StudentAssistantRequestType,
    readonly StudentAssistantToolName[]
  > = {
    // A greeting reads the student's own name and nothing else; the capability
    // overview reads nothing at all. Pulling a checklist to say hello is both
    // slower and worse -- it invites the answer to become a status report.
    greeting: ["getStudentProfile"],
    capability_overview: [],
    // "I don't know where to start" is a real question about this student, and
    // the useful answer is where to start.
    general_help: [
      "getOnboardingChecklist",
      "getEnrollmentHolds",
      "getStudentDeadlines",
    ],
    remaining_steps: ["getOnboardingChecklist"],
    completed_steps: ["getOnboardingChecklist"],
    next_action: [
      "getOnboardingChecklist",
      "getEnrollmentHolds",
      "getStudentDeadlines",
    ],
    missing_documents: [
      "getOnboardingChecklist",
      "getDocumentStatuses",
    ],
    document_status: ["getOnboardingChecklist", "getDocumentStatuses"],
    onboarding_status: ["getOnboardingChecklist"],
    holds_and_blockers: ["getEnrollmentHolds"],
    deadlines: ["getStudentDeadlines"],
    explain_requirement: [
      "getOnboardingChecklist",
      "retrieveApprovedPolicy",
    ],
    request_support: ["getSupportOptions"],
    aid_status: ["getFinancialAidStatus"],
    aid_remaining_steps: ["getFinancialAidStatus"],
    aid_incomplete_reason: ["getFinancialAidStatus"],
    aid_missing_documents: ["getFinancialAidStatus"],
    aid_verification_status: ["getFinancialAidStatus"],
    aid_deadlines: ["getFinancialAidStatus"],
    aid_award_acceptance_status: ["getFinancialAidStatus"],
    aid_requirement_explanation: [
      "getFinancialAidStatus",
      "retrieveApprovedFinancialAidPolicy",
    ],
    aid_next_action: ["getFinancialAidStatus"],
    aid_support: ["getFinancialAidStatus", "getFinancialAidSupportOptions"],
    aid_summary: ["getFinancialAidSummary", "getFinancialAidStatus"],
    aid_application_status: ["getFinancialAidSummary", "getFinancialAidStatus"],
    aid_disbursement: [
      "getAidDisbursements",
      "getFinancialAidSummary",
      "getEnrollmentHolds",
    ],
    // Coverage crosses into billing by its nature: "does my aid cover tuition"
    // is unanswerable from the aid record alone.
    aid_coverage: ["getFinancialAidSummary", "getStudentAccountSummary"],
    housing_status: ["getStudentHousingStatus"],
    housing_options: ["getHousingOptions"],
    housing_remaining_steps: ["getStudentHousingStatus"],
    housing_deadlines: ["getStudentHousingStatus", "getStudentDeadlines"],
    housing_next_action: ["getStudentHousingStatus", "getStudentDeadlines"],
    housing_support: ["getStudentHousingStatus", "getSupportOptions"],
    housing_eligibility: [
      "getStudentHousingEligibility",
      "getStudentHousingStatus",
      "getOnboardingChecklist",
      "getEnrollmentHolds",
    ],
    registration_status: [
      "getRegistrationStatus",
      "getEnrollmentHolds",
      "getOnboardingChecklist",
    ],
    student_account: ["getStudentAccountSummary", "getEnrollmentHolds"],
    academic_calendar: ["getAcademicCalendar"],
    appointments: ["getStudentAppointments", "getAcademicCalendar"],
    policy_lookup: ["searchApprovedPolicies"],
    unsupported_or_out_of_scope: [],
  };
  return [...rules[requestType]];
}

export async function executeToolReadsNode(
  selectedTools: readonly StudentAssistantToolName[],
  tools: StudentAssistantTools,
  trustedContext: StudentToolContext,
  options: ExecuteToolReadsNodeOptions = {},
): Promise<ToolExecutionNodeResult> {
  const approvedTools = selectedTools.filter(
    (
      tool,
    ): tool is Exclude<
      StudentAssistantToolName,
      | "retrieveApprovedPolicy"
      | "retrieveApprovedFinancialAidPolicy"
      | "searchApprovedPolicies"
    > =>
      tool !== "retrieveApprovedPolicy" &&
      tool !== "retrieveApprovedFinancialAidPolicy" &&
      tool !== "searchApprovedPolicies",
  );
  const timeoutMs = Math.max(25, Math.min(options.timeoutMs ?? 2_500, 30_000));
  const executions = await Promise.all(
    approvedTools.map(async (tool, index) => {
      const result = await boundedToolRead(
        () => invokeReadOnlyTool(tool, tools, trustedContext),
        timeoutMs,
      );
      const receipt = createReceipt(tool, result, index + 1);
      return { tool, read: { result, receipt } };
    }),
  );

  const reads: StudentAssistantToolExecutions = {};
  const receipts: StudentAssistantContextReceipt[] = [];
  const unavailableData: StudentAssistantUnavailableData[] = [];
  for (const execution of executions) {
    assignRead(reads, execution.tool, execution.read);
    receipts.push(execution.read.receipt);
    if (execution.read.result.status === "unavailable") {
      unavailableData.push({
        source: execution.tool,
        reason: execution.read.result.reason,
        retryable: execution.read.result.retryable,
      });
    }
  }
  return {
    reads,
    receipts,
    executedTools: executions.map((execution) => execution.tool),
    unavailableData,
  };
}

export function deriveStudentStateNode(
  classification: RequestClassification,
  execution: ToolExecutionNodeResult,
  options: DeriveStudentStateNodeOptions,
): DerivedStudentState {
  const evidence = collectRequirementEvidence(execution.reads);
  const requirements = deriveRequirements(evidence);
  const unavailableData = deduplicateUnavailable([
    ...execution.unavailableData,
    ...requirements.unavailableData,
  ]);
  const holds = execution.reads.getEnrollmentHolds;
  const allBlockers =
    holds?.result.status === "available"
      ? deriveEnrollmentBlockers(holds.result.data, holds.receipt.id)
      : { officialHolds: [], derivedBlockers: [], nonBlockingActions: [] };
  const blockerDerivation = filterEnrollmentBlockers(
    allBlockers,
    classification.blockerScope,
  );
  const deadlineRead = execution.reads.getStudentDeadlines;
  const deadlineDerivation =
    deadlineRead?.result.status === "available"
      ? deriveStudentDeadlines({
          items: deadlineRead.result.data.items,
          receiptId: deadlineRead.receipt.id,
          receiptObservedAt: deadlineRead.receipt.observedAt,
          receiptSourceVersion: deadlineRead.receipt.sourceVersion,
          institutionalTimeZone: options.institutionalTimeZone,
          now: options.now,
          window: classification.deadlineWindow ?? "all",
          requestedEntity: classification.requestedEntity,
        })
      : {
          allOutstanding: [],
          visible: [],
          bucketCounts: emptyDeadlineBucketCounts(),
          unavailableData: [],
        };
  unavailableData.push(...deadlineDerivation.unavailableData);
  const unavailableDeadlineOrHoldSourceCount =
    (holds?.result.status === "available"
      ? holds.result.data.unavailableSources.length
      : holds
        ? 1
        : 0) +
    (deadlineRead?.result.status === "available"
      ? deadlineRead.result.data.unavailableSources.length
      : deadlineRead
        ? 1
        : 0);
  if (
    holds?.result.status === "available" &&
    holds.result.data.unavailableSources.length > 0
  ) {
    unavailableData.push({
      source: "getEnrollmentHolds",
      reason: "incomplete",
      retryable: holds.result.data.unavailableSources.some(
        (source) => source.retryable,
      ),
    });
  }
  if (
    deadlineRead?.result.status === "available" &&
    deadlineRead.result.data.unavailableSources.length > 0
  ) {
    unavailableData.push({
      source: "getStudentDeadlines",
      reason: "incomplete",
      retryable: deadlineRead.result.data.unavailableSources.some(
        (source) => source.retryable,
      ),
    });
  }
  const missingDocuments = deriveMissingDocuments(
    execution.reads,
    requirements.all,
  );
  const documentStates = deriveDocumentStates(execution.reads, requirements.all);
  const deadlines =
    classification.requestType === "housing_next_action"
      ? deadlineDerivation.visible.filter(
          (deadline) => deadline.requirementCode === "housing_preference",
        )
      : deadlineDerivation.visible;
  const supportOptions = deriveSupportOptions(execution.reads);
  const derivedPriority = derivePrioritizedAction({
    requirements: requirements.remaining,
    holdRead: holds?.result.status === "available" ? holds.result.data : null,
    officialHolds: allBlockers.officialHolds,
    deadlines: deadlineDerivation.allOutstanding,
  });
  const prioritizedAction = currentPriorityAction(
    classification,
    options.priorPriority ?? null,
    derivedPriority.action,
    requirements.remaining,
    deadlineDerivation.allOutstanding,
  );
  const priorityEvidence = prioritizedAction
    ? derivePriorityEvidence({
        action: prioritizedAction,
        holdRead: holds?.result.status === "available" ? holds.result.data : null,
        deadlines: deadlineDerivation.allOutstanding,
        now: options.now,
        currentTopActionId: derivedPriority.action?.id ?? null,
      })
    : null;
  const registrationEligibility =
    classification.blockerScope === "registration_ambiguous"
      ? {
          status: "clarification_required" as const,
          studentSafeReason:
            "Do you mean course registration or orientation registration?",
          contextReceiptIds: [],
        }
      : classification.blockerScope === "course_registration" &&
          holds?.result.status === "available"
      ? {
          status: "unknown" as const,
          studentSafeReason:
            "The current sources do not include an authoritative course-registration hold record; available academic prerequisites are reported separately.",
          contextReceiptIds: [holds.receipt.id],
        }
      : null;
  if (classification.blockerScope === "course_registration") {
    unavailableData.push({
      source: "getEnrollmentHolds",
      reason: "not_configured",
      retryable: false,
    });
  }
  if (derivedPriority.dependencyCycle) {
    unavailableData.push({
      source: "getEnrollmentHolds",
      reason: "conflicting_data",
      retryable: false,
    });
  }
  const nextStep =
    (prioritizedAction?.relatedRequirementCode
      ? requirements.remaining.find(
          (step) => step.code === prioritizedAction.relatedRequirementCode,
        )
      : null) ??
    requirements.remaining.find(
      (step) =>
        step.status !== "blocked" &&
        step.status !== "submitted" &&
        step.status !== "under_review",
    ) ??
    null;
  const suggestedActions = deriveSuggestedActions(
    classification.requestType,
    nextStep,
    missingDocuments,
    prioritizedAction,
  );
  const includeRequirementLists =
    classification.requestType !== "holds_and_blockers";
  const incompleteNonBlockingRequirements =
    classification.blockerScope === "enrollment"
      ? requirements.remaining.filter((requirement) => !requirement.blocking)
      : [];
  // "I don't know where to start" is a next-action question wearing different
  // words, so it gets the same derived priority. Other request types keep it
  // null: a priority attached to a housing question reads as a non-sequitur.
  const responsePrioritizedAction =
    classification.requestType === "next_action" ||
    classification.requestType === "general_help"
      ? prioritizedAction
      : null;
  const financialAidRead = execution.reads.getFinancialAidStatus;
  const financialAid =
    financialAidRead?.result.status === "available"
      ? deriveFinancialAidResult({
          read: financialAidRead.result.data,
          receiptId: financialAidRead.receipt.id,
          receiptObservedAt: financialAidRead.receipt.observedAt,
          receiptSourceVersion: financialAidRead.receipt.sourceVersion,
          institutionalTimeZone: options.institutionalTimeZone,
          now: options.now,
          window: classification.deadlineWindow ?? "all",
          entity: classification.financialAidEntity,
        })
      : null;
  if (financialAid) unavailableData.push(...financialAid.unavailableData);
  const financialAidSupportRead = execution.reads.getFinancialAidSupportOptions;
  if (financialAid && financialAidSupportRead?.result.status === "available") {
    financialAid.supportOptions = financialAidSupportRead.result.data.options;
    financialAid.contextReceiptIds = [
      ...new Set([
        ...financialAid.contextReceiptIds,
        financialAidSupportRead.receipt.id,
      ]),
    ];
  }
  const housingStatusRead = execution.reads.getStudentHousingStatus;
  const housingOptionsRead = execution.reads.getHousingOptions;
  const housingRequest =
    classification.requestType.startsWith("housing_") ||
    housingStatusRead !== undefined ||
    housingOptionsRead !== undefined;
  const housingUnavailable = housingRequest
    ? execution.unavailableData.filter(
        (item) =>
          item.source === "getStudentHousingStatus" ||
          item.source === "getHousingOptions" ||
          item.source === "getStudentDeadlines" ||
          item.source === "getSupportOptions",
      )
    : [];
  const housing = housingRequest
    ? deriveHousingResult({
        status:
          housingStatusRead?.result.status === "available"
            ? housingStatusRead.result.data
            : null,
        statusReceiptId:
          housingStatusRead?.result.status === "available"
            ? housingStatusRead.receipt.id
            : null,
        options:
          housingOptionsRead?.result.status === "available"
            ? housingOptionsRead.result.data
            : null,
        optionsReceiptId:
          housingOptionsRead?.result.status === "available"
            ? housingOptionsRead.receipt.id
            : null,
        deadlines:
          classification.requestType === "housing_deadlines" ||
          classification.requestType === "housing_next_action"
            ? deadlines.filter(
                (deadline) => deadline.requirementCode === "housing_preference",
              )
            : [],
        supportOptions:
          classification.requestType === "housing_support" ? supportOptions : [],
        unavailableData: housingUnavailable,
      })
    : null;

  // Broader university capabilities are carried through in normalized form.
  // They need no bespoke derivation: the reads are already shaped for
  // reasoning, and the evidence builder turns them into grounded facts.
  const capabilityReceiptIds: Record<string, string[]> = {};
  const capability = <T,>(
    read: { result: { status: string }; receipt: { id: string } } | undefined,
    name: string,
  ): T | null => {
    if (!read || read.result.status !== "available") return null;
    capabilityReceiptIds[name] = [read.receipt.id];
    return (read.result as unknown as { data: T }).data;
  };

  return {
    aidSummary: capability<FinancialAidSummaryRead>(
      execution.reads.getFinancialAidSummary,
      "getFinancialAidSummary",
    ),
    aidDisbursements: capability<AidDisbursementsRead>(
      execution.reads.getAidDisbursements,
      "getAidDisbursements",
    ),
    housingEligibility: capability<StudentHousingEligibilityRead>(
      execution.reads.getStudentHousingEligibility,
      "getStudentHousingEligibility",
    ),
    registration: capability<RegistrationStatusRead>(
      execution.reads.getRegistrationStatus,
      "getRegistrationStatus",
    ),
    account: capability<StudentAccountSummaryRead>(
      execution.reads.getStudentAccountSummary,
      "getStudentAccountSummary",
    ),
    calendar: capability<AcademicCalendarRead>(
      execution.reads.getAcademicCalendar,
      "getAcademicCalendar",
    ),
    appointments: capability<StudentAppointmentsRead>(
      execution.reads.getStudentAppointments,
      "getStudentAppointments",
    ),
    policyMatches: capability<ApprovedPolicySearchRead>(
      execution.reads.searchApprovedPolicies,
      "searchApprovedPolicies",
    ),
    capabilityReceiptIds,
    profile:
      execution.reads.getStudentProfile?.result.status === "available"
        ? {
            firstName:
              execution.reads.getStudentProfile.result.data.firstName ?? null,
            preferredName:
              execution.reads.getStudentProfile.result.data.preferredName ?? null,
          }
        : null,
    completedSteps: includeRequirementLists ? requirements.completed : [],
    remainingSteps: includeRequirementLists ? requirements.remaining : [],
    awaitingReviewSteps: includeRequirementLists
      ? requirements.awaitingReview
      : [],
    documentStates,
    blockedSteps: includeRequirementLists ? requirements.blocked : [],
    officialHolds: blockerDerivation.officialHolds,
    derivedBlockers: blockerDerivation.derivedBlockers,
    incompleteNonBlockingRequirements,
    nonBlockingActions: blockerDerivation.nonBlockingActions,
    missingDocuments,
    deadlines,
    prioritizedAction: responsePrioritizedAction,
    priorityEvidence: responsePrioritizedAction ? priorityEvidence : null,
    holdReceiptId:
      holds?.result.status === "available" ? holds.receipt.id : null,
    registrationEligibility,
    capabilitySummary: {
      deadlineBucketCounts: deadlineDerivation.bucketCounts,
      officialHoldCount: blockerDerivation.officialHolds.length,
      derivedBlockerCount: blockerDerivation.derivedBlockers.length,
      priorityDerivationResultCode:
        unavailableDeadlineOrHoldSourceCount > 0 && !prioritizedAction
          ? "data_unavailable"
          : prioritizedAction?.reasonCode ?? derivedPriority.resultCode,
      unavailableDeadlineOrHoldSourceCount,
      financialAidCompletedCount: financialAid?.completedRequirements.length ?? 0,
      financialAidRemainingCount: financialAid?.remainingRequirements.length ?? 0,
      financialAidMissingDocumentCount: financialAid?.missingDocuments.length ?? 0,
      financialAidVerificationState: financialAid?.verificationStatus ?? "unknown",
      financialAidUnavailableSourceCount: financialAid?.unavailableData.length ?? 0,
      financialAidNextActionReasonCode: financialAid?.nextAction?.reasonCode ?? null,
      housingStateResultCode: housing?.planRequirementState ?? "unavailable",
      housingRemainingStepCount: housing?.remainingSteps.length ?? 0,
      housingDeadlineCount: housing?.deadlines.length ?? 0,
      housingUnavailableSourceCount: housing?.unavailableData.length ?? 0,
      housingNextActionReasonCode: housing?.nextAction?.reasonCode ?? null,
    },
    supportOptions,
    suggestedActions,
    unavailableData,
    nextStep,
    policy: null,
    policyReceiptId: null,
    financialAid,
    housing,
  };
}

export async function retrievePolicyNode(input: {
  classification: RequestClassification;
  normalized: NormalizedStudentRequest;
  derived: DerivedStudentState;
  tools: StudentAssistantTools;
  trustedContext: StudentToolContext;
  receiptSequence: number;
  timeoutMs?: number;
}): Promise<RetrievePolicyNodeResult> {
  if (input.classification.requestType === "aid_requirement_explanation") {
    const aid = input.derived.financialAid;
    const requirement = aid
      ? [...aid.remainingRequirements, ...aid.completedRequirements].find(
          (item) =>
            financialAidRequirementMatchesEntity(
              item,
              input.classification.financialAidEntity,
            ),
        )
      : null;
    if (!aid || !requirement || !requirement.policyTopic) {
      return {
        derived: input.derived,
        read: null,
        aidRead: null,
        unavailableData: [
          {
            source: "retrieveApprovedFinancialAidPolicy",
            reason: "incomplete",
            retryable: false,
          },
        ],
        executed: false,
      };
    }
    const timeoutMs = Math.max(25, Math.min(input.timeoutMs ?? 2_500, 30_000));
    const result = await boundedToolRead(
      () =>
        input.tools.retrieveApprovedFinancialAidPolicy(input.trustedContext, {
          requirementCode: requirement.code,
          topic: requirement.policyTopic!,
        }),
      timeoutMs,
    );
    const receipt = createReceipt(
      "retrieveApprovedFinancialAidPolicy",
      result,
      input.receiptSequence,
    );
    const aidRead = { result, receipt };
    if (result.status === "unavailable") {
      return {
        derived: input.derived,
        read: null,
        aidRead,
        unavailableData: [
          {
            source: "retrieveApprovedFinancialAidPolicy",
            reason: result.reason,
            retryable: result.retryable,
          },
        ],
        executed: true,
      };
    }
    if (
      result.data.requirementCode !== requirement.code ||
      result.data.topic !== requirement.policyTopic ||
      !result.data.citationUrl.startsWith("https://") ||
      !isSafeApprovedPolicyText(result.data.studentVisibleText)
    ) {
      const unavailable = {
        status: "unavailable" as const,
        reason: "conflicting_data" as const,
        retryable: false,
      };
      return {
        derived: input.derived,
        read: null,
        aidRead: {
          result: unavailable,
          receipt: {
            ...receipt,
            status: "unavailable",
            observedAt: null,
            sourceVersion: null,
            recordCount: 0,
          },
        },
        unavailableData: [
          {
            source: "retrieveApprovedFinancialAidPolicy",
            reason: "conflicting_data",
            retryable: false,
          },
        ],
        executed: true,
      };
    }
    return {
      derived: {
        ...input.derived,
        financialAid: {
          ...aid,
          policyExplanation: result.data,
          policyContextReceiptId: receipt.id,
          contextReceiptIds: [...new Set([...aid.contextReceiptIds, receipt.id])],
        },
      },
      read: null,
      aidRead,
      unavailableData: [],
      executed: true,
    };
  }
  if (input.classification.requestType !== "explain_requirement") {
    return {
      derived: input.derived,
      read: null,
      aidRead: null,
      unavailableData: [],
      executed: false,
    };
  }
  const requirement = resolvePolicyRequirement(
    input.classification,
    input.normalized,
    input.derived,
  );
  if (!requirement) {
    return {
      derived: input.derived,
      read: null,
      aidRead: null,
      unavailableData: [
        {
          source: "retrieveApprovedPolicy",
          reason: "incomplete",
          retryable: false,
        },
      ],
      executed: false,
    };
  }
  const timeoutMs = Math.max(
    25,
    Math.min(input.timeoutMs ?? 2_500, 30_000),
  );
  const result = await boundedToolRead(
    () =>
      input.tools.retrieveApprovedPolicy(input.trustedContext, {
        requirementCode: requirement.code,
      }),
    timeoutMs,
  );
  const receipt = createReceipt(
    "retrieveApprovedPolicy",
    result,
    input.receiptSequence,
  );
  const read = { result, receipt };
  if (result.status === "unavailable") {
    return {
      derived: input.derived,
      read,
      aidRead: null,
      unavailableData: [
        {
          source: "retrieveApprovedPolicy",
          reason: result.reason,
          retryable: result.retryable,
        },
      ],
      executed: true,
    };
  }
  if (result.data.requirementCode !== requirement.code) {
    const unavailable = {
      status: "unavailable" as const,
      reason: "conflicting_data" as const,
      retryable: false,
    };
    return {
      derived: input.derived,
      read: {
        result: unavailable,
        receipt: {
          ...receipt,
          status: "unavailable",
          observedAt: null,
          sourceVersion: null,
          recordCount: 0,
        },
      },
      aidRead: null,
      unavailableData: [
        {
          source: "retrieveApprovedPolicy",
          reason: "conflicting_data",
          retryable: false,
        },
      ],
      executed: true,
    };
  }
  return {
    derived: {
      ...input.derived,
      policy: result.data,
      policyReceiptId: receipt.id,
    },
    read,
    aidRead: null,
    unavailableData: [],
    executed: true,
  };
}

function isSafeApprovedPolicyText(value: string): boolean {
  return !/<\/?(?:script|iframe|object|embed)|javascript:|data:text\/html|ignore (?:all |the )?(?:previous|system)|system prompt/i.test(
    value,
  );
}

function normalizeText(value: string, maximumCharacters: number): string {
  return value
    .normalize("NFKC")
    .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, maximumCharacters);
}

function resolvePolicyRequirement(
  classification: RequestClassification,
  normalized: NormalizedStudentRequest,
  derived: DerivedStudentState,
) {
  const requirements = [
    ...derived.remainingSteps,
    ...derived.completedSteps,
  ];
  const context = [
    classification.requirementReference ?? "",
    normalized.text,
    ...normalized.history.slice(-4).map((message) => message.content),
  ]
    .join(" ")
    .toLocaleLowerCase("en-US")
    .replaceAll("_", " ")
    .replaceAll("-", " ");
  const matches = requirements
    .filter((requirement) => {
      const code = requirement.code.toLocaleLowerCase("en-US").replaceAll("_", " ");
      const title = requirement.title.toLocaleLowerCase("en-US");
      return context.includes(code) || context.includes(title);
    })
    .sort((left, right) => right.title.length - left.title.length);
  if (matches[0]) return matches[0];
  return requirements.length === 1 ? requirements[0]! : null;
}

const writeIntentPattern =
  /(?:mark|set|change|update|waive|approve|delete|remove).{0,40}(?:complete|completed|status|requirement|hold|record)|(?:approve|accept|decline|submit|upload|change|update).{0,40}(?:award|worksheet|financial[ -]?aid|document)|(?:change|update|select|choose|accept|sign|pay|remove)\b.{0,64}(?:housing|residence|roommate|waitlist)|(?:accept|sign|pay)\b.{0,48}(?:agreement|deposit).{0,32}housing|(?:submit|upload|pay|accept).{0,40}(?:for me|on my behalf)|(?:bypass|override).{0,30}(?:requirement|hold|status)/;

function detectsMutationRequest(message: string): boolean {
  const text = message.toLocaleLowerCase("en-US");
  // Evaluated clause by clause so that a question and an instruction in the
  // same message are judged separately: "what's left? also waive my
  // immunisation requirement" is still a write request.
  return splitClauses(text).some((clause) => {
    if (!writeIntentPattern.test(clause)) return false;
    // Two phrasings look like write verbs but are not requests to write.
    // "I uploaded my document yesterday, why is it still incomplete?" reports
    // something already done, and "did I submit my identity document?" asks
    // about the student's own record. Refusing either sent a student asking a
    // perfectly ordinary question away with a refusal.
    if (requestsActionFromEdward(clause)) return true;
    return !reportsCompletedAction(clause) && !asksAboutOwnRecord(clause);
  });
}

function splitClauses(text: string): string[] {
  return text
    .split(/[.?!;]+|\s+(?:and also|also,|but also)\s+/)
    .map((clause) => clause.trim())
    .filter((clause) => clause.length > 0);
}

function reportsCompletedAction(text: string): boolean {
  return /\bi\s*(?:'ve|have)?\s*(?:already\s+)?(?:uploaded|submitted|sent|paid|completed|finished|accepted|signed|selected|filed)\b/.test(
    text,
  );
}

/**
 * Interrogative openers that make a clause a question about existing state
 * rather than an instruction. An imperative ("mark my transcript complete")
 * starts with the verb and is deliberately not on this list.
 */
function asksAboutOwnRecord(text: string): boolean {
  return /^(?:did|have|has|had|was|were|do|does|is|are|any|what|where|when|which|how|why|who)\b/.test(
    text.trim(),
  );
}

function requestsActionFromEdward(text: string): boolean {
  return /\b(?:for me|on my behalf|can you|could you|please)\b/.test(text);
}

function detectsSensitiveFinancialData(message: string): boolean {
  const text = message.toLocaleLowerCase("en-US");
  return (
    /(?:my\s+)?(?:ssn|social security(?: number)?|bank account|routing number|card number)\s*(?:is|:|#)/.test(
      text,
    ) ||
    /(?:here (?:is|are)|i(?:'m| am) (?:pasting|sharing)|pasted|attached).{0,32}(?:tax return|fafsa|bank statement)/.test(
      text,
    )
  );
}

/** An unimplemented capability reads as "not configured", never as a throw. */
function optionalRead(
  value: Promise<ToolReadResult<unknown>> | undefined,
): Promise<ToolReadResult<unknown>> {
  return (
    value ??
    Promise.resolve({
      status: "unavailable" as const,
      reason: "not_configured" as const,
      retryable: false,
    })
  );
}

async function invokeReadOnlyTool(
  tool: Exclude<
    StudentAssistantToolName,
    | "retrieveApprovedPolicy"
    | "retrieveApprovedFinancialAidPolicy"
    | "searchApprovedPolicies"
  >,
  tools: StudentAssistantTools,
  context: StudentToolContext,
): Promise<ToolReadResult<unknown>> {
  switch (tool) {
    case "getStudentProfile":
      return tools.getStudentProfile(context);
    case "getOnboardingChecklist":
      return tools.getOnboardingChecklist(context);
    case "getDocumentStatuses":
      return tools.getDocumentStatuses(context);
    case "getEnrollmentHolds":
      return tools.getEnrollmentHolds(context);
    case "getStudentDeadlines":
      return tools.getStudentDeadlines(context);
    case "getSupportOptions":
      return tools.getSupportOptions(context);
    case "getFinancialAidSummary":
      return optionalRead(tools.getFinancialAidSummary?.(context));
    case "getAidDisbursements":
      return optionalRead(tools.getAidDisbursements?.(context));
    case "getStudentHousingEligibility":
      return optionalRead(tools.getStudentHousingEligibility?.(context));
    case "getRegistrationStatus":
      return optionalRead(tools.getRegistrationStatus?.(context));
    case "getStudentAccountSummary":
      return optionalRead(tools.getStudentAccountSummary?.(context));
    case "getAcademicCalendar":
      return optionalRead(tools.getAcademicCalendar?.(context));
    case "getStudentAppointments":
      return optionalRead(tools.getStudentAppointments?.(context));
    case "getFinancialAidStatus":
      return tools.getFinancialAidStatus(context);
    case "getFinancialAidSupportOptions":
      return tools.getFinancialAidSupportOptions(context);
    case "getStudentHousingStatus":
      return tools.getStudentHousingStatus(context);
    case "getHousingOptions":
      return tools.getHousingOptions(context);
  }
}

async function boundedToolRead<T>(
  read: () => Promise<ToolReadResult<T>>,
  timeoutMs: number,
): Promise<ToolReadResult<T>> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      Promise.resolve().then(read),
      new Promise<ToolReadResult<T>>((resolve) => {
        timer = setTimeout(
          () =>
            resolve({
              status: "unavailable",
              reason: "timeout",
              retryable: true,
            }),
          timeoutMs,
        );
      }),
    ]);
  } catch {
    return {
      status: "unavailable",
      reason: "upstream_error",
      retryable: true,
    };
  } finally {
    if (timer) clearTimeout(timer);
  }
}

function createReceipt(
  source: StudentAssistantToolName,
  result: ToolReadResult<unknown>,
  sequence: number,
): StudentAssistantContextReceipt {
  return {
    id: `receipt-${sequence}-${source}`,
    source,
    status: result.status,
    observedAt: result.status === "available" ? result.observedAt : null,
    sourceVersion:
      result.status === "available" ? (result.sourceVersion ?? null) : null,
    recordCount: result.status === "available" ? countRecords(source, result.data) : 0,
  };
}

function countRecords(source: StudentAssistantToolName, data: unknown): number {
  if (!data || typeof data !== "object") return 0;
  if (source === "getOnboardingChecklist" || source === "getDocumentStatuses") {
    const items = (data as { items?: unknown }).items;
    return Array.isArray(items) ? items.length : 0;
  }
  if (source === "getStudentDeadlines") {
    const items = (data as { items?: unknown }).items;
    return Array.isArray(items) ? items.length : 0;
  }
  if (source === "getFinancialAidStatus") {
    const aid = data as { items?: unknown; awards?: unknown };
    return (
      (Array.isArray(aid.items) ? aid.items.length : 0) +
      (Array.isArray(aid.awards) ? aid.awards.length : 0)
    );
  }
  if (source === "getFinancialAidSupportOptions") {
    const options = (data as { options?: unknown }).options;
    return Array.isArray(options) ? options.length : 0;
  }
  if (source === "getHousingOptions") {
    const items = (data as { items?: unknown }).items;
    return Array.isArray(items) ? items.length : 0;
  }
  if (source === "getEnrollmentHolds") {
    const holds = data as {
      journey?: unknown;
      requirements?: unknown;
      academicPlan?: unknown;
      financialActions?: unknown;
    };
    return (
      (holds.journey ? 1 : 0) +
      (Array.isArray(holds.requirements) ? holds.requirements.length : 0) +
      (Array.isArray(holds.academicPlan) ? holds.academicPlan.length : 0) +
      (Array.isArray(holds.financialActions) ? holds.financialActions.length : 0)
    );
  }
  if (source === "getSupportOptions") {
    const articles = (data as { articles?: unknown }).articles;
    return (Array.isArray(articles) ? articles.length : 0) + 1;
  }
  return 1;
}

function assignRead(
  reads: StudentAssistantToolExecutions,
  tool: Exclude<
    StudentAssistantToolName,
    | "retrieveApprovedPolicy"
    | "retrieveApprovedFinancialAidPolicy"
    | "searchApprovedPolicies"
  >,
  read: ExecutedToolRead<unknown>,
): void {
  Object.assign(reads, { [tool]: read });
}

function collectRequirementEvidence(
  reads: StudentAssistantToolExecutions,
): RequirementEvidence[] {
  const result: RequirementEvidence[] = [];
  const checklist = reads.getOnboardingChecklist;
  if (checklist?.result.status === "available") {
    checklist.result.data.items.forEach((requirement, order) =>
      result.push({
        requirement,
        receiptId: checklist.receipt.id,
        source: "getOnboardingChecklist",
        order,
      }),
    );
  }
  const holds = reads.getEnrollmentHolds;
  if (holds?.result.status === "available") {
    holds.result.data.requirements.forEach((requirement, order) =>
      result.push({
        requirement: {
          id: requirement.id,
          code: requirement.code,
          title: requirement.label,
          description: requirement.description,
          status: requirement.status,
          blocking: requirement.blockingRequirement,
          dueAt: requirement.dueAt,
          progressPercent: requirement.progressPercent,
        },
        receiptId: holds.receipt.id,
        source: "getEnrollmentHolds",
        order,
      }),
    );
  }
  const deadlines = reads.getStudentDeadlines;
  if (deadlines?.result.status === "available") {
    deadlines.result.data.items.forEach((item, order) => {
      if (
        item.source !== "student_requirement" ||
        !item.requirementId ||
        !item.requirementCode ||
        !isRequirementStatus(item.sourceStatus)
      ) {
        return;
      }
      result.push({
        requirement: {
          id: item.requirementId,
          code: item.requirementCode,
          title: item.label,
          description: item.label,
          status: item.sourceStatus,
          blocking: item.blockingRequirement,
          dueAt: item.dueAt,
          progressPercent: item.completionState === "satisfied" ? 100 : 0,
        },
        receiptId: deadlines.receipt.id,
        source: "getStudentDeadlines",
        order,
      });
    });
  }
  return result;
}

function isRequirementStatus(
  value: string,
): value is import("@vv/contracts").RequirementStatus {
  return [
    "not_applicable",
    "blocked",
    "ready",
    "in_progress",
    "submitted",
    "under_review",
    "completed",
    "waived",
    "rejected",
    "expired",
  ].includes(value);
}

/**
 * Every document-backed requirement with its lifecycle state, derived from the
 * two authoritative reads rather than from either one alone. The checklist says
 * what is required; the document list says what has arrived. Reading only the
 * first is what let Edward report an uploaded transcript as missing.
 */
function deriveDocumentStates(
  reads: StudentAssistantToolExecutions,
  requirements: readonly GroundedRequirement[],
): StudentDocumentState[] {
  const checklist = reads.getOnboardingChecklist;
  const documents = reads.getDocumentStatuses;
  if (
    checklist?.result.status !== "available" ||
    documents?.result.status !== "available"
  ) {
    return [];
  }
  const documentItems = documents.result.data.items;
  const detailsByCode = new Map(
    checklist.result.data.items.map((item) => [item.code, item]),
  );
  return requirements.flatMap((requirement) => {
    const detail = detailsByCode.get(requirement.code);
    if (!detail || detail.submissionType !== "document") return [];
    const category = detail.documentCategory ?? null;
    const candidates = documentItems.filter(
      (document) =>
        document.requirementId === requirement.id ||
        (!document.requirementId && category && document.category === category),
    );
    const submissionState = strongestSubmissionState(candidates);
    const leading = candidates.find(
      (document) => documentSubmissionState(document) === submissionState,
    );
    return [
      {
        requirementId: requirement.id,
        requirementCode: requirement.code,
        title: requirement.title,
        category,
        submissionState,
        requirementStatus: requirement.status,
        owner: isSettledSubmissionState(submissionState)
          ? ("nobody" as const)
          : isAwaitingReviewSubmissionState(submissionState)
            ? ("university" as const)
            : ("student" as const),
        dueAt: requirement.dueAt,
        fileName: leading?.fileName ?? null,
        submittedAt: leading?.createdAt ?? null,
        responsibleOffice: detail.responsibleOffice ?? null,
        navigationRoute: `/enrollment/requirements/${requirement.slug}`,
        contextReceiptIds: [
          ...new Set([...requirement.contextReceiptIds, documents.receipt.id]),
        ],
      },
    ];
  });
}

function deriveMissingDocuments(
  reads: StudentAssistantToolExecutions,
  requirements: readonly ReturnType<typeof deriveRequirements>["all"][number][],
) {
  const checklist = reads.getOnboardingChecklist;
  const documents = reads.getDocumentStatuses;
  if (
    checklist?.result.status !== "available" ||
    documents?.result.status !== "available"
  ) {
    return [];
  }
  const documentItems = documents.result.data.items;
  const detailsByCode = new Map(
    checklist.result.data.items.map((item) => [item.code, item]),
  );
  return requirements.flatMap((requirement) => {
    const detail = detailsByCode.get(requirement.code);
    if (
      !detail ||
      detail.submissionType !== "document" ||
      !detail.documentCategory ||
      isCompletedRequirementStatus(requirement.status)
    ) {
      return [];
    }
    const candidates = documentItems.filter(
      (document) =>
        document.requirementId === requirement.id ||
        (!document.requirementId &&
          document.category === detail.documentCategory),
    );
    if (hasCurrentDocument(candidates)) return [];
    return [
      {
        requirementId: requirement.id,
        requirementCode: requirement.code,
        title: requirement.title,
        category: detail.documentCategory,
        requirementStatus: requirement.status,
        dueAt: requirement.dueAt,
        currentDocumentStatus: mostRelevantDocumentStatus(candidates),
        contextReceiptIds: [
          ...new Set([
            ...requirement.contextReceiptIds,
            documents.receipt.id,
          ]),
        ],
      },
    ];
  });
}

function deriveSupportOptions(reads: StudentAssistantToolExecutions) {
  const supportRead = reads.getSupportOptions;
  if (supportRead?.result.status !== "available") return [];
  const { support, articles } = supportRead.result.data;
  const receiptIds = [supportRead.receipt.id];
  return [
    ...(support.email
      ? [
          {
            kind: "email" as const,
            label: "Support email",
            value: support.email,
            contextReceiptIds: receiptIds,
          },
        ]
      : []),
    ...(support.phone
      ? [
          {
            kind: "phone" as const,
            label: "Support phone",
            value: support.phone,
            contextReceiptIds: receiptIds,
          },
        ]
      : []),
    ...(support.hours
      ? [
          {
            kind: "hours" as const,
            label: "Support hours",
            value: support.hours,
            contextReceiptIds: receiptIds,
          },
        ]
      : []),
    ...articles.slice(0, 6).map((article) => ({
      kind: "article" as const,
      label: article.question,
      article,
      contextReceiptIds: receiptIds,
    })),
  ];
}

function deriveSuggestedActions(
  requestType: StudentAssistantRequestType,
  nextStep: ReturnType<typeof deriveRequirements>["remaining"][number] | null,
  missingDocuments: readonly { requirementCode: string }[],
  prioritizedAction: import("./contracts").PrioritizedStudentAction | null,
) {
  if (requestType.startsWith("aid_")) {
    if (requestType === "aid_support") {
      return [
        { label: "Schedule financial-aid help", href: "/appointments", readOnly: true as const },
      ];
    }
    return [
      { label: "View financial aid", href: "/financials", readOnly: true as const },
    ];
  }
  if (requestType === "request_support") {
    return [{ label: "View support options", href: "/help", readOnly: true as const }];
  }
  if (requestType === "deadlines" || requestType === "onboarding_status") {
    return [{ label: "View enrollment", href: "/enrollment", readOnly: true as const }];
  }
  if (requestType === "holds_and_blockers") {
    return [{ label: "View support options", href: "/help", readOnly: true as const }];
  }
  if (requestType === "next_action" && prioritizedAction?.navigationRoute) {
    return [
      {
        label:
          prioritizedAction.kind === "contact_support"
            ? "View support options"
            : "View requirement",
        href: prioritizedAction.navigationRoute,
        readOnly: true as const,
      },
    ];
  }
  const target =
    requestType === "missing_documents" && missingDocuments[0]
      ? missingDocuments[0].requirementCode.toLowerCase().replaceAll("_", "-")
      : nextStep?.slug;
  return target
    ? [
        {
          label: "View requirement",
          href: `/enrollment/requirements/${target}`,
          readOnly: true as const,
        },
      ]
    : [];
}

function currentPriorityAction(
  classification: RequestClassification,
  priorPriority: StudentAssistantPriorPriorityContext | null,
  derivedAction: PrioritizedStudentAction | null,
  requirements: readonly GroundedRequirement[],
  deadlines: readonly StudentDeadline[],
): PrioritizedStudentAction | null {
  if (!classification.priorityExplanationRequested || !priorPriority) {
    return derivedAction;
  }
  const prior = priorPriority.action;
  if (prior.kind === "contact_support") {
    return derivedAction?.reasonCode === "official_hold_support"
      ? derivedAction
      : null;
  }
  const requirement = prior.relatedRequirementCode
    ? requirements.find((item) => item.code === prior.relatedRequirementCode)
    : null;
  if (requirement) {
    return {
      ...prior,
      label: requirement.title,
      relatedRequirementId: requirement.id,
      navigationRoute: `/enrollment/requirements/${requirement.slug}`,
      contextReceiptIds: requirement.contextReceiptIds,
    };
  }
  const deadline = deadlines.find(
    (item) =>
      (prior.relatedRequirementCode &&
        item.requirementCode === prior.relatedRequirementCode) ||
      item.title === prior.label,
  );
  return deadline
    ? { ...prior, contextReceiptIds: deadline.contextReceiptIds }
    : null;
}

function emptyDeadlineBucketCounts() {
  return {
    overdue: 0,
    dueToday: 0,
    dueWithinSevenDays: 0,
    upcoming: 0,
    completedOrSatisfied: 0,
    unknownDate: 0,
  };
}

function deduplicateUnavailable(
  values: readonly StudentAssistantUnavailableData[],
): StudentAssistantUnavailableData[] {
  const seen = new Set<string>();
  return values.filter((value) => {
    const key = `${value.source}:${value.reason}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}
