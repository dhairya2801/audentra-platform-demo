export type OfferStatus = "offered" | "accepted" | "declined" | "expired";

export type RequirementStatus =
  | "not_applicable"
  | "blocked"
  | "ready"
  | "help_requested"
  | "in_progress"
  | "submitted"
  | "under_review"
  | "completed"
  | "waived"
  | "rejected"
  | "expired";

export interface AdmissionOfferSummary {
  id: string;
  programName: string;
  termName: string;
  campusName: string;
  responseDeadline: string;
  depositAmountCents: number;
  status: OfferStatus;
}

export interface StudentRequirementSummary {
  id: string;
  code: string;
  title: string;
  description: string;
  status: RequirementStatus;
  blocking: boolean;
  priority?: number;
  order?: number;
  dueAt: string | null;
  progressPercent: number;
  reward?: {
    points: number;
    earned: boolean;
  };
}

export interface StudentRewardSummary {
  pointName: string;
  pointsPerUsd: number;
  lifetimePoints: number;
  bookstoreCreditCents: number;
}

export interface StudentDashboard {
  student: {
    id: string;
    preferredName: string;
    fullName: string;
    classYear: number;
  };
  offer: AdmissionOfferSummary;
  journey: {
    id: string | null;
    status:
      | "not_started"
      | "created"
      | "in_progress"
      | "ready_for_review"
      | "submitted"
      | "on_hold"
      | "completed"
      | "cancelled";
    completionPercent: number;
    nextAction: {
      code: string;
      label: string;
      href: string;
    };
    requirements: StudentRequirementSummary[];
  };
  unreadMessageCount: number;
  projectionVersion: number;
  generatedAt: string;
}

export type ActivityEventName =
  | "ui.portal_session_started.v1"
  | "ui.dashboard_viewed.v1"
  | "ui.admission_offer_viewed.v1"
  | "ui.admission_decision_started.v1"
  | "ui.enrollment_started.v1"
  | "ui.enrollment_step_viewed.v1"
  | "ui.portal_section_viewed.v1"
  | "ui.enrollment_task_viewed.v1"
  | "ui.enrollment_task_abandoned.v1"
  | "ui.financial_aid_viewed.v1"
  | "ui.course_catalog_searched.v1"
  | "ui.course_viewed.v1"
  | "ui.exemption_reviewed.v1"
  | "ui.campus_event_viewed.v1"
  | "ui.club_viewed.v1"
  | "ui.edward_context_receipts_received.v1"
  /** @deprecated Retained only so older portal clients can finish sending batches. */
  | "ui.edward_tool_invoked.v1"
  | "ui.edward_action_widget_viewed.v1"
  | "ui.edward_action_completed.v1"
  | "ui.help_opened.v1";

export interface ActivityEventInput {
  eventId: string;
  eventName: ActivityEventName;
  occurredAt: string;
  sessionId: string;
  pageInstanceId: string;
  correlationId?: string;
  properties: Record<string, string | number | boolean | null>;
}

export interface AcceptOfferResponse {
  offerId: string;
  offerStatus: "accepted";
  journeyId: string;
  journeyStatus: "in_progress" | "completed";
  onboardingRequired: boolean;
  initialRoute: "/onboarding" | "/dashboard";
  projectionVersion: number;
  acceptedAt: string;
}

export type OnboardingStatus = "not_started" | "in_progress" | "completed";
export type OnboardingStep =
  | "offer"
  | "about_you"
  | "housing"
  | "campus_life"
  | "emergency_contacts"
  | "family_permissions"
  | "review_and_sign"
  | "deposit";

export type HousingPreference =
  | "on_campus"
  | "off_campus"
  | "commuting"
  | "undecided"
  | "family";
export type HousingResidenceOption =
  | "aster_residence_hall"
  | "aster_apartments"
  | "student_village"
  | null;

export interface OnboardingEmergencyContact {
  fullName: string;
  relationship:
    | "parent"
    | "guardian"
    | "partner"
    | "sibling"
    | "relative"
    | "friend"
    | "other";
  mobilePhone: string;
}

export interface OnboardingFamilyPermission {
  fullName: string;
  relationship:
    | "parent"
    | "guardian"
    | "partner"
    | "sponsor"
    | "other";
  email: string;
  scopes: string[];
  purpose:
    | "education_and_expenses"
    | "academic_planning"
    | "billing_and_aid"
    | "other";
  expires:
    | "end_first_year"
    | "end_enrollment"
    | "registrar_date";
}

export interface StudentOnboardingData {
  firstName?: string;
  lastName?: string;
  preferredName?: string;
  personalEmail?: string;
  mobilePhone?: string;
  citizenshipStatus?:
    | "us_citizen"
    | "permanent_resident"
    | "eligible_noncitizen"
    | "international";
  communicationPreference?: "email" | "sms";
  residencyStatus?: "domestic" | "international";
  residencyVerificationPath?:
    | "home_address_review"
    | "document_upload"
    | "advisor_review";
  streetAddress?: string;
  addressLine2?: string;
  city?: string;
  stateOrProvince?: string;
  postalCode?: string;
  country?: string;
  supportNeeds?: string[];
  accommodationInterest?: "not_now" | "housing" | "academic" | "both";
  housingPreference?: HousingPreference;
  housingResidenceOption?: HousingResidenceOption;
  housingResidencePreferences?: Array<
    Exclude<HousingResidenceOption, null>
  >;
  insuranceInterest?:
    | "not_now"
    | "learn_more"
    | "tuition"
    | "housing"
    | "both";
  housingRoomType?: string;
  bathroomPreference?: string;
  roommateMatching?: string;
  knownRoommateName?: string;
  knownRoommateEmail?: string;
  sleepSchedule?: string;
  studyHabits?: string;
  roomNoise?: string;
  cleanliness?: string;
  guestPreference?: string;
  temperaturePreference?: string;
  smokeVapeCompatibility?: string;
  substanceFreeHousing?: boolean;
  genderInclusiveHousing?: boolean;
  accessibleHousingInformation?: boolean;
  livingLearningCommunities?: string[];
  offCampusStatus?: string;
  offCampusResources?: string[];
  commuteMode?: string;
  commuteDuration?: string;
  commuterResources?: string[];
  skippedSteps?: OnboardingStep[];
  campusInterests?: string[];
  socialComfort?: string;
  firstMonthGoals?: string[];
  emergencyContacts?: OnboardingEmergencyContact[];
  familyPermissions?: OnboardingFamilyPermission[];
  signatureFullName?: string;
  signatureMethod?: "typed" | "drawn";
  signatureImageData?: string;
  signatureConsent?: boolean;
  signedDocumentIds?: string[];
  depositChoice?: "pay_now" | "pay_later" | "waiver_or_deferral";
  customFields?: Record<string, string | string[] | boolean>;
}

export type AboutYouConfigurableField =
  | "firstName"
  | "lastName"
  | "preferredName"
  | "personalEmail"
  | "mobilePhone"
  | "citizenshipStatus"
  | "streetAddress"
  | "city"
  | "stateOrProvince"
  | "postalCode"
  | "country"
  | "residencyVerificationPath";

export interface StudentOnboardingScreenConfiguration {
  label?: string;
  title: string;
  description: string;
  requiredFields?: AboutYouConfigurableField[];
  identityQuickUpload?: boolean;
  fields?: StudentRequirementInputField[];
  form?: StudentRequirementFormDefinition;
}

