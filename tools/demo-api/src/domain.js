import { createHash, randomUUID } from "node:crypto";
import {
  badRequest,
  conflict,
  notFound,
} from "./errors.js";
import {
  createJourney,
  createRequirements,
  ids,
  ONBOARDING_STEPS,
} from "./seed.js";
import {
  booleanValue,
  enumValue,
  exactKeys,
  integerValue,
  isoTimestamp,
  objectBody,
  optionalString,
  requiredString,
  uuidValue,
} from "./validation.js";

const terminalRequirementStatuses = new Set([
  "completed",
  "waived",
  "not_applicable",
]);
const appointmentTypes = [
  "admissions_counseling",
  "enrollment_support",
  "financial_aid",
];
const helpArticles = [
  {
    id: ids.helpGettingStarted,
    category: "getting_started",
    question: "Where should I begin?",
    answer:
      "Start with the next action on your dashboard and complete onboarding when prompted.",
  },
  {
    id: ids.helpDocuments,
    category: "documents",
    question: "Which document formats are accepted?",
    answer:
      "The preview accepts PDF, JPEG, and PNG metadata up to 10 MB per document.",
  },
  {
    id: ids.helpPayments,
    category: "payments",
    question: "How does the demo deposit work?",
    answer:
      "The dummy processor records a successful deposit without charging a payment method.",
  },
];

const activityPropertyAllowlists = {
  "ui.portal_session_started.v1": new Set(["entry_point"]),
  "ui.dashboard_viewed.v1": new Set([
    "projection_version",
    "journey_status",
  ]),
  "ui.admission_offer_viewed.v1": new Set([
    "offer_id",
    "offer_status",
  ]),
  "ui.admission_decision_started.v1": new Set([
    "offer_id",
    "decision",
    "entry_point",
  ]),
  "ui.enrollment_started.v1": new Set(["journey_id", "entry_point"]),
  "ui.enrollment_step_viewed.v1": new Set([
    "step_code",
    "entry_point",
  ]),
  "ui.portal_section_viewed.v1": new Set(["section", "entry_point"]),
  "ui.enrollment_task_viewed.v1": new Set([
    "task_code",
    "task_status",
    "entry_point",
  ]),
  "ui.enrollment_task_abandoned.v1": new Set([
    "task_code",
    "task_status",
    "duration_bucket",
    "last_interaction",
  ]),
  "ui.financial_aid_viewed.v1": new Set(["surface", "aid_status"]),
  "ui.course_catalog_searched.v1": new Set([
    "query_length_bucket",
    "result_count",
  ]),
  "ui.course_viewed.v1": new Set(["course_code", "surface"]),
  "ui.exemption_reviewed.v1": new Set([
    "rule_code",
    "recommendation_status",
  ]),
  "ui.campus_event_viewed.v1": new Set(["event_id", "surface"]),
  "ui.club_viewed.v1": new Set(["club_id", "surface"]),
  "ui.edward_tool_invoked.v1": new Set(["tool_name", "page_context"]),
  "ui.edward_action_widget_viewed.v1": new Set([
    "widget_type",
    "page_context",
  ]),
  "ui.edward_action_completed.v1": new Set([
    "widget_type",
    "outcome",
  ]),
  "ui.help_opened.v1": new Set(["context", "surface", "topic_code"]),
};
const prohibitedActivityProperty =
  /(password|token|secret|email|phone|address|government|payment|card|ssn)/i;

export function buildDashboard(state, clock = () => new Date()) {
  const requirements = state.requirements.map(requirementSummary);
  const completionPercent =
    requirements.length === 0
      ? 0
      : Math.round(
          requirements.reduce(
            (total, requirement) => total + requirement.progressPercent,
            0,
          ) / requirements.length,
        );
  const nextRequirement =
    requirements.find((requirement) =>
      ["rejected", "ready", "in_progress"].includes(requirement.status),
    ) ??
    requirements.find(
      (requirement) => !terminalRequirementStatuses.has(requirement.status),
    );

  return {
    student: {
      id: state.profile.studentId,
      preferredName: state.profile.preferredName,
      fullName: `${state.profile.firstName} ${state.profile.lastName}`,
      classYear: state.profile.classYear,
    },
    offer: {
      id: state.offer.id,
      programName: state.offer.programName,
      termName: state.offer.termName,
      campusName: state.offer.campusName,
      responseDeadline: state.offer.responseDeadline,
      depositAmountCents: state.offer.depositAmountCents,
      status: state.offer.status,
    },
    journey: {
      id: state.journey?.id ?? null,
      status: state.journey?.status ?? "not_started",
      completionPercent,
      nextAction:
        state.journey && nextRequirement
          ? {
              code: nextRequirement.code,
              label: nextRequirement.title,
              href: `/enrollment?requirement=${encodeURIComponent(nextRequirement.code)}`,
            }
          : state.journey
            ? {
                code: "review_enrollment",
                label: "Review your enrollment",
                href: "/enrollment",
              }
            : {
                code: "accept_offer",
                label: "Review and accept your offer",
                href: "/offer",
              },
      requirements,
    },
    unreadMessageCount: state.messages.filter(
      (message) => message.readAt === null,
    ).length,
    projectionVersion: state.portalProjectionVersion,
    generatedAt: clock().toISOString(),
  };
}

export function buildOnboarding(state) {
  return {
    studentId: state.profile.studentId,
    status: state.onboarding.status,
    currentStep: state.onboarding.currentStep,
    completedSteps: [...state.onboarding.completedSteps],
    data: structuredClone(state.onboarding.data),
    version: state.onboarding.version,
    completedAt: state.onboarding.completedAt,
    updatedAt: state.onboarding.updatedAt,
  };
}

export function buildBootstrap(state, clock) {
  const onboardingRequired = state.onboarding.status !== "completed";
  return {
    authenticated: true,
    student: {
      id: state.profile.studentId,
      preferredName: state.profile.preferredName,
      fullName: `${state.profile.firstName} ${state.profile.lastName}`,
    },
    onboarding: {
      required: onboardingRequired,
      status: state.onboarding.status,
      currentStep: state.onboarding.currentStep,
      version: state.onboarding.version,
    },
    initialRoute: onboardingRequired ? "/onboarding" : "/dashboard",
    generatedAt: clock().toISOString(),
  };
}

