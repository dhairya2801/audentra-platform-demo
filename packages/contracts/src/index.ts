export type OfferStatus = "offered" | "accepted" | "declined" | "expired";

export interface TenantContact {
  label: string;
  email: string | null;
  phone: string | null;
  hours: string | null;
  url: string | null;
}

export interface TenantBootstrap {
  tenantId: string;
  slug: string;
  version: number;
  names: {
    displayName: string;
    legalName: string;
    shortName: string;
  };
  branding: {
    logoUrl: string;
    logoAlt: string;
    logoDarkUrl: string | null;
    logoDarkAlt: string | null;
    faviconUrl: string | null;
    heroImageUrl: string | null;
    heroImageAlt: string | null;
    primaryColor: string;
    secondaryColor: string;
    accentColor: string;
  };
  localization: {
    locale: string;
    timeZone: string;
    currencyCode: string;
    countryCode: string;
  };
  academicContext: {
    academicYearLabel: string;
    currentTermLabel: string;
    defaultCampusName: string | null;
  };
  contacts: {
    support: TenantContact;
    admissions: TenantContact | null;
    financialAid: TenantContact | null;
  };
  capabilities: Record<string, boolean>;
  publicLinks: Record<string, string>;
  updatedAt: string;
}

export interface UpdateTenantPortalConfigurationRequest {
  expectedVersion: number;
  names?: TenantBootstrap["names"];
  branding?: TenantBootstrap["branding"];
  localization?: TenantBootstrap["localization"];
  academicContext?: TenantBootstrap["academicContext"];
  contacts?: TenantBootstrap["contacts"];
  capabilities?: Record<string, boolean>;
  publicLinks?: Record<string, string>;
}

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
/** Tenant-owned residence code returned by the active housing inventory. */
export type HousingResidenceOption = string | null;

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
  /** Optional emergency-contact email, used only to prefill a later FERPA choice. */
  email?: string;
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
  actor?:
    | { type: "student" }
    | {
        type: "delegate";
        delegateId: string;
        name: string;
        relationship: string;
        studentId: string;
        studentName: string;
        scopes: FerpaPortalScope[];
      };
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
  onboarding?:
    | {
        required: boolean;
        status: OnboardingStatus;
        currentStep: OnboardingStep;
        version: number;
      }
    | {
        required: boolean;
        status: "restricted";
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
  | "scheduling"
  | "ferpa";

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

export type FerpaPortalScope =
  | "dashboard"
  | "enrollment"
  | "financials"
  | "classrooms"
  | "campus_life"
  | "edward"
  | "documents"
  | "messages"
  | "appointments"
  | "payments"
  | "profile"
  | "help";

export type FerpaAccessDecision = "grant" | "no_access";

export interface FerpaDelegateLinkState {
  status: "not_issued" | "active" | "revoked";
  issuedAt: string | null;
  rotatedAt: string | null;
  lastUsedAt: string | null;
  updatedAt: string;
}

export interface StudentFerpaDelegate {
  id: string;
  fullName: string;
  relationship:
    | "parent"
    | "guardian"
    | "partner"
    | "sponsor"
    | "relative"
    | "other";
  email: string;
  scopes: FerpaPortalScope[];
  legacyReviewRequired?: boolean;
  link: FerpaDelegateLinkState;
}

export interface StudentFerpaAuthorization {
  id: string;
  requirementId: string;
  requirementVersion: number;
  flowKind: "onboarding" | "enrollment";
  status: "incomplete" | "completed";
  accessDecision: FerpaAccessDecision | null;
  document:
    | { status: "unsigned" }
    | {
        status: "signed";
        signedDocumentId: string;
        title: string;
        fileName: string;
        signedAt: string;
        signerName: string;
        signatureMethod: "typed" | "drawn";
      };
  delegates: StudentFerpaDelegate[];
  version: number;
  completedAt: string | null;
  updatedAt: string;
  configuration: {
    signatureProvider: "built_in" | "docusign";
    docusignTemplateId: string | null;
    portalScopes: FerpaPortalScope[];
  };
  capabilities: {
    canSign: boolean;
    canManageAccess: boolean;
    canManageLinks: boolean;
  };
}

export interface StudentFerpaAuthorizationEnvelope {
  authorization: StudentFerpaAuthorization | null;
}

export interface FerpaDelegateInput {
  id?: string;
  fullName: string;
  relationship: StudentFerpaDelegate["relationship"];
  email: string;
  scopes: FerpaPortalScope[];
}

export interface FerpaSignatureInput {
  accepted: true;
  signerName: string;
  signatureMethod: "typed" | "drawn";
  signatureImageData?: string;
}

export interface CompleteStudentFerpaInput {
  expectedVersion: number;
  signature?: FerpaSignatureInput;
  accessDecision: FerpaAccessDecision;
  delegates: FerpaDelegateInput[];
}

export interface UpdateStudentFerpaAccessInput {
  expectedVersion: number;
  accessDecision: FerpaAccessDecision;
  delegates: FerpaDelegateInput[];
}

export interface FerpaLinkCommandInput {
  expectedVersion: number;
}

export interface FerpaDelegateLinkIssueResult {
  authorizationVersion: number;
  delegateId: string;
  status: "active";
  token: string;
  /** Relative portal URL. The bearer token is kept in the fragment. */
  url: `/delegate#token=${string}`;
  issuedAt: string;
}

export interface DelegateSession {
  authenticated: true;
  mode: "delegate";
  actorType: "delegate";
  delegate: {
    id: string;
    fullName: string;
    relationship: string;
    email: string;
    studentId: string;
    studentName: string;
    studentPreferredName: string;
    scopes: FerpaPortalScope[];
  };
  initialRoute:
    | "/dashboard"
    | "/enrollment"
    | "/financials"
    | "/classrooms"
    | "/campus-life"
    | "/edward"
    | "/documents"
    | "/messages"
    | "/appointments"
    | "/payments"
    | "/profile"
    | "/help";
  capabilities: { canManageFerpa: false; canSignFerpa: false };
  expiresAt: number;
}

export interface ExchangeDelegateLinkInput {
  token: string;
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
  title?: string | null;
  roleCode?: string;
  externalRef?: string | null;
  employmentStatus?: StaffEmploymentStatus;
  /** Date (YYYY-MM-DD) leave ends, when `employmentStatus` is `on_leave`. */
  leaveUntil?: string | null;
  /** End of a current absence (vacation, sick, conference, leave) — null when present. */
  awayUntil?: string | null;
  awayKind?: string | null;
}

export type StaffEmploymentStatus = "active" | "on_leave" | "departed";
export type StaffAssignmentRole =
  | "primary_advisor"
  | "admissions_counselor"
  | "financial_aid_counselor"
  | "international_adviser"
  | "housing_coordinator";

/** A staff member as other people and students see them. */
export interface StaffPersonBrief {
  id: string;
  name: string;
  email: string;
  component: string;
  title: string | null;
  roleCode: string;
  externalRef: string | null;
  employmentStatus: StaffEmploymentStatus;
  leaveUntil: string | null;
  endedAt: string | null;
}

export interface StaffPerson extends StaffPersonBrief {
  active: boolean;
  employmentType: "full_time" | "part_time";
  startedAt: string | null;
  timezone: string;
  officeLocation: string | null;
  caseloadCap: number | null;
  studentFacing: boolean;
  appointmentTypes: StudentAppointmentType[];
  managerId: string | null;
}

export interface StaffAvailabilitySummary {
  bookable: boolean;
  reason: "departed" | "on_leave" | "no_availability" | "does_not_offer_type" | null;
  nextOpenSlotAt: string | null;
  openSlotsNext14Days: number;
  bookedNext14Days: number;
  timezone?: string;
  weekly?: StaffAvailabilityRule[];
  timeOff?: StaffTimeOff[];
}

export interface StaffAvailabilityRule {
  weekday: number;
  startMinute: number;
  endMinute: number;
  slotMinutes: number;
  modality: "in_person" | "virtual" | "either";
  location: string | null;
  appointmentTypes: StudentAppointmentType[];
}

export interface StaffTimeOff {
  id: string;
  startsAt: string;
  endsAt: string;
  kind: "leave" | "vacation" | "sick" | "training" | "conference" | "blocked" | "other";
  note: string | null;
  blocksBookings: boolean;
  current: boolean;
}

export interface StaffCaseloadSummary {
  primaryAdvisees: number;
  cap: number | null;
  utilization: number | null;
  overCap: boolean;
}

export interface StaffWorkSummary {
  open: number;
  overdue: number;
  staleInProgress: number;
  appointmentsAwaitingOutcome: number;
  urgent?: number;
  escalated?: number;
  completedLast7Days?: number;
}

export interface StaffTeamMember extends StaffPersonBrief {
  /** 1 = reports to me directly; 2+ = reports to one of my reports. */
  level: number;
  reportsTo: string | null;
  caseload: StaffCaseloadSummary & { assignments: number };
  work: StaffWorkSummary;
  availability: StaffAvailabilitySummary;
  flags: Array<
    | "departed_with_caseload"
    | "departed"
    | "on_leave_with_caseload"
    | "on_leave"
    | "over_cap"
    | "no_open_slots"
    | "falling_behind"
    | "spare_capacity"
  >;
}

export interface StaffComponentSummary {
  component: string;
  members: number;
  directReports: number;
  membersOnLeave: number;
  membersDeparted: number;
  membersOverCap: number;
  primaryAdvisees: number;
  caseloadCap: number | null;
  studentsWithDepartedAdviser: number;
  studentsWithAdviserOnLeave: number;
  acceptedStudentsWithoutPrimaryAdviser: number;
  unassignedComponentItems: number;
  overdueComponentItems: number;
}

/** GET /v1/staff/me */
export interface StaffMe {
  staff: StaffPerson;
  manager: StaffPersonBrief | null;
  directReports: StaffTeamMember[];
  /** Everyone under me, direct reports first. */
  team: StaffTeamMember[];
  caseload: StaffCaseloadSummary & { byRole: Record<StaffAssignmentRole, number> };
  work: StaffWorkSummary;
  availability: StaffAvailabilitySummary;
  appointmentsToday: StaffAppointment[];
  componentSummary: StaffComponentSummary | null;
  generatedAt: string;
}

export interface StaffCaseloadItem {
  role: StaffAssignmentRole;
  assignedAt: string;
  source: string;
  note: string | null;
  student: {
    id: string;
    name: string;
    preferredName: string;
    externalRef: string | null;
    classYear: number | null;
    programName: string;
  };
  offerStatus: string | null;
  journeyStatus: string | null;
  requirements: { completed: number; total: number; percent: number | null };
  advising: {
    status: "completed" | "scheduled" | "missed" | "none";
    lastCompletedAt: string | null;
    nextAppointmentAt: string | null;
    nextAppointmentId: string | null;
  };
  work: { open: number; overdue: number };
}

/** GET /v1/staff/caseload */
export interface StaffCaseload {
  staff: StaffPersonBrief;
  items: StaffCaseloadItem[];
  total: number;
  summary: {
    byRole: Partial<Record<StaffAssignmentRole, number>>;
    primaryAdvisees: number;
    cap: number | null;
    utilization: number | null;
    overCap: boolean;
    advising: { completed: number; scheduled: number; missed: number; none: number };
    withOpenWork: number;
    withOverdueWork: number;
  };
  generatedAt: string;
}

export interface StaffAppointment extends StudentAppointment {
  student: {
    id: string;
    name: string;
    preferredName: string | null;
    externalRef: string | null;
  };
}

/** GET /v1/staff/appointments */
export interface StaffAppointmentCalendar {
  staff: StaffPersonBrief;
  from: string;
  to: string;
  items: StaffAppointment[];
  total: number;
  counts: {
    scheduled: number;
    completed: number;
    cancelled: number;
    noShow: number;
    awaitingOutcome: number;
  };
  availability: StaffAvailabilitySummary;
}

export interface UpdateStaffAppointmentInput {
  status: "cancelled" | "completed" | "no_show";
  reason?: string;
  outcomeNote?: string;
  expectedVersion?: number;
}

/** GET /v1/auth/demo/staff/directory — development and preview only. */
export interface DemoStaffDirectoryEntry {
  id: string;
  name: string;
  email: string;
  component: string;
  title: string | null;
  roleCode: string;
  externalRef: string | null;
  employmentStatus: StaffEmploymentStatus;
  leaveUntil: string | null;
  managerName: string | null;
  caseload: { primaryAdvisees: number; cap: number | null };
  openWorkItems: number;
  directReports: number;
  canSignIn: boolean;
}

export interface DemoStaffDirectory {
  items: DemoStaffDirectoryEntry[];
  total: number;
  notice: string;
}

export interface DemoStaffSignInInput {
  staffRef: string;
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

/** Why an item's owner cannot be expected to work it right now. */
export type StaffWorkItemOwnerRisk = "departed" | "on_leave" | "away";

/**
 * Operational signals derived per item at read time from its own timestamps
 * and its owner's status. `stale` means in progress and untouched for 10+ days.
 */
export interface StaffWorkItemSignals {
  overdue: boolean;
  overdueDays: number | null;
  stale: boolean;
  staleDays: number | null;
  ageDays: number;
  unassigned: boolean;
  ownerRisk: StaffWorkItemOwnerRisk | null;
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
        type: "onboarding" | "requirement" | "document" | "message" | "scheduled_rule";
        id: string;
      }
    | null;
  /** Populated on board pages for the items on the page and on the detail read. */
  history: StaffWorkItemLog[];
  signals: StaffWorkItemSignals;
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

export type StaffActionCenterStatusScope = "open" | "closed" | "all";
export type StaffActionCenterDueWindow =
  | "all"
  | "overdue"
  | "today"
  | "seven_days"
  | "no_due";
export type StaffActionCenterSort =
  | "priority"
  | "due"
  | "updated"
  | "created"
  | "stale";

/**
 * Query parameters of `GET /v1/staff/action-center`. Every field is optional;
 * the default is open work, priority order, first 50 items.
 */
export interface StaffActionCenterQuery {
  status?: StaffActionCenterStatusScope | StaffWorkItemStatus;
  priority?: StaffWorkItemPriority;
  component?: string;
  /** `me`, `unassigned`, a staff member id, or part of an owner's name. */
  assignee?: string;
  /** Matches key, title, description, component, student and owner name. */
  search?: string;
  due?: StaffActionCenterDueWindow;
  stale?: boolean;
  ownerRisk?: boolean;
  escalated?: boolean;
  actionType?: StaffActionType;
  workType?: StaffWorkItemType;
  /** Only the items of one student. */
  studentId?: string;
  /** In progress and untouched for more than this many days. */
  inProgressDays?: number;
  sort?: StaffActionCenterSort;
  /** 1–200, default 50. */
  limit?: number;
  offset?: number;
}

export interface StaffActionCenterFacets {
  /** Open work per component, most open first. */
  components: {
    component: string;
    open: number;
    overdue: number;
    unassigned: number;
    stale: number;
    ownerRisk: number;
    urgent: number;
  }[];
  /** Open work per owner (unassigned first), at most 25 rows. */
  assignees: {
    staff: StaffMemberSummary | null;
    open: number;
    overdue: number;
    stale: number;
    urgent: number;
  }[];
}

/**
 * One bounded page of the board plus board-wide counts and facets. The board
 * is never returned in full: page further with `offset`, or narrow with the
 * query. `counts.todo…escalated` describe the whole board (the legacy shape);
 * `counts.open…ownerRisk` describe open work only.
 */
export interface StaffActionCenter {
  items: StaffWorkItem[];
  /** Active staff (including people on leave, flagged), for assignment. */
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
    open: number;
    overdue: number;
    stale: number;
    unassigned: number;
    ownerRisk: number;
  };
  page: {
    limit: number;
    offset: number;
    /** Items matching the query across all pages. */
    total: number;
    hasMore: boolean;
    distinctStudents: number;
  };
  facets: StaffActionCenterFacets;
  /** The normalized query this page answers. */
  query: {
    status: string;
    priority: string | null;
    component: string | null;
    assignee: string | null;
    search: string | null;
    due: StaffActionCenterDueWindow;
    stale: boolean | null;
    ownerRisk: boolean | null;
    escalated: boolean | null;
    actionType: string | null;
    workType: string | null;
    studentId: string | null;
    inProgressDays: number | null;
    sort: StaffActionCenterSort;
    limit: number;
    offset: number;
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

export type StudentSsoProviderId = "google" | "microsoft";

export interface StudentSsoProvider {
  id: StudentSsoProviderId;
  label: string;
}

export interface StudentSsoConfiguration {
  providers: StudentSsoProvider[];
  passwordEnabled: boolean;
}

export interface StaffSession {
  authenticated: true;
  mode: "credentials" | "google" | "microsoft" | "demo";
  actorType: "staff";
  staff: StaffMemberSummary;
  notice: string;
}

export type StaffIdentityProvider = "google" | "microsoft";

export interface StaffAuthOptions {
  tenantSlug: string;
  providers: StaffIdentityProvider[];
  passwordEnabled: boolean;
}

export interface StaffMailbox {
  id: string;
  provider: StaffIdentityProvider;
  address: string;
  displayName: string | null;
  kind: "personal" | "shared";
  status: "active" | "reconnect_required" | "disabled";
  canRead: boolean;
  canSend: boolean;
  canManage: boolean;
  lastSyncedAt: string | null;
}

export interface StaffMailboxList {
  items: StaffMailbox[];
  total: number;
}

export interface StaffMailMessage {
  id?: string;
  providerMessageId?: string;
  threadId: string | null;
  sender: string;
  recipients: string[];
  subject: string | null;
  body: string | null;
  direction?: "inbound" | "outbound";
  receivedAt: string | null;
}

export interface StaffMailMessageList {
  items: StaffMailMessage[];
  total: number;
}

export interface SearchStaffMailInput {
  mailboxId: string;
  query: string;
  limit?: number;
}

export interface CreateStaffEmailSendIntentInput {
  mailboxId: string;
  studentId?: string;
  replyToMessageId?: string;
  interactionId?: string;
  subject: string;
  body: string;
}

export interface StaffEmailSendIntent {
  id: string;
  version: number;
  status:
    | "pending_confirmation"
    | "queued"
    | "sending"
    | "sent"
    | "failed"
    | "expired"
    | "cancelled";
  mailboxId: string;
  sender: string;
  recipients: string[];
  subject: string;
  body: string;
  contentSha256: string;
  expiresAt: string;
  confirmedAt?: string | null;
  sentAt?: string | null;
  error?: { code: string; message: string } | null;
}

export interface ConfirmStaffEmailSendIntentInput {
  expectedVersion: number;
  contentSha256: string;
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
    | "scheduling"
    | "ferpa";
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
  document: Record<string, unknown>;
  yaml: string;
  recordCount: number;
  updatedAt: string;
  updatedBy: string;
  changeSummary?: string;
}

export interface UpdateStaffManagedConfigurationInput {
  expectedVersion: number;
  document?: Record<string, unknown>;
  /** Legacy import/audit compatibility. `document` is the canonical representation. */
  yaml?: string;
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
  document: Record<string, unknown>;
  yaml: string;
  summary: string;
  changes: string[];
  warnings: string[];
  executionMode: "draft_requires_confirmation";
  persisted: false;
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
  synthetic: boolean;
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
    managedYaml: "import_only";
    managedDocument: true;
  };
  generatedAt: string;
}

/* ------------------------------------------------------------ morning brew */

/**
 * Morning Brew is the staff briefing surface. Every number in it is a count of
 * canonical PostgreSQL rows read at one instant, and every population is a
 * `CohortFilter` the staff assistant can re-run — so any headline can be
 * expanded into the students behind it.
 *
 * There are deliberately no targets, benchmarks, projections, or probabilities
 * in this contract. The platform holds no plan figures and no validated
 * predictive model, so a field for them would only invite invention.
 */
export type StaffBrewTopic =
  | "admissions"
  | "financial_aid"
  | "housing"
  | "registrar"
  | "student_success";

export type StaffBrewSeverity = "high" | "medium" | "positive";
export type StaffBrewTone = "positive" | "watch" | "neutral";
export type StaffBrewWindowId = "now" | "day";

export type StaffBrewDestination =
  | "overview"
  | "outreach"
  | "tasks"
  | "students"
  | "messages"
  | "campus_life"
  | "academics"
  | "journeys"
  | "knowledge"
  | "edward";

/** The canonical selection behind a number, plus how to re-ask for it. */
export interface StaffBrewCohortRef {
  key: string;
  /** Reads as a noun phrase: "deposited students with an overdue requirement". */
  label: string;
  /** Arguments accepted verbatim by the assistant's `findStudents` filter. */
  filter: Record<string, string | number | boolean>;
  /** The same filter in words, from the canonical filter description. */
  clauses: string[];
  /** A question that reproduces this cohort through Staff Edward. */
  question: string;
}

export interface StaffBrewChangeValue {
  value: number;
  label: string;
  direction: "up" | "down" | "flat";
  favorable: boolean;
  comparison: string;
  /** The exact column the change was counted from. */
  basis: string;
}

export interface StaffBrewMetricFrame {
  windowId: StaffBrewWindowId;
  value: number;
  /** What the value describes in this window. */
  window: string;
  /** The denominator this value is a share of, when one exists. */
  basisLabel: string | null;
  basisValue: number | null;
  basisPercent: number | null;
  note: string;
  /** True when the window cannot be reconstructed for this metric. */
  unavailable: boolean;
  change: StaffBrewChangeValue | null;
}

export interface StaffBrewMetric {
  id: string;
  topic: StaffBrewTopic;
  label: string;
  icon: string;
  format: "int";
  definition: string;
  source: "canonical_postgres";
  cohort: StaffBrewCohortRef;
  frames: StaffBrewMetricFrame[];
  segments: {
    key: string;
    label: string;
    value: number;
    percent: number | null;
  }[];
}

export interface StaffBrewChange {
  id: string;
  topic: StaffBrewTopic;
  title: string;
  count: number;
  metric: string;
  detail: string;
  tone: StaffBrewTone;
  /** The most recent event of this kind inside the window, if any. */
  occurredAt: string | null;
  destination: StaffBrewDestination;
  basis: string;
  basisNote: string;
  /** False where the timestamp records the last write rather than the event. */
  exact: boolean;
}

export interface StaffBrewAttentionItem {
  id: string;
  topic: StaffBrewTopic;
  /** The office that owns the follow-up. */
  label: string;
  title: string;
  severity: StaffBrewSeverity;
  summary: string;
  scope: string;
  impactLabel: string;
  impact: { label: string; tone: "negative" | "positive" | "neutral" }[];
  recommendedAction: string;
  priorityLevel: "High" | "Medium" | "Low";
  destination: StaffBrewDestination;
  cohort: StaffBrewCohortRef;
  detail: {
    narrative: string[];
    drivers: { label: string; value: string; note: string }[];
    breakdown: {
      code: string;
      title: string;
      students: number;
      requirements: number;
      overdue: number;
    }[];
    breakdownNote: string | null;
    actions: { title: string; detail: string; owner: string; due: string }[];
    students: { id: string; name: string; program: string; note: string }[];
    studentsNote: string | null;
    evidence: string[];
  };
}

export interface StaffBrewPriority {
  id: string;
  topic: StaffBrewTopic;
  title: string;
  count: number;
  level: "High" | "Medium" | "Low";
  icon: string;
  detail: string;
  linkLabel: string;
  destination: StaffBrewDestination;
  window: string;
  breakdown: { label: string; value: string }[];
  steps: string[];
  /** Action Center query that opens the queue pre-filtered, when one applies. */
  boardQuery?: StaffActionCenterQuery | null;
}

export interface StaffBrewDeadline {
  id: string;
  kind: "requirement" | "offer_response";
  code: string;
  title: string;
  /** Earliest due date in this bucket; `latestDueAt` when they are staggered. */
  dueAt: string;
  latestDueAt: string;
  bucket: "overdue" | "today" | "this_week" | "this_month";
  relativeLabel: string;
  students: number;
  priority: "high" | "medium" | "low";
  detail: string;
  destination: StaffBrewDestination;
}

export interface StaffBrewRequest {
  id: string;
  subject: string;
  summary: string;
  status: "new" | "open" | "waiting_on_student" | "resolved";
  priority: "urgent" | "high" | "medium" | "low";
  topicCode: string;
  studentName: string;
  programName: string;
  assigneeName: string | null;
  createdAt: string;
  lastMessageAt: string;
  waitingHours: number;
  waitingLabel: string;
  destination: "messages";
}

export interface StaffBrewSynthesis {
  headline: string;
  bullets: string[];
  /** `deterministic` means the prose was templated from counted values. */
  source: "deterministic";
  basis: string;
}

export type StaffBrewCapacitySeverity = "critical" | "high" | "medium" | "positive";
export type StaffBrewCapacitySignalKind =
  | "departed_with_caseload"
  | "on_leave_with_caseload"
  | "over_cap_no_slots"
  | "over_cap"
  | "away_with_backlog"
  | "falling_behind"
  | "spare_capacity"
  | "component_backlog"
  | "unassigned_backlog"
  | "students_without_adviser";

export interface StaffBrewCapacitySignal {
  id: string;
  kind: StaffBrewCapacitySignalKind;
  severity: StaffBrewCapacitySeverity;
  title: string;
  detail: string;
  count: number;
  action: string;
  staff: {
    id: string;
    name: string;
    title: string | null;
    component: string | null;
    employmentStatus: StaffEmploymentStatus | null;
  } | null;
  component: string | null;
  destination: StaffBrewDestination;
  /** Action Center query that shows the items behind the signal, when there are any. */
  boardQuery: StaffActionCenterQuery | null;
}

export interface StaffBrewCapacity {
  available: boolean;
  summary: {
    staff: number;
    active: number;
    onLeave: number;
    departed: number;
    awayNow: number;
    overCap: number;
    fallingBehind: number;
    spareCapacity: number;
    itemsOwnedByUnavailable: number;
    staleItems: number;
    unassignedItems: number;
    acceptedWithoutAdviser: number;
    depositedWithoutAdviser: number;
    studentsWithDepartedAdviser: number;
    studentsWithAdviserOnLeave: number;
    signalKinds: StaffBrewCapacitySignalKind[];
    signalsTotal: number;
  } | null;
  /** At most 8, ranked by severity then size. */
  signals: StaffBrewCapacitySignal[];
  signalsOmitted?: number;
  /** Open work per office, most overdue first, at most 12 rows. */
  components: {
    component: string;
    open: number;
    overdue: number;
    unassigned: number;
    stale: number;
    urgent: number;
    ownerRisk: number;
    oldestOverdueDays: number | null;
  }[];
  basis: string;
}

export interface StaffMorningBrew {
  generatedAt: string;
  window: {
    id: "day";
    hours: number;
    label: string;
    since: string;
    basis: string;
  };
  windows: {
    id: StaffBrewWindowId;
    label: string;
    short: string;
    description: string;
  }[];
  population: {
    students: number;
    cohorts: Record<string, number>;
  };
  synthesis: StaffBrewSynthesis;
  metrics: StaffBrewMetric[];
  changes: StaffBrewChange[];
  attention: StaffBrewAttentionItem[];
  priorities: StaffBrewPriority[];
  deadlines: StaffBrewDeadline[];
  requests: {
    items: StaffBrewRequest[];
    total: number;
    awaitingFirstReply: number;
    unassigned: number;
  };
  staffWork: {
    openItems: number;
    urgent: number;
    escalated: number;
    overdue: number;
    unassigned: number;
    assignedToMe: number;
  };
  /**
   * The people dimension: where capacity is constrained or available, who is
   * absent while owning work, and which offices are behind. A short ranked
   * list of rule-based signals, never a per-person dashboard.
   */
  staffCapacity: StaffBrewCapacity;
  /** Freshness of the deterministic engagement scan behind attention flags. */
  engagementScan: {
    available: boolean;
    snapshots: number;
    lastProjectedAt: string | null;
    /** Portal activity events in the last 30 days; 0 means inactivity is unknown. */
    activityEvents30d?: number;
    activitySignal?: boolean;
  };
  coverage: {
    source: "canonical_postgres";
    notes: string[];
    /** Named on purpose: what this briefing cannot honestly report, and why. */
    unsupported: { metric: string; reason: string }[];
  };
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
      /**
       * Read-only portal route per row, aligned by index with `rows` (null
       * for rows without a destination); renderers link the row's first cell.
       * Never an action or an external link.
       */
      rowHrefs?: (string | null)[];
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

/** One mutable response-scoped feedback record shared by Student/Staff Edward. */
export type EdwardFeedbackRating = "positive" | "negative";

export interface EdwardFeedbackInput {
  /** The request/trace id returned with this exact assistant message. */
  traceId: string;
  /** Omit to preserve the current rating; null clears it when written feedback remains. */
  rating?: EdwardFeedbackRating | null;
  /** Omit to preserve the current comment; null clears it when a rating remains. */
  writtenFeedback?: string | null;
}

export interface EdwardResponseFeedback {
  id: string;
  assistantKind: "student" | "staff";
  tenantId: string;
  actorType: "student" | "staff";
  actorId: string;
  actorName: string | null;
  referencedStudentId: string | null;
  referencedStudentName: string | null;
  conversationId: string;
  userMessageId: string;
  assistantMessageId: string;
  traceId: string;
  question: string;
  response: string;
  rating: EdwardFeedbackRating | null;
  writtenFeedback: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface EdwardFeedbackListResponse {
  items: EdwardResponseFeedback[];
  total: number;
  limit: number;
  offset: number;
}

/* ---------------------------------------------------------------------------
 * Staff Edward — read-only staff assistant and its durable transcript.
 *
 * Tenant, staff member, and student referent identities are intentionally
 * absent from the request. The API binds staff/tenant identity from the
 * authenticated session and resolves student referents server-side.
 * ------------------------------------------------------------------------- */

export interface StaffAssistantDraftBlock {
  type: "draft";
  fallbackText: string;
  channel: "email" | "sms";
  subject?: string;
  body: string;
  disclaimer: string;
}

export type StaffAssistantResponseBlock = AssistantResponseBlock | StaffAssistantDraftBlock;

export interface AskStaffEdwardInput {
  message: string;
  /** Omit for a stateless turn or when `clientMessageId` should create the conversation. */
  conversationId?: string;
  /** Staff-scoped replay key; retries return the exact stored exchange. */
  clientMessageId?: string;
}

export interface StaffAssistantResolvedStudent {
  id: string;
  name: string;
}

export interface StaffAssistantContextReceipt {
  source: string;
}

export interface AskStaffEdwardResponse {
  message: string;
  blocks?: StaffAssistantResponseBlock[];
  provider: AskEdwardResponse["provider"];
  model: string | null;
  usage: AskEdwardResponse["usage"];
  contextReceipts: StaffAssistantContextReceipt[];
  /** Present on student-grounded turns; omitted by pre-pipeline safety replies. */
  resolvedStudent?: StaffAssistantResolvedStudent | null;
  requestId: string;
  /** Present when the platform persisted this exchange. */
  conversationId?: string;
  userMessageId?: string;
  assistantMessageId?: string;
}

export interface StaffAssistantConversationMessage {
  id: string;
  conversationId: string;
  role: AssistantMessageRole;
  content: string;
  clientMessageId: string | null;
  requestId: string | null;
  provider: AskEdwardResponse["provider"] | null;
  model: string | null;
  usage: AskEdwardResponse["usage"];
  blocks: StaffAssistantResponseBlock[] | null;
  contextReceipts: StaffAssistantContextReceipt[];
  referencedStudentId: string | null;
  createdAt: string;
}

export interface StaffAssistantConversation {
  id: string;
  status: AssistantConversationStatus;
  messages: StaffAssistantConversationMessage[];
  createdAt: string;
}

export interface StaffAssistantConversationMessagesResponse {
  conversationId: string;
  activeStudentId: string | null;
  messages: StaffAssistantConversationMessage[];
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
  | "enrollment_support"
  | "academic_advising"
  | "international_check_in";

export type StudentAppointmentStatus =
  | "scheduled"
  | "cancelled"
  | "completed"
  | "no_show"
  | "rescheduled";

export interface AppointmentStaffSummary {
  id: string;
  name: string;
  title: string | null;
  component: string | null;
  email: string | null;
  employmentStatus: StaffEmploymentStatus;
}

export interface StudentAppointment {
  id: string;
  type: StudentAppointmentType;
  startsAt: string;
  endsAt?: string | null;
  notes: string | null;
  status: StudentAppointmentStatus;
  createdAt: string;
  modality?: "in_person" | "virtual" | null;
  location?: string | null;
  bookedVia?: "student_portal" | "staff" | "walk_in" | "import";
  cancelledAt?: string | null;
  cancelReason?: string | null;
  rescheduledToId?: string | null;
  rescheduledFromId?: string;
  outcomeNote?: string | null;
  version?: number;
  /** The person the appointment is with; null for legacy or unassigned bookings. */
  staff?: AppointmentStaffSummary | null;
}

export interface StudentAppointmentList {
  items: StudentAppointment[];
  total: number;
}

export interface CreateStudentAppointmentInput {
  type: StudentAppointmentType;
  startsAt: string;
  notes?: string;
  /** Book with a specific person; otherwise the student's assigned staff for the type. */
  staffMemberId?: string;
  modality?: "in_person" | "virtual";
}

export interface CancelStudentAppointmentInput {
  reason?: string;
}

export interface RescheduleStudentAppointmentInput {
  startsAt: string;
  staffMemberId?: string;
  modality?: "in_person" | "virtual";
  notes?: string;
}

export interface AppointmentSlot {
  startsAt: string;
  endsAt: string;
  modality: "in_person" | "virtual" | "either";
  location: string | null;
}

export interface AppointmentAvailabilityStaff extends StaffPersonBrief {
  relationship: StaffAssignmentRole | null;
  reason: "departed" | "on_leave" | "does_not_offer_type" | "no_open_slots" | null;
  slots: AppointmentSlot[];
  nextOpenSlotAt: string | null;
}

/** GET /v1/student/appointments/availability */
export interface AppointmentAvailability {
  type: StudentAppointmentType;
  from: string;
  to: string;
  staff: AppointmentAvailabilityStaff[];
  generatedAt: string;
}

export interface StudentAdviserAssignment {
  role: StaffAssignmentRole;
  assignedAt: string;
  source: string;
  staff: StaffPersonBrief & { officeLocation?: string | null };
  availability: StaffAvailabilitySummary;
}

export interface StudentAdvisingGap {
  code: "no_primary_adviser" | "adviser_departed" | "adviser_on_leave" | "adviser_no_open_slots";
  message: string;
}

/** GET /v1/student/advising */
export interface StudentAdvising {
  primaryAdviser: StudentAdviserAssignment | null;
  advisers: StudentAdviserAssignment[];
  advising: {
    type: StudentAppointmentType;
    status: "completed" | "scheduled" | "missed" | "none";
    lastCompletedAt: string | null;
    nextAppointment: StudentAppointment | null;
    missedCount: number;
  };
  gaps: StudentAdvisingGap[];
  generatedAt: string;
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