export interface StudentOnboarding {
  studentId: string;
  status: OnboardingStatus;
  currentStep: OnboardingStep;
  completedSteps: OnboardingStep[];
  data: StudentOnboardingData;
  configurationVersion?: number;
  screenConfigurations?: Partial<
    Record<OnboardingStep, StudentOnboardingScreenConfiguration>
  >;
  version: number;
  completedAt: string | null;
  updatedAt: string;
}

export interface UpdateStudentOnboardingInput {
  expectedVersion: number;
  currentStep: OnboardingStep;
  data: StudentOnboardingData;
  skip?: boolean;
}

export interface CompleteStudentOnboardingInput {
  expectedVersion: number;
}

export interface StudentHousingPlan {
  preference: HousingPreference | null;
  residenceOption: HousingResidenceOption;
  residencePreferences?: Exclude<HousingResidenceOption, null>[];
  roomType?: string | null;
  bathroomPreference?: string | null;
  roommateMatching?: string | null;
  knownRoommateName?: string | null;
  knownRoommateEmail?: string | null;
  sleepSchedule?: string | null;
  studyHabits?: string | null;
  roomNoise?: string | null;
  cleanliness?: string | null;
  guestPreference?: string | null;
  temperaturePreference?: string | null;
  smokeVapeCompatibility?: string | null;
  substanceFreeHousing?: boolean | null;
  genderInclusiveHousing?: boolean | null;
  accessibleHousingInformation?: boolean | null;
  livingLearningCommunities?: string[];
  residences: StudentHousingResidence[];
  version: number;
  updatedAt: string;
}

export interface StudentHousingResidence {
  id: string;
  value: Exclude<HousingResidenceOption, null>;
  name: string;
  description: string;
  amenities: string[];
  imageUrl: string;
  imageAlt: string;
  attribution: string;
  sourceUrl: string;
}

export interface UpdateStudentHousingPlanInput {
  expectedVersion: number;
  preference: HousingPreference;
  residenceOption?: Exclude<HousingResidenceOption, null>;
  residencePreferences?: Exclude<HousingResidenceOption, null>[] | null;
  roomType?: string | null;
  bathroomPreference?: string | null;
  roommateMatching?: string | null;
  knownRoommateName?: string | null;
  knownRoommateEmail?: string | null;
  sleepSchedule?: string | null;
  studyHabits?: string | null;
  roomNoise?: string | null;
  cleanliness?: string | null;
  guestPreference?: string | null;
  temperaturePreference?: string | null;
  smokeVapeCompatibility?: string | null;
  substanceFreeHousing?: boolean | null;
  genderInclusiveHousing?: boolean | null;
  accessibleHousingInformation?: boolean | null;
  livingLearningCommunities?: string[] | null;
}

export interface StudentExperienceUpdate {
  id: string;
  kind: "onboarding" | "enrollment" | "academics" | "campus_life";
  title: string;
  description: string;
  requirementSlug: string | null;
  status: "pending" | "deferred";
  version: number;
  createdAt: string;
}

export interface DecideStudentExperienceUpdateInput {
  action: "handle_now" | "later";
  expectedVersion: number;
}

export interface DeferStudentExperienceUpdatesInput {
  updates: Array<{
    id: string;
    expectedVersion: number;
  }>;
}

export interface StudentExperienceUpdateDecision {
  id: string;
  status: "acknowledged" | "deferred";
  version: number;
  requirementSlug: string | null;
}

export interface DeferStudentExperienceUpdatesResult {
  updates: StudentExperienceUpdateDecision[];
}

export interface StudentBootstrap {
  authenticated: true;
  tenant?: {
    id: string;
    slug: string;
    name: string;
    shortName: string;
    mark: string;
    supportEmail: string;
    admissionsEmail: string;
    registrarEmail: string;
  };
  student: {
    id: string;
    preferredName: string;
    fullName: string;
  };
  onboarding: {
    required: boolean;
    status: OnboardingStatus;
    currentStep: OnboardingStep;
    version: number;
  };
  rewards?: StudentRewardSummary;
  unreadMessageCount: number;
  experienceUpdates: StudentExperienceUpdate[];
  initialRoute: "/onboarding" | "/dashboard";
  generatedAt: string;
}

export type StudentRequirementInteractionType =
  | "information"
  | "approval"
  | "form"
  | "single_select"
  | "multiple_select"
  | "selection_flow"
  | "upload_file"
  | "signature"
  | "payment"
  | "scheduling";

export interface StudentRequirementInputField {
  id: string;
  title: string;
  field_type:
    | "text"
    | "email"
    | "phone"
    | "date"
    | "number"
    | "checkbox"
    | "single_select"
    | "multiple_select";
  required: boolean;
  options?: string[];
  maximum_selections?: number;
  minimum?: number;
  maximum?: number;
  step?: number;
  when?: { field: string; equals: string };
}

export interface StudentRequirementFormPage {
  id: string;
  title: string;
  description?: string;
  fields: StudentRequirementInputField[];
}

export interface StudentRequirementFormDefinition {
  version: 1;
  pages: StudentRequirementFormPage[];
}

export type JourneyRouteOperator =
  | "equals"
  | "not_equals"
  | "one_of"
  | "none_of"
  | "contains"
  | "not_contains"
  | "greater_than"
  | "greater_than_or_equal"
  | "less_than"
  | "less_than_or_equal";

export interface JourneyRouteRule {
  sourceTaskId: string;
  fieldId: string;
  operator: JourneyRouteOperator;
  value: string | number | boolean | string[];
}

export interface JourneyRouteActivation {
  match: "all" | "any";
  rules: JourneyRouteRule[];
}

export interface StudentRequirementInputConfig {
  options?: string[];
  maximumSelections?: number;
  fields?: StudentRequirementInputField[];
  flow?: StudentRequirementInputField[];
  form?: StudentRequirementFormDefinition;
  signatureProvider?: "built_in" | "docusign";
  docusignTemplateId?: string;
  acceptedMimeTypes?: string[];
  documentCategories?: string[];
  [key: string]: unknown;
}

export interface StudentRequirementDetail extends StudentRequirementSummary {
  slug: string;
  journeyId: string;
  version: number;
  submissionType: "form" | "document" | "payment" | "appointment" | "none";
  flowKind: "onboarding" | "enrollment";
  interactionType: StudentRequirementInteractionType;
  inputConfig: StudentRequirementInputConfig;
  documentCategory: StudentDocumentCategory | null;
  responsibleOffice: string;
  dependencyCodes: string[];
  immunizationPolicy?: {
    id: string;
    code: string;
    version: number;
    name: string;
    effectiveFrom: string;
    effectiveUntil: string | null;
    requirements: Array<{
      id: string;
      code: string;
      name: string;
      description: string;
      required: boolean;
      doseCount: number | null;
      validityDays: number | null;
    }>;
  };
}

export type StudentRequirementResponseValue =
  | string
  | number
  | boolean
  | string[]
  | null;

export type StudentRequirementResponsePayload =
  | { acknowledged: true }
  | { approved: true }
  | { values: Record<string, StudentRequirementResponseValue> }
  | { selectedOption: string }
  | { selectedOptions: string[] }
  | {
      accepted: true;
      signerName: string;
      signatureMethod?: "typed" | "drawn";
    }
  | { appointmentId: string };

export interface SubmitStudentRequirementResponseInput {
  expectedVersion: number;
  response: StudentRequirementResponsePayload;
}

export interface StudentRequirementResponseRecord {
  id: string;
  interactionType: StudentRequirementInteractionType;
  data: Record<string, unknown>;
  version: number;
  submittedAt: string;
}

export interface SubmitStudentRequirementResponseResult
  extends StudentRequirementDetail {
  response: StudentRequirementResponseRecord;
}

