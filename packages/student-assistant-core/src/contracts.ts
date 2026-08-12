import type {
  AssistantInputMode,
  AssistantPageContext,
  EdwardChatMessage,
  HelpArticle,
  RequirementStatus,
  StudentDocument,
  StudentDocumentCategory,
  StudentDocumentList,
  StudentHelp,
  StudentProfile,
  StudentRequirementList,
  StudentRequirementSummary,
  HousingPreference,
  HousingResidenceOption,
} from "@vv/contracts";

export const studentAssistantRequestTypes = [
  "greeting",
  "capability_overview",
  "general_help",
  "remaining_steps",
  "completed_steps",
  "next_action",
  "missing_documents",
  "document_status",
  "onboarding_status",
  "holds_and_blockers",
  "deadlines",
  "explain_requirement",
  "request_support",
  "aid_status",
  "aid_remaining_steps",
  "aid_incomplete_reason",
  "aid_missing_documents",
  "aid_verification_status",
  "aid_deadlines",
  "aid_award_acceptance_status",
  "aid_requirement_explanation",
  "aid_next_action",
  "aid_support",
  "aid_summary",
  "aid_application_status",
  "aid_disbursement",
  "aid_coverage",
  "housing_status",
  "housing_options",
  "housing_remaining_steps",
  "housing_deadlines",
  "housing_next_action",
  "housing_support",
  "housing_eligibility",
  "registration_status",
  "student_account",
  "academic_calendar",
  "appointments",
  "policy_lookup",
  "unsupported_or_out_of_scope",
] as const;

export type StudentAssistantRequestType =
  (typeof studentAssistantRequestTypes)[number];

export const studentAssistantToolNames = [
  "getStudentProfile",
  "getOnboardingChecklist",
  "getDocumentStatuses",
  "getEnrollmentHolds",
  "getStudentDeadlines",
  "getSupportOptions",
  "retrieveApprovedPolicy",
  "getFinancialAidStatus",
  "getFinancialAidSupportOptions",
  "getFinancialAidSummary",
  "getAidDisbursements",
  "retrieveApprovedFinancialAidPolicy",
  "getStudentHousingStatus",
  "getHousingOptions",
  "getStudentHousingEligibility",
  "getRegistrationStatus",
  "getStudentAccountSummary",
  "getAcademicCalendar",
  "getStudentAppointments",
  "searchApprovedPolicies",
] as const;

export type StudentAssistantToolName =
  (typeof studentAssistantToolNames)[number];

/**
 * This context is created by the hosting application after authentication.
 * There is intentionally no tenant or student selector in the user request or
 * in any model result.
 */
export interface TrustedStudentAssistantContext {
  tenantId: string;
  studentId: string;
  conversationId: string;
  inputMode: AssistantInputMode;
  history: readonly EdwardChatMessage[];
  pageContext?: AssistantPageContext;
  priorPriority?: StudentAssistantPriorPriorityContext | null;
}

export interface StudentAssistantInput {
  message: string;
  context: TrustedStudentAssistantContext;
}

export type StudentDeadlineWindow =
  | "all"
  | "today"
  | "this_week"
  | "upcoming"
  | "overdue";

export type StudentAssistantEntity =
  | "official_transcript"
  | "financial_aid_verification"
  | "identity_document"
  | "enrollment_deposit"
  | "housing_preference"
  | "immunization_record"
  | "orientation_registration";

export type FinancialAidEntity =
  | "financial_aid"
  | "fafsa"
  | "verification_worksheet"
  | "financial_aid_verification"
  | "award_acceptance"
  | "requested_financial_aid_documents";

export type HousingEntity =
  | "housing_plan"
  | "housing_application"
  | "housing_residence_preference"
  | "housing_assignment"
  | "housing_waitlist"
  | "housing_agreement"
  | "housing_deposit"
  | "roommate_preferences"
  | "meal_plan"
  | "housing_accommodation";

export type StudentDeadlineScope = "all" | "targeted";

export type StudentBlockerQueryScope =
  | "official_holds"
  | "enrollment"
  | "orientation"
  | "course_registration"
  | "registration_ambiguous";

/** The only identity-bearing value ever passed to a backend tool. */
export interface StudentToolContext {
  tenantId: string;
  studentId: string;
  conversationId: string;
  inputMode: AssistantInputMode;
}

export type ToolUnavailableReason =
  | "not_configured"
  | "not_found"
  | "forbidden"
  | "timeout"
  | "upstream_error"
  | "incomplete"
  | "conflicting_data";