export function buildStudentAcademics(state, clock = () => new Date()) {
  const selectedProgram =
    state.academicCatalog.programs.find(
      (program) => program.code === state.academics.selectedProgramCode,
    ) ?? state.academicCatalog.programs[0];
  const courseByCode = new Map(
    state.academicCatalog.courses.map((course) => [course.code, course]),
  );
  const recommendations = buildExemptionRecommendations(state);
  const approvedOrSuggested = new Map(
    recommendations
      .filter((recommendation) =>
        ["suggested", "needs_review", "approved"].includes(
          recommendation.status,
        ),
      )
      .map((recommendation) => [
        recommendation.targetCourseCode,
        recommendation,
      ]),
  );
  const completedCourseCodes = new Set(
    recommendations
      .filter((recommendation) => recommendation.status === "approved")
      .map((recommendation) => recommendation.targetCourseCode),
  );
  const plan = selectedProgram.requirements
    .map(([courseCode, category, recommendedTerm]) => {
      const course = courseByCode.get(courseCode);
      if (!course) return null;
      const exemption = approvedOrSuggested.get(courseCode);
      const satisfiedPrerequisiteCodes = course.prerequisites
        .map((item) => item.courseCode)
        .filter((code) => completedCourseCodes.has(code));
      const missingPrerequisiteCodes = course.prerequisites
        .map((item) => item.courseCode)
        .filter((code) => !completedCourseCodes.has(code));
      let status = missingPrerequisiteCodes.length > 0 ? "blocked" : "eligible";
      if (exemption?.status === "approved") status = "exempted";
      else if (exemption) status = "exemption_suggested";
      return {
        course: structuredClone(course),
        category,
        recommendedTerm,
        status,
        satisfiedPrerequisiteCodes,
        missingPrerequisiteCodes,
      };
    })
    .filter(Boolean);
  const exemptedCredits = plan
    .filter((item) => item.status === "exempted")
    .reduce((sum, item) => sum + item.course.credits, 0);

  return {
    selectedProgram: publicProgram(selectedProgram),
    availablePrograms: state.academicCatalog.programs.map(publicProgram),
    transcriptCredits: structuredClone(state.academics.transcriptCredits),
    exemptionRecommendations: recommendations,
    plan,
    progress: {
      completedCredits: 0,
      exemptedCredits,
      requiredCredits: selectedProgram.totalCredits,
      percent: Math.round((exemptedCredits / selectedProgram.totalCredits) * 100),
    },
    catalogVersion: state.academicCatalog.version,
    generatedAt: clock().toISOString(),
  };
}

export function listCatalogCourses(state, query = "") {
  const normalized = query.trim().toLowerCase().slice(0, 120);
  const items = state.academicCatalog.courses
    .filter((course) => {
      if (!normalized) return true;
      return `${course.code} ${course.title} ${course.description}`
        .toLowerCase()
        .includes(normalized);
    })
    .map((course) => structuredClone(course));
  return {
    items,
    total: items.length,
    catalogVersion: state.academicCatalog.version,
  };
}

export function buildStudentFinancials(state, clock = () => new Date()) {
  const acceptedAidCents = state.financials.awards.reduce(
    (sum, award) => sum + award.acceptedAmountCents,
    0,
  );
  const pendingAidCents = state.financials.awards.reduce(
    (sum, award) =>
      sum +
      (["offered", "pending"].includes(award.status)
        ? award.offeredAmountCents
        : 0),
    0,
  );
  const depositPaymentsCents = state.payments
    .filter((payment) => payment.status === "succeeded")
    .reduce((sum, payment) => sum + payment.amountCents, 0);
  const paymentsCents = state.financials.paymentsCents + depositPaymentsCents;
  const remainingBalanceCents = Math.max(
    0,
    state.financials.costOfAttendanceCents - acceptedAidCents - paymentsCents,
  );
  return {
    academicYear: state.financials.academicYear,
    costOfAttendanceCents: state.financials.costOfAttendanceCents,
    acceptedAidCents,
    pendingAidCents,
    paymentsCents,
    remainingBalanceCents,
    awards: structuredClone(state.financials.awards),
    requiredDocuments: structuredClone(state.financials.requiredDocuments),
    paymentPlans: state.financials.paymentPlans.map((plan) => ({
      ...structuredClone(plan),
      installmentAmountCents: Math.ceil(
        remainingBalanceCents / plan.installmentCount,
      ),
    })),
    sap: structuredClone(state.financials.sap),
    generatedAt: clock().toISOString(),
  };
}

export function selectFinancialPaymentPlan(draft, input) {
  const body = objectBody(input);
  exactKeys(body, ["planId"]);
  const planId = uuidValue(body.planId, "planId");
  const selected = draft.financials.paymentPlans.find(
    (plan) => plan.id === planId,
  );
  if (!selected) {
    throw notFound(
      "PAYMENT_PLAN_NOT_FOUND",
      "The selected payment plan was not found",
    );
  }
  for (const plan of draft.financials.paymentPlans) {
    plan.status = plan.id === planId ? "enrolled" : "available";
  }
  draft.portalProjectionVersion += 1;
  return { planId, status: "enrolled" };
}

export function buildCampusLife(state, clock = () => new Date()) {
  return {
    events: structuredClone(state.campusLife.events).sort((left, right) =>
      left.startsAt.localeCompare(right.startsAt),
    ),
    clubs: structuredClone(state.campusLife.clubs).sort((left, right) =>
      left.name.localeCompare(right.name),
    ),
    generatedAt: clock().toISOString(),
  };
}

export async function idempotentMutation({
  store,
  operation,
  key,
  body,
  mutate,
}) {
  const fingerprint = createHash("sha256")
    .update(canonicalJson({ operation, body }))
    .digest("hex");
  return store.transact(async (draft, transaction) => {
    const recordKey = `${operation}:${key}`;
    const previous = draft.idempotency[recordKey];
    if (previous) {
      if (previous.fingerprint !== fingerprint) {
        throw conflict(
          "IDEMPOTENCY_KEY_REUSED",
          "This idempotency key was already used for a different request",
        );
      }
      transaction.skipWrite();
      return {
        response: structuredClone(previous.response),
        replayed: true,
      };
    }
    const response = await mutate(draft);
    draft.idempotency[recordKey] = {
      fingerprint,
      response: structuredClone(response),
      recordedAt: draft.fixture.updatedAt,
    };
    return { response, replayed: false };
  });
}

