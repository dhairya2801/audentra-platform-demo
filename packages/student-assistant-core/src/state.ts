import type {
  StudentDocumentList,
  StudentHelp,
  StudentProfile,
  StudentRequirementList,
} from "@vv/contracts";
import type {
  ApprovedPolicy,
  ApprovedAidPolicyExcerpt,
  AidChecklistRead,
  EnrollmentHoldsRead,
  FinancialAidResult,
  AcademicCalendarRead,
  ApprovedPolicySearchRead,
  HousingOptionsRead,
  HousingResult,
  RegistrationStatusRead,
  StudentAccountSummaryRead,
  StudentAppointmentsRead,
  StudentHousingEligibilityRead,
  StudentHousingStatusRead,
  FinancialAidSupportRead,
  GroundedRequirement,
  MissingDocument,
  NormalizedEnrollmentBlocker,
  PriorityEvidence,
  PrioritizedStudentAction,
  RegistrationEligibilityResult,
  RequestClassification,
  StudentAssistantCapabilitySummary,
  StudentAssistantContextReceipt,
  StudentAssistantGraphTraceEntry,
  StudentAssistantInput,
  StudentAssistantSuggestedAction,
  StudentAssistantToolName,
  StudentAssistantUnavailableData,
  StudentDeadline,
  StudentDeadlinesRead,
  StudentDocumentState,
  FinancialAidSummaryRead,
  AidDisbursementsRead,
  StudentSupportOption,
  ToolReadResult,
} from "./contracts";

export interface NormalizedConversationMessage {
  role: "user" | "assistant";
  content: string;
}

export interface NormalizedStudentRequest {
  text: string;
  /**
   * The question with its referent resolved, for the planner and the writer.
   * "How do I fix that?" is unanswerable on its own; `text` stays the student's
   * literal words for classification and for anything shown back to them.
   */
  resolvedText: string;
  comparableText: string;
  history: NormalizedConversationMessage[];
  pagePath: string | null;
  pageLabel: string | null;
  isFollowUp: boolean;
  isMutationRequest: boolean;
  containsSensitiveFinancialData: boolean;
}

export interface ExecutedToolRead<T> {
  result: ToolReadResult<T>;
  receipt: StudentAssistantContextReceipt;
}

export interface StudentAssistantToolExecutions {
  getStudentProfile?: ExecutedToolRead<StudentProfile>;
  getOnboardingChecklist?: ExecutedToolRead<StudentRequirementList>;
  getDocumentStatuses?: ExecutedToolRead<StudentDocumentList>;
  getEnrollmentHolds?: ExecutedToolRead<EnrollmentHoldsRead>;
  getStudentDeadlines?: ExecutedToolRead<StudentDeadlinesRead>;
  getSupportOptions?: ExecutedToolRead<StudentHelp>;
  retrieveApprovedPolicy?: ExecutedToolRead<ApprovedPolicy>;
  getFinancialAidStatus?: ExecutedToolRead<AidChecklistRead>;
  getFinancialAidSupportOptions?: ExecutedToolRead<FinancialAidSupportRead>;
  retrieveApprovedFinancialAidPolicy?: ExecutedToolRead<ApprovedAidPolicyExcerpt>;
  getStudentHousingStatus?: ExecutedToolRead<StudentHousingStatusRead>;
  getHousingOptions?: ExecutedToolRead<HousingOptionsRead>;
  getFinancialAidSummary?: ExecutedToolRead<FinancialAidSummaryRead>;
  getAidDisbursements?: ExecutedToolRead<AidDisbursementsRead>;
  getStudentHousingEligibility?: ExecutedToolRead<StudentHousingEligibilityRead>;
  getRegistrationStatus?: ExecutedToolRead<RegistrationStatusRead>;
  getStudentAccountSummary?: ExecutedToolRead<StudentAccountSummaryRead>;
  getAcademicCalendar?: ExecutedToolRead<AcademicCalendarRead>;
  getStudentAppointments?: ExecutedToolRead<StudentAppointmentsRead>;
  searchApprovedPolicies?: ExecutedToolRead<ApprovedPolicySearchRead>;
}

export interface ToolExecutionNodeResult {
  reads: StudentAssistantToolExecutions;
  receipts: StudentAssistantContextReceipt[];
  executedTools: StudentAssistantToolName[];
  unavailableData: StudentAssistantUnavailableData[];
}

export interface DerivedStudentState {
  /** The student's own name, for addressing them. Never used as evidence. */
  profile: { firstName: string | null; preferredName: string | null } | null;
  completedSteps: GroundedRequirement[];
  remainingSteps: GroundedRequirement[];
  /** Submitted and waiting on the university, not on the student. */
  awaitingReviewSteps: GroundedRequirement[];
  /** Every document-backed requirement with its authoritative lifecycle state. */
  documentStates: StudentDocumentState[];
  blockedSteps: GroundedRequirement[];
  officialHolds: NormalizedEnrollmentBlocker[];
  derivedBlockers: NormalizedEnrollmentBlocker[];
  incompleteNonBlockingRequirements: GroundedRequirement[];
  nonBlockingActions: NormalizedEnrollmentBlocker[];
  missingDocuments: MissingDocument[];
  deadlines: StudentDeadline[];
  prioritizedAction: PrioritizedStudentAction | null;
  priorityEvidence: PriorityEvidence | null;
  holdReceiptId: string | null;
  registrationEligibility: RegistrationEligibilityResult | null;
  capabilitySummary: StudentAssistantCapabilitySummary;
  supportOptions: StudentSupportOption[];
  suggestedActions: StudentAssistantSuggestedAction[];
  unavailableData: StudentAssistantUnavailableData[];
  nextStep: GroundedRequirement | null;
  policy: ApprovedPolicy | null;
  policyReceiptId: string | null;
  financialAid: FinancialAidResult | null;
  housing: HousingResult | null;
  /** Reads from the broader university capabilities, kept in raw normalized form. */
  aidSummary: FinancialAidSummaryRead | null;
  aidDisbursements: AidDisbursementsRead | null;
  housingEligibility: StudentHousingEligibilityRead | null;
  registration: RegistrationStatusRead | null;
  account: StudentAccountSummaryRead | null;
  calendar: AcademicCalendarRead | null;
  appointments: StudentAppointmentsRead | null;
  policyMatches: ApprovedPolicySearchRead | null;
  /** Receipt ids for each of the above, keyed by tool name. */
  capabilityReceiptIds: Record<string, string[]>;
}

export interface StudentAssistantGraphState {
  input: StudentAssistantInput;
  normalized: NormalizedStudentRequest | null;
  classification: RequestClassification | null;
  selectedTools: StudentAssistantToolName[];
  toolExecution: ToolExecutionNodeResult;
  derived: DerivedStudentState | null;
  draftMessage: string;
  validatedMessage: string;
  trace: StudentAssistantGraphTraceEntry[];
  classificationFallback: boolean;
  compositionFallback: boolean;
  failureCodes: string[];
}

export function createInitialGraphState(
  input: StudentAssistantInput,
): StudentAssistantGraphState {
  return {
    input,
    normalized: null,
    classification: null,
    selectedTools: [],
    toolExecution: {
      reads: {},
      receipts: [],
      executedTools: [],
      unavailableData: [],
    },
    derived: null,
    draftMessage: "",
    validatedMessage: "",
    trace: [],
    classificationFallback: false,
    compositionFallback: false,
    failureCodes: [],
  };
}