export type ToolReadResult<T> =
  | {
      status: "available";
      data: T;
      observedAt: string;
      sourceVersion?: string;
    }
  | {
      status: "unavailable";
      reason: ToolUnavailableReason;
      retryable: boolean;
    };

export type StudentDeadlineSource =
  | "admission_offer"
  | "student_requirement"
  | "financial_document_requirement"
  | "student_appointment";

export type StudentDeadlineKind =
  | "offer_response"
  | "profile"
  | "document"
  | "financial_aid"
  | "housing"
  | "enrollment_deposit"
  | "orientation"
  | "appointment"
  | "other_requirement";

export interface NormalizedStudentDeadlineSource {
  id: string;
  label: string;
  kind: StudentDeadlineKind;
  dueAt: string | null;
  duePrecision: "date" | "instant";
  sourceStatus: string;
  requirementId: string | null;
  requirementCode: string | null;
  source: StudentDeadlineSource;
  completionState: "outstanding" | "satisfied";
  currentlyBlocking: boolean;
  blockingRequirement: boolean;
  hardOrRecommended: "hard" | "recommended" | null;
  dependencyCodes: string[];
  resolutionOwner: string | null;
  navigationRoute: string | null;
  sourceOrder: number;
  lastVerifiedAt: string | null;
  sourceVersion: string | null;
}

export interface NormalizedUnavailableSource {
  source: StudentDeadlineSource | "student_academics";
  reason: ToolUnavailableReason;
  retryable: boolean;
}

export interface StudentDeadlinesRead {
  items: NormalizedStudentDeadlineSource[];
  unavailableSources: NormalizedUnavailableSource[];
}

export interface NormalizedJourneyHoldSource {
  id: string;
  status: string;
  supportRoute: string | null;
  lastVerifiedAt: string | null;
  sourceVersion: string | null;
  domain: "enrollment";
}

export interface NormalizedRequirementBlockerSource {
  id: string;
  code: string;
  label: string;
  description: string;
  status: RequirementStatus;
  blockingRequirement: boolean;
  dueAt: string | null;
  progressPercent: number;
  slug: string;
  dependencyCodes: string[];
  resolutionOwner: string | null;
  submissionType: string | null;
  supportRoute: string | null;
  sourceOrder: number;
  lastVerifiedAt: string | null;
  sourceVersion: string | null;
  domain: "enrollment";
}

export interface NormalizedAcademicBlockerSource {
  id: string;
  courseCode: string;
  label: string;
  status: string;
  missingPrerequisiteCodes: string[];
  supportRoute: string | null;
  sourceOrder: number;
  lastVerifiedAt: string | null;
  sourceVersion: string | null;
  domain: "course_registration";
}

export interface NormalizedFinancialActionSource {
  id: string;
  code: string;
  label: string;
  status: string;
  supportRoute: string | null;
  sourceOrder: number;
  lastVerifiedAt: string | null;
  sourceVersion: string | null;
  domain: "financial_aid";
}

export interface EnrollmentHoldsRead {
  journey: NormalizedJourneyHoldSource | null;
  requirements: NormalizedRequirementBlockerSource[];
  academicPlan: NormalizedAcademicBlockerSource[];
  financialActions: NormalizedFinancialActionSource[];
  unavailableSources: NormalizedUnavailableSource[];
}

/**
 * Tenant-approved policy text has no existing public repository contract.
 * This is the deliberately narrow adapter result needed by the explanation
 * node; provider/source internals never enter graph state.
 */
export interface ApprovedPolicy {
  id: string;
  requirementCode: string;
  title: string;
  text: string;
  version: string;
  effectiveFrom: string | null;
  effectiveUntil: string | null;
}

export interface ApprovedPolicyRequest {
  requirementCode: string;
}

export type AidRequirementKind =
  | "fafsa"
  | "institutional_form"
  | "verification_document"
  | "award_acceptance"
  | "other";

export type AidRequirementStatus =
  | "not_started"
  | "action_required"
  | "submitted"
  | "under_review"
  | "satisfied"
  | "rejected"
  | "unknown";

export interface NormalizedAidRequirement {
  id: string;
  code: string;
  kind: AidRequirementKind;
  label: string;
  status: AidRequirementStatus;
  dueAt: string | null;
  navigationRoute: string | null;
  policyTopic: string | null;
  authoritativeDocumentState:
    | "none"
    | "uploaded"
    | "processing"
    | "under_review"
    | "accepted"
    | "rejected"
    | "unknown";
  lastVerifiedAt: string | null;
  sourceVersion: string | null;
}

