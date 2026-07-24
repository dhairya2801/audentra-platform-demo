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
  | "other";

export interface StudentDocument {
  id: string;
  fileName: string;
  mimeType: "application/pdf" | "image/jpeg" | "image/png";
  sizeBytes: number;
  category: StudentDocumentCategory;
  status: "placeholder" | "uploaded" | "under_review" | "accepted" | "rejected";
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