const requirementSlugByCode = {
  profile_verification: "profile-verification",
  identity_document: "identity-document-upload",
  official_transcript: "transcript-upload",
  financial_aid_verification: "financial-aid-verification",
  immunization_record: "immunization-upload",
  enrollment_deposit: "enrollment-deposit",
} as const;

const documentCategoryByRequirementCode: Partial<
  Record<string, StudentDocumentCategory>
> = {
  identity_document: "identity",
  official_transcript: "transcript",
  financial_aid_verification: "financial_aid",
  immunization_record: "health",
};

export function studentRequirementSlug(code: string): string {
  return (
    requirementSlugByCode[code as keyof typeof requirementSlugByCode] ??
    code.toLowerCase().replaceAll("_", "-")
  );
}

export function studentRequirementCodeFromSlug(slug: string): string {
  const match = Object.entries(requirementSlugByCode).find(
    ([, candidate]) => candidate === slug,
  );
  return match?.[0] ?? slug.toLowerCase().replaceAll("-", "_");
}

export function documentCategoryForRequirement(
  code: string,
): StudentDocumentCategory | null {
  return documentCategoryByRequirementCode[code] ?? null;
}

export interface StudentRequirementList {
  items: StudentRequirementDetail[];
  total: number;
}

export interface StudentMessage {
  id: string;
  subject: string;
  body: string;
  senderName: string;
  kind?: string;
  href?: string | null;
  sentAt: string;
  readAt: string | null;
}

export interface StudentMessageList {
  items: StudentMessage[];
  unreadCount: number;
}

export type StudentDocumentCategory =
  | "identity"
  | "residency"
  | "transcript"
  | "financial_aid"
  | "health"
  | "consent"
  | "other";

export type StudentDocumentProcessingMode =
  | "agentic"
  | "classification_only"
  | "manual_review"
  | "generated";

/**
 * Server-authored upload policy. The browser may suggest a category, but it
 * must never decide whether an uploaded document is sent to an AI provider.
 */
export function documentProcessingModeForCategory(
  category: StudentDocumentCategory,
): StudentDocumentProcessingMode {
  if (category === "identity" || category === "transcript") return "agentic";
  if (category === "financial_aid") return "classification_only";
  return "manual_review";
}

export interface ExtractedDocumentField {
  key: string;
  label: string;
  value: string;
  confidence: number;
}

export interface ExtractedTranscriptCourse {
  sourceCode: string | null;
  title: string;
  credits: number | null;
  grade: string | null;
  score: string | null;
  term: string | null;
  confidence: number;
}

export interface ExtractedDocumentVisualRegion {
  kind: "profile_photo";
  pageNumber: number | null;
  /** Normalized coordinates in the rendered page/image coordinate space. */
  x: number;
  y: number;
  width: number;
  height: number;
  confidence: number;
}

export type StudentDocumentContextTargetType =
  | "profile"
  | "onboarding"
  | "requirement";

/**
 * A reviewable, account-scoped suggestion produced while a document is parsed.
 * Target identifiers are accepted only when they were supplied by the server
 * for the authenticated student; model-authored identifiers are discarded.
 */
export interface StudentDocumentContextMatch {
  targetType: StudentDocumentContextTargetType;
  targetId: string;
  title: string;
  status: "sufficient" | "partial";
  matchedFieldKeys: string[];
  evidenceKeys: string[];
  confidence: number;
  rationale: string;
  applied: boolean;
  reviewRequired: true;
  href: string | null;
}

export interface CourseExemptionDecision {
  sourceCourseKey: string;
  sourceCode: string | null;
  sourceTitle: string;
  status: "matched" | "needs_review" | "no_match" | "policy_gap";
  targetCourseId: string | null;
  equivalencyRuleId: string | null;
  confidence: number;
  rationale: string;
  contextIds: string[];
}

export interface CourseExemptionEvaluation {
  catalogVersionId: string;
  policyVersion: string;
  evaluatedCourseCount: number;
  decisions: CourseExemptionDecision[];
  warnings: string[];
  generatedAt: string;
}

export interface ImmunizationRequirementResult {
  ruleId: string;
  code: string;
  name: string;
  status: "met" | "missing" | "uncertain" | "not_applicable" | "expired";
  rationale: string;
  evidenceKeys: string[];
}

export interface ImmunizationComplianceEvaluation {
  policyVersionId: string;
  policyVersion: string;
  requirements: ImmunizationRequirementResult[];
  warnings: string[];
  generatedAt: string;
}

/**
 * A deliberately small, student-safe description of why an extraction could
 * not finish. Provider response bodies are never persisted in this contract.
 */
export type StudentDocumentExtractionFailureCode =
  | "provider_unavailable"
  | "unsupported_capability"
  | "provider_configuration"
  | "timeout"
  | "invalid_response"
  | "unknown";

export interface StudentDocumentExtraction {
  status:
    | "pending_configuration"
    | "processing"
    | "completed"
    | "failed";
  documentType:
    | "transcript"
    | "identity"
    | "financial_aid"
    | "ferpa"
    | "immunization"
    | "residency"
    | "other";
  summary: string;
  studentName: string | null;
  institutionName: string | null;
  issueDate: string | null;
  academicTerm: string | null;
  fields: ExtractedDocumentField[];
  courses?: ExtractedTranscriptCourse[];
  visualRegions?: ExtractedDocumentVisualRegion[];
  contextMatches?: StudentDocumentContextMatch[];
  warnings: string[];
  model: string | null;
  provider: "openrouter" | "groq" | "local";
  processingStartedAt?: string;
  processingDeadlineAt?: string;
  processedAt: string | null;
  verifiedAt: string | null;
  failureCode?: StudentDocumentExtractionFailureCode;
  retryable?: boolean;
  acceptedFieldKeys?: string[];
  courseExemptionEvaluation?: CourseExemptionEvaluation;
  immunizationCompliance?: ImmunizationComplianceEvaluation;
}

export interface StudentDocument {
  id: string;
  requirementId?: string;
  fileName: string;
  mimeType: "application/pdf" | "image/jpeg" | "image/png";
  sizeBytes: number;
  category: StudentDocumentCategory;
  processingMode?: StudentDocumentProcessingMode;
  status:
    | "placeholder"
    | "uploaded"
    | "processing"
    | "needs_review"
    | "under_review"
    | "accepted"
    | "rejected"
    | "needs_resubmission"
    | "waived";
  /** Present once a reviewer has ruled on the document. */
  review?: {
    decision:
      | "accepted"
      | "rejected"
      | "needs_resubmission"
      | "waived"
      | "under_review";
    decidedAt: string;
    note: string | null;
    synthetic?: boolean;
  };
  sha256?: string;
  contentUrl?: string;
  extraction?: StudentDocumentExtraction;
  signature?: {
    templateCode: string;
    title: string;
    signerName: string;
    method: "typed" | "drawn";
    signedAt: string;
    onboardingVersion: number;
  };
  createdAt: string;
}

export interface StudentDocumentList {
  items: StudentDocument[];
  total: number;
}

export type StaffWorkItemStatus =
  | "todo"
  | "in_progress"
  | "follow_up_required"
  | "blocked"
  | "done"
  | "cancelled";
export type StaffWorkItemPriority = "urgent" | "high" | "medium" | "low";
export type StaffWorkItemType =
  | "enrollment"
  | "document_review"
  | "communication";
export type StaffCommunicationChannel = "email" | "sms" | "voice" | "portal";
export type StaffActionType =
  | "enrollment_follow_up"
  | "onboarding_assistance"
  | "document_review"
  | "missing_information"
  | "external_verification"
  | "deadline_risk"
  | "staff_decision"
  | "communication_response"
  | "blocked_dependency";