export interface NormalizedAidAwardAcceptance {
  awardId: string;
  awardLabel: string;
  awardType: "grant" | "scholarship" | "loan" | "work_study";
  status: "offered" | "accepted" | "declined" | "pending" | "unknown";
  requiresAction: boolean;
  academicYear: string | null;
  lastVerifiedAt: string | null;
  sourceVersion: string | null;
}

export interface AidChecklistRead {
  academicYear: string | null;
  overallStatus: "complete" | "incomplete" | "unknown";
  items: NormalizedAidRequirement[];
  awards: NormalizedAidAwardAcceptance[];
  highLevelVerificationStatus: RequirementStatus | null;
  unavailableSources: NormalizedUnavailableSource[];
}

export interface ApprovedAidPolicyExcerpt {
  policyId: string;
  topic: string;
  requirementCode: string;
  title: string;
  sourceOwner: string;
  version: string;
  effectiveFrom: string;
  effectiveUntil: string | null;
  sectionId: string;
  studentVisibleText: string;
  citationLabel: string;
  citationUrl: string;
  synthetic: boolean;
}

export interface ApprovedAidPolicyRequest {
  requirementCode: string;
  topic: string;
}

export interface FinancialAidSupportRead {
  financialAidSpecificConfigured: boolean;
  options: Array<
    | { kind: "email" | "phone" | "hours"; label: string; value: string }
    | { kind: "route"; label: string; href: string }
  >;
}

export interface StudentHousingStatusRead {
  plan: {
    preference: HousingPreference | null;
    residencePreference: HousingResidenceOption;
    updatedAt: string;
    version: number;
  };
  requirement: {
    id: string;
    code: "housing_preference";
    title: string;
    status: RequirementStatus;
    dueAt: string | null;
    progressPercent: number;
    blocking: boolean;
    dependencyCodes: string[];
    responsibleOffice: string;
    supportRoute: string;
  } | null;
  supplementalSignals: {
    roommatePreferenceState:
      | "not_applicable"
      | "not_provided"
      | "partially_provided"
      | "provided";
    mealPlanInterest: boolean | null;
    offCampusSearchStatus:
      | "lease_signed"
      | "actively_looking"
      | "need_roommates"
      | "living_with_family"
      | null;
  };
}

export interface HousingOptionsRead {
  items: Array<{
    code: Exclude<HousingResidenceOption, null>;
    name: string;
    description: string;
    amenities: string[];
    listingState: "listed";
    synthetic: boolean;
  }>;
}

/* ---------------------------------------------------------------------------
 * Reusable university capabilities beyond onboarding.
 *
 * Each read answers a family of student questions rather than one phrasing, and
 * each carries enough state for Edward to explain *why* something is the way it
 * is: not just "you cannot register" but which gate is closed, who owns it, and
 * what clears it. Every field is populated from tenant data or the synthetic
 * demo dataset — never from prompt text.
 * ------------------------------------------------------------------------- */

export type EligibilityState = "eligible" | "blocked" | "not_yet_open" | "closed" | "unknown";

export interface EligibilityGate {
  /** Stable machine code, e.g. "enrollment_deposit_unpaid". */
  code: string;
  label: string;
  satisfied: boolean;
  /** Student-safe explanation of what this gate checks. */
  reason: string;
  /** Which office resolves it, when the source knows. */
  resolutionOwner: string | null;
  navigationRoute: string | null;
  /** Requirement or record this gate reads from, for traceability. */
  relatedRequirementCode: string | null;
}

export interface OpenWindow {
  opensAt: string | null;
  closesAt: string | null;
  /** True when the source can place "now" inside the window. */
  open: boolean | null;
}

export interface StudentHousingEligibilityRead {
  state: EligibilityState;
  applicationWindow: OpenWindow;
  gates: EligibilityGate[];
  assignment: {
    state: "assigned" | "not_assigned" | "waitlisted" | "unknown";
    residenceName: string | null;
    roomLabel: string | null;
    moveInAt: string | null;
  };
  /** Housing rules that apply to this student, from the policy layer. */
  applicablePolicyCodes: string[];
  synthetic: boolean;
}

export interface RegistrationStatusRead {
  termCode: string;
  termName: string;
  state: EligibilityState;
  /** When this student's registration window opens, if assigned. */
  registrationWindow: OpenWindow;
  gates: EligibilityGate[];
  registeredCreditCount: number;
  registeredCourseCount: number;
  minimumCredits: number | null;
  maximumCredits: number | null;
  advisingRequired: boolean;
  advisingHoldCleared: boolean | null;
  synthetic: boolean;
}