export function acceptOffer(draft, offerId, now) {
  uuidValue(offerId, "offerId");
  if (offerId !== draft.offer.id) {
    throw notFound(
      "ADMISSION_OFFER_NOT_FOUND",
      "The admission offer was not found",
    );
  }
  if (draft.offer.status === "accepted") {
    return acceptOfferResponse(draft);
  }
  if (draft.offer.status !== "offered") {
    throw conflict(
      "ADMISSION_OFFER_NOT_ACTIVE",
      "Only an active admission offer can be accepted",
    );
  }

  const acceptedAt = now.toISOString();
  draft.offer.status = "accepted";
  draft.offer.acceptedAt = acceptedAt;
  draft.offer.version += 1;
  draft.journey = createJourney(acceptedAt);
  draft.requirements = createRequirements(acceptedAt);
  draft.portalProjectionVersion += 1;
  return acceptOfferResponse(draft);
}

function acceptOfferResponse(state) {
  return {
    offerId: state.offer.id,
    offerStatus: "accepted",
    journeyId: state.journey.id,
    journeyStatus: state.journey.status,
    projectionVersion: state.portalProjectionVersion,
    acceptedAt: state.offer.acceptedAt,
  };
}

export function updateOnboarding(draft, input, now) {
  const body = objectBody(input);
  exactKeys(body, ["expectedVersion", "currentStep", "data"]);
  const expectedVersion = integerValue(
    body.expectedVersion,
    "expectedVersion",
    1,
    Number.MAX_SAFE_INTEGER,
  );
  const currentStep = enumValue(
    body.currentStep,
    "currentStep",
    ONBOARDING_STEPS,
  );
  if (draft.onboarding.status === "completed") {
    throw conflict(
      "ONBOARDING_ALREADY_COMPLETED",
      "Completed onboarding cannot be changed",
    );
  }
  if (draft.onboarding.version !== expectedVersion) {
    throw conflict(
      "VERSION_CONFLICT",
      "Onboarding changed in another session",
    );
  }
  if (draft.onboarding.currentStep !== currentStep) {
    throw conflict(
      "ONBOARDING_STEP_OUT_OF_ORDER",
      `The next required onboarding step is ${draft.onboarding.currentStep}`,
    );
  }
  validateCompletedStepSequence(draft);
  const suppliedData = validateOnboardingData(body.data);
  const mergedData = {
    ...draft.onboarding.data,
    ...suppliedData,
  };
  validateOnboardingStep(draft, currentStep, mergedData);

  draft.onboarding.data = mergedData;
  draft.onboarding.completedSteps.push(currentStep);
  const currentIndex = ONBOARDING_STEPS.indexOf(currentStep);
  draft.onboarding.currentStep =
    ONBOARDING_STEPS[currentIndex + 1] ?? currentStep;
  draft.onboarding.status = "in_progress";
  draft.onboarding.version += 1;
  draft.onboarding.updatedAt = now.toISOString();
  return buildOnboarding(draft);
}

export function completeOnboarding(draft, input, now) {
  const body = objectBody(input);
  exactKeys(body, ["expectedVersion"]);
  const expectedVersion = integerValue(
    body.expectedVersion,
    "expectedVersion",
    1,
    Number.MAX_SAFE_INTEGER,
  );
  if (draft.onboarding.status === "completed") {
    return buildOnboarding(draft);
  }
  if (draft.onboarding.version !== expectedVersion) {
    throw conflict(
      "VERSION_CONFLICT",
      "Onboarding changed in another session",
    );
  }
  if (
    draft.onboarding.completedSteps.length !== ONBOARDING_STEPS.length ||
    draft.onboarding.completedSteps.some(
      (step, index) => step !== ONBOARDING_STEPS[index],
    )
  ) {
    throw conflict(
      "ONBOARDING_INCOMPLETE",
      "Every onboarding step must be completed in order",
    );
  }
  draft.onboarding.status = "completed";
  draft.onboarding.completedAt = now.toISOString();
  draft.onboarding.version += 1;
  draft.onboarding.updatedAt = now.toISOString();
  return buildOnboarding(draft);
}

export function patchProfile(draft, input, now) {
  const body = objectBody(input);
  exactKeys(body, [
    "expectedVersion",
    "preferredName",
    "pronouns",
    "mobilePhone",
    "communicationPreference",
  ]);
  const expectedVersion = integerValue(
    body.expectedVersion,
    "expectedVersion",
    1,
    Number.MAX_SAFE_INTEGER,
  );
  if (draft.profile.version !== expectedVersion) {
    throw conflict(
      "VERSION_CONFLICT",
      "The profile changed in another session",
    );
  }
  const fields = [
    "preferredName",
    "pronouns",
    "mobilePhone",
    "communicationPreference",
  ];
  if (!fields.some((field) => Object.hasOwn(body, field))) {
    throw badRequest(
      "PROFILE_UPDATE_EMPTY",
      "At least one profile field must be supplied",
    );
  }
  const preferredName = optionalString(
    body.preferredName,
    "preferredName",
    { min: 1, max: 120 },
  );
  const pronouns =
    body.pronouns === undefined
      ? undefined
      : body.pronouns === null
        ? null
        : typeof body.pronouns === "string" &&
            body.pronouns.length <= 80
          ? body.pronouns
          : (() => {
              throw badRequest(
                "INVALID_FIELD",
                "pronouns must be a string or null",
              );
            })();
  const mobilePhone =
    body.mobilePhone === undefined
      ? undefined
      : body.mobilePhone === null
        ? null
        : requiredString(body.mobilePhone, "mobilePhone", {
            min: 7,
            max: 32,
          });
  if (
    typeof mobilePhone === "string" &&
    !/^\+?[0-9 ()-]{7,32}$/.test(mobilePhone)
  ) {
    throw badRequest("INVALID_FIELD", "mobilePhone has an invalid format");
  }
  const communicationPreference =
    body.communicationPreference === undefined
      ? undefined
      : enumValue(
          body.communicationPreference,
          "communicationPreference",
          ["email", "sms"],
        );

  if (preferredName !== undefined) draft.profile.preferredName = preferredName;
  if (pronouns !== undefined) draft.profile.pronouns = pronouns;
  if (mobilePhone !== undefined) draft.profile.mobilePhone = mobilePhone;
  if (communicationPreference !== undefined) {
    draft.profile.communicationPreference = communicationPreference;
  }
  draft.profile.version += 1;
  draft.profile.updatedAt = now.toISOString();
  completeRequirementAndRefreshDependencies(draft, "profile_verification");
  draft.portalProjectionVersion += 1;
  return profileResponse(draft);
}

