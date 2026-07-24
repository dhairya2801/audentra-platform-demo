export type OfferStatus = "offered" | "accepted" | "declined" | "expired";

export type RequirementStatus =
  | "not_applicable"
  | "blocked"
  | "ready"
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
  dueAt: string | null;
  progressPercent: number;
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
  journeyStatus: "in_progress";
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
  | "other_records"
  | "family_permissions"
  | "review_and_sign"
  | "deposit";

export interface StudentOnboardingData {
  legalNameConfirmed?: boolean;
  contactInformationConfirmed?: boolean;
  communicationPreference?: "email" | "sms";
  residencyStatus?: "domestic" | "international";
  supportNeeds?: string[];
  homeAddressConfirmed?: boolean;
  housingPreference?: "on_campus" | "off_campus" | "undecided";
  campusInterests?: string[];
  emergencyContactConfirmed?: boolean;
  recordsConfirmed?: boolean;
  familyPermissionsReviewed?: boolean;
  signatureConfirmed?: boolean;
  depositAcknowledged?: boolean;
}

export interface StudentOnboarding {
  studentId: string;
  status: OnboardingStatus;
  currentStep: OnboardingStep;
  completedSteps: OnboardingStep[];
  data: StudentOnboardingData;
  version: number;
  completedAt: string | null;
  updatedAt: string;
}

export interface UpdateStudentOnboardingInput {
  expectedVersion: number;
  currentStep: OnboardingStep;
  data: StudentOnboardingData;
}

export interface CompleteStudentOnboardingInput {
  expectedVersion: number;
}

export interface StudentBootstrap {
  authenticated: true;
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
  initialRoute: "/onboarding" | "/dashboard";
  generatedAt: string;
}

export interface StudentRequirementDetail
  extends StudentRequirementSummary {
  journeyId: string;
  submissionType: "form" | "document" | "payment" | "none";
  responsibleOffice: string;
  dependencyCodes: string[];
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
  warnings: string[];
  model: string | null;
  provider: "openrouter" | "local";
  processedAt: string | null;
  verifiedAt: string | null;
  acceptedFieldKeys?: string[];
}

export interface StudentDocument {
  id: string;
  fileName: string;
  mimeType: "application/pdf" | "image/jpeg" | "image/png";
  sizeBytes: number;
  category: StudentDocumentCategory;
  status:
    | "placeholder"
    | "uploaded"
    | "processing"
    | "needs_review"
    | "under_review"
    | "accepted"
    | "rejected";
  sha256?: string;
  contentUrl?: string;
  extraction?: StudentDocumentExtraction;
  createdAt: string;
}

export interface StudentDocumentList {
  items: StudentDocument[];
  total: number;
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

export interface AskEdwardInput {
  message: string;
  pageContext: string;
  history?: EdwardChatMessage[];
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

export interface AskEdwardResponse {
  message: string;
  provider: "openrouter" | "guided";
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
  toolsUsed: string[];
  widgets: EdwardActionWidget[];
}

export interface CatalogCourse {
  id: string;
  code: string;
  title: string;
  description: string;
  credits: number;
  level: number;
  prerequisites: {
    courseCode: string;
    minimumGrade: string | null;
  }[];
}

export interface AcademicProgram {
  id: string;
  code: string;
  name: string;
  degree: string;
  totalCredits: number;
  description: string;
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
  href: string;
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
  support: {
    email: string;
    phone: string;
    hours: string;
  };
}

export interface ApiErrorResponse {
  error: {
    code: string;
    message: string;
    requestId: string;
  };
}
