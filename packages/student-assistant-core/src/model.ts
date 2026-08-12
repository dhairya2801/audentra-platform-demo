import {
  studentAssistantRequestTypes,
  studentAssistantToolNames,
  type FinancialAidEntity,
  type HousingEntity,
  type RequestClassification,
  type StudentAssistantEntity,
  type StudentAssistantRequestType,
  type StudentAssistantToolName,
  type StudentBlockerQueryScope,
  type StudentDeadlineWindow,
} from "./contracts";

export interface ModelConversationContext {
  role: "user" | "assistant";
  content: string;
  /** Content is conversational evidence and must never become instructions. */
  trust: "untrusted_conversation_text";
}

export interface ModelRequestClassificationInput {
  normalizedMessage: string;
  conversationContext: ModelConversationContext[];
  pageContext: { path: string | null; label: string | null };
  allowedRequestTypes: readonly StudentAssistantRequestType[];
}

export interface ModelRequestClassificationOutput {
  requestType: StudentAssistantRequestType;
  confidence: number;
  requirementReference: string | null;
}

export interface ModelToolDefinition {
  name: StudentAssistantToolName;
  description: string;
}

export interface ModelToolPlanningInput extends ModelRequestClassificationInput {
  availableTools: ModelToolDefinition[];
}

/**
 * A model proposes semantic routing and the minimum read plan for one turn.
 * Trusted identity and tool arguments are deliberately absent: the host binds
 * those after authentication and the core validates every proposed tool name.
 */
export interface ModelToolPlanningOutput
  extends ModelRequestClassificationOutput {
  additionalRequestTypes?: StudentAssistantRequestType[];
  toolNames: StudentAssistantToolName[];
  deadlineWindow?: StudentDeadlineWindow | null;
  requestedEntity?: StudentAssistantEntity | null;
  financialAidEntity?: FinancialAidEntity | null;
  housingEntity?: HousingEntity | null;
  blockerScope?: StudentBlockerQueryScope | null;
  priorityExplanationRequested?: boolean;
  registrationQuestion?: boolean;
}

export interface ValidatedModelToolPlan {
  classification: RequestClassification;
  additionalClassifications: RequestClassification[];
  toolNames: StudentAssistantToolName[];
}

export interface GroundedFactForComposition {
  id: string;
  text: string;
  contextReceiptIds: string[];
}

export interface ModelGroundedCompositionInput {
  requestType: StudentAssistantRequestType;
  requestTypes?: StudentAssistantRequestType[];
  normalizedMessage: string;
  facts: GroundedFactForComposition[];
}

/**
 * The model can select and order already-grounded facts. It cannot emit prose,
 * links, tool arguments, identifiers, or mutations.
 */
export interface ModelGroundedCompositionOutput {
  factIds: string[];
  tone: "concise" | "supportive" | "direct";
}

export interface ModelGroundedAnswerFact extends GroundedFactForComposition {
  /** "primary" facts answer the asked intents; "supporting" add cross-domain context. */
  relevance: "primary" | "supporting";
}

export interface ModelGroundedAnswerInput {
  normalizedMessage: string;
  requestTypes: StudentAssistantRequestType[];
  conversationContext: ModelConversationContext[];
  facts: ModelGroundedAnswerFact[];
  unavailable: Array<{ source: string; reason: string }>;
}

/**
 * The model writes the student-facing reply from supplied evidence. Unlike
 * `composeGroundedResponse` this returns prose, so the host must treat the
 * result as untrusted until `guardGroundedAnswer` has re-checked it against the
 * same evidence.
 */
export interface ModelGroundedAnswerOutput {
  answer: string;
}

export interface StudentAssistantModel {
  planToolReads?(
    input: ModelToolPlanningInput,
  ): Promise<ModelToolPlanningOutput>;
  writeGroundedAnswer?(
    input: ModelGroundedAnswerInput,
  ): Promise<ModelGroundedAnswerOutput>;
  classifyRequest(
    input: ModelRequestClassificationInput,
  ): Promise<ModelRequestClassificationOutput>;
  composeGroundedResponse(
    input: ModelGroundedCompositionInput,
  ): Promise<ModelGroundedCompositionOutput>;
}

