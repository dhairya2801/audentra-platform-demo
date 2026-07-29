import type {
  AcceptOfferResponse,
  ActivityEventInput,
  CompleteStudentOnboardingInput,
  ConfirmStudentDocumentExtractionInput,
  CreateDepositPaymentInput,
  CreateStudentAppointmentInput,
  CreateStudentDocumentInput,
  ExtractedTranscriptCourse,
  CampusLifeFeed,
  CatalogCourse,
  StudentAcademics,
  StudentAppointment,
  StudentAppointmentList,
  StudentBootstrap,
  StudentDashboard,
  StudentDocument,
  StudentDocumentExtraction,
  StudentDocumentList,
  StudentHelp,
  StudentHousingPlan,
  StudentFinancials,
  StudentMessage,
  StudentMessageList,
  StudentOnboarding,
  StudentPayment,
  StudentPaymentList,
  StudentProfile,
  StudentRequirementDetail,
  StudentRequirementList,
  UpdateStudentOnboardingInput,
  UpdateStudentHousingPlanInput,
  UpdateStudentProfileInput,
} from "@vv/contracts";
import type { AuthContext } from "../auth/auth-context";

export const PLATFORM_STORE = Symbol("PLATFORM_STORE");

export interface ActivityIngestionResult {
  accepted: number;
  duplicates: number;
}

export interface AiProviderResponseAttempt {
  id: string;
  tenantId: string;
  studentId: string;
  documentId: string | null;
  requestId: string;
  attempt: number;
  operation:
    | "edward_chat"
    | "document_classification"
    | "document_extraction"
    | "transcript_segment_extraction"
    | "transcript_merge"
    | "course_label_normalization"
    | "course_exemption_mapping"
    | "immunization_extraction"
    | "immunization_compliance";
  provider: "openrouter" | "groq";
  requestedModel: string | null;
  responseModel: string | null;
  providerRequestId: string | null;
  httpStatus: number | null;
  responseOk: boolean;
  finishReason: string | null;
  usage: Record<string, unknown> | null;
  rawResponseText: string | null;
  responseBody: unknown;
  transportError: { name: string; message: string } | null;
  durationMs: number;
  recordedAt: string;
  promptTemplateVersionId: string | null;
  contextPolicyVersionId: string | null;
  outputSchemaVersionId: string | null;
  configRevision: number | null;
  contextSha256: string | null;
  promptCacheStatus: "hit" | "miss" | "reloaded" | "fallback";
}

export interface CourseExemptionContext {
  program: {
    id: string;
    code: string;
    name: string;
  };
  catalogVersion: {
    id: string;
    code: string;
    effectiveFrom: string;
    updatedAt: string;
  };
  policyVersion: string;
  catalogCourses: Array<{
    id: string;
    code: string;
    title: string;
    credits: number;
  }>;
  programRequirements: Array<{
    id: string;
    courseId: string;
    category: string;
    required: boolean;
    recommendedTerm: number;
  }>;
  prerequisites: Array<{
    courseId: string;
    prerequisiteCourseId: string;
    minimumGrade: string | null;
  }>;
  equivalencyRules: Array<{
    id: string;
    code: string;
    version: number;
    sourceType: string;
    sourceCode: string;
    minimumScore: number | null;
    minimumGrade: string | null;
    minimumCredits: number | null;
    targetCourseId: string;
    confidence: number;
  }>;
}

export interface ImmunizationPolicyContext {
  policyVersion: {
    id: string;
    code: string;
    version: number;
    name: string;
    effectiveFrom: string;
    effectiveUntil: string | null;
    updatedAt: string;
  };
  requirements: Array<{
    id: string;
    code: string;
    name: string;
    description: string;
    required: boolean;
    doseCount: number | null;
    validityDays: number | null;
    appliesWhen: Record<string, unknown>;
    evidenceCriteria: Record<string, unknown>;
  }>;
}