export function profileResponse(state) {
  return {
    studentId: state.profile.studentId,
    preferredName: state.profile.preferredName,
    pronouns: state.profile.pronouns,
    mobilePhone: state.profile.mobilePhone,
    communicationPreference: state.profile.communicationPreference,
    version: state.profile.version,
    updatedAt: state.profile.updatedAt,
  };
}

export function listRequirements(state) {
  const items = state.requirements.map(requirementDetailResponse);
  return {
    items,
    total: items.length,
  };
}

export function requirementDetail(state, identifier) {
  const decoded = decodeURIComponent(identifier);
  const requirement = state.requirements.find(
    (candidate) =>
      candidate.id === decoded ||
      candidate.code === requirementCodeFromSlug(decoded),
  );
  if (!requirement) {
    throw notFound(
      "STUDENT_REQUIREMENT_NOT_FOUND",
      "The enrollment requirement was not found",
    );
  }
  return requirementDetailResponse(requirement);
}

export function listMessages(state) {
  return {
    unreadCount: state.messages.filter((message) => message.readAt === null)
      .length,
    items: state.messages
      .map((message) => structuredClone(message))
      .sort((left, right) => right.sentAt.localeCompare(left.sentAt)),
  };
}

export function markMessageRead(draft, messageId, now, transaction) {
  uuidValue(messageId, "messageId");
  const message = draft.messages.find((candidate) => candidate.id === messageId);
  if (!message) {
    throw notFound("STUDENT_MESSAGE_NOT_FOUND", "The message was not found");
  }
  if (message.readAt !== null) {
    transaction?.skipWrite();
    return structuredClone(message);
  }
  message.readAt = now.toISOString();
  draft.portalProjectionVersion += 1;
  return structuredClone(message);
}

export function createDocumentMetadata(draft, input, now) {
  const body = objectBody(input);
  exactKeys(body, ["fileName", "mimeType", "sizeBytes", "category"]);
  const fileName = requiredString(body.fileName, "fileName", {
    min: 1,
    max: 255,
  });
  if (
    fileName.includes("/") ||
    fileName.includes("\\") ||
    /[\u0000-\u001f]/.test(fileName)
  ) {
    throw badRequest("INVALID_FILE_NAME", "fileName must be a plain file name");
  }
  const mimeType = enumValue(body.mimeType, "mimeType", [
    "application/pdf",
    "image/jpeg",
    "image/png",
  ]);
  const sizeBytes = integerValue(
    body.sizeBytes,
    "sizeBytes",
    1,
    10_485_760,
  );
  const requestedCategory = enumValue(body.category, "category", [
    "identity",
    "residency",
    "transcript",
    "financial_aid",
    "health",
    "consent",
    "other",
  ]);
  const document = {
    id: randomUUID(),
    fileName,
    mimeType,
    sizeBytes,
    category: requestedCategory,
    status: "placeholder",
    sha256: null,
    storageKey: null,
    extraction: null,
    createdAt: now.toISOString(),
  };
  draft.documents.push(document);
  return documentResponse(document);
}

export function createUploadedDocument(draft, input, now) {
  const body = objectBody(input);
  exactKeys(body, [
    "id",
    "fileName",
    "mimeType",
    "sizeBytes",
    "category",
    "requirementId",
    "sha256",
    "storageKey",
    "extraction",
  ]);
  const id = uuidValue(body.id, "id");
  const fileName = requiredString(body.fileName, "fileName", {
    min: 1,
    max: 255,
  });
  if (
    fileName.includes("/") ||
    fileName.includes("\\") ||
    /[\u0000-\u001f]/.test(fileName)
  ) {
    throw badRequest("INVALID_FILE_NAME", "fileName must be a plain file name");
  }
  const mimeType = enumValue(body.mimeType, "mimeType", [
    "application/pdf",
    "image/jpeg",
    "image/png",
  ]);
  const sizeBytes = integerValue(
    body.sizeBytes,
    "sizeBytes",
    1,
    10_485_760,
  );
  const requestedCategory = enumValue(body.category, "category", [
    "identity",
    "residency",
    "transcript",
    "financial_aid",
    "health",
    "consent",
    "other",
  ]);
  const requirementId =
    body.requirementId === undefined
      ? undefined
      : uuidValue(body.requirementId, "requirementId");
  const sha256 = requiredString(body.sha256, "sha256", { min: 64, max: 64 });
  if (!/^[0-9a-f]{64}$/.test(sha256)) {
    throw badRequest("INVALID_FIELD", "sha256 must be a lowercase SHA-256 hash");
  }
  const storageKey = requiredString(body.storageKey, "storageKey", {
    min: 39,
    max: 42,
  });
  if (!/^[0-9a-f-]{36}\.[a-z0-9]{2,5}$/i.test(storageKey)) {
    throw badRequest("INVALID_FIELD", "storageKey is invalid");
  }
  const extraction = validateExtraction(body.extraction);
  const category =
    requestedCategory === "other" && extraction.status === "completed"
      ? documentCategoryForExtraction(extraction.documentType)
      : requestedCategory;
  const document = {
    id,
    ...(requirementId ? { requirementId } : {}),
    fileName,
    mimeType,
    sizeBytes,
    category,
    status:
      extraction.status === "completed" ? "needs_review" : "uploaded",
    sha256,
    storageKey,
    extraction,
    createdAt: now.toISOString(),
  };
  draft.documents.push(document);
  updateDocumentRequirement(draft, document);
  draft.portalProjectionVersion += 1;
  return documentResponse(document);
}