export type StaffAiProcessingState =
  | "not_requested"
  | "pending"
  | "running"
  | "ready"
  | "stale"
  | "failed_retryable"
  | "dead_letter";

export interface StaffMemberSummary {
  id: string;
  name: string;
  email: string;
  component: string;
}

export interface StaffWorkItemLog {
  id: string;
  action:
    | "created"
    | "status_changed"
    | "assigned"
    | "escalated"
    | "commented"
    | "document_decided"
    | "student_preferences_updated"
    | "channel_selected"
    | "interaction_started"
    | "communication_recorded"
    | "outcome_recorded"
    | "follow_up_scheduled"
    | "blocked"
    | "cancelled"
    | "ai_refresh_requested"
    | "ai_outcome_updated"
    | "ai_task_insight_updated"
    | "call_recording_uploaded"
    | "call_transcription_updated"
    | "scheduled_rule_matched"
    | "student_summary_updated";
  message: string;
  actorName: string;
  occurredAt: string;
}

export interface StaffWorkItem {
  id: string;
  key: string;
  title: string;
  description: string;
  status: StaffWorkItemStatus;
  priority: StaffWorkItemPriority;
  type: StaffWorkItemType;
  actionType: StaffActionType;
  component: string;
  dueAt: string | null;
  escalated: boolean;
  selectedChannel: StaffCommunicationChannel | null;
  attemptCount: number;
  followUpAt: string | null;
  blocker: {
    code: string;
    detail: string;
    reviewAt: string | null;
  } | null;
  outcomeCode: string | null;
  resolutionCode: string | null;
  nextStep: string | null;
  terminalReason: string | null;
  startedAt: string | null;
  interactionCompletedAt: string | null;
  completedAt: string | null;
  cancelledAt: string | null;
  version: number;
  createdAt: string;
  updatedAt: string;
  assignee: StaffMemberSummary | null;
  student: {
    id: string;
    name: string;
    preferredName: string;
    programName: string;
    classYear: number;
  };
  source:
    | {
        type: "onboarding" | "requirement" | "document" | "message";
        id: string;
      }
    | null;
  history: StaffWorkItemLog[];
}

export interface StaffWorkComment {
  id: string;
  body: string;
  author: StaffMemberSummary;
  mentions: StaffMemberSummary[];
  createdAt: string;
}

export interface StaffCommunicationEvent {
  id: string;
  channel: StaffCommunicationChannel;
  direction: "inbound" | "outbound";
  subject: string | null;
  body: string | null;
  deliveryStatus:
    | "draft"
    | "queued"
    | "sent"
    | "delivered"
    | "failed"
    | "bounced"
    | "recorded"
    | "received";
  sourceSequence: number;
  occurredAt: string;
}

export interface StaffConversationSignalMetric {
  label: string;
  score: number | null;
}

export interface StaffConversationSignals {
  sentiment: StaffConversationSignalMetric;
  engagement: StaffConversationSignalMetric;
  intent: string;
  likelihoodToProgress: StaffConversationSignalMetric;
}

export interface StaffInteractionOutcome {
  id: string;
  version: number;
  finality: "provisional" | "final";
  summary: string;
  channelResults: Array<{
    channel: StaffCommunicationChannel;
    result: string;
  }>;
  outcomeCode: string | null;
  resolutionCode: string | null;
  nextStep: string | null;
  followUpRequired: boolean;
  sourceIds: string[];
  coveredSourceVersion: number;
  confidence: number | null;
  conversationSignals: StaffConversationSignals;
  provider: string;
  model: string;
  generatedAt: string;
}

export type StaffRelatedDocument = Pick<
  StudentDocument,
  | "id"
  | "fileName"
  | "mimeType"
  | "sizeBytes"
  | "category"
  | "processingMode"
  | "status"
  | "contentUrl"
  | "createdAt"
>;

export interface StaffInteraction {
  id: string;
  objective: string;
  status:
    | "collecting"
    | "enrichment_pending"
    | "provisional"
    | "completed"
    | "stale"
    | "failed_retryable";
  selectedChannel: StaffCommunicationChannel | null;
  sourceVersion: number;
  coveredSourceVersion: number;
  version: number;
  quietUntil: string | null;
  lastActivityAt: string | null;
  completedAt: string | null;
  communications: StaffCommunicationEvent[];
  recordings: StaffCallRecording[];
  outcome: StaffInteractionOutcome | null;
  aiState: StaffAiProcessingState;
}

export type StaffCallRecordingStatus =
  | "uploading"
  | "queued"
  | "transcribing"
  | "ready"
  | "upload_failed"
  | "failed_retryable"
  | "dead_letter"
  | "pending_configuration";

export interface StaffCallTranscriptRevision {
  id: string;
  version: number;
  transcript: string;
  language: string | null;
  durationSeconds: number | null;
  segments: Array<{
    start: number | null;
    end: number | null;
    text: string;
  }>;
  provider: string;
  model: string;
  generatedAt: string;
}

export interface StaffCallRecording {
  id: string;
  interactionId: string;
  fileName: string;
  mimeType: string;
  sizeBytes: number;
  sha256: string;
  status: StaffCallRecordingStatus;
  attempts: number;
  version: number;
  lastError: string | null;
  uploadedAt: string | null;
  transcribedAt: string | null;
  downloadUrl: string;
  currentTranscript: StaffCallTranscriptRevision | null;
  transcriptHistory: StaffCallTranscriptRevision[];
  createdAt: string;
  updatedAt: string;
}

export interface StaffStudentAiSummary {
  state: StaffAiProcessingState;
  version: number | null;
  summary: string | null;
  keyFacts: string[];
  risks: string[];
  nextSteps: string[];
  sourceIds: string[];
  sourceRevision: number;
  provider: string | null;
  model: string | null;
  generatedAt: string | null;
}

export interface StaffTaskAiInsight {
  state: StaffAiProcessingState;
  version: number | null;
  summary: string | null;
  whyThisMatters: string | null;
  objective: string | null;
  successDefinition: string | null;
  suggestedApproach: string | null;
  suggestedChannel: StaffCommunicationChannel | null;
  sourceIds: string[];
  sourceRevision: number;
  provider: string | null;
  model: string | null;
  generatedAt: string | null;
}

export interface StaffWorkItemDetail {
  workItem: StaffWorkItem;
  taskInsight: StaffTaskAiInsight;
  studentSummary: StaffStudentAiSummary;
  interactions: StaffInteraction[];
  comments: StaffWorkComment[];
  relatedItems: StaffWorkItem[];
  relatedDocuments: StaffRelatedDocument[];
  aiState: StaffAiProcessingState;
  generatedAt: string;
}

export interface StaffActionCenter {
  items: StaffWorkItem[];
  staff: StaffMemberSummary[];
  counts: {
    todo: number;
    inProgress: number;
    followUpRequired: number;
    blocked: number;
    done: number;
    cancelled: number;
    urgent: number;
    escalated: number;
  };
  generatedAt: string;
}

export interface StaffStudentRecord {
  student: StaffWorkItem["student"];
  onboarding: StudentOnboarding;
  profile: StudentProfile;
  requirements: StudentRequirementList;
  documents: StudentDocumentList;
  syntheticTestRecord?: boolean;
  operation?: StaffStudentOperation | null;
}

export interface StaffSession {
  authenticated: true;
  mode: "credentials";
  actorType: "staff";
  staff: StaffMemberSummary;
  notice: string;
}

export interface StaffSignInInput {
  email: string;
  password: string;
}