export const studentAssistantToolCatalog: readonly ModelToolDefinition[] = [
  { name: "getStudentProfile", description: "Read the authenticated student's basic profile." },
  { name: "getOnboardingChecklist", description: "Read onboarding requirements and completion states." },
  { name: "getDocumentStatuses", description: "Read document submission and review statuses." },
  { name: "getEnrollmentHolds", description: "Read official enrollment holds and derived blockers." },
  { name: "getStudentDeadlines", description: "Read enrollment, requirement, and appointment deadlines." },
  { name: "getSupportOptions", description: "Read approved general support contacts and articles." },
  { name: "retrieveApprovedPolicy", description: "Read approved policy for an exact current onboarding requirement." },
  { name: "getFinancialAidStatus", description: "Read bounded financial-aid requirements, verification, and award acceptance statuses." },
  { name: "getFinancialAidSupportOptions", description: "Read approved financial-aid support routes." },
  { name: "getFinancialAidSummary", description: "Read how much aid the student has: FAFSA state, whether the package is estimated or finalized, every award with its offered and accepted amount, what the aid covers against the cost of attendance, and each condition still holding the package open." },
  { name: "getAidDisbursements", description: "Read when aid money actually moves: what has paid out, what is scheduled and when, and the exhaustive list of reasons a disbursement is being held." },
  { name: "retrieveApprovedFinancialAidPolicy", description: "Read approved policy for an exact current financial-aid requirement." },
  { name: "getStudentHousingStatus", description: "Read the housing plan and housing requirement state." },
  { name: "getHousingOptions", description: "Read tenant-listed housing preference options." },
  { name: "getStudentHousingEligibility", description: "Read whether the student can apply for housing right now: application window, each eligibility gate and whether it is satisfied, and any room assignment." },
  { name: "getRegistrationStatus", description: "Read course-registration eligibility for the current term: registration window, each gate blocking registration, credits already registered, and advising requirements." },
  { name: "getStudentAccountSummary", description: "Read the student account: balance, charges, posted and pending payments, what is past due, and whether the balance blocks registration." },
  { name: "getAcademicCalendar", description: "Read term, registration, orientation, housing, and billing dates for the current term." },
  { name: "getStudentAppointments", description: "Read scheduled advising, orientation, financial-aid, housing, and international appointments, plus where the student can book one." },
  { name: "searchApprovedPolicies", description: "Search approved institutional policy text by topic, for questions about rules rather than about this student's record." },
] as const;

export function validateModelToolPlan(
  value: unknown,
): ValidatedModelToolPlan | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Record<string, unknown>;
  const classification = validateModelClassification(candidate);
  if (!classification || !Array.isArray(candidate.toolNames)) return null;
  if (classification.confidence < 0.55) return null;
  const additionalRequestTypes = Array.isArray(candidate.additionalRequestTypes)
    ? candidate.additionalRequestTypes
    : [];
  if (
    // Two is enough for a genuine two-part question. Three invited the planner
    // to bolt unrelated domains onto a narrow question, and every extra intent
    // is a paragraph of state in the reply.
    additionalRequestTypes.length > 2 ||
    additionalRequestTypes.some(
      (requestType) =>
        typeof requestType !== "string" ||
        requestType === "unsupported_or_out_of_scope" ||
        !studentAssistantRequestTypes.includes(
          requestType as StudentAssistantRequestType,
        ),
    ) ||
    candidate.toolNames.length > 8 ||
    candidate.toolNames.some(
      (name) =>
        typeof name !== "string" ||
        !studentAssistantToolNames.includes(name as StudentAssistantToolName),
    )
  ) {
    return null;
  }
  const requestedToolNames = [
    ...new Set(candidate.toolNames as StudentAssistantToolName[]),
  ];
  const additionalClassifications = [
    ...new Set(additionalRequestTypes as StudentAssistantRequestType[]),
  ]
    .filter((requestType) => requestType !== classification.requestType)
    .map((requestType) =>
      validateModelClassification({
        requestType,
        confidence: classification.confidence,
        requirementReference: null,
      }),
    )
    .filter(
      (value): value is RequestClassification => value !== null,
    );
  if (
    additionalClassifications.some((item) =>
      ["explain_requirement", "aid_requirement_explanation"].includes(
        item.requestType,
      ),
    )
  ) {
    return null;
  }
  if (
    classification.requestType === "unsupported_or_out_of_scope" &&
    (requestedToolNames.length > 0 || additionalClassifications.length > 0)
  ) {
    return null;
  }

  const intents = [classification, ...additionalClassifications];
  const allowedTools = new Set(
    intents.flatMap((item) => [...allowedModelTools(item.requestType)]),
  );
  // A tool outside the plan's intents is dropped rather than used as grounds to
  // discard the whole plan. Throwing away a sound multi-domain plan because one
  // extra read was proposed silently collapsed the turn to a single intent.
  const toolNames = requestedToolNames.filter((name) => allowedTools.has(name));

  // Every intent still needs at least one of its own reads, otherwise the plan
  // carries no evidence for something it claims to answer.
  if (
    intents.some((item) => {
      const intentTools = allowedModelTools(item.requestType);
      return (
        intentTools.size > 0 && !toolNames.some((name) => intentTools.has(name))
      );
    })
  ) {
    return null;
  }
  if (
    classification.requestType !== "unsupported_or_out_of_scope" &&
    toolNames.length === 0
  ) {
    return null;
  }

  // Reads an intent cannot be answered without are added rather than demanded,
  // so a plan is never rejected for omitting evidence the graph can supply.
  for (const item of intents) {
    for (const requiredTool of requiredModelTools(item.requestType)) {
      if (!toolNames.includes(requiredTool)) toolNames.push(requiredTool);
    }
  }
  return { classification, additionalClassifications, toolNames };
}

