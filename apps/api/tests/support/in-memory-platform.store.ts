import { randomUUID } from "node:crypto";
import type {
  AcceptOfferResponse,
  ActivityEventInput,
  CompleteStudentOnboardingInput,
  CreateDepositPaymentInput,
  CreateStudentAppointmentInput,
  CreateStudentDocumentInput,
  OnboardingStep,
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
import type { AuthContext } from "../../src/auth/auth-context";
import {
  ConflictError,
  NotFoundError,
} from "../../src/common/api-error";
import { BadRequestError } from "../../src/common/api-error";
import { DEMO_IDS } from "../../src/config/app-config";
import type {
  ActivityIngestionResult,
  PlatformStore,
} from "../../src/platform/platform-store";
import { validateActivityEventProperties } from "../../src/platform/postgres-platform.store";
import {
  ONBOARDING_STEPS,
  validateOnboardingStepData,
} from "../../src/portal/onboarding-policy";

export class InMemoryPlatformStore implements PlatformStore {
  private readonly idempotentResponses = new Map<
    string,
    { offerId: string; response: AcceptOfferResponse }
  >();
  private readonly activityEventIds = new Set<string>();
  private acceptedResponse: AcceptOfferResponse | undefined;
  private readonly portalIdempotency = new Map<string, unknown>();
  private onboarding: StudentOnboarding = {
    studentId: DEMO_IDS.studentId,
    status: "in_progress",
    currentStep: "offer",
    completedSteps: [],
    data: {},
    version: 1,
    completedAt: null,
    updatedAt: "2026-07-24T00:00:00.000Z",
  };
  private requirements: StudentRequirementDetail[] = [];
  private readonly messages: StudentMessage[] = [
    {
      id: DEMO_IDS.welcomeMessageId,
      subject: "Welcome to your enrollment portal",
      body: "Your portal keeps every enrollment action in one place.",
      senderName: "Enrollment Services",
      sentAt: "2026-07-24T09:00:00.000Z",
      readAt: null,
    },
    {
      id: DEMO_IDS.reminderMessageId,
      subject: "Your next enrollment step",
      body: "Review your admission offer and continue when you are ready.",
      senderName: "Admissions Office",
      sentAt: "2026-07-24T10:00:00.000Z",
      readAt: null,
    },
  ];
  private readonly documents: StudentDocument[] = [
    {
      id: DEMO_IDS.sampleDocumentId,
      fileName: "identity-document-placeholder.pdf",
      mimeType: "application/pdf",
      sizeBytes: 2048,
      category: "identity",
      status: "placeholder",
      createdAt: "2026-07-24T00:00:00.000Z",
    },
  ];
  private readonly appointments: StudentAppointment[] = [
    {
      id: DEMO_IDS.sampleAppointmentId,
      type: "enrollment_support",
      startsAt: "2027-08-05T14:00:00.000Z",
      notes: "Welcome and enrollment planning",
      status: "scheduled",
      createdAt: "2026-07-24T00:00:00.000Z",
    },
  ];
  private readonly payments: StudentPayment[] = [];
  private profile: StudentProfile = {
    studentId: DEMO_IDS.studentId,
    preferredName: "Alex",
    pronouns: null,
    mobilePhone: null,
    communicationPreference: "email",
    version: 1,
    updatedAt: "2026-07-24T00:00:00.000Z",
  };

  readonly effects = {
    journeys: 0,
    requirementSets: 0,
    auditEvents: 0,
    outboxEvents: 0,
  };

  private readonly dashboard: StudentDashboard = {
    student: {
      id: DEMO_IDS.studentId,
      preferredName: "Alex",
      fullName: "Alex Morgan",
      classYear: 2027,
    },
    offer: {
      id: DEMO_IDS.offerId,
      programName: "Computer Science",
      termName: "Fall 2027",
      campusName: "Main Campus",
      responseDeadline: "2027-08-15",
      depositAmountCents: 50_000,
      status: "offered",
    },
    journey: {
      id: null,
      status: "not_started",
      completionPercent: 0,
      nextAction: {
        code: "accept_offer",
        label: "Review and accept your offer",
        href: "/offer",
      },
      requirements: [],
    },
    unreadMessageCount: 0,
    projectionVersion: 1,
    generatedAt: "2026-07-24T00:00:00.000Z",
  };

  async getStudentDashboard(auth: AuthContext): Promise<StudentDashboard> {
    this.authorize(auth);
    return structuredClone(this.dashboard);
  }

  async acceptAdmissionOffer(input: {
    auth: AuthContext;
    offerId: string;
    idempotencyKey: string;
    requestId: string;
  }): Promise<AcceptOfferResponse> {
    this.authorize(input.auth);
    const replayKey = `${input.auth.tenantId}:${input.auth.actorId}:${input.idempotencyKey}`;
    const replay = this.idempotentResponses.get(replayKey);
    if (replay) {
      if (replay.offerId !== input.offerId) {
        throw new ConflictError(
          "IDEMPOTENCY_KEY_REUSED",
          "This idempotency key was already used for a different request",
        );
      }
      return structuredClone(replay.response);
    }
    if (input.offerId !== DEMO_IDS.offerId) {
      throw new NotFoundError(
        "ADMISSION_OFFER_NOT_FOUND",
        "The admission offer was not found",
      );
    }

    if (!this.acceptedResponse) {
      const journeyId = randomUUID();
      this.acceptedResponse = {
        offerId: input.offerId,
        offerStatus: "accepted",
        journeyId,
        journeyStatus: "in_progress",
        projectionVersion: 2,
        acceptedAt: "2026-07-24T12:00:00.000Z",
      };
      this.effects.journeys += 1;
      this.effects.requirementSets += 1;
      this.effects.auditEvents += 1;
      this.effects.outboxEvents += 2;
      this.dashboard.offer.status = "accepted";
      this.dashboard.journey = {
        id: journeyId,
        status: "in_progress",
        completionPercent: 0,
        nextAction: {
          code: "profile_verification",
          label: "Verify your profile",
          href: "/enrollment?requirement=profile_verification",
        },
        requirements: [],
      };
      this.dashboard.projectionVersion = 2;
      this.requirements = [
        {
          id: randomUUID(),
          journeyId,
          code: "profile_verification",
          title: "Verify your profile",
          description: "Confirm your personal and contact information.",
          status: "ready",
          blocking: true,
          dueAt: "2026-07-31T12:00:00.000Z",
          progressPercent: 0,
          submissionType: "form",
          responsibleOffice: "Enrollment Services",
          dependencyCodes: [],
        },
      ];
    }

    this.idempotentResponses.set(replayKey, {
      offerId: input.offerId,
      response: this.acceptedResponse,
    });
    return structuredClone(this.acceptedResponse);
  }

  async ingestActivityEvents(input: {
    auth: AuthContext;
    events: ActivityEventInput[];
    requestId: string;
  }): Promise<ActivityIngestionResult> {
    this.authorize(input.auth);
    input.events.forEach(validateActivityEventProperties);
    let accepted = 0;
    for (const event of input.events) {
      const key = `${input.auth.tenantId}:${event.eventId}`;
      if (!this.activityEventIds.has(key)) {
        this.activityEventIds.add(key);
        accepted += 1;
      }
    }
    return {
      accepted,
      duplicates: input.events.length - accepted,
    };
  }

  async getStudentBootstrap(auth: AuthContext): Promise<StudentBootstrap> {
    if (
      auth.tenantId !== DEMO_IDS.tenantId ||
      auth.studentId !== DEMO_IDS.studentId
    ) {
      throw new NotFoundError(
        "STUDENT_NOT_FOUND",
        "The authenticated student was not found",
      );
    }
    const required = this.onboarding.status !== "completed";
    return {
      authenticated: true,
      student: {
        id: DEMO_IDS.studentId,
        preferredName: this.profile.preferredName,
        fullName: "Alex Morgan",
      },
      onboarding: {
        required,
        status: this.onboarding.status,
        currentStep: this.onboarding.currentStep,
        version: this.onboarding.version,
      },
      initialRoute: required ? "/onboarding" : "/dashboard",
      generatedAt: "2026-07-24T12:00:00.000Z",
    };
  }

  async getStudentOnboarding(auth: AuthContext): Promise<StudentOnboarding> {
    this.authorize(auth);
    return structuredClone(this.onboarding);
  }

  async updateStudentOnboarding(input: {
    auth: AuthContext;
    update: UpdateStudentOnboardingInput;
    requestId: string;
  }): Promise<StudentOnboarding> {
    this.authorize(input.auth);
    if (this.onboarding.status === "completed") {
      throw new ConflictError(
        "ONBOARDING_ALREADY_COMPLETED",
        "Completed onboarding cannot be changed",
      );
    }
    if (input.update.expectedVersion !== this.onboarding.version) {
      throw new ConflictError(
        "VERSION_CONFLICT",
        "Onboarding changed in another session",
      );
    }
    if (input.update.currentStep !== this.onboarding.currentStep) {
      throw new ConflictError(
        "ONBOARDING_STEP_OUT_OF_ORDER",
        `The next required onboarding step is ${this.onboarding.currentStep}`,
      );
    }
    const mergedData = { ...this.onboarding.data, ...input.update.data };
    validateOnboardingStepData(input.update.currentStep, mergedData);
    if (input.update.currentStep === "offer" && !this.acceptedResponse) {
      throw new ConflictError(
        "ACCEPTED_OFFER_REQUIRED",
        "Accept the admission offer before completing this step",
      );
    }
    if (
      input.update.currentStep === "deposit" &&
      this.payments.length === 0
    ) {
      throw new ConflictError(
        "DEPOSIT_REQUIRED",
        "Complete the enrollment deposit before saving this step",
      );
    }
    const index = ONBOARDING_STEPS.indexOf(this.onboarding.currentStep);
    this.onboarding = {
      ...this.onboarding,
      status: "in_progress",
      currentStep: ONBOARDING_STEPS[index + 1] ?? "deposit",
      completedSteps: [
        ...this.onboarding.completedSteps,
        this.onboarding.currentStep,
      ],
      data: mergedData,
      version: this.onboarding.version + 1,
      updatedAt: "2026-07-24T12:00:00.000Z",
    };
    return structuredClone(this.onboarding);
  }

  async completeStudentOnboarding(input: {
    auth: AuthContext;
    update: CompleteStudentOnboardingInput;
    idempotencyKey: string;
    requestId: string;
  }): Promise<StudentOnboarding> {
    this.authorize(input.auth);
    const key = `onboarding:${input.idempotencyKey}`;
    const replay = this.portalIdempotency.get(key) as
      | StudentOnboarding
      | undefined;
    if (replay) return structuredClone(replay);
    if (this.onboarding.status !== "completed") {
      if (input.update.expectedVersion !== this.onboarding.version) {
        throw new ConflictError(
          "VERSION_CONFLICT",
          "Onboarding changed in another session",
        );
      }
      if (this.onboarding.completedSteps.length !== 9) {
        throw new ConflictError(
          "ONBOARDING_INCOMPLETE",
          "Every onboarding step must be completed in order",
        );
      }
      this.onboarding = {
        ...this.onboarding,
        status: "completed",
        version: this.onboarding.version + 1,
        completedAt: "2026-07-24T12:00:00.000Z",
        updatedAt: "2026-07-24T12:00:00.000Z",
      };
    }
    this.portalIdempotency.set(key, structuredClone(this.onboarding));
    return structuredClone(this.onboarding);
  }

  async getStudentRequirements(
    auth: AuthContext,
  ): Promise<StudentRequirementList> {
    this.authorize(auth);
    return {
      items: structuredClone(this.requirements),
      total: this.requirements.length,
    };
  }

  async getStudentRequirement(
    auth: AuthContext,
    requirementId: string,
  ): Promise<StudentRequirementDetail> {
    this.authorize(auth);
    const requirement = this.requirements.find(
      (item) => item.id === requirementId,
    );
    if (!requirement) {
      throw new NotFoundError(
        "STUDENT_REQUIREMENT_NOT_FOUND",
        "The requirement was not found",
      );
    }
    return structuredClone(requirement);
  }

  async getStudentMessages(auth: AuthContext): Promise<StudentMessageList> {
    this.authorize(auth);
    return {
      items: structuredClone(this.messages),
      unreadCount: this.messages.filter((message) => message.readAt === null)
        .length,
    };
  }

  async markStudentMessageRead(input: {
    auth: AuthContext;
    messageId: string;
    requestId: string;
  }): Promise<StudentMessage> {
    this.authorize(input.auth);
    const message = this.messages.find(
      (candidate) => candidate.id === input.messageId,
    );
    if (!message) {
      throw new NotFoundError(
        "STUDENT_MESSAGE_NOT_FOUND",
        "The message was not found",
      );
    }
    message.readAt ??= "2026-07-24T12:00:00.000Z";
    return structuredClone(message);
  }

  async getStudentDocuments(auth: AuthContext): Promise<StudentDocumentList> {
    this.authorize(auth);
    return {
      items: structuredClone(this.documents),
      total: this.documents.length,
    };
  }

  async createStudentDocument(input: {
    auth: AuthContext;
    document: CreateStudentDocumentInput;
    idempotencyKey: string;
    requestId: string;
  }): Promise<StudentDocument> {
    this.authorize(input.auth);
    const key = `document:${input.idempotencyKey}`;
    const replay = this.portalIdempotency.get(key) as
      | StudentDocument
      | undefined;
    if (replay) return structuredClone(replay);
    const document: StudentDocument = {
      id: randomUUID(),
      ...input.document,
      status: "placeholder",
      createdAt: "2026-07-24T12:00:00.000Z",
    };
    this.documents.unshift(document);
    this.portalIdempotency.set(key, structuredClone(document));
    return structuredClone(document);
  }

  async getStudentAppointments(
    auth: AuthContext,
  ): Promise<StudentAppointmentList> {
    this.authorize(auth);
    return {
      items: structuredClone(this.appointments),
      total: this.appointments.length,
    };
  }

  async createStudentAppointment(input: {
    auth: AuthContext;
    appointment: CreateStudentAppointmentInput;
    idempotencyKey: string;
    requestId: string;
  }): Promise<StudentAppointment> {
    this.authorize(input.auth);
    if (new Date(input.appointment.startsAt).getTime() <= Date.now()) {
      throw new BadRequestError(
        "APPOINTMENT_MUST_BE_FUTURE",
        "Appointment time must be in the future",
      );
    }
    const key = `appointment:${input.idempotencyKey}`;
    const replay = this.portalIdempotency.get(key) as
      | StudentAppointment
      | undefined;
    if (replay) return structuredClone(replay);
    const appointment: StudentAppointment = {
      id: randomUUID(),
      type: input.appointment.type,
      startsAt: input.appointment.startsAt,
      notes: input.appointment.notes ?? null,
      status: "scheduled",
      createdAt: "2026-07-24T12:00:00.000Z",
    };
    this.appointments.push(appointment);
    this.portalIdempotency.set(key, structuredClone(appointment));
    return structuredClone(appointment);
  }

  async getStudentPayments(auth: AuthContext): Promise<StudentPaymentList> {
    this.authorize(auth);
    return {
      items: structuredClone(this.payments),
      total: this.payments.length,
    };
  }

  async createDepositPayment(input: {
    auth: AuthContext;
    payment: CreateDepositPaymentInput;
    idempotencyKey: string;
    requestId: string;
  }): Promise<StudentPayment> {
    this.authorize(input.auth);
    if (!this.acceptedResponse || input.payment.offerId !== DEMO_IDS.offerId) {
      throw new ConflictError(
        "ACCEPTED_OFFER_REQUIRED",
        "An accepted admission offer is required before paying a deposit",
      );
    }
    const key = `payment:${input.idempotencyKey}`;
    const replay = this.portalIdempotency.get(key) as StudentPayment | undefined;
    if (replay) return structuredClone(replay);
    const existing = this.payments[0];
    if (existing) return structuredClone(existing);
    const id = randomUUID();
    const payment: StudentPayment = {
      id,
      offerId: input.payment.offerId,
      type: "enrollment_deposit",
      amountCents: 50_000,
      status: "succeeded",
      processor: "dummy",
      processorReference: `dummy_${id.replaceAll("-", "")}`,
      createdAt: "2026-07-24T12:00:00.000Z",
    };
    this.payments.push(payment);
    this.portalIdempotency.set(key, structuredClone(payment));
    return structuredClone(payment);
  }

  async getStudentProfile(auth: AuthContext): Promise<StudentProfile> {
    this.authorize(auth);
    return structuredClone(this.profile);
  }

  async updateStudentProfile(input: {
    auth: AuthContext;
    update: UpdateStudentProfileInput;
    requestId: string;
  }): Promise<StudentProfile> {
    this.authorize(input.auth);
    if (input.update.expectedVersion !== this.profile.version) {
      throw new ConflictError(
        "VERSION_CONFLICT",
        "The profile changed in another session",
      );
    }
    const { expectedVersion: _expectedVersion, ...changes } = input.update;
    if (Object.keys(changes).length === 0) {
      throw new BadRequestError(
        "PROFILE_UPDATE_EMPTY",
        "At least one profile field must be supplied",
      );
    }
    this.profile = {
      ...this.profile,
      ...changes,
      version: this.profile.version + 1,
      updatedAt: "2026-07-24T12:00:00.000Z",
    };
    return structuredClone(this.profile);
  }

  async getStudentHelp(auth: AuthContext): Promise<StudentHelp> {
    this.authorize(auth);
    return {
      articles: [
        {
          id: DEMO_IDS.helpGettingStartedId,
          category: "getting_started",
          question: "Where should I begin?",
          answer: "Start with the next action shown on your dashboard.",
        },
      ],
      support: {
        email: "enrollment-support@vv.example",
        phone: "+1 555 010 2027",
        hours: "Monday-Friday, 09:00-17:00",
      },
    };
  }

  private authorize(auth: AuthContext): void {
    if (
      auth.tenantId !== DEMO_IDS.tenantId ||
      auth.studentId !== DEMO_IDS.studentId
    ) {
      throw new NotFoundError(
        "STUDENT_DASHBOARD_NOT_FOUND",
        "No dashboard is available for this student",
      );
    }
  }
}