export interface StaffSignUpInput extends StaffSignInInput {
  institutionAccessCode: string;
}

export interface UpdateStaffWorkItemInput {
  expectedVersion: number;
  status?: StaffWorkItemStatus;
  assigneeId?: string | null;
  escalated?: boolean;
  selectedChannel?: StaffCommunicationChannel | null;
  followUpAt?: string | null;
  blockerCode?: string | null;
  blockerDetail?: string | null;
  blockerReviewAt?: string | null;
  outcomeCode?: string | null;
  resolutionCode?: string | null;
  nextStep?: string | null;
  terminalReason?: string | null;
  note?: string;
}

export interface CreateStaffWorkItemInput {
  studentId: string;
  flowKind: "enrollment" | "onboarding";
  requirementId?: string | null;
  title: string;
  description: string;
  component: string;
  assigneeId?: string | null;
  priority: StaffWorkItemPriority;
  status?: Exclude<StaffWorkItemStatus, "done" | "cancelled">;
  dueAt?: string | null;
  actionType?: StaffActionType | null;
}

export interface StaffRealtimeEvent {
  cursor: number;
  type: string;
  resourceType: string;
  resourceId: string;
  workItemId: string | null;
  data: Record<string, unknown>;
  occurredAt: string;
}

export interface CreateStaffWorkCommentInput {
  expectedWorkItemVersion: number;
  body: string;
  mentionIds?: string[];
}

export interface StartStaffInteractionInput {
  expectedWorkItemVersion: number;
  channel: StaffCommunicationChannel;
  objective: string;
}

export interface RecordStaffCommunicationInput {
  expectedInteractionVersion: number;
  channel: StaffCommunicationChannel;
  direction: "inbound" | "outbound";
  subject?: string | null;
  body: string;
  occurredAt?: string;
}

export interface CompleteStaffInteractionInput {
  expectedInteractionVersion: number;
  expectedWorkItemVersion: number;
  outcomeCode: string;
  resolutionCode: string;
  nextStep?: string | null;
  followUpAt?: string | null;
}

export interface RequestStaffAiRefreshInput {
  expectedWorkItemVersion: number;
  scope: "interaction" | "student_summary" | "task_insight" | "both";
  interactionId?: string;
}

export interface RetryStaffCallTranscriptionInput {
  expectedRecordingVersion: number;
}

export type StaffActionRuleSignal = "requirement_due" | "student_inactive";

export interface StaffActionRule {
  id: string;
  code: string;
  name: string;
  description: string;
  enabled: boolean;
  signalType: StaffActionRuleSignal;
  flowKind: "enrollment" | "onboarding" | null;
  requirementCode: string | null;
  lookaheadDays: number | null;
  inactivityDays: number | null;
  cadenceMinutes: number;
  component: string;
  priority: StaffWorkItemPriority;
  actionType: StaffActionType;
  titleTemplate: string;
  descriptionTemplate: string;
  version: number;
  lastEvaluatedAt: string | null;
  updatedAt: string;
  updatedBy: StaffMemberSummary | null;
}

export interface StaffActionRuleList {
  items: StaffActionRule[];
  generatedAt: string;
}

export interface StaffNotification {
  id: string;
  kind: string;
  title: string;
  body: string;
  resourceType: string | null;
  resourceId: string | null;
  workItemKey: string | null;
  target: "staff" | "team";
  isRead: boolean;
  readAt: string | null;
  createdAt: string;
}

export interface StaffNotificationList {
  items: StaffNotification[];
  unreadCount: number;
  generatedAt: string;
}

export interface StaffNotificationReadResult {
  id: string;
  isRead: true;
  readAt: string;
}

export interface CreateStaffActionRuleInput {
  code: string;
  name: string;
  description: string;
  enabled?: boolean;
  signalType: StaffActionRuleSignal;
  flowKind?: "enrollment" | "onboarding" | null;
  requirementCode?: string | null;
  lookaheadDays?: number | null;
  inactivityDays?: number | null;
  cadenceMinutes: number;
  component: string;
  priority: StaffWorkItemPriority;
  actionType: StaffActionType;
  titleTemplate: string;
  descriptionTemplate: string;
}

export interface UpdateStaffActionRuleInput {
  expectedVersion: number;
  name?: string;
  description?: string;
  enabled?: boolean;
  flowKind?: "enrollment" | "onboarding" | null;
  requirementCode?: string | null;
  lookaheadDays?: number | null;
  inactivityDays?: number | null;
  cadenceMinutes?: number;
  component?: string;
  priority?: StaffWorkItemPriority;
  actionType?: StaffActionType;
  titleTemplate?: string;
  descriptionTemplate?: string;
}

export interface UpdateStaffStudentPreferencesInput {
  expectedOnboardingVersion: number;
  expectedProfileVersion: number;
  communicationPreference: "email" | "sms";
  housingPreference: HousingPreference;
  accommodationInterest: NonNullable<
    StudentOnboardingData["accommodationInterest"]
  >;
  residencyVerificationPath: NonNullable<
    StudentOnboardingData["residencyVerificationPath"]
  >;
  notifyStudent: boolean;
  note?: string;
}

export interface ReviewStaffDocumentInput {
  workItemId: string;
  expectedWorkItemVersion: number;
  decision: "accepted" | "rejected";
  note: string;
  notifyStudent: boolean;
}

export interface StaffDocumentDecisionResult {
  document: StudentDocument;
  workItem: StaffWorkItem;
  notification: StudentMessage | null;
}

export type StaffManagedContentStatus = "draft" | "published" | "archived";

export interface StaffKnowledgeCard {
  id: string;
  title: string;
  summary: string;
  body: string;
  category: string;
  audience: "internal" | "student";
  status: StaffManagedContentStatus;
  owner: string;
  version: number;
  updatedAt: string;
}

export interface StaffCorePlay {
  id: string;
  title: string;
  description: string;
  trigger: string;
  audience: string;
  steps: string[];
  status: "draft" | "active" | "archived";
  owner: string;
  version: number;
  updatedAt: string;
}

export interface StaffInquiry {
  id: string;
  student: StaffWorkItem["student"];
  topicCode: "getting_started" | "documents" | "payments" | "support";
  subject: string;
  message: string;
  status: "new" | "open" | "waiting_on_student" | "resolved";
  priority: StaffWorkItemPriority;
  assignee: StaffMemberSummary | null;
  createdAt: string;
  updatedAt: string;
  version: number;
}

export interface StaffInquiryThread {
  id: string;
  status: StaffInquiry["status"] | "archived";
  version: number;
  lastMessageAt: string;
  expiresAt: string;
  archivedAt: string | null;
  messages: StudentInquiryMessage[];
}

export interface StaffJourneyBlueprintItem {
  id: string;
  kind: "onboarding" | "enrollment";
  flowId: string;
  flowTitle: string;
  title: string;
  description: string;
  owner: string;
  required: boolean;
  published: boolean;
  /** Defaults to true for configuration versions published before task activation controls. */
  active?: boolean;
  priority?: number;
  order: number;
  dueOffsetDays?: number | null;
  taskType:
    | "information"
    | "form"
    | "upload_file"
    | "approval"
    | "single_select"
    | "multiple_select"
    | "selection_flow"
    | "signature"
    | "payment"
    | "scheduling";
  submissionType: "none" | "form" | "document" | "payment" | "appointment";
  selectionOptions?: string[];
  maximumSelections?: number | null;
  signatureProvider?: "built_in" | "docusign" | null;
  docusignTemplateId?: string | null;
  /** @deprecated Read only for configuration versions authored before canonical naming. */
  signatureTemplateId?: string | null;
  acceptedMimeTypes?: string[];
  /** @deprecated Read only for configuration versions authored before canonical naming. */
  acceptedFileTypes?: string[];
  documentCategories?: string[];
  interactionType?: StudentRequirementInteractionType;
  inputConfig?: StudentRequirementInputConfig;
  form?: StudentRequirementFormDefinition;
  points: number;
  studentStep: string | null;
  dependsOn: string[];
  /** Answer-driven conditions that decide whether this path applies. */
  activation?: JourneyRouteActivation;
  flow: Array<{
    id: string;
    title: string;
    field_type: string;
    required: boolean;
    options?: string[];
    maximum_selections?: number;
    when?: { field: string; equals: string };
  }>;
  configurationVersion: number;
}