export interface StudentAccountCharge {
  code: string;
  label: string;
  amountUsd: number;
  dueAt: string | null;
  state: "outstanding" | "paid" | "waived" | "pending";
}

export interface StudentAccountPayment {
  id: string;
  label: string;
  amountUsd: number;
  /** "posted" means it has cleared and other systems can rely on it. */
  state: "posted" | "pending" | "failed";
  postedAt: string | null;
  appliesToChargeCode: string | null;
}

export interface StudentAccountSummaryRead {
  currency: "USD";
  balanceUsd: number;
  pastDueUsd: number;
  charges: StudentAccountCharge[];
  payments: StudentAccountPayment[];
  paymentPlanEnrolled: boolean;
  /** Set when an unpaid balance is itself blocking something. */
  blocksRegistration: boolean;
  nextPaymentDueAt: string | null;
  synthetic: boolean;
}

export interface AcademicCalendarEvent {
  code: string;
  label: string;
  startsAt: string;
  endsAt: string | null;
  category:
    | "term"
    | "registration"
    | "orientation"
    | "housing"
    | "billing"
    | "advising"
    | "holiday";
  audience: "all" | "new_students" | "international" | "residential";
  description: string;
}

export interface AcademicCalendarRead {
  termCode: string;
  termName: string;
  events: AcademicCalendarEvent[];
  synthetic: boolean;
}

export interface StudentAppointmentRecord {
  id: string;
  kind: "advising" | "orientation" | "financial_aid" | "housing" | "international";
  label: string;
  startsAt: string;
  endsAt: string | null;
  location: string | null;
  state: "scheduled" | "completed" | "cancelled" | "no_show";
  withWhom: string | null;
}

export interface StudentAppointmentsRead {
  scheduled: StudentAppointmentRecord[];
  /** Whether self-service booking exists, and where. Never a booking action. */
  bookingRoutes: Array<{ kind: StudentAppointmentRecord["kind"]; label: string; href: string }>;
  synthetic: boolean;
}

export interface ApprovedPolicyExcerpt {
  policyId: string;
  code: string;
  topic: string;
  title: string;
  studentVisibleText: string;
  appliesTo: string;
  sourceOwner: string;
  version: string;
  effectiveFrom: string;
  effectiveUntil: string | null;
  synthetic: boolean;
}

export interface ApprovedPolicySearchRequest {
  /** Free-text topic from the planner. Never an identity selector. */
  topic: string;
}

export interface ApprovedPolicySearchRead {
  matches: ApprovedPolicyExcerpt[];
}

/* ---------------------------------------------------------------------------
 * Financial aid, as a student experiences it.
 *
 * The existing aid read answers "what is outstanding?". These two answer the
 * other questions students actually ask, and they are deliberately two reads
 * rather than one per phrasing: everything about how much aid there is and
 * whether it covers the bill comes from the summary, and everything about when
 * money actually moves comes from the disbursement read.
 *
 * Both follow the gate pattern the other capabilities use -- they report the
 * conditions behind a state, not just the state -- so Edward can say which
 * condition is closed, who owns it, and what clears it, instead of inventing a
 * cause.
 * ------------------------------------------------------------------------- */

export type FafsaStatus =
  | "not_required"
  | "not_received"
  | "received"
  | "selected_for_verification"
  | "verification_complete"
  | "rejected"
  | "unknown";

/**
 * How settled the package is. An estimated award can change; a finalized one is
 * what the student will actually receive. Collapsing the two is how an
 * assistant ends up promising money.
 */
export type AidPackageState =
  | "no_application"
  | "not_packaged"
  | "estimated"
  | "finalized"
  | "revised"
  | "unknown";

export interface AidAwardSummary {
  id: string;
  name: string;
  awardType: "grant" | "scholarship" | "loan" | "work_study";
  source: string | null;
  offeredUsd: number;
  acceptedUsd: number;
  status: "offered" | "accepted" | "declined" | "pending" | "cancelled" | "unknown";
  requiresAction: boolean;
  /** Work-study is earned, not credited to the bill. Kept explicit. */
  appliesToBill: boolean;
}

export interface AidCoverage {
  costOfAttendanceUsd: number;
  /** The portion of the bill aid is expected to cover. */
  aidAppliedUsd: number;
  /** What the student still owes after aid and payments. */
  remainingBalanceUsd: number;
  coversFullCost: boolean;
  /** Positive when credited aid exceeds charges. */
  estimatedRefundUsd: number;
  /** True when some of the above depends on an award that is not finalized. */
  includesEstimatedAid: boolean;
}