function documentCategoryForExtraction(documentType) {
  return (
    {
      transcript: "transcript",
      identity: "identity",
      financial_aid: "financial_aid",
      ferpa: "consent",
      immunization: "health",
      residency: "residency",
    }[documentType] ?? "other"
  );
}

export function confirmDocumentExtraction(draft, documentId, input, now) {
  uuidValue(documentId, "documentId");
  const body = objectBody(input);
  exactKeys(body, ["acceptedFieldKeys"]);
  if (
    !Array.isArray(body.acceptedFieldKeys) ||
    body.acceptedFieldKeys.length > 24 ||
    body.acceptedFieldKeys.some((key) => typeof key !== "string")
  ) {
    throw badRequest(
      "INVALID_FIELD",
      "acceptedFieldKeys must be an array of up to 24 strings",
    );
  }
  const document = draft.documents.find(
    (candidate) => candidate.id === documentId,
  );
  if (!document) {
    throw notFound("STUDENT_DOCUMENT_NOT_FOUND", "The document was not found");
  }
  if (!document.extraction || document.extraction.status !== "completed") {
    throw conflict(
      "DOCUMENT_EXTRACTION_NOT_READY",
      "Document extraction is not ready for review",
    );
  }
  const availableKeys = new Set(
    document.extraction.fields.map((field) => field.key),
  );
  const accepted = [...new Set(body.acceptedFieldKeys)];
  if (accepted.some((key) => !availableKeys.has(key))) {
    throw badRequest(
      "UNKNOWN_EXTRACTED_FIELD",
      "One or more extracted fields do not belong to this document",
    );
  }
  document.extraction.acceptedFieldKeys = accepted;
  document.extraction.verifiedAt = now.toISOString();
  document.status = "under_review";
  if (
    document.extraction.documentType === "transcript" &&
    Array.isArray(document.extraction.courses)
  ) {
    ingestTranscriptCourses(draft, document, now);
  }
  draft.portalProjectionVersion += 1;
  return documentResponse(document);
}

export function listDocuments(state) {
  const items = state.documents
    .toSorted(
      (left, right) =>
        right.createdAt.localeCompare(left.createdAt) ||
        left.id.localeCompare(right.id),
    )
    .map(documentResponse);
  return { items, total: items.length };
}

export function findDocumentForDownload(state, documentId) {
  uuidValue(documentId, "documentId");
  const document = state.documents.find(
    (candidate) => candidate.id === documentId,
  );
  if (!document || !document.storageKey) {
    throw notFound(
      "STUDENT_DOCUMENT_CONTENT_NOT_FOUND",
      "The uploaded document content was not found",
    );
  }
  return structuredClone(document);
}

export function createAppointment(draft, input, now) {
  const body = objectBody(input);
  exactKeys(body, ["type", "startsAt", "notes"]);
  const type = enumValue(body.type, "type", appointmentTypes);
  const startsAt = isoTimestamp(body.startsAt, "startsAt");
  if (new Date(startsAt).getTime() <= now.getTime()) {
    throw badRequest(
      "APPOINTMENT_MUST_BE_FUTURE",
      "Appointment time must be in the future",
    );
  }
  const notes =
    body.notes === undefined
      ? null
      : optionalString(body.notes, "notes", { min: 0, max: 500 }) ?? null;
  const appointment = {
    id: randomUUID(),
    type,
    startsAt,
    notes,
    status: "scheduled",
    createdAt: now.toISOString(),
  };
  draft.appointments.push(appointment);
  return structuredClone(appointment);
}

export function createDepositPayment(draft, input, now) {
  const body = objectBody(input);
  exactKeys(body, ["offerId"]);
  const offerId = uuidValue(body.offerId, "offerId");
  if (offerId !== draft.offer.id || draft.offer.status !== "accepted") {
    throw conflict(
      "ACCEPTED_OFFER_REQUIRED",
      "An accepted admission offer is required before paying a deposit",
    );
  }
  const existing = draft.payments.find(
    (payment) =>
      payment.type === "enrollment_deposit" &&
      payment.offerId === offerId &&
      payment.status === "succeeded",
  );
  if (existing) return structuredClone(existing);

  const id = randomUUID();
  const payment = {
    id,
    offerId,
    type: "enrollment_deposit",
    amountCents: draft.offer.depositAmountCents,
    status: "succeeded",
    processor: "dummy",
    processorReference: `dummy_${id.replaceAll("-", "")}`,
    createdAt: now.toISOString(),
  };
  draft.payments.push(payment);
  const financialDocument = draft.financials.requiredDocuments.find(
    (document) => document.code === "deposit",
  );
  if (financialDocument) financialDocument.status = "verified";
  const requirement = draft.requirements.find(
    (candidate) => candidate.code === "enrollment_deposit",
  );
  if (requirement) {
    completeRequirementAndRefreshDependencies(
      draft,
      "enrollment_deposit",
    );
  }
  draft.portalProjectionVersion += 1;
  return structuredClone(payment);
}

export function getHelpTopics() {
  return {
    articles: structuredClone(helpArticles),
    support: {
      email: "enrollment-support@vv.example",
      phone: "+1 555 010 2027",
      hours: "Monday-Friday, 09:00-17:00",
    },
  };
}

export function createHelpRequest(draft, input, now) {
  const body = objectBody(input);
  exactKeys(body, ["topicCode", "message"]);
  const topicCode = enumValue(body.topicCode, "topicCode", [
    "getting_started",
    "documents",
    "payments",
    "support",
  ]);
  const message = requiredString(body.message, "message", {
    min: 1,
    max: 500,
  });
  const request = {
    id: randomUUID(),
    topicCode,
    message,
    status: "received",
    createdAt: now.toISOString(),
  };
  draft.helpRequests.push(request);
  return structuredClone(request);
}