export interface PlatformStore {
  recordAiProviderResponse(input: AiProviderResponseAttempt): Promise<void>;
  getStudentDashboard(auth: AuthContext): Promise<StudentDashboard>;
  getStudentAcademics(auth: AuthContext): Promise<StudentAcademics>;
  searchCatalogCourses(
    auth: AuthContext,
    query: string,
  ): Promise<{ items: CatalogCourse[]; total: number; catalogVersion: string }>;
  getStudentFinancials(auth: AuthContext): Promise<StudentFinancials>;
  selectFinancialPaymentPlan(input: {
    auth: AuthContext;
    planId: string;
    idempotencyKey: string;
    requestId: string;
  }): Promise<{ planId: string; status: "enrolled" }>;
  getCampusLife(auth: AuthContext): Promise<CampusLifeFeed>;
  acceptAdmissionOffer(input: {
    auth: AuthContext;
    offerId: string;
    idempotencyKey: string;
    requestId: string;
  }): Promise<AcceptOfferResponse>;
  ingestActivityEvents(input: {
    auth: AuthContext;
    events: ActivityEventInput[];
    requestId: string;
  }): Promise<ActivityIngestionResult>;
  getStudentBootstrap(auth: AuthContext): Promise<StudentBootstrap>;
  getStudentOnboarding(auth: AuthContext): Promise<StudentOnboarding>;
  updateStudentOnboarding(input: {
    auth: AuthContext;
    update: UpdateStudentOnboardingInput;
    requestId: string;
  }): Promise<StudentOnboarding>;
  completeStudentOnboarding(input: {
    auth: AuthContext;
    update: CompleteStudentOnboardingInput;
    idempotencyKey: string;
    requestId: string;
  }): Promise<StudentOnboarding>;
  getStudentHousingPlan(auth: AuthContext): Promise<StudentHousingPlan>;
  updateStudentHousingPlan(input: {
    auth: AuthContext;
    update: UpdateStudentHousingPlanInput;
    requestId: string;
  }): Promise<StudentHousingPlan>;
  getStudentRequirements(auth: AuthContext): Promise<StudentRequirementList>;
  getStudentRequirement(
    auth: AuthContext,
    requirementId: string,
  ): Promise<StudentRequirementDetail>;
  getStudentMessages(auth: AuthContext): Promise<StudentMessageList>;
  markStudentMessageRead(input: {
    auth: AuthContext;
    messageId: string;
    requestId: string;
  }): Promise<StudentMessage>;
  getStudentDocuments(auth: AuthContext): Promise<StudentDocumentList>;
  saveStudentSignedDocument(input: {
    auth: AuthContext;
    document: {
      id: string;
      templateCode: string;
      onboardingVersion: number;
      title: string;
      fileName: string;
      sizeBytes: number;
      storageKey: string;
      sha256: string;
      signerName: string;
      signatureMethod: "typed" | "drawn";
      signedAt: string;
    };
    requestId: string;
  }): Promise<StudentDocument>;
  createStudentDocument(input: {
    auth: AuthContext;
    document: CreateStudentDocumentInput;
    idempotencyKey: string;
    requestId: string;
  }): Promise<StudentDocument>;
  reserveStudentDocumentUpload(input: {
    auth: AuthContext;
    document: CreateStudentDocumentInput & { sha256: string };
    requirementId?: string;
    idempotencyKey: string;
    requestId: string;
  }): Promise<StudentDocument>;
  claimStudentDocumentProcessing(input: {
    auth: AuthContext;
    documentId: string;
    /**
     * An initial upload may claim only an unprocessed document. A retry may
     * claim only a stored failed or pending-configuration extraction, so an
     * accidental retry cannot spend another model call for a reviewed file.
     */
    retry?: boolean;
    requestId?: string;
    /** Required for retry claims so a lost response cannot start another parse. */
    retryIdempotencyKey?: string;
  }): Promise<boolean>;
  releaseStudentDocumentProcessing(input: {
    auth: AuthContext;
    documentId: string;
    requestId: string;
  }): Promise<void>;
  completeStudentDocumentExtraction(input: {
    auth: AuthContext;
    documentId: string;
    extraction: StudentDocumentExtraction;
    requestId: string;
    /** Finalizes the retry idempotency record once the parse result is stored. */
    retryIdempotencyKey?: string;
  }): Promise<StudentDocument>;
  getCourseExemptionContext(input: {
    auth: AuthContext;
    courses: ExtractedTranscriptCourse[];
  }): Promise<CourseExemptionContext | null>;
  getImmunizationPolicyContext(
    auth: AuthContext,
  ): Promise<ImmunizationPolicyContext | null>;
  getStudentDocument(input: {
    auth: AuthContext;
    documentId: string;
  }): Promise<StudentDocument>;
  getStudentDocumentContentReference(input: {
    auth: AuthContext;
    documentId: string;
  }): Promise<{
    storageKey: string;
    fileName: string;
    mimeType: StudentDocument["mimeType"];
  }>;
  confirmStudentDocumentExtraction(input: {
    auth: AuthContext;
    documentId: string;
    confirmation: ConfirmStudentDocumentExtractionInput;
    idempotencyKey: string;
    requestId: string;
  }): Promise<StudentDocument>;
  getStudentAppointments(auth: AuthContext): Promise<StudentAppointmentList>;
  createStudentAppointment(input: {
    auth: AuthContext;
    appointment: CreateStudentAppointmentInput;
    idempotencyKey: string;
    requestId: string;
  }): Promise<StudentAppointment>;
  getStudentPayments(auth: AuthContext): Promise<StudentPaymentList>;
  createDepositPayment(input: {
    auth: AuthContext;
    payment: CreateDepositPaymentInput;
    idempotencyKey: string;
    requestId: string;
  }): Promise<StudentPayment>;
  getStudentProfile(auth: AuthContext): Promise<StudentProfile>;
  updateStudentProfile(input: {
    auth: AuthContext;
    update: UpdateStudentProfileInput;
    requestId: string;
  }): Promise<StudentProfile>;
  getStudentHelp(auth: AuthContext): Promise<StudentHelp>;
}