export type StaffManagedConfigurationKind =
  | "journeys"
  | "campus_life"
  | "academics";

export interface StaffManagedConfiguration {
  kind: StaffManagedConfigurationKind;
  fileName: string;
  version: number;
  yaml: string;
  recordCount: number;
  updatedAt: string;
  updatedBy: string;
  changeSummary?: string;
}

export interface UpdateStaffManagedConfigurationInput {
  expectedVersion: number;
  yaml: string;
  changeSummary?: string;
}

export interface StaffEdwardConfigurationDraftInput {
  kind: StaffManagedConfigurationKind;
  expectedVersion: number;
  instruction: string;
}

export interface StaffEdwardConfigurationDraft {
  kind: StaffManagedConfigurationKind;
  expectedVersion: number;
  yaml: string;
  summary: string;
  changes: string[];
  warnings: string[];
  executionMode: "draft_requires_confirmation";
}

export interface StaffCommunicationHistoryItem {
  id: string;
  channel: "email" | "sms" | "voice" | "portal";
  direction: "inbound" | "outbound";
  summary: string;
  outcome:
    | "opened"
    | "delivered"
    | "no_response"
    | "needs_follow_up"
    | "completed";
  occurredAt: string;
}

export interface StaffStudentOperation {
  id: string;
  name: string;
  preferredName: string;
  programName: string;
  classYear: number;
  assignedStaffId: string;
  syntheticSeed: boolean;
  journey: {
    stage: string;
    completedTasks: number;
    totalTasks: number;
    lastActivityAt: string;
  };
  risk: {
    score: number;
    band: "low" | "medium" | "high" | "critical";
    category:
      | "financial"
      | "academic"
      | "belonging"
      | "administrative"
      | "family"
      | "engagement"
      | "geographic"
      | "confidence"
      | "timing";
    meltLikelihoodPercent: number;
    recoveryLikelihoodPercent: number;
    reason: string;
    signals: string[];
    modelVersion: string;
    evaluatedAt: string;
  };
  recommendedAction: {
    title: string;
    rationale: string;
    channel: "email" | "sms" | "voice" | "portal";
    expectedImpact: string;
    taskId: string | null;
    recommendedToday: boolean;
  };
  communicationHistory: StaffCommunicationHistoryItem[];
}

export interface StaffPersonalActionCenter {
  staff: StaffMemberSummary;
  students: StaffStudentOperation[];
  tasks: StaffWorkItem[];
  counts: {
    studentsToday: number;
    critical: number;
    highRisk: number;
    inProgress: number;
    completed: number;
  };
  generatedAt: string;
}

export interface StaffCohortSeed {
  synthetic: true;
  count: number;
  purpose: string;
  generatedAt: string;
  tenantSlug: string;
}

export interface StaffPortalInventoryItem {
  id:
    | "onboarding"
    | "enrollment"
    | "classrooms"
    | "campus_life"
    | "financials"
    | "messages"
    | "help";
  label: string;
  description: string;
  recordCount: number;
  managementState: "editable" | "partially_editable" | "planned";
}

export interface StaffOutreachRun {
  id: string;
  title: string;
  audience: string;
  channel: "email" | "sms" | "voice";
  requestedCount: number;
  status: "simulation_only";
  createdBy: string;
  createdAt: string;
}

export interface StaffOperationsWorkspace {
  currentStaff: StaffMemberSummary;
  actionCenter: StaffActionCenter;
  personalActionCenter: StaffPersonalActionCenter;
  cohort: StaffStudentOperation[];
  cohortSeed: StaffCohortSeed;
  student: StaffStudentRecord;
  knowledgeBase: StaffKnowledgeCard[];
  corePlays: StaffCorePlay[];
  inquiries: StaffInquiry[];
  journeyBlueprint: StaffJourneyBlueprintItem[];
  academicCatalog: {
    version: string;
    courses: CatalogCourse[];
  };
  configurations: {
    journeys: StaffManagedConfiguration;
    campusLife: StaffManagedConfiguration;
    academics: StaffManagedConfiguration;
  };
  campusLife: CampusLifeFeed;
  portalInventory: StaffPortalInventoryItem[];
  outreachRuns: StaffOutreachRun[];
  capabilities: {
    sharedStudentEdits: true;
    campusContentEdits: true;
    knowledgeBaseEdits: true;
    corePlayEdits: true;
    inquiryReplies: true;
    externalOutreach: "simulation_only";
    staffEdward: "preview_only";
    managedYaml: true;
  };
  generatedAt: string;
}

export interface UpdateStaffKnowledgeCardInput {
  expectedVersion: number;
  title: string;
  summary: string;
  body: string;
  category: string;
  audience: StaffKnowledgeCard["audience"];
  status: StaffKnowledgeCard["status"];
}

export type CreateStaffKnowledgeCardInput = Omit<
  UpdateStaffKnowledgeCardInput,
  "expectedVersion"
>;

export interface UpdateStaffCorePlayInput {
  expectedVersion: number;
  title: string;
  description: string;
  trigger: string;
  audience: string;
  steps: string[];
  status: StaffCorePlay["status"];
}

export type CreateStaffCorePlayInput = Omit<
  UpdateStaffCorePlayInput,
  "expectedVersion"
>;

export interface UpdateStaffInquiryInput {
  expectedVersion: number;
  status: StaffInquiry["status"];
  assigneeId?: string | null;
  responseNote?: string;
  notifyStudent: boolean;
}

export interface UpdateStaffClubInput {
  expectedVersion: number;
  name: string;
  category: string;
  description: string;
  latestUpdate: string;
  contactName: string;
  contactRole: string;
  contactChannel: string;
  membershipOpen: boolean;
}

export interface CreateStaffClubInput {
  name: string;
  category: string;
  description: string;
  latestUpdate: string;
  contactName: string;
  contactRole: string;
  contactChannel: string;
  membershipOpen: boolean;
  imageUrl?: string;
}

export interface SimulateStaffOutreachInput {
  title: string;
  audience: string;
  channel: StaffOutreachRun["channel"];
  requestedCount: number;
}

export interface StaffEdwardPreviewInput {
  message: string;
}

export interface StaffEdwardPreview {
  message: string;
  plan: {
    label: string;
    capability:
      | "read_student_data"
      | "update_journey"
      | "draft_message"
      | "launch_outreach";
    status: "available" | "needs_confirmation" | "simulation_only";
  }[];
  dataSources: string[];
  executionMode: "preview_only";
}

export interface CreateStudentDocumentInput {
  fileName: string;
  mimeType: StudentDocument["mimeType"];
  sizeBytes: number;
  category: StudentDocumentCategory;
}

export interface ConfirmStudentDocumentExtractionInput {
  acceptedFieldKeys: string[];
}

export interface EdwardChatMessage {
  role: "user" | "assistant";
  content: string;
}