export function ingestActivities(draft, input, now) {
  const body = objectBody(input);
  exactKeys(body, ["events"]);
  if (!Array.isArray(body.events) || body.events.length < 1 || body.events.length > 100) {
    throw badRequest("INVALID_ACTIVITY_BATCH", "events must contain 1-100 items");
  }
  let accepted = 0;
  let duplicates = 0;
  const knownIds = new Set(draft.activities.map((event) => event.eventId));
  for (const candidate of body.events) {
    const event = validateActivity(candidate);
    if (knownIds.has(event.eventId)) {
      duplicates += 1;
      continue;
    }
    knownIds.add(event.eventId);
    draft.activities.push({
      ...event,
      receivedAt: now.toISOString(),
    });
    accepted += 1;
  }
  if (draft.activities.length > 1_000) {
    draft.activities = draft.activities.slice(-1_000);
  }
  return { accepted, duplicates };
}

function validateExtraction(value) {
  const extraction = objectBody(value);
  const status = enumValue(extraction.status, "extraction.status", [
    "pending_configuration",
    "processing",
    "completed",
    "failed",
  ]);
  const documentType = enumValue(
    extraction.documentType,
    "extraction.documentType",
    [
      "transcript",
      "identity",
      "financial_aid",
      "ferpa",
      "immunization",
      "residency",
      "other",
    ],
  );
  const fields = Array.isArray(extraction.fields)
    ? extraction.fields.slice(0, 24).map((candidate, index) => {
        const field = objectBody(candidate);
        return {
          key: requiredString(field.key, `fields[${index}].key`, {
            min: 1,
            max: 80,
          }),
          label: requiredString(field.label, `fields[${index}].label`, {
            min: 1,
            max: 120,
          }),
          value: requiredString(field.value, `fields[${index}].value`, {
            min: 0,
            max: 500,
          }),
          confidence:
            typeof field.confidence === "number" &&
            field.confidence >= 0 &&
            field.confidence <= 1
              ? field.confidence
              : 0,
        };
      })
    : [];
  const courses = Array.isArray(extraction.courses)
    ? extraction.courses.slice(0, 80).map((candidate, index) => {
        const course = objectBody(candidate);
        return {
          sourceCode: nullableBoundedText(course.sourceCode, 80),
          title: requiredString(
            course.title,
            `courses[${index}].title`,
            { min: 1, max: 180 },
          ),
          credits:
            typeof course.credits === "number" &&
            course.credits >= 0 &&
            course.credits <= 20
              ? course.credits
              : null,
          grade: nullableBoundedText(course.grade, 32),
          score: nullableBoundedText(course.score, 32),
          term: nullableBoundedText(course.term, 80),
          confidence:
            typeof course.confidence === "number" &&
            course.confidence >= 0 &&
            course.confidence <= 1
              ? course.confidence
              : 0,
        };
      })
    : [];
  return {
    status,
    documentType,
    summary: requiredString(extraction.summary, "extraction.summary", {
      min: 1,
      max: 800,
    }),
    studentName: nullableBoundedText(extraction.studentName, 160),
    institutionName: nullableBoundedText(extraction.institutionName, 200),
    issueDate: nullableBoundedText(extraction.issueDate, 80),
    academicTerm: nullableBoundedText(extraction.academicTerm, 120),
    fields,
    courses,
    warnings: Array.isArray(extraction.warnings)
      ? extraction.warnings
          .slice(0, 12)
          .map((warning, index) =>
            requiredString(warning, `warnings[${index}]`, {
              min: 1,
              max: 400,
            }),
          )
      : [],
    model: nullableBoundedText(extraction.model, 160),
    provider: enumValue(extraction.provider, "extraction.provider", [
      "openrouter",
      "local",
    ]),
    processedAt: nullableBoundedText(extraction.processedAt, 80),
    verifiedAt: null,
  };
}

function publicProgram(program) {
  return {
    id: program.id,
    code: program.code,
    name: program.name,
    degree: program.degree,
    totalCredits: program.totalCredits,
    description: program.description,
  };
}

function buildExemptionRecommendations(state) {
  const existingByRuleAndCredit = new Map(
    state.academics.exemptionRecommendations.map((recommendation) => [
      `${recommendation.ruleCode}:${recommendation.transcriptCreditId}`,
      recommendation,
    ]),
  );
  const courseByCode = new Map(
    state.academicCatalog.courses.map((course) => [course.code, course]),
  );
  const recommendations = [];
  for (const credit of state.academics.transcriptCredits) {
    for (const rule of state.academicCatalog.equivalencyRules) {
      const sourceMatches =
        credit.sourceCode?.trim().toLowerCase() ===
        rule.sourceCode.trim().toLowerCase();
      const score = Number.parseFloat(credit.gradeOrScore ?? "");
      if (
        !sourceMatches ||
        !Number.isFinite(score) ||
        score < rule.minimumScore
      ) {
        continue;
      }
      const target = courseByCode.get(rule.targetCourseCode);
      if (!target) continue;
      const existing = existingByRuleAndCredit.get(
        `${rule.code}:${credit.id}`,
      );
      recommendations.push({
        id: existing?.id ?? `recommendation:${rule.code}:${credit.id}`,
        transcriptCreditId: credit.id,
        targetCourseCode: target.code,
        targetCourseTitle: target.title,
        ruleCode: rule.code,
        rationale: `${credit.sourceCode} score ${credit.gradeOrScore} meets the stored minimum score of ${rule.minimumScore}.`,
        confidence: rule.confidence,
        status: existing?.status ?? "suggested",
        requiresStaffReview: true,
      });
    }
  }
  return recommendations;
}

function ingestTranscriptCourses(draft, document, now) {
  const existingKeys = new Set(
    draft.academics.transcriptCredits.map(
      (credit) =>
        `${credit.sourceCode ?? ""}:${credit.title}:${credit.sourceDocumentId ?? ""}`.toLowerCase(),
    ),
  );
  for (const course of document.extraction.courses) {
    const key =
      `${course.sourceCode ?? ""}:${course.title}:${document.id}`.toLowerCase();
    if (existingKeys.has(key)) continue;
    const sourceLabel = `${course.sourceCode ?? ""} ${course.title}`.toLowerCase();
    draft.academics.transcriptCredits.push({
      id: randomUUID(),
      sourceType: sourceLabel.includes("ap ")
        ? "ap"
        : sourceLabel.includes("ib ")
          ? "ib"
          : "transcript",
      sourceCode: course.sourceCode,
      title: course.title,
      gradeOrScore: course.score ?? course.grade,
      credits: course.credits,
      institutionName: document.extraction.institutionName,
      sourceDocumentId: document.id,
      importedAt: now.toISOString(),
    });
    existingKeys.add(key);
  }
  draft.academics.exemptionRecommendations =
    buildExemptionRecommendations(draft);
}

