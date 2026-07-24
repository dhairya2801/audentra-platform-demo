import type {
  AcceptOfferResponse,
  ActivityEventInput,
  CompleteStudentOnboardingInput,
  CreateDepositPaymentInput,
  CreateStudentAppointmentInput,
  CreateStudentDocumentInput,
  StudentAppointment,
  StudentAppointmentList,
  StudentBootstrap,
  StudentDashboard,
  StudentDocument,
  StudentDocumentList,
  StudentHelp,
  StudentMessage,
  StudentMessageList,
  StudentOnboarding,
  StudentPayment,
  StudentPaymentList,
  StudentProfile,
  StudentRequirementDetail,
  StudentRequirementList,
  UpdateStudentOnboardingInput,
  UpdateStudentProfileInput,
} from "@vv/contracts";
import type { AuthContext } from "../auth/auth-context";

export const PLATFORM_STORE = Symbol("PLATFORM_STORE");

export interface ActivityIngestionResult {
  accepted: number;
  duplicates: number;
}

export interface PlatformStore {
  getStudentDashboard(auth: AuthContext): Promise<StudentDashboard>;
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
  createStudentDocument(input: {
    auth: AuthContext;
    document: CreateStudentDocumentInput;
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