export type AssistantConversationStatus = "active" | "closed";
export type AssistantMessageRole = "user" | "assistant";
export type AssistantInputMode = "text" | "voice";

export interface AssistantPageContext {
  path: string;
  label: string;
}

export interface CreateAssistantConversationInput {
  pageContext: AssistantPageContext;
}

export interface AskEdwardInput {
  /** Omitted on the first turn; the platform then opens a conversation. */
  conversationId?: string;
  /** Client-generated id so a retried send never duplicates a message. */
  clientMessageId?: string;
  message: string;
  inputMode?: AssistantInputMode;
  pageContext: string | AssistantPageContext;
  history?: EdwardChatMessage[];
}

/**
 * A bounded record projection that was collected by the request orchestrator
 * before Edward generated a reply. These are receipts for real reads, not
 * declarations of model-selected tools.
 */
export interface EdwardContextReceipt {
  source:
    | "dashboard"
    | "profile"
    | "documents"
    | "onboarding"
    | "payments"
    | "academics"
    | "financials"
    | "financial_aid"
    | "housing"
    | "holds"
    | "registration"
    | "deadlines"
    | "appointments"
    | "policies"
    | "account"
    | "messages"
    | "campus_life";
}

export type EdwardActionWidget =
  | {
      type: "deposit_payment";
      id: string;
      title: string;
      description: string;
      offerId: string;
      amountCents: number;
      status: "ready" | "completed";
    }
  | {
      type: "document_upload";
      id: string;
      title: string;
      description: string;
      category: StudentDocumentCategory;
      href: string;
    }
  | {
      type: "appointment";
      id: string;
      title: string;
      description: string;
      appointmentType: StudentAppointmentType;
      href: string;
    };

/* ---------------------------------------------------------------------------
 * Assistant presentation blocks.
 *
 * The portal renders these; `message` stays the plain-text channel for voice
 * and transcripts. Deliberately not markdown: the assistant never authors
 * layout, so malformed markup is unrepresentable and a table can never arrive
 * as a wall of pipe characters.
 *
 * Every block carries `fallbackText`, its own plain-text rendering, so a client
 * that does not recognise a block type still shows something true. That is what
 * makes this list safe to extend toward cards, badges, and appointment pickers.
 * ------------------------------------------------------------------------- */

export interface AssistantBlockListItem {
  text: string;
  /** A read-only portal route. Never an action or an external link. */
  href?: string;
}

export interface AssistantBlockNextStep extends AssistantBlockListItem {
  owner?: "student" | "university";
}

export interface AssistantBlockTableColumn {
  key: string;
  label: string;
  align?: "left" | "right";
}

export type AssistantResponseBlock =
  | { type: "text"; fallbackText: string; text: string }
  | {
      type: "bullet_list";
      fallbackText: string;
      title?: string;
      items: AssistantBlockListItem[];
    }
  | {
      type: "numbered_list";
      fallbackText: string;
      title?: string;
      items: AssistantBlockListItem[];
    }
  | {
      type: "table";
      fallbackText: string;
      caption?: string;
      columns: AssistantBlockTableColumn[];
      rows: Record<string, string>[];
    }
  | {
      type: "next_steps";
      fallbackText: string;
      title?: string;
      items: AssistantBlockNextStep[];
    };

export interface AskEdwardResponse {
  message: string;
  /** Structured rendering of `message`. Absent on legacy gateway replies. */
  blocks?: AssistantResponseBlock[];
  provider: "openai" | "openrouter" | "guided";
  model: string | null;
  usage: {
    promptTokens: number;
    completionTokens: number;
    totalTokens: number;
  } | null;
  suggestedActions: {
    label: string;
    href: string;
  }[];
  contextReceipts: EdwardContextReceipt[];
  widgets: EdwardActionWidget[];
  /** Present when the platform persisted this exchange to a conversation. */
  conversationId?: string;
  userMessageId?: string;
  assistantMessageId?: string;
  requestId?: string;
}

export interface AssistantConversationMessage {
  id: string;
  conversationId: string;
  role: AssistantMessageRole;
  inputMode: AssistantInputMode;
  content: string;
  clientMessageId: string | null;
  requestId: string | null;
  provider: AskEdwardResponse["provider"] | null;
  model: string | null;
  usage: AskEdwardResponse["usage"];
  blocks?: AssistantResponseBlock[];
  contextReceipts: EdwardContextReceipt[];
  suggestedActions: AskEdwardResponse["suggestedActions"];
  widgets: EdwardActionWidget[];
  createdAt: string;
}

export interface AssistantConversation {
  id: string;
  status: AssistantConversationStatus;
  messages: AssistantConversationMessage[];
  createdAt: string;
}

export interface AssistantConversationMessagesResponse {
  conversationId: string;
  messages: AssistantConversationMessage[];
}

export interface CreateAssistantVoiceSessionInput {
  conversationId: string;
  pageContext: AssistantPageContext;
}

export interface AssistantVoiceSessionCredentials {
  server_url: string;
  participant_token: string;
  voice_session_id: string;
  expires_at: string;
}

export interface AssistantVoiceSessionDetails {
  voiceSessionId: string;
  conversationId: string;
  provider: "livekit";
  roomName: string;
  participantIdentity: string;
  pageContext: AssistantPageContext;
  status: "active" | "ended";
  expiresAt: string;
  endedAt: string | null;
  createdAt: string;
}

export interface SubmitAssistantVoiceTurnInput {
  clientMessageId: string;
  text: string;
  inputMode: "voice";
  pageContext: AssistantPageContext;
  livekitStreamId: string;
}

export interface CatalogCourse {
  id: string;
  code: string;
  title: string;
  description: string;
  credits: number;
  level: number;
  availabilityLabel?: string | null;
  instructorNames?: string[];
  meetingPattern?: string | null;
  source?: PortalContentSource | null;
  resources?: CourseResource[];
  relatedVideos?: CourseVideo[];
  prerequisites: {
    courseCode: string;
    minimumGrade: string | null;
  }[];
}

export interface CourseResource {
  id: string;
  title: string;
  description: string;
  url: string;
  format: "pdf";
  provider: string;
  licenseLabel: string;
}

export interface CourseVideo {
  id: string;
  title: string;
  description?: string | null;
  url: string;
  provider: "YouTube";
  sourceLabel?: string | null;
}

export interface PortalContentSource {
  label: string;
  url: string;
  dataStatus: "official_source" | "synthetic_preview" | "tenant_authored";
}

export interface AcademicProgram {
  id: string;
  code: string;
  name: string;
  degree: string;
  totalCredits: number;
  description: string;
  source?: PortalContentSource | null;
}

export type AcademicPlanItemStatus =
  | "required"
  | "eligible"
  | "blocked"
  | "in_progress"
  | "completed"
  | "exemption_suggested"
  | "exempted";

export interface StudentAcademicPlanItem {
  course: CatalogCourse;
  category: "major_core" | "math_science" | "general_education" | "elective";
  recommendedTerm: number;
  status: AcademicPlanItemStatus;
  satisfiedPrerequisiteCodes: string[];
  missingPrerequisiteCodes: string[];
}

export interface TranscriptCredit {
  id: string;
  sourceType: "ap" | "ib" | "dual_enrollment" | "transfer" | "transcript";
  sourceCode: string | null;
  title: string;
  gradeOrScore: string | null;
  credits: number | null;
  institutionName: string | null;
  sourceDocumentId: string | null;
}

export interface CourseExemptionRecommendation {
  id: string;
  transcriptCreditId: string;
  targetCourseCode: string;
  targetCourseTitle: string;
  ruleCode: string;
  rationale: string;
  confidence: number;
  status: "suggested" | "needs_review" | "approved" | "denied";
  requiresStaffReview: boolean;
}