function nullableBoundedText(value, maximum) {
  if (value === null || value === undefined) return null;
  return requiredString(value, "extraction field", {
    min: 1,
    max: maximum,
  });
}

function documentResponse(document) {
  const {
    storageKey: _storageKey,
    sha256,
    extraction,
    ...publicDocument
  } = structuredClone(document);
  return {
    ...publicDocument,
    ...(sha256 ? { sha256 } : {}),
    ...(document.storageKey
      ? { contentUrl: `/v1/student/documents/${document.id}/content` }
      : {}),
    ...(extraction ? { extraction } : {}),
  };
}

function updateDocumentRequirement(draft, document) {
  if (document.extraction?.status !== "completed") return;
  if (
    document.requirementId &&
    documentCategoryForExtraction(document.extraction?.documentType) !==
      document.category
  ) {
    return;
  }
  const requirementCode = {
    identity: "identity_document",
    transcript: "official_transcript",
    financial_aid: "financial_aid_verification",
    health: "immunization_record",
  }[document.category];
  if (!requirementCode) return;
  const requirement = draft.requirements.find(
    (candidate) => candidate.code === requirementCode,
  );
  if (!requirement) return;
  requirement.status = "under_review";
  requirement.progressPercent = 80;
  if (document.category === "financial_aid") {
    const financialDocument = draft.financials.requiredDocuments.find(
      (item) => item.code === "verification_worksheet",
    );
    if (financialDocument) financialDocument.status = "under_review";
  }
}

function validateActivity(input) {
  const event = objectBody(input);
  exactKeys(event, [
    "eventId",
    "eventName",
    "occurredAt",
    "sessionId",
    "pageInstanceId",
    "correlationId",
    "properties",
  ]);
  const eventId = uuidValue(event.eventId, "eventId");
  const eventName = enumValue(
    event.eventName,
    "eventName",
    Object.keys(activityPropertyAllowlists),
  );
  const occurredAt = isoTimestamp(event.occurredAt, "occurredAt");
  const sessionId = safeTrackingId(event.sessionId, "sessionId");
  const pageInstanceId = safeTrackingId(
    event.pageInstanceId,
    "pageInstanceId",
  );
  const correlationId =
    event.correlationId === undefined
      ? undefined
      : safeTrackingId(event.correlationId, "correlationId");
  const properties = objectBody(event.properties);
  const allowlist = activityPropertyAllowlists[eventName];
  for (const [key, value] of Object.entries(properties)) {
    if (prohibitedActivityProperty.test(key) || !allowlist.has(key)) {
      throw badRequest(
        "INVALID_ACTIVITY_PROPERTY",
        `Property "${key}" is not allowed for ${eventName}`,
      );
    }
    if (
      value !== null &&
      !["string", "number", "boolean"].includes(typeof value)
    ) {
      throw badRequest(
        "INVALID_ACTIVITY_PROPERTY",
        `Property "${key}" must be a primitive value`,
      );
    }
  }
  return {
    eventId,
    eventName,
    occurredAt,
    sessionId,
    pageInstanceId,
    ...(correlationId ? { correlationId } : {}),
    properties: structuredClone(properties),
  };
}

function safeTrackingId(value, name) {
  const text = requiredString(value, name, { min: 8, max: 128 });
  if (!/^[A-Za-z0-9][A-Za-z0-9._:-]*$/.test(text)) {
    throw badRequest("INVALID_FIELD", `${name} contains unsafe characters`);
  }
  return text;
}

function requirementSummary(requirement) {
  return {
    id: requirement.id,
    code: requirement.code,
    title: requirement.title,
    description: requirement.description,
    status: requirement.status,
    blocking: requirement.blocking,
    dueAt: requirement.dueAt,
    progressPercent: requirement.progressPercent,
  };
}

function requirementDetailResponse(requirement) {
  return {
    ...requirementSummary(requirement),
    slug: requirementSlug(requirement.code),
    journeyId: requirement.journeyId ?? ids.journey,
    submissionType: requirement.submissionType,
    documentCategory: documentCategoryForRequirement(requirement.code),
    responsibleOffice: requirement.responsibleOffice,
    dependencyCodes: [...requirement.dependsOnCodes],
  };
}

function requirementSlug(code) {
  return (
    {
      profile_verification: "profile-verification",
      identity_document: "identity-document-upload",
      official_transcript: "transcript-upload",
      financial_aid_verification: "financial-aid-verification",
      immunization_record: "immunization-upload",
      enrollment_deposit: "enrollment-deposit",
    }[code] ?? code.toLowerCase().replaceAll("_", "-")
  );
}

function requirementCodeFromSlug(slug) {
  const codes = [
    "profile_verification",
    "identity_document",
    "official_transcript",
    "financial_aid_verification",
    "immunization_record",
    "enrollment_deposit",
  ];
  return (
    codes.find((code) => requirementSlug(code) === slug) ??
    slug.toLowerCase().replaceAll("-", "_")
  );
}

function documentCategoryForRequirement(code) {
  return (
    {
      identity_document: "identity",
      official_transcript: "transcript",
      financial_aid_verification: "financial_aid",
      immunization_record: "health",
    }[code] ?? null
  );
}

function updateJourneyStatus(state) {
  if (!state.journey) return;
  const allComplete =
    state.requirements.length > 0 &&
    state.requirements.every((requirement) =>
      terminalRequirementStatuses.has(requirement.status),
    );
  state.journey.status = allComplete ? "ready_for_review" : "in_progress";
  state.journey.version += 1;
}

