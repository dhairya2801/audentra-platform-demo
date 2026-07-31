import { randomUUID } from "node:crypto";
import {
  documentCategoryForRequirement,
  studentRequirementCodeFromSlug,
  studentRequirementSlug,
  type AcceptOfferResponse,
  type CampusLifeFeed,
  type CatalogCourse,
  type ActivityEventInput,
  type CompleteStudentOnboardingInput,
  type ConfirmStudentDocumentExtractionInput,
  type CreateDepositPaymentInput,
  type CreateStudentAppointmentInput,
  type CreateStudentDocumentInput,
  type OnboardingStep,
  type StudentAppointment,
  type StudentAcademics,
  type StudentAppointmentList,
  type StudentBootstrap,
  type StudentDashboard,
  type StudentDocument,
  type StudentDocumentExtraction,
  type StudentDocumentList,
  type StudentHelp,
  type StudentHousingPlan,
  type StudentFinancials,
  type StudentMessage,
  type StudentMessageList,
  type StudentOnboarding,
  type StudentPayment,
  type StudentPaymentList,
  type StudentProfile,
  type StudentRequirementDetail,
  type StudentRequirementList,
  type UpdateStudentOnboardingInput,
  type UpdateStudentHousingPlanInput,
  type UpdateStudentProfileInput,
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
  AiProviderResponseAttempt,
  PlatformStore,
} from "../../src/platform/platform-store";
import { validateActivityEventProperties } from "../../src/platform/postgres-platform.store";
import {
  isSkippableOnboardingStep,
  ONBOARDING_STEPS,
  validateOnboardingStepData,
} from "../../src/portal/onboarding-policy";

export class InMemoryPlatformStore implements PlatformStore {
  readonly aiProviderResponses: AiProviderResponseAttempt[] = [];
  private readonly idempotentResponses = new Map<
    string,
    { offerId: string; response: AcceptOfferResponse }
  >();
  private readonly activityEventIds = new Set<string>();
  private acceptedResponse: AcceptOfferResponse | undefined;
  private readonly portalIdempotency = new Map<string, unknown>();
  private readonly documentStorageKeys = new Map<string, string>();
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

  async recordAiProviderResponse(
    input: AiProviderResponseAttempt,
  ): Promise<void> {
    this.aiProviderResponses.push(structuredClone(input));
  }

  async getCourseExemptionContext(): Promise<null> {
    return null;
  }

  async getImmunizationPolicyContext(): Promise<null> {
    return null;
  }

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

  async getStudentAcademics(auth: AuthContext): Promise<StudentAcademics> {
    this.authorize(auth);
    const course: CatalogCourse = {
      id: "10000000-0000-7000-8000-000000000201",
      code: "CS 101",
      title: "Programming Fundamentals",
      description: "Problem solving and introductory software development.",
      credits: 4,
      level: 100,
      prerequisites: [],
    };
    return {
      selectedProgram: {
        id: DEMO_IDS.programId,
        code: "BS-CS",
        name: "Computer Science",
        degree: "Bachelor of Science",
        totalCredits: 120,
        description: "Computer science degree program.",
      },
      availablePrograms: [],
      transcriptCredits: [],
      exemptionRecommendations: [],
      plan: [
        {
          course,
          category: "major_core",
          recommendedTerm: 1,
          status: "eligible",
          satisfiedPrerequisiteCodes: [],
          missingPrerequisiteCodes: [],
        },
      ],
      progress: {
        completedCredits: 0,
        exemptedCredits: 0,
        requiredCredits: 120,
        percent: 0,
      },
      catalogVersion: "2027-2028.v1",
      generatedAt: "2026-07-24T00:00:00.000Z",
    };
  }

  async searchCatalogCourses(
    auth: AuthContext,
    query: string,
  ): Promise<{ items: CatalogCourse[]; total: number; catalogVersion: string }> {
    const academics = await this.getStudentAcademics(auth);
    const items = academics.plan
      .map((item) => item.course)
      .filter((course) =>
        `${course.code} ${course.title}`.toLowerCase().includes(query.toLowerCase()),
      );
    return { items, total: items.length, catalogVersion: academics.catalogVersion };
  }