export interface FinancialAidSummaryRead {
  aidYear: string | null;
  fafsaStatus: FafsaStatus;
  fafsaReceivedAt: string | null;
  packageState: AidPackageState;
  awards: AidAwardSummary[];
  totals: {
    offeredUsd: number;
    acceptedUsd: number;
    declinedUsd: number;
    pendingUsd: number;
  };
  coverage: AidCoverage;
  /** Conditions that must close before the package can be finalized. */
  gates: EligibilityGate[];
  sapStatus: "meeting" | "warning" | "probation" | "suspension" | "unknown";
  synthetic: boolean;
}

export interface AidDisbursementRecord {
  id: string;
  awardId: string | null;
  awardName: string;
  amountUsd: number;
  termCode: string | null;
  scheduledFor: string | null;
  disbursedAt: string | null;
  state: "scheduled" | "disbursed" | "held" | "cancelled";
  /** Why a held disbursement is held, in student-safe words. */
  holdReasons: string[];
}

export interface AidDisbursementsRead {
  aidYear: string | null;
  items: AidDisbursementRecord[];
  totalDisbursedUsd: number;
  totalScheduledUsd: number;
  nextScheduledFor: string | null;
  /** Conditions that must close before any money moves. */
  gates: EligibilityGate[];
  /** The institution's first disbursement date for the term, if known. */
  firstDisbursementDate: string | null;
  synthetic: boolean;
}

export interface StudentAssistantTools {
  getStudentProfile(
    context: StudentToolContext,
  ): Promise<ToolReadResult<StudentProfile>>;
  getOnboardingChecklist(
    context: StudentToolContext,
  ): Promise<ToolReadResult<StudentRequirementList>>;
  getDocumentStatuses(
    context: StudentToolContext,
  ): Promise<ToolReadResult<StudentDocumentList>>;
  getEnrollmentHolds(
    context: StudentToolContext,
  ): Promise<ToolReadResult<EnrollmentHoldsRead>>;
  getStudentDeadlines(
    context: StudentToolContext,
  ): Promise<ToolReadResult<StudentDeadlinesRead>>;
  getSupportOptions(
    context: StudentToolContext,
  ): Promise<ToolReadResult<StudentHelp>>;
  retrieveApprovedPolicy(
    context: StudentToolContext,
    request: ApprovedPolicyRequest,
  ): Promise<ToolReadResult<ApprovedPolicy>>;
  getFinancialAidStatus(
    context: StudentToolContext,
  ): Promise<ToolReadResult<AidChecklistRead>>;
  getFinancialAidSupportOptions(
    context: StudentToolContext,
  ): Promise<ToolReadResult<FinancialAidSupportRead>>;
  retrieveApprovedFinancialAidPolicy(
    context: StudentToolContext,
    request: ApprovedAidPolicyRequest,
  ): Promise<ToolReadResult<ApprovedAidPolicyExcerpt>>;
  getStudentHousingStatus(
    context: StudentToolContext,
  ): Promise<ToolReadResult<StudentHousingStatusRead>>;
  getHousingOptions(
    context: StudentToolContext,
  ): Promise<ToolReadResult<HousingOptionsRead>>;
  /*
   * Optional so a host can adopt capabilities incrementally. An absent method
   * is reported as an unavailable source, exactly like a read that failed, so
   * Edward says what it could not check instead of guessing.
   */
  getFinancialAidSummary?(
    context: StudentToolContext,
  ): Promise<ToolReadResult<FinancialAidSummaryRead>>;
  getAidDisbursements?(
    context: StudentToolContext,
  ): Promise<ToolReadResult<AidDisbursementsRead>>;
  getStudentHousingEligibility?(
    context: StudentToolContext,
  ): Promise<ToolReadResult<StudentHousingEligibilityRead>>;
  getRegistrationStatus?(
    context: StudentToolContext,
  ): Promise<ToolReadResult<RegistrationStatusRead>>;
  getStudentAccountSummary?(
    context: StudentToolContext,
  ): Promise<ToolReadResult<StudentAccountSummaryRead>>;
  getAcademicCalendar?(
    context: StudentToolContext,
  ): Promise<ToolReadResult<AcademicCalendarRead>>;
  getStudentAppointments?(
    context: StudentToolContext,
  ): Promise<ToolReadResult<StudentAppointmentsRead>>;
  searchApprovedPolicies?(
    context: StudentToolContext,
    request: ApprovedPolicySearchRequest,
  ): Promise<ToolReadResult<ApprovedPolicySearchRead>>;
}