function completeRequirementAndRefreshDependencies(state, code) {
  const requirement = state.requirements.find(
    (candidate) => candidate.code === code,
  );
  if (!requirement) return;
  requirement.status = "completed";
  requirement.progressPercent = 100;
  const terminalCodes = new Set(
    state.requirements
      .filter((candidate) =>
        terminalRequirementStatuses.has(candidate.status),
      )
      .map((candidate) => candidate.code),
  );
  for (const candidate of state.requirements) {
    if (
      candidate.status === "blocked" &&
      candidate.dependsOnCodes.every((dependency) =>
        terminalCodes.has(dependency),
      )
    ) {
      candidate.status = "ready";
    }
  }
  updateJourneyStatus(state);
}

function validateCompletedStepSequence(state) {
  const currentIndex = ONBOARDING_STEPS.indexOf(
    state.onboarding.currentStep,
  );
  const expected = ONBOARDING_STEPS.slice(0, currentIndex);
  if (
    state.onboarding.completedSteps.length !== expected.length ||
    state.onboarding.completedSteps.some(
      (step, index) => step !== expected[index],
    )
  ) {
    throw new Error("Stored onboarding sequence is inconsistent");
  }
}

function validateOnboardingData(input) {
  const data = objectBody(input);
  const fields = [
    "legalNameConfirmed",
    "contactInformationConfirmed",
    "communicationPreference",
    "residencyStatus",
    "supportNeeds",
    "homeAddressConfirmed",
    "housingPreference",
    "campusInterests",
    "emergencyContactConfirmed",
    "recordsConfirmed",
    "familyPermissionsReviewed",
    "signatureConfirmed",
    "depositAcknowledged",
  ];
  exactKeys(data, fields);
  const result = {};
  for (const field of [
    "legalNameConfirmed",
    "contactInformationConfirmed",
    "homeAddressConfirmed",
    "emergencyContactConfirmed",
    "recordsConfirmed",
    "familyPermissionsReviewed",
    "signatureConfirmed",
    "depositAcknowledged",
  ]) {
    if (data[field] !== undefined) {
      result[field] = booleanValue(data[field], field);
    }
  }
  if (data.communicationPreference !== undefined) {
    result.communicationPreference = enumValue(
      data.communicationPreference,
      "communicationPreference",
      ["email", "sms"],
    );
  }
  if (data.residencyStatus !== undefined) {
    result.residencyStatus = enumValue(
      data.residencyStatus,
      "residencyStatus",
      ["domestic", "international"],
    );
  }
  if (data.housingPreference !== undefined) {
    result.housingPreference = enumValue(
      data.housingPreference,
      "housingPreference",
      ["on_campus", "off_campus", "undecided"],
    );
  }
  for (const field of ["supportNeeds", "campusInterests"]) {
    if (data[field] === undefined) continue;
    if (!Array.isArray(data[field])) {
      throw badRequest("INVALID_FIELD", `${field} must be an array`);
    }
    result[field] = data[field].map((value, index) =>
      requiredString(value, `${field}[${index}]`, { min: 1, max: 80 }),
    );
  }
  return result;
}

function validateOnboardingStep(state, step, data) {
  const invalid = (message) => {
    throw badRequest("ONBOARDING_STEP_INVALID", message);
  };
  if (step === "offer") {
    if (state.offer.status !== "accepted") {
      throw conflict(
        "ACCEPTED_OFFER_REQUIRED",
        "Accept the admission offer before completing this step",
      );
    }
    return;
  }
  if (step === "about_you") {
    if (
      data.legalNameConfirmed !== true ||
      data.contactInformationConfirmed !== true ||
      data.homeAddressConfirmed !== true ||
      !data.communicationPreference ||
      !data.residencyStatus
    ) {
      invalid(
        "Confirm legal name, contact information, home address, communication preference, and residency status",
      );
    }
    return;
  }
  if (step === "housing") {
    if (!data.housingPreference) invalid("Choose a housing preference");
    return;
  }
  if (step === "campus_life") {
    if (!data.campusInterests?.length) {
      invalid("Choose at least one campus interest");
    }
    return;
  }
  if (step === "emergency_contacts") {
    if (data.emergencyContactConfirmed !== true) {
      invalid("Confirm the emergency contact information");
    }
    return;
  }
  if (step === "other_records") {
    if (data.recordsConfirmed !== true) {
      invalid("Confirm the identity, health, and accessibility records");
    }
    return;
  }
  if (step === "family_permissions") {
    if (data.familyPermissionsReviewed !== true) {
      invalid("Review the family and FERPA permissions");
    }
    return;
  }
  if (step === "review_and_sign") {
    if (data.signatureConfirmed !== true) {
      invalid("Confirm the enrollment review and signature");
    }
    return;
  }
  if (step === "deposit") {
    if (data.depositAcknowledged !== true) {
      invalid("Acknowledge the completed enrollment deposit");
    }
    if (
      !state.payments.some(
        (payment) =>
          payment.type === "enrollment_deposit" &&
          payment.status === "succeeded",
      )
    ) {
      throw conflict(
        "DEPOSIT_REQUIRED",
        "Complete the enrollment deposit before saving this step",
      );
    }
    return;
  }
}

function canonicalJson(value) {
  if (Array.isArray(value)) {
    return `[${value.map(canonicalJson).join(",")}]`;
  }
  if (value !== null && typeof value === "object") {
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

export function fixtureSummary(state) {
  return {
    fixtureVersion: state.fixture.version,
    schemaVersion: state.schemaVersion,
    revision: state.fixture.revision,
    seededAt: state.fixture.seededAt,
    updatedAt: state.fixture.updatedAt,
    offerStatus: state.offer.status,
    journeyStatus: state.journey?.status ?? "not_started",
    projectionVersion: state.portalProjectionVersion,
    counts: {
      requirements: state.requirements.length,
      unreadMessages: state.messages.filter((message) => message.readAt === null)
        .length,
      documents: state.documents.length,
      appointments: state.appointments.length,
      payments: state.payments.length,
      helpRequests: state.helpRequests.length,
      activities: state.activities.length,
      idempotencyRecords: Object.keys(state.idempotency).length,
    },
    demoStudentId: ids.student,
  };
}