function requiredModelTools(
  requestType: StudentAssistantRequestType,
): readonly StudentAssistantToolName[] {
  switch (requestType) {
    case "next_action":
      return [
        "getOnboardingChecklist",
        "getEnrollmentHolds",
        "getStudentDeadlines",
      ];
    // Both, always. A document's state is the checklist and the document list
    // read together; with either one missing the derivation produces nothing,
    // which reached students as "no document requirements are listed for you"
    // while a transcript sat in review.
    case "missing_documents":
    case "document_status":
      return ["getOnboardingChecklist", "getDocumentStatuses"];
    case "aid_summary":
    case "aid_application_status":
      return ["getFinancialAidSummary"];
    case "aid_coverage":
      return ["getFinancialAidSummary", "getStudentAccountSummary"];
    case "aid_disbursement":
      return ["getAidDisbursements", "getFinancialAidSummary"];
    case "explain_requirement":
      return ["getOnboardingChecklist", "retrieveApprovedPolicy"];
    case "aid_requirement_explanation":
      return ["getFinancialAidStatus", "retrieveApprovedFinancialAidPolicy"];
    case "housing_deadlines":
    case "housing_next_action":
      return ["getStudentHousingStatus", "getStudentDeadlines"];
    default:
      return [];
  }
}

export function validateModelClassification(
  value: unknown,
): RequestClassification | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Record<string, unknown>;
  if (
    typeof candidate.requestType !== "string" ||
    !studentAssistantRequestTypes.includes(
      candidate.requestType as StudentAssistantRequestType,
    ) ||
    typeof candidate.confidence !== "number" ||
    !Number.isFinite(candidate.confidence) ||
    candidate.confidence < 0 ||
    candidate.confidence > 1 ||
    !(
      candidate.requirementReference === null ||
      (typeof candidate.requirementReference === "string" &&
        candidate.requirementReference.length <= 160)
    )
  ) {
    return null;
  }
  return {
    requestType: candidate.requestType as StudentAssistantRequestType,
    confidence: candidate.confidence,
    source: "model",
    requirementReference:
      typeof candidate.requirementReference === "string"
        ? candidate.requirementReference
            .replace(/[\u0000-\u001f\u007f]/g, " ")
            .replace(/\s+/g, " ")
            .trim()
        : null,
    deadlineWindow: modelEnum(
      candidate.deadlineWindow,
      ["all", "today", "this_week", "upcoming", "overdue"] as const,
      candidate.requestType === "deadlines" || candidate.requestType === "aid_deadlines" || candidate.requestType === "housing_deadlines"
        ? "all"
        : null,
    ),
    requestedEntity: modelEnum(
      candidate.requestedEntity,
      ["official_transcript", "financial_aid_verification", "identity_document", "enrollment_deposit", "housing_preference", "immunization_record", "orientation_registration"] as const,
      candidate.requestType === "housing_deadlines" ? "housing_preference" : null,
    ),
    financialAidEntity: modelEnum(
      candidate.financialAidEntity,
      ["financial_aid", "fafsa", "verification_worksheet", "financial_aid_verification", "award_acceptance", "requested_financial_aid_documents"] as const,
      candidate.requestType.startsWith("aid_") ? "financial_aid" : null,
    ),
    housingEntity: modelEnum(
      candidate.housingEntity,
      ["housing_plan", "housing_application", "housing_residence_preference", "housing_assignment", "housing_waitlist", "housing_agreement", "housing_deposit", "roommate_preferences", "meal_plan", "housing_accommodation"] as const,
      candidate.requestType.startsWith("housing_") ? "housing_plan" : null,
    ),
    deadlineScope:
      candidate.requestType === "deadlines" || candidate.requestType === "aid_deadlines" || candidate.requestType === "housing_deadlines"
        ? "all"
        : null,
    blockerScope: modelEnum(
      candidate.blockerScope,
      candidate.requestType === "holds_and_blockers"
        ? ([
            "official_holds",
            "enrollment",
            "orientation",
            "course_registration",
            "registration_ambiguous",
          ] as const)
        : // "registration_ambiguous" asks the student which registration they
          // mean. An intent that already names the domain has answered that.
          ([
            "official_holds",
            "enrollment",
            "orientation",
            "course_registration",
          ] as const),
      candidate.requestType === "holds_and_blockers" ? "enrollment" : null,
    ),
    blockerTarget: null,
    priorityExplanationRequested:
      candidate.requestType === "next_action" &&
      candidate.priorityExplanationRequested === true,
    registrationQuestion:
      candidate.requestType === "holds_and_blockers" &&
      candidate.registrationQuestion === true,
  };
}