  async getStudentFinancials(auth: AuthContext): Promise<StudentFinancials> {
    this.authorize(auth);
    return {
      academicYear: "2027–2028",
      costOfAttendanceCents: 3_240_000,
      acceptedAidCents: 1_539_500,
      pendingAidCents: 350_000,
      paymentsCents: 0,
      remainingBalanceCents: 1_700_500,
      awards: [],
      requiredDocuments: [],
      paymentPlans: [],
      sap: {
        status: "meeting",
        cumulativeGpa: 3.42,
        minimumGpa: 2,
        completionRatePercent: 78,
        minimumCompletionRatePercent: 67,
        attemptedCredits: 28,
        maximumAttemptedCredits: 180,
      },
      generatedAt: "2026-07-24T00:00:00.000Z",
    };
  }

  async selectFinancialPaymentPlan(input: {
    auth: AuthContext;
    planId: string;
    idempotencyKey: string;
    requestId: string;
  }): Promise<{ planId: string; status: "enrolled" }> {
    this.authorize(input.auth);
    return { planId: input.planId, status: "enrolled" };
  }

  async getCampusLife(auth: AuthContext): Promise<CampusLifeFeed> {
    this.authorize(auth);
    return {
      events: [],
      clubs: [],
      generatedAt: "2026-07-24T00:00:00.000Z",
    };
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
          slug: studentRequirementSlug("profile_verification"),
          journeyId,
          code: "profile_verification",
          title: "Verify your profile",
          description: "Confirm your personal and contact information.",
          status: "ready",
          blocking: true,
          dueAt: "2026-07-31T12:00:00.000Z",
          progressPercent: 0,
          submissionType: "form",
          documentCategory: null,
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
      unreadMessageCount: this.messages.filter(
        (message) => message.readAt === null,
      ).length,
      initialRoute: required ? "/onboarding" : "/dashboard",
      generatedAt: "2026-07-24T12:00:00.000Z",
    };
  }

  async getStudentOnboarding(auth: AuthContext): Promise<StudentOnboarding> {
    this.authorize(auth);
    return structuredClone(this.onboarding);
  }

  async getStudentHousingPlan(auth: AuthContext): Promise<StudentHousingPlan> {
    this.authorize(auth);
    const preference = this.onboarding.data.housingPreference;
    const residenceOption = this.onboarding.data.housingResidenceOption;
    return {
      preference:
        preference === "on_campus" ||
        preference === "off_campus" ||
        preference === "commuting" ||
        preference === "undecided" ||
        preference === "family"
          ? preference
          : null,
      residenceOption:
        residenceOption === "aster_residence_hall" ||
        residenceOption === "aster_apartments" ||
        residenceOption === "student_village"
          ? residenceOption
          : null,
      residences: [],
      version: this.onboarding.version,
      updatedAt: this.onboarding.updatedAt,
    };
  }

  async updateStudentHousingPlan(input: {
    auth: AuthContext;
    update: UpdateStudentHousingPlanInput;
    requestId: string;
  }): Promise<StudentHousingPlan> {
    this.authorize(input.auth);
    if (input.update.expectedVersion !== this.onboarding.version) {
      throw new ConflictError(
        "VERSION_CONFLICT",
        "Your housing plan changed in another session",
      );
    }
    const residenceOption =
      input.update.preference === "on_campus"
        ? input.update.residenceOption ?? null
        : null;
    this.onboarding = {
      ...this.onboarding,
      data: {
        ...this.onboarding.data,
        housingPreference: input.update.preference,
        housingResidenceOption: residenceOption,
      },
      version: this.onboarding.version + 1,
      updatedAt: "2026-07-24T12:00:00.000Z",
    };
    const requirement = this.requirements.find(
      (candidate) => candidate.code === "housing_preference",
    );
    if (requirement) {
      requirement.status = "completed";
      requirement.progressPercent = 100;
    }
    return this.getStudentHousingPlan(input.auth);
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
    const targetStep = input.update.currentStep;
    const targetIndex = ONBOARDING_STEPS.indexOf(targetStep);
    const activeIndex = ONBOARDING_STEPS.indexOf(
      this.onboarding.currentStep,
    );
    const editingCompletedStep =
      this.onboarding.completedSteps.includes(targetStep);
    if (
      targetIndex < 0 ||
      activeIndex < 0 ||
      (targetStep !== this.onboarding.currentStep &&
        !editingCompletedStep) ||
      targetIndex > activeIndex
    ) {
      throw new ConflictError(
        "ONBOARDING_STEP_OUT_OF_ORDER",
        `The next required onboarding step is ${this.onboarding.currentStep}`,
      );
    }
    const skip = input.update.skip === true;
    if (skip && !isSkippableOnboardingStep(input.update.currentStep)) {
      throw new BadRequestError(
        "ONBOARDING_STEP_REQUIRED",
        "This onboarding step is required before you can continue",
      );
    }
    const mergedData = {
      ...this.onboarding.data,
      ...input.update.data,
      skippedSteps: skip
        ? [
            ...new Set([
              ...(this.onboarding.data.skippedSteps ?? []),
              targetStep,
            ]),
          ]
        : (this.onboarding.data.skippedSteps ?? []).filter(
            (step) => step !== targetStep,
          ),
    };
    if (!skip) {
      validateOnboardingStepData(input.update.currentStep, mergedData);
    }
    if (targetStep === "offer" && !skip && !this.acceptedResponse) {
      throw new ConflictError(
        "ACCEPTED_OFFER_REQUIRED",
        "Accept the admission offer before completing this step",
      );
    }
    if (
      input.update.currentStep === "deposit" &&
      mergedData.depositChoice === "pay_now" &&
      this.payments.length === 0
    ) {
      throw new ConflictError(
        "DEPOSIT_REQUIRED",
        "Complete the enrollment deposit before saving this step",
      );
    }
    const advancingCurrentStep = targetStep === this.onboarding.currentStep;
    this.onboarding = {
      ...this.onboarding,
      status: "in_progress",
      currentStep: advancingCurrentStep
        ? (ONBOARDING_STEPS[activeIndex + 1] ?? "deposit")
        : this.onboarding.currentStep,
      completedSteps: advancingCurrentStep
        ? [
            ...this.onboarding.completedSteps,
            this.onboarding.currentStep,
          ]
        : this.onboarding.completedSteps,
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
      if (
        this.onboarding.completedSteps.length !== ONBOARDING_STEPS.length ||
        (this.onboarding.data.skippedSteps ?? []).some(
          (step) => !isSkippableOnboardingStep(step),
        )
      ) {
        throw new ConflictError(
          "ONBOARDING_INCOMPLETE",
          "Every required onboarding step must be completed in order",
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
    requirementIdentifier: string,
  ): Promise<StudentRequirementDetail> {
    this.authorize(auth);
    const requirement = this.requirements.find(
      (item) =>
        item.id === requirementIdentifier ||
        item.code === studentRequirementCodeFromSlug(requirementIdentifier),
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

  async saveStudentSignedDocument(input: {
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
  }): Promise<StudentDocument> {
    this.authorize(input.auth);
    const existing = this.documents.find(
      (document) =>
        document.signature?.templateCode === input.document.templateCode &&
        document.signature.onboardingVersion ===
          input.document.onboardingVersion,
    );
    if (existing) return structuredClone(existing);
    const document: StudentDocument = {
      id: input.document.id,
      fileName: input.document.fileName,
      mimeType: "application/pdf",
      sizeBytes: input.document.sizeBytes,
      category: "other",
      processingMode: "generated",
      status: "accepted",
      contentUrl: `/v1/student/documents/${input.document.id}/content`,
      sha256: input.document.sha256,
      signature: {
        templateCode: input.document.templateCode,
        title: input.document.title,
        signerName: input.document.signerName,
        method: input.document.signatureMethod,
        signedAt: input.document.signedAt,
        onboardingVersion: input.document.onboardingVersion,
      },
      createdAt: input.document.signedAt,
    };
    this.documents.unshift(document);
    this.documentStorageKeys.set(
      input.document.id,
      input.document.storageKey,
    );
    return structuredClone(document);
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

  async reserveStudentDocumentUpload(input: {
    auth: AuthContext;
    document: CreateStudentDocumentInput & { sha256: string };
    requirementId?: string;
    idempotencyKey: string;
    requestId: string;
  }): Promise<StudentDocument> {
    this.authorize(input.auth);
    const key = `document-upload:${input.idempotencyKey}`;
    const replay = this.portalIdempotency.get(key) as
      | StudentDocument
      | undefined;
    if (replay) return structuredClone(replay);
    const id = randomUUID();
    const extension =
      input.document.mimeType === "application/pdf"
        ? ".pdf"
        : input.document.mimeType === "image/jpeg"
          ? ".jpg"
          : ".png";
    const storageKey = `${input.auth.tenantId}/${input.auth.studentId}/${id}${extension}`;
    const requirement = input.requirementId
      ? this.requirements.find(
          (candidate) =>
            candidate.id === input.requirementId &&
            candidate.submissionType === "document",
        )
      : undefined;
    if (input.requirementId && !requirement) {
      throw new NotFoundError(
        "DOCUMENT_REQUIREMENT_NOT_FOUND",
        "The document requirement was not found",
      );
    }
    const document: StudentDocument = {
      id,
      ...(input.requirementId ? { requirementId: input.requirementId } : {}),
      fileName: input.document.fileName,
      mimeType: input.document.mimeType,
      sizeBytes: input.document.sizeBytes,
      category:
        (requirement
          ? documentCategoryForRequirement(requirement.code)
          : null) ?? input.document.category,
      status: "uploaded",
      sha256: input.document.sha256,
      contentUrl: `/v1/student/documents/${id}/content`,
      createdAt: "2026-07-24T12:00:00.000Z",
    };
    this.documents.unshift(document);
    this.documentStorageKeys.set(id, storageKey);
    this.portalIdempotency.set(key, structuredClone(document));
    return structuredClone(document);
  }

  async claimStudentDocumentProcessing(input: {
    auth: AuthContext;
    documentId: string;
    retry?: boolean;
    requestId?: string;
    retryIdempotencyKey?: string;
  }): Promise<boolean> {
    this.authorize(input.auth);
    const retryRecordKey = input.retry
      ? `document-extraction-retry:${input.documentId}:${input.retryIdempotencyKey ?? ""}`
      : undefined;
    if (input.retry && !input.retryIdempotencyKey) {
      throw new BadRequestError(
        "IDEMPOTENCY_KEY_REQUIRED",
        "The Idempotency-Key header is required",
      );
    }
    if (retryRecordKey && this.portalIdempotency.has(retryRecordKey)) {
      return false;
    }
    const document = this.documents.find(
      (candidate) => candidate.id === input.documentId,
    );
    const canClaim = input.retry
      ? canRetryStoredExtraction(document?.extraction)
      : !document?.extraction;
    if (!document || document.status !== "uploaded" || !canClaim) {
      return false;
    }
    document.status = "processing";
    document.extraction = {
      status: "processing",
      documentType: "other",
      summary:
        "The original file is safely stored. Edward is preparing a reviewable record.",
      studentName: null,
      institutionName: null,
      issueDate: null,
      academicTerm: null,
      fields: [],
      courses: [],
      warnings: [],
      model: null,
      provider: "local",
      processedAt: null,
      verifiedAt: null,
    };
    if (retryRecordKey) {
      this.portalIdempotency.set(retryRecordKey, {
        documentId: input.documentId,
        status: "processing",
      });
    }
    this.effects.auditEvents += 1;
    this.effects.outboxEvents += 1;
    return true;
  }

  async releaseStudentDocumentProcessing(input: {
    auth: AuthContext;
    documentId: string;
    requestId: string;
  }): Promise<void> {
    this.authorize(input.auth);
    const document = this.documents.find(
      (candidate) => candidate.id === input.documentId,
    );
    if (!document || document.status !== "processing") {
      throw new ConflictError(
        "DOCUMENT_PROCESSING_STATE_CHANGED",
        "The document processing state changed before the upload could be retried",
      );
    }
    document.status = "uploaded";
    delete document.extraction;
  }

  async completeStudentDocumentExtraction(input: {
    auth: AuthContext;
    documentId: string;
    extraction: StudentDocumentExtraction;
    requestId: string;
    retryIdempotencyKey?: string;
  }): Promise<StudentDocument> {
    this.authorize(input.auth);
    const document = this.documents.find(
      (candidate) => candidate.id === input.documentId,
    );
    if (!document || document.status !== "processing") {
      throw new ConflictError(
        "DOCUMENT_PROCESSING_STATE_CHANGED",
        "The document processing state changed before completion",
      );
    }
    document.extraction = structuredClone(input.extraction);
    document.status =
      input.extraction.status === "completed" ? "needs_review" : "uploaded";
    const response = structuredClone(document);
    if (input.retryIdempotencyKey) {
      this.portalIdempotency.set(
        `document-extraction-retry:${input.documentId}:${input.retryIdempotencyKey}`,
        response,
      );
    }
    return response;
  }

  async getStudentDocument(input: {
    auth: AuthContext;
    documentId: string;
  }): Promise<StudentDocument> {
    this.authorize(input.auth);
    const document = this.documents.find(
      (candidate) => candidate.id === input.documentId,
    );
    if (!document) {
      throw new NotFoundError(
        "STUDENT_DOCUMENT_NOT_FOUND",
        "The document was not found",
      );
    }
    return structuredClone(document);
  }

  async getStudentDocumentContentReference(input: {
    auth: AuthContext;
    documentId: string;
  }): Promise<{
    storageKey: string;
    fileName: string;
    mimeType: StudentDocument["mimeType"];
  }> {
    const document = await this.getStudentDocument(input);
    const storageKey = this.documentStorageKeys.get(input.documentId);
    if (!storageKey) {
      throw new NotFoundError(
        "STUDENT_DOCUMENT_CONTENT_NOT_FOUND",
        "The uploaded document content was not found",
      );
    }
    return {
      storageKey,
      fileName: document.fileName,
      mimeType: document.mimeType,
    };
  }

  async confirmStudentDocumentExtraction(input: {
    auth: AuthContext;
    documentId: string;
    confirmation: ConfirmStudentDocumentExtractionInput;
    idempotencyKey: string;
    requestId: string;
  }): Promise<StudentDocument> {
    const document = await this.getStudentDocument(input);
    if (!document.extraction || document.extraction.status !== "completed") {
      throw new ConflictError(
        "DOCUMENT_EXTRACTION_NOT_READY",
        "Document extraction is not ready for review",
      );
    }
    const available = new Set(
      document.extraction.fields.map((field) => field.key),
    );
    if (
      input.confirmation.acceptedFieldKeys.some((key) => !available.has(key))
    ) {
      throw new BadRequestError(
        "UNKNOWN_EXTRACTED_FIELD",
        "One or more extracted fields do not belong to this document",
      );
    }
    const stored = this.documents.find(
      (candidate) => candidate.id === input.documentId,
    )!;
    stored.extraction = {
      ...document.extraction,
      acceptedFieldKeys: [
        ...new Set(input.confirmation.acceptedFieldKeys),
      ],
      verifiedAt: "2026-07-24T12:00:00.000Z",
    };
    stored.status = "under_review";
    return structuredClone(stored);
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

function canRetryStoredExtraction(
  extraction: StudentDocumentExtraction | undefined,
): boolean {
  return (
    extraction?.status === "pending_configuration" ||
    (extraction?.status === "failed" && extraction.retryable !== false)
  );
}