export interface RequestClassification {
  requestType: StudentAssistantRequestType;
  confidence: number;
  source: "deterministic" | "model" | "safe_fallback";
  requirementReference: string | null;
  deadlineWindow: StudentDeadlineWindow | null;
  requestedEntity: StudentAssistantEntity | null;
  financialAidEntity: FinancialAidEntity | null;
  housingEntity: HousingEntity | null;
  deadlineScope: StudentDeadlineScope | null;
  blockerScope: StudentBlockerQueryScope | null;
  blockerTarget: StudentAssistantEntity | null;
  priorityExplanationRequested: boolean;
  registrationQuestion: boolean;
}

export interface StudentAssistantContextReceipt {
  id: string;
  source: StudentAssistantToolName;
  status: "available" | "unavailable";
  observedAt: string | null;
  sourceVersion: string | null;
  recordCount: number;
}

export interface GroundedRequirement extends StudentRequirementSummary {
  slug: string;
  contextReceiptIds: string[];
}

/**
 * One document-backed requirement with its authoritative lifecycle state.
 *
 * This replaces the old binary reading of the checklist, where a requirement
 * was either done or "missing". A student whose transcript is sitting with the
 * registrar is in neither of those states, and telling them it is missing is
 * both wrong and actionable in the worst way: they upload it again.
 */
export interface StudentDocumentState {
  requirementId: string;
  requirementCode: string;
  title: string;
  category: StudentDocumentCategory | null;
  submissionState: import("./document-lifecycle").DocumentSubmissionState;
  requirementStatus: RequirementStatus;
  /** Whose move it is next. */
  owner: "student" | "university" | "nobody";
  dueAt: string | null;
  fileName: string | null;
  submittedAt: string | null;
  responsibleOffice: string | null;
  navigationRoute: string | null;
  contextReceiptIds: string[];
}

export interface MissingDocument {
  requirementId: string;
  requirementCode: string;
  title: string;
  category: StudentDocumentCategory;
  requirementStatus: RequirementStatus;
  dueAt: string | null;
  currentDocumentStatus: StudentDocument["status"] | null;
  contextReceiptIds: string[];
}

export type StudentDeadlineUrgency =
  | "overdue"
  | "due_today"
  | "due_within_7_days"
  | "upcoming"
  | "unknown_date";

export interface StudentDeadline {
  id: string;
  kind: StudentDeadlineKind;
  title: string;
  dueAt: string | null;
  duePrecision: "date" | "instant";
  institutionalTimeZone: string;
  urgency: StudentDeadlineUrgency;
  sourceStatus: string;
  requirementId: string | null;
  requirementCode: string | null;
  source: StudentDeadlineSource;
  currentlyBlocking: boolean;
  blockingRequirement: boolean;
  hardOrRecommended: "hard" | "recommended" | null;
  lastVerifiedAt: string | null;
  sourceVersion: string | null;
  navigationRoute: string | null;
  contextReceiptIds: string[];
}

export interface StudentDeadlineBucketCounts {
  overdue: number;
  dueToday: number;
  dueWithinSevenDays: number;
  upcoming: number;
  completedOrSatisfied: number;
  unknownDate: number;
}

export type EnrollmentBlockerType =
  | "official_enrollment_hold"
  | "requirement_dependency"
  | "document_review"
  | "academic_prerequisite"
  | "financial_action";

export interface NormalizedEnrollmentBlocker {
  id: string;
  label: string | null;
  type: EnrollmentBlockerType;
  officialOrDerived: "official" | "derived";
  severity: "blocking" | "warning" | null;
  blockingScope:
    | "enrollment_journey"
    | "onboarding_requirement"
    | "course_eligibility"
    | "document_submission"
    | null;
  reasonCode: string | null;
  studentSafeReason: string | null;
  relatedRequirementId: string | null;
  relatedRequirementCode: string | null;
  resolutionOwner: string | null;
  selfResolvable: boolean | null;
  supportRoute: string | null;
  contextReceiptIds: string[];
  domain: "enrollment" | "course_registration" | "financial_aid";
  blockerTarget: string | null;
}

export type PriorityDerivationResultCode =
  | "official_hold_support"
  | "dependency_prerequisite"
  | "urgent_deadline"
  | "required_step"
  | "recommended_step"
  | "no_action"
  | "dependency_cycle"
  | "data_unavailable";

export interface PrioritizedStudentAction {
  id: string;
  label: string;
  kind: "contact_support" | "requirement" | "deadline";
  reasonCode: PriorityDerivationResultCode;
  relatedRequirementId: string | null;
  relatedRequirementCode: string | null;
  navigationRoute: string | null;
  contextReceiptIds: string[];
}