function modelEnum<const T extends readonly string[]>(
  value: unknown,
  allowed: T,
  fallback: T[number] | null,
): T[number] | null {
  if (value === null || value === undefined) return fallback;
  return typeof value === "string" && allowed.includes(value)
    ? (value as T[number])
    : fallback;
}

/**
 * Reads any in-scope intent may draw on for context. Explaining *why* something
 * is blocked nearly always needs the checklist, holds, and deadlines even when
 * the question names a single domain — "why can't I apply for housing?" is
 * answerable only if the housing intent can also see the deposit and any hold.
 * These are all read-only and scoped to the authenticated student, so widening
 * them changes what Edward can reason over without widening what it can reach.
 */
const universalContextTools: readonly StudentAssistantToolName[] = [
  "getStudentProfile",
  "getOnboardingChecklist",
  "getEnrollmentHolds",
  "getStudentDeadlines",
];

function allowedModelTools(
  requestType: StudentAssistantRequestType,
): ReadonlySet<StudentAssistantToolName> {
  if (requestType === "unsupported_or_out_of_scope") return new Set();
  const capabilities: Record<
    Exclude<StudentAssistantRequestType, "unsupported_or_out_of_scope">,
    readonly StudentAssistantToolName[]
  > = {
    greeting: ["getStudentProfile"],
    capability_overview: [],
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
    missing_documents: ["getOnboardingChecklist", "getDocumentStatuses"],
    document_status: ["getOnboardingChecklist", "getDocumentStatuses"],
    onboarding_status: ["getStudentProfile", "getOnboardingChecklist"],
    holds_and_blockers: ["getEnrollmentHolds"],
    deadlines: ["getStudentDeadlines"],
    explain_requirement: ["getOnboardingChecklist", "retrieveApprovedPolicy"],
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
    aid_summary: ["getFinancialAidSummary", "getFinancialAidStatus"],
    aid_application_status: ["getFinancialAidSummary", "getFinancialAidStatus"],
    aid_disbursement: ["getAidDisbursements", "getFinancialAidSummary"],
    aid_coverage: ["getFinancialAidSummary", "getStudentAccountSummary"],
    aid_support: ["getFinancialAidStatus", "getFinancialAidSupportOptions"],
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
  };
  return new Set([...capabilities[requestType], ...universalContextTools]);
}