export interface StudentAcademics {
  selectedProgram: AcademicProgram;
  availablePrograms: AcademicProgram[];
  transcriptCredits: TranscriptCredit[];
  exemptionRecommendations: CourseExemptionRecommendation[];
  plan: StudentAcademicPlanItem[];
  progress: {
    completedCredits: number;
    exemptedCredits: number;
    requiredCredits: number;
    percent: number;
  };
  catalogVersion: string;
  generatedAt: string;
}

export interface FinancialDocumentRequirement {
  id: string;
  code: string;
  title: string;
  description: string;
  status: "not_started" | "submitted" | "under_review" | "verified" | "action_required";
  dueAt: string | null;
  documentId?: string | null;
  href: string;
  version: number;
  updatedAt: string;
}

export interface FinancialPaymentScheduleItem {
  id: string;
  kind: "deposit" | "installment";
  label: string;
  amountCents: number;
  enrollmentFeeCents: number;
  dueAt: string;
  status: "paid" | "due" | "projected";
  projected: boolean;
}

export interface FinancialAward {
  id: string;
  source: "federal" | "state" | "institutional" | "private";
  name: string;
  type: "grant" | "scholarship" | "loan" | "work_study";
  offeredAmountCents: number;
  acceptedAmountCents: number;
  status: "offered" | "accepted" | "declined" | "pending";
  requiresAction: boolean;
  updatedAt: string;
}

export interface StudentFinancials {
  academicYear: string;
  costOfAttendanceCents: number;
  acceptedAidCents: number;
  pendingAidCents: number;
  paymentsCents: number;
  remainingBalanceCents: number;
  awards: FinancialAward[];
  requiredDocuments: FinancialDocumentRequirement[];
  paymentPlans: {
    id: string;
    name: string;
    installmentCount: number;
    installmentAmountCents: number;
    enrollmentFeeCents: number;
    status: "available" | "enrolled";
  }[];
  paymentSchedule?: FinancialPaymentScheduleItem[];
  sap: {
    status: "meeting" | "warning" | "probation" | "not_meeting" | "appeal_pending";
    cumulativeGpa: number;
    minimumGpa: number;
    completionRatePercent: number;
    minimumCompletionRatePercent: number;
    attemptedCredits: number;
    maximumAttemptedCredits: number;
  };
  generatedAt: string;
}

export interface CampusEvent {
  id: string;
  title: string;
  description: string;
  startsAt: string;
  endsAt: string;
  location: string;
  category: "academic" | "social" | "career" | "wellness" | "athletics";
  featured: boolean;
  accent: "gold" | "navy" | "blue" | "coral";
  visualTheme?: "festival" | "discovery" | "career" | "community";
  imageUrl?: string | null;
  imageAlt?: string | null;
  imageAttribution?: string | null;
  imageSourceUrl?: string | null;
  advertisementStartsAt?: string | null;
  advertisementEndsAt?: string | null;
  source?: PortalContentSource | null;
  registrationUrl?: string | null;
  version: number;
  registrationStatus?:
    | "registered"
    | "cancelled_by_event"
    | "cancelled_by_student"
    | null;
}

export interface RegisterCampusEventInput {
  expectedVersion: number;
}

export interface CampusEventRegistration {
  id: string;
  eventId: string;
  status: "registered";
  eventVersion: number;
  registeredAt: string;
}

export interface StaffPortalMediaUpload {
  fileName: string;
  mimeType: "image/jpeg" | "image/png" | "image/webp";
  sizeBytes: number;
  sha256: string;
  publicPath: string;
  publicUrl: string;
}

export interface StudentClubSocialLink {
  label: string;
  url: string;
}

export interface StudentClubEvent {
  id: string;
  title: string;
  description: string;
  startsAt: string;
  endsAt: string;
  location: string;
  category: "meeting" | "workshop" | "social" | "competition" | "service";
  registrationUrl?: string | null;
}

export interface StudentClub {
  id: string;
  name: string;
  category: string;
  description: string;
  contactName: string;
  contactRole: string;
  contactChannel: string;
  latestUpdate: string;
  nextActivity: string | null;
  imageUrl: string;
  imageAlt: string;
  imageAttribution: string;
  imageSourceUrl: string;
  source?: PortalContentSource | null;
  socialLinks?: StudentClubSocialLink[];
  longDescription?: string | null;
  meetingSchedule?: string | null;
  membershipOpen?: boolean;
  events?: StudentClubEvent[];
  /** Present on staff-managed content records; student clients may ignore it. */
  version?: number;
  updatedAt?: string;
}

export interface CampusLifeFeed {
  events: CampusEvent[];
  clubs: StudentClub[];
  generatedAt: string;
}

export type StudentAppointmentType =
  | "admissions_counseling"
  | "financial_aid"
  | "enrollment_support";

export interface StudentAppointment {
  id: string;
  type: StudentAppointmentType;
  startsAt: string;
  notes: string | null;
  status: "scheduled" | "cancelled" | "completed";
  createdAt: string;
}

export interface StudentAppointmentList {
  items: StudentAppointment[];
  total: number;
}

export interface CreateStudentAppointmentInput {
  type: StudentAppointmentType;
  startsAt: string;
  notes?: string;
}

export interface StudentPayment {
  id: string;
  offerId: string;
  type: "enrollment_deposit";
  amountCents: number;
  status: "succeeded" | "failed" | "refunded";
  processor: "dummy";
  processorReference: string;
  createdAt: string;
}

export interface StudentPaymentList {
  items: StudentPayment[];
  total: number;
}

export interface CreateDepositPaymentInput {
  offerId: string;
}

export interface StudentProfile {
  studentId: string;
  preferredName: string;
  firstName?: string;
  lastName?: string;
  email?: string;
  emailVerified?: boolean;
  phoneVerified?: boolean;
  pronouns: string | null;
  mobilePhone: string | null;
  communicationPreference: "email" | "sms";
  version: number;
  updatedAt: string;
}

export interface UpdateStudentProfileInput {
  expectedVersion: number;
  preferredName?: string;
  pronouns?: string | null;
  mobilePhone?: string | null;
  communicationPreference?: "email" | "sms";
}

export interface HelpArticle {
  id: string;
  category: "getting_started" | "documents" | "payments" | "support";
  question: string;
  answer: string;
}

export interface StudentHelp {
  articles: HelpArticle[];
  requests: StudentHelpRequest[];
  support: {
    email: string;
    phone: string;
    hours: string;
  };
}

export interface CreateStudentHelpRequestInput {
  topicCode: HelpArticle["category"];
  message: string;
  requirementId?: string | null;
}

export interface StudentHelpRequest {
  id: string;
  topicCode: HelpArticle["category"];
  subject: string;
  message: string;
  status: "new" | "open" | "waiting_on_student" | "resolved";
  priority: StaffWorkItemPriority;
  assigneeId: string | null;
  requirementId?: string | null;
  workItemId?: string | null;
  createdAt: string;
  updatedAt: string;
  lastMessageAt?: string;
  expiresAt?: string;
  version: number;
  messages: StudentInquiryMessage[];
}

export interface StudentInquiryMessage {
  id: string;
  direction: "student" | "staff";
  body: string;
  authorName: string;
  deliveryStatus: "received" | "delivered" | "recorded";
  privateToStaff?: boolean;
  createdAt: string;
}

export interface CreateStudentInquiryMessageInput {
  expectedVersion: number;
  body: string;
}

export interface ApiErrorResponse {
  error: {
    code: string;
    message: string;
    requestId: string;
  };
}