export interface PriorityEvidence {
  actionId: string;
  deadlineAt: string | null;
  deadlinePrecision: "date" | "instant" | null;
  institutionalTimeZone: string | null;
  urgency: StudentDeadlineUrgency | null;
  daysRemaining: number | null;
  blocksRequirementCodes: string[];
  blocksRequirementLabels: string[];
  rankingBasis: PriorityDerivationResultCode;
  isCurrentTopPriority: boolean;
  contextReceiptIds: string[];
}

export interface StudentAssistantPriorPriorityContext {
  action: PrioritizedStudentAction;
  evidence: PriorityEvidence | null;
}

export interface StudentAssistantCapabilitySummary {
  deadlineBucketCounts: StudentDeadlineBucketCounts;
  officialHoldCount: number;
  derivedBlockerCount: number;
  priorityDerivationResultCode: PriorityDerivationResultCode;
  unavailableDeadlineOrHoldSourceCount: number;
  financialAidCompletedCount: number;
  financialAidRemainingCount: number;
  financialAidMissingDocumentCount: number;
  financialAidVerificationState: AidVerificationStatus;
  financialAidUnavailableSourceCount: number;
  financialAidNextActionReasonCode: FinancialAidNextActionReasonCode | null;
  housingStateResultCode: HousingPlanRequirementState | "unavailable";
  housingRemainingStepCount: number;
  housingDeadlineCount: number;
  housingUnavailableSourceCount: number;
  housingNextActionReasonCode: HousingNextActionReasonCode | null;
}

export type HousingPlanRequirementState =
  | "complete"
  | "incomplete"
  | "waived"
  | "not_applicable"
  | "conflicting"
  | "unknown";

export type HousingNextActionReasonCode =
  | "select_plan"
  | "complete_requirement"
  | "resolve_conflict"
  | "contact_support"
  | "no_action"
  | "data_unavailable";

export interface HousingNextAction {
  label: string;
  reasonCode: HousingNextActionReasonCode;
  navigationRoute: string | null;
  contextReceiptIds: string[];
}

export interface HousingRemainingStep {
  code: "select_housing_plan" | "complete_housing_requirement";
  label: string;
  blocked: boolean;
  navigationRoute: string | null;
  contextReceiptIds: string[];
}

export interface HousingResult {
  applicationStatus: "unavailable";
  planRequirementState: HousingPlanRequirementState;
  planStatus: "selected" | "not_selected" | "conflicting" | "unknown";
  housingOptionType: HousingPreference | null;
  residencePreferenceState: "not_applicable" | "not_selected" | "selected";
  residencePreference: Exclude<HousingResidenceOption, null> | null;
  depositStatus: "unavailable";
  agreementStatus: "unavailable";
  assignmentStatus: "unavailable";
  waitlistStatus: "unavailable";
  roommatePreferenceState:
    | "not_applicable"
    | "not_provided"
    | "partially_provided"
    | "provided";
  mealPlanStatus: "unavailable";
  remainingSteps: HousingRemainingStep[];
  deadlines: StudentDeadline[];
  options: HousingOptionsRead["items"];
  nextAction: HousingNextAction | null;
  supportOptions: Array<
    | { kind: "office" | "email" | "phone" | "hours"; label: string; value: string }
    | { kind: "route"; label: string; href: string }
  >;
  unavailableData: StudentAssistantUnavailableData[];
  contextReceiptIds: string[];
}

export type AidVerificationStatus =
  | "not_required"
  | "action_required"
  | "submitted"
  | "under_review"
  | "verified"
  | "needs_correction"
  | "unknown";

export interface MissingAidDocument {
  requirementId: string;
  requirementCode: string;
  label: string;
  requirementStatus: AidRequirementStatus;
  authoritativeDocumentState: NormalizedAidRequirement["authoritativeDocumentState"];
  dueAt: string | null;
  navigationRoute: string | null;
  contextReceiptIds: string[];
}

export type FinancialAidNextActionReasonCode =
  | "correct_rejected_document"
  | "submit_required_document"
  | "accept_or_decline_award"
  | "await_verification_review"
  | "no_action"
  | "data_unavailable";

export interface FinancialAidNextAction {
  id: string;
  label: string;
  reasonCode: FinancialAidNextActionReasonCode;
  navigationRoute: string | null;
  contextReceiptIds: string[];
}

export interface FinancialAidResult {
  status: "complete" | "incomplete" | "unknown";
  completedRequirements: NormalizedAidRequirement[];
  remainingRequirements: NormalizedAidRequirement[];
  blockedRequirements: NormalizedAidRequirement[];
  readyRequirements: NormalizedAidRequirement[];
  missingDocuments: MissingAidDocument[];
  verificationStatus: AidVerificationStatus;
  awardAcceptanceStatuses: NormalizedAidAwardAcceptance[];
  deadlines: StudentDeadline[];
  nextAction: FinancialAidNextAction | null;
  supportOptions: FinancialAidSupportRead["options"];
  policyExplanation: ApprovedAidPolicyExcerpt | null;
  policyContextReceiptId: string | null;
  unavailableData: StudentAssistantUnavailableData[];
  contextReceiptIds: string[];
}

export interface RegistrationEligibilityResult {
  status: "unknown" | "clarification_required";
  studentSafeReason: string;
  contextReceiptIds: string[];
}

export type StudentSupportOption =
  | {
      kind: "email" | "phone" | "hours";
      label: string;
      value: string;
      contextReceiptIds: string[];
    }
  | {
      kind: "article";
      label: string;
      article: HelpArticle;
      contextReceiptIds: string[];
    };

export interface StudentAssistantUnavailableData {
  source: StudentAssistantToolName | "classification" | "composition";
  reason: ToolUnavailableReason | "model_error" | "invalid_model_result";
  retryable: boolean;
}

export interface StudentAssistantSafeFailure {
  partial: boolean;
  classificationFallback: boolean;
  compositionFallback: boolean;
  supportRecommended: boolean;
  codes: string[];
}

export type StudentAssistantGraphNodeName =
  | "normalize_request"
  | "classify_request"
  | "select_tool_reads"
  | "execute_tool_reads"
  | "derive_student_state"
  | "retrieve_policy"
  | "compose_grounded_answer"
  | "validate_grounding"
  | "finalize_response";

export interface StudentAssistantGraphTraceEntry {
  node: StudentAssistantGraphNodeName;
  status: "completed" | "fallback" | "skipped";
  durationMs: number;
  summary: string;
}

export interface StudentAssistantGraphExecution {
  graphVersion: "student-onboarding-v1";
  durationMs: number;
  toolSelectionSource: "model_plan" | "deterministic_fallback" | "safety_gate";
  selectedTools: StudentAssistantToolName[];
  executedTools: StudentAssistantToolName[];
  trace: StudentAssistantGraphTraceEntry[];
}

export interface StudentAssistantSuggestedAction {
  label: string;
  href: string;
  readOnly: true;
}

export interface StudentAssistantResponse {
  message: string;
  /**
   * The same answer as presentation blocks. `message` remains the plain-text
   * channel (voice, transcripts); the portal renders these. There is no
   * markdown path: the model never authors layout.
   */
  blocks: import("./blocks").AssistantBlock[];
  requestType: StudentAssistantRequestType;
  requestTypes: StudentAssistantRequestType[];
  requestedEntity: StudentAssistantEntity | null;
  financialAidEntity: FinancialAidEntity | null;
  housingEntity: HousingEntity | null;
  deadlineScope: StudentDeadlineScope | null;
  matchedDeadlines: StudentDeadline[];
  blockerTarget: StudentAssistantEntity | null;
  completedSteps: GroundedRequirement[];
  remainingSteps: GroundedRequirement[];
  awaitingReviewSteps: GroundedRequirement[];
  documentStates: StudentDocumentState[];
  blockedSteps: GroundedRequirement[];
  officialHolds: NormalizedEnrollmentBlocker[];
  derivedBlockers: NormalizedEnrollmentBlocker[];
  incompleteNonBlockingRequirements: GroundedRequirement[];
  nonBlockingActions: NormalizedEnrollmentBlocker[];
  missingDocuments: MissingDocument[];
  deadlines: StudentDeadline[];
  prioritizedAction: PrioritizedStudentAction | null;
  priorityReasonCode: PriorityDerivationResultCode | null;
  priorityEvidence: PriorityEvidence | null;
  registrationEligibility: RegistrationEligibilityResult | null;
  capabilitySummary: StudentAssistantCapabilitySummary;
  supportOptions: StudentSupportOption[];
  financialAid: FinancialAidResult | null;
  housing: HousingResult | null;
  contextReceipts: StudentAssistantContextReceipt[];
  suggestedActions: StudentAssistantSuggestedAction[];
  unavailableData: StudentAssistantUnavailableData[];
  safeFailure: StudentAssistantSafeFailure | null;
  graphExecution: StudentAssistantGraphExecution;
}
