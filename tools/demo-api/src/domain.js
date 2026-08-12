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
  enrollmentRequirementsFromConfiguration,
  managedConfigurationSummary,
} from "./managed-config.js";
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
  "ui.edward_context_receipts_received.v1": new Set([
    "source_count",
    "page_context",
  ]),
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
  const requirements = state.requirements.map((requirement) =>
    requirementSummary(requirement, state),
  );
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
    tenant: structuredClone(state.tenant),
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
    ...(state.rewards?.program?.enabled
      ? { rewards: rewardSummary(state) }
      : {}),
    unreadMessageCount: state.messages.filter(
      (message) => message.readAt === null,
    ).length,
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
    (sum, award) =>
      sum +
      (award.type === "work_study" ? 0 : award.acceptedAmountCents),
    0,
  );
  const pendingAidCents = state.financials.awards.reduce(
    (sum, award) =>
      sum +
      (award.type !== "work_study" &&
      ["offered", "pending"].includes(award.status)
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
  const configuredRequirements =
    enrollmentRequirementsFromConfiguration(draft, acceptedAt);
  draft.requirements =
    configuredRequirements.length > 0
      ? configuredRequirements
      : createRequirements(acceptedAt);
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
  exactKeys(body, ["expectedVersion", "currentStep", "data", "skip"]);
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
  const targetIndex = ONBOARDING_STEPS.indexOf(currentStep);
  const activeIndex = ONBOARDING_STEPS.indexOf(
    draft.onboarding.currentStep,
  );
  const editingCompletedStep =
    draft.onboarding.completedSteps.includes(currentStep);
  if (
    targetIndex < 0 ||
    activeIndex < 0 ||
    (draft.onboarding.currentStep !== currentStep &&
      !editingCompletedStep) ||
    targetIndex > activeIndex
  ) {
    throw conflict(
      "ONBOARDING_STEP_OUT_OF_ORDER",
      `The next required onboarding step is ${draft.onboarding.currentStep}`,
    );
  }
  validateCompletedStepSequence(draft);
  const skip = body.skip === true;
  if (body.skip !== undefined && typeof body.skip !== "boolean") {
    throw badRequest("INVALID_FIELD", "skip must be a boolean");
  }
  if (skip && !isSkippableOnboardingStep(currentStep)) {
    throw badRequest(
      "ONBOARDING_STEP_REQUIRED",
      "This onboarding step is required before you can continue",
    );
  }
  const suppliedData = validateOnboardingData(body.data);
  const mergedData = {
    ...draft.onboarding.data,
    ...suppliedData,
    skippedSteps: skip
      ? [
          ...new Set([
            ...(draft.onboarding.data.skippedSteps ?? []),
            currentStep,
          ]),
        ]
      : (draft.onboarding.data.skippedSteps ?? []).filter(
          (step) => step !== currentStep,
        ),
  };
  if (!skip) {
    validateOnboardingStep(draft, currentStep, mergedData);
  }

  draft.onboarding.data = mergedData;
  if (currentStep === "about_you" && !skip) {
    draft.profile.firstName = mergedData.firstName;
    draft.profile.lastName = mergedData.lastName;
    draft.profile.preferredName = mergedData.preferredName;
    draft.profile.mobilePhone = mergedData.mobilePhone;
    draft.profile.communicationPreference =
      mergedData.communicationPreference;
    draft.profile.version += 1;
    draft.profile.updatedAt = now.toISOString();
    draft.auth.demoIdentity.displayName =
      `${mergedData.firstName} ${mergedData.lastName}`.trim();
    // About-you is the authoritative first collection of these same profile
    // fields. Completing it must satisfy the enrollment profile requirement
    // and release downstream document tasks instead of asking the student to
    // verify identical data a second time.
    completeRequirementAndRefreshDependencies(
      draft,
      "profile_verification",
      now,
    );
    draft.portalProjectionVersion += 1;
  }
  const advancingCurrentStep =
    draft.onboarding.currentStep === currentStep;
  if (advancingCurrentStep) {
    draft.onboarding.completedSteps.push(currentStep);
    draft.onboarding.currentStep =
      ONBOARDING_STEPS[targetIndex + 1] ?? currentStep;
  }
  draft.onboarding.status = "in_progress";
  draft.onboarding.version += 1;
  draft.onboarding.updatedAt = now.toISOString();
  return buildOnboarding(draft);
}

export function housingPlanResponse(state) {
  const isHarvard = state.tenant?.slug === "harvard";
  const preference = [
    "on_campus",
    "off_campus",
    "commuting",
    "undecided",
    "family",
  ].includes(state.onboarding.data.housingPreference)
    ? state.onboarding.data.housingPreference
    : null;
  const residenceOption = [
    "aster_residence_hall",
    "aster_apartments",
    "student_village",
  ].includes(state.onboarding.data.housingResidenceOption)
    ? state.onboarding.data.housingResidenceOption
    : null;
  return {
    preference,
    residenceOption,
    residences: [
      {
        id: "71000000-0000-7000-8000-000000000101",
        value: "aster_residence_hall",
        name: isHarvard ? "Harvard Yard Residence" : "Aster Residence Hall",
        description:
          "Classic first-year community with shared lounges and peer mentors.",
        amenities: ["Shared lounges", "Community kitchen", "Laundry"],
        imageUrl: "/media/housing/aster-residence-hall-room.jpg",
        imageAlt:
          "Bright shared room with two beds, wardrobes, and a window desk",
        attribution: "Photo by deno wang via Pexels",
        sourceUrl:
          "https://www.pexels.com/photo/two-beds-in-a-bedroom-11671086/",
      },
      {
        id: "71000000-0000-7000-8000-000000000102",
        value: "aster_apartments",
        name: isHarvard ? "Harvard Houses" : "Aster Apartments",
        description:
          "Apartment-style rooms with smaller communities and shared kitchens.",
        amenities: ["Shared kitchen", "Study room", "In-unit living space"],
        imageUrl: "/media/housing/aster-apartments-room.jpg",
        imageAlt: "Modern shared bedroom with twin beds and a large window",
        attribution: "Photo by Alan Antony via Pexels",
        sourceUrl:
          "https://www.pexels.com/photo/modern-bedroom-interior-18470955/",
      },
      {
        id: "71000000-0000-7000-8000-000000000103",
        value: "student_village",
        name: isHarvard ? "Cambridge Student Village" : "Student Village",
        description:
          "A social residential neighborhood close to recreation and dining.",
        amenities: ["Dining nearby", "Recreation access", "Community events"],
        imageUrl: "/media/housing/student-village-room.jpg",
        imageAlt: "Warm shared room with two beds, lamps, and neutral bedding",
        attribution: "Photo by Luis Zambrano via Pexels",
        sourceUrl:
          "https://www.pexels.com/photo/two-beds-in-bedroom-16436954/",
      },
    ],
    version: state.onboarding.version,
    updatedAt: state.onboarding.updatedAt,
  };
}

export function updateHousingPlan(draft, input, now) {
  const body = objectBody(input);
  exactKeys(body, ["expectedVersion", "preference", "residenceOption"]);
  const expectedVersion = integerValue(
    body.expectedVersion,
    "expectedVersion",
    1,
    Number.MAX_SAFE_INTEGER,
  );
  if (draft.onboarding.version !== expectedVersion) {
    throw conflict(
      "VERSION_CONFLICT",
      "Your housing plan changed in another session",
    );
  }
  const preference = enumValue(body.preference, "preference", [
    "on_campus",
    "off_campus",
    "commuting",
    "undecided",
    "family",
  ]);
  const residenceOption =
    preference === "on_campus"
      ? body.residenceOption === undefined
        ? null
        : enumValue(body.residenceOption, "residenceOption", [
            "aster_residence_hall",
            "aster_apartments",
            "student_village",
          ])
      : null;

  draft.onboarding.data = {
    ...draft.onboarding.data,
    housingPreference: preference,
    housingResidenceOption: residenceOption,
  };
  draft.onboarding.version += 1;
  draft.onboarding.updatedAt = now.toISOString();
  completeRequirementAndRefreshDependencies(draft, "housing_preference", now);
  draft.portalProjectionVersion += 1;
  return housingPlanResponse(draft);
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
    ) ||
    (draft.onboarding.data.skippedSteps ?? []).some(
      (step) => !isSkippableOnboardingStep(step),
    )
  ) {
    throw conflict(
      "ONBOARDING_INCOMPLETE",
      "Every required onboarding step must be completed in order",
    );
  }
  draft.onboarding.status = "completed";
  draft.onboarding.completedAt = now.toISOString();
  draft.onboarding.version += 1;
  draft.onboarding.updatedAt = now.toISOString();
  awardMatchingRewards(
    draft,
    "onboarding_completed",
    "onboarding",
    draft.profile.studentId,
    {},
    now,
  );
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
  if (mobilePhone !== undefined) {
    if (draft.profile.mobilePhone !== mobilePhone) {
      draft.profile.phoneVerified = false;
    }
    draft.profile.mobilePhone = mobilePhone;
  }
  if (communicationPreference !== undefined) {
    draft.profile.communicationPreference = communicationPreference;
  }
  draft.profile.version += 1;
  draft.profile.updatedAt = now.toISOString();
  completeRequirementAndRefreshDependencies(
    draft,
    "profile_verification",
    now,
  );
  draft.portalProjectionVersion += 1;
  return profileResponse(draft);
}

export function profileResponse(state) {
  return {
    studentId: state.profile.studentId,
    preferredName: state.profile.preferredName,
    firstName: state.profile.firstName,
    lastName: state.profile.lastName,
    ...(state.profile.email
      ? {
          email: state.profile.email,
          emailVerified: state.profile.emailVerified === true,
          phoneVerified: state.profile.phoneVerified === true,
        }
      : {}),
    pronouns: state.profile.pronouns,
    mobilePhone: state.profile.mobilePhone,
    communicationPreference: state.profile.communicationPreference,
    version: state.profile.version,
    updatedAt: state.profile.updatedAt,
  };
}

export function listRequirements(state) {
  const items = state.requirements.map((requirement) =>
    requirementDetailResponse(requirement, state),
  );
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
  return requirementDetailResponse(requirement, state);
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

const staffPriorityOrder = {
  urgent: 0,
  high: 1,
  medium: 2,
  low: 3,
};

function staffStudentSummary(state, studentId = state.profile.studentId) {
  const cohortStudent = state.staff.cohort?.find(
    (student) => student.id === studentId,
  );
  if (cohortStudent) {
    return {
      id: cohortStudent.id,
      name: cohortStudent.name,
      preferredName: cohortStudent.preferredName,
      programName: cohortStudent.programName,
      classYear: cohortStudent.classYear,
    };
  }
  if (studentId === state.profile.studentId) {
    return {
      id: state.profile.studentId,
      name: `${state.profile.firstName} ${state.profile.lastName}`,
      preferredName: state.profile.preferredName,
      programName: state.offer.programName,
      classYear: state.profile.classYear,
    };
  }
  return null;
}

function staffMember(state, staffId) {
  return state.staff.members.find((member) => member.id === staffId) ?? null;
}

function staffActorName(state, staffId) {
  return staffMember(state, staffId)?.name ?? "Staff member";
}

function mapStaffWorkItem(state, item) {
  const student = staffStudentSummary(state, item.studentId);
  if (!student) {
    throw new Error(`Staff work item ${item.id} references an unknown student`);
  }
  return {
    ...structuredClone(item),
    assignee: item.assigneeId
      ? structuredClone(staffMember(state, item.assigneeId))
      : null,
    student,
    history: state.staff.workLogs
      .filter((log) => log.workItemId === item.id)
      .map(({ workItemId: _workItemId, ...log }) => structuredClone(log))
      .sort((left, right) =>
        right.occurredAt.localeCompare(left.occurredAt),
      ),
    assigneeId: undefined,
    studentId: undefined,
  };
}

function addStaffWorkLog(
  draft,
  workItemId,
  action,
  message,
  actorName,
  now,
) {
  draft.staff.workLogs.push({
    id: randomUUID(),
    workItemId,
    action,
    message,
    actorName,
    occurredAt: now.toISOString(),
  });
}

export function ensureStaffDocumentWorkItems(draft, now) {
  let created = 0;
  for (const document of draft.documents) {
    if (!["needs_review", "under_review"].includes(document.status)) continue;
    const existing = draft.staff.workItems.find(
      (item) =>
        item.source?.type === "document" && item.source.id === document.id,
    );
    if (existing) continue;
    const component =
      document.category === "financial_aid"
        ? "Financial Aid"
        : document.category === "health"
          ? "Student Health"
          : "Registrar";
    const assignee =
      draft.staff.members.find((member) => member.component === component) ??
      draft.staff.members[1] ??
      draft.staff.members[0] ??
      null;
    const item = {
      id: randomUUID(),
      key: `DOC-${301 + draft.staff.workItems.length}`,
      studentId: draft.profile.studentId,
      title: `Review ${document.fileName}`,
      description:
        "Verify the stored original and make the official staff decision.",
      status: "todo",
      priority:
        document.category === "financial_aid" ? "urgent" : "high",
      type: "document_review",
      component,
      dueAt: new Date(now.getTime() + 2 * 86_400_000).toISOString(),
      escalated: false,
      assigneeId: assignee?.id ?? null,
      source: { type: "document", id: document.id },
      version: 1,
      createdAt: now.toISOString(),
      updatedAt: now.toISOString(),
    };
    draft.staff.workItems.push(item);
    addStaffWorkLog(
      draft,
      item.id,
      "created",
      "Created when the student document entered staff review.",
      "VV workflow",
      now,
    );
    created += 1;
  }
  return created;
}

export function buildStaffActionCenter(state) {
  const items = state.staff.workItems
    .map((item) => mapStaffWorkItem(state, item))
    .sort((left, right) => {
      const priority =
        staffPriorityOrder[left.priority] - staffPriorityOrder[right.priority];
      if (priority !== 0) return priority;
      if (left.dueAt && right.dueAt) {
        const due = left.dueAt.localeCompare(right.dueAt);
        if (due !== 0) return due;
      }
      return right.updatedAt.localeCompare(left.updatedAt);
    });
  return {
    items,
    staff: structuredClone(state.staff.members),
    counts: {
      todo: items.filter((item) => item.status === "todo").length,
      inProgress: items.filter((item) => item.status === "in_progress").length,
      done: items.filter((item) => item.status === "done").length,
      urgent: items.filter((item) => item.priority === "urgent").length,
      escalated: items.filter((item) => item.escalated).length,
    },
    generatedAt: state.fixture.updatedAt,
  };
}

export function buildStaffStudentRecord(state, studentId) {
  uuidValue(studentId, "studentId");
  const summary = staffStudentSummary(state, studentId);
  if (!summary) {
    throw notFound("STAFF_STUDENT_NOT_FOUND", "The student was not found");
  }
  if (studentId !== state.profile.studentId) {
    const operation = state.staff.cohort?.find(
      (student) => student.id === studentId,
    );
    return {
      student: summary,
      onboarding: {
        status: "in_progress",
        currentStep: "offer",
        completedAt: null,
        completedSteps: [],
        data: {},
        version: 1,
        updatedAt:
          operation?.journey.lastActivityAt ?? state.fixture.updatedAt,
      },
      profile: {
        ...structuredClone(state.profile),
        studentId,
        preferredName: summary.preferredName,
        firstName: summary.name.split(" ")[0] ?? summary.preferredName,
        lastName: summary.name.split(" ").slice(1).join(" "),
        classYear: summary.classYear,
        email: null,
        mobilePhone: null,
        version: 1,
        updatedAt:
          operation?.journey.lastActivityAt ?? state.fixture.updatedAt,
      },
      requirements: {
        items: [],
        total: 0,
        generatedAt: state.fixture.updatedAt,
      },
      documents: { items: [], total: 0 },
      syntheticTestRecord: true,
      operation: operation ? structuredClone(operation) : null,
    };
  }
  return {
    student: summary,
    onboarding: buildOnboarding(state),
    profile: structuredClone(state.profile),
    requirements: listRequirements(state),
    documents: listDocuments(state),
    syntheticTestRecord: false,
    operation:
      state.staff.cohort?.find((student) => student.id === studentId) ?? null,
  };
}

function staffInquiry(state, inquiry) {
  return {
    id: inquiry.id,
    student: staffStudentSummary(state),
    topicCode: inquiry.topicCode,
    subject:
      inquiry.subject ??
      `Student question about ${inquiry.topicCode.replaceAll("_", " ")}`,
    message: inquiry.message,
    status: inquiry.status === "received" ? "new" : inquiry.status,
    priority: inquiry.priority ?? "medium",
    assignee: inquiry.assigneeId
      ? structuredClone(staffMember(state, inquiry.assigneeId) ?? null)
      : null,
    createdAt: inquiry.createdAt,
    updatedAt: inquiry.updatedAt ?? inquiry.createdAt,
    version: inquiry.version ?? 1,
  };
}

export function buildStaffOperationsWorkspace(
  state,
  staffId = state.staff.members[0]?.id,
) {
  const actionCenter = buildStaffActionCenter(state);
  const knowledgeBase = structuredClone(state.staff.knowledgeBase ?? []);
  const corePlays = structuredClone(state.staff.corePlays ?? []);
  const journeyBlueprint = structuredClone(state.staff.journeyBlueprint ?? []);
  const inquiries = (state.helpRequests ?? [])
    .map((inquiry) => staffInquiry(state, inquiry))
    .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt));
  const campusLife = buildCampusLife(state);
  const currentStaff =
    staffMember(state, staffId) ?? state.staff.members[0] ?? null;
  if (!currentStaff) {
    throw notFound(
      "STAFF_PREVIEW_NOT_CONFIGURED",
      "No staff identity is configured",
    );
  }
  const cohort = structuredClone(state.staff.cohort ?? []);
  const personalStudents = cohort
    .filter(
      (student) =>
        student.assignedStaffId === currentStaff.id &&
        student.recommendedAction?.recommendedToday,
    )
    .sort((left, right) => right.risk.score - left.risk.score);
  const personalTaskIds = new Set(
    personalStudents
      .map((student) => student.recommendedAction?.taskId)
      .filter(Boolean),
  );
  const personalTasks = actionCenter.items.filter((item) =>
    personalTaskIds.has(item.id),
  );
  const configurations = {
    journeys: managedConfigurationSummary(
      state.staff.managedConfigurations.journeys,
    ),
    campusLife: managedConfigurationSummary(
      state.staff.managedConfigurations.campus_life,
    ),
    academics: managedConfigurationSummary(
      state.staff.managedConfigurations.academics,
    ),
  };
  return {
    currentStaff: structuredClone(currentStaff),
    actionCenter,
    personalActionCenter: {
      staff: structuredClone(currentStaff),
      students: personalStudents,
      tasks: personalTasks,
      counts: {
        studentsToday: personalStudents.length,
        critical: personalStudents.filter(
          (student) => student.risk.band === "critical",
        ).length,
        highRisk: personalStudents.filter(
          (student) => student.risk.band === "high",
        ).length,
        inProgress: personalTasks.filter(
          (task) => task.status === "in_progress",
        ).length,
        completed: personalTasks.filter((task) => task.status === "done").length,
      },
      generatedAt: state.fixture.updatedAt,
    },
    cohort,
    cohortSeed: structuredClone(state.staff.cohortSeed),
    student: buildStaffStudentRecord(state, state.profile.studentId),
    knowledgeBase,
    corePlays,
    inquiries,
    journeyBlueprint,
    academicCatalog: {
      version: state.academicCatalog.version,
      courses: structuredClone(state.academicCatalog.courses),
    },
    configurations,
    campusLife,
    portalInventory: [
      {
        id: "onboarding",
        label: "Onboarding",
        description:
          "Student intake stages, support preferences, and signed consent.",
        recordCount: journeyBlueprint.filter((item) => item.kind === "onboarding")
          .length,
        managementState: "partially_editable",
      },
      {
        id: "enrollment",
        label: "Enrollment",
        description:
          "Enrollment checklist templates and each student's live requirements.",
        recordCount:
          journeyBlueprint.filter((item) => item.kind === "enrollment").length +
          state.requirements.length,
        managementState: "editable",
      },
      {
        id: "classrooms",
        label: "Classrooms",
        description:
          "Programs, courses, prerequisites, instructors, and learning resources.",
        recordCount: state.academicCatalog.courses.length,
        managementState: "editable",
      },
      {
        id: "campus_life",
        label: "Campus life",
        description:
          "Student clubs, contacts, membership status, and university events.",
        recordCount: campusLife.clubs.length + campusLife.events.length,
        managementState: "editable",
      },
      {
        id: "financials",
        label: "Financials",
        description:
          "Aid package content, required documents, deadlines, and payment plans.",
        recordCount:
          state.financials.awards.length +
          state.financials.requiredDocuments.length,
        managementState: "planned",
      },
      {
        id: "messages",
        label: "Messages",
        description:
          "Official notices sent to students and incoming student inquiries.",
        recordCount: state.messages.length + inquiries.length,
        managementState: "editable",
      },
      {
        id: "help",
        label: "Help and guidance",
        description:
          "Student-safe knowledge cards and institutional support information.",
        recordCount: knowledgeBase.filter((item) => item.audience === "student")
          .length,
        managementState: "editable",
      },
    ],
    outreachRuns: structuredClone(state.staff.outreachRuns ?? []),
    capabilities: {
      sharedStudentEdits: true,
      campusContentEdits: true,
      knowledgeBaseEdits: true,
      corePlayEdits: true,
      inquiryReplies: true,
      externalOutreach: "simulation_only",
      staffEdward: "preview_only",
      managedYaml: true,
    },
    generatedAt: state.fixture.updatedAt,
  };
}

export function createStaffKnowledgeCard(draft, input, staffId, now) {
  const body = objectBody(input);
  exactKeys(body, [
    "title",
    "summary",
    "body",
    "category",
    "audience",
    "status",
  ]);
  const card = {
    id: randomUUID(),
    title: requiredString(body.title, "title", { min: 2, max: 120 }),
    summary: requiredString(body.summary, "summary", { min: 3, max: 240 }),
    body: requiredString(body.body, "body", { min: 3, max: 2_000 }),
    category: requiredString(body.category, "category", {
      min: 2,
      max: 60,
    }),
    audience: enumValue(body.audience, "audience", ["internal", "student"]),
    status: enumValue(body.status, "status", [
      "draft",
      "published",
      "archived",
    ]),
    owner: staffActorName(draft, staffId),
    version: 1,
    updatedAt: now.toISOString(),
  };
  draft.staff.knowledgeBase ??= [];
  draft.staff.knowledgeBase.unshift(card);
  return structuredClone(card);
}

export function createStaffCorePlay(draft, input, staffId, now) {
  const body = objectBody(input);
  exactKeys(body, [
    "title",
    "description",
    "trigger",
    "audience",
    "steps",
    "status",
  ]);
  if (
    !Array.isArray(body.steps) ||
    body.steps.length < 1 ||
    body.steps.length > 12
  ) {
    throw badRequest(
      "INVALID_CORE_PLAY_STEPS",
      "A core play must contain 1-12 steps",
    );
  }
  const play = {
    id: randomUUID(),
    title: requiredString(body.title, "title", { min: 2, max: 120 }),
    description: requiredString(body.description, "description", {
      min: 3,
      max: 500,
    }),
    trigger: requiredString(body.trigger, "trigger", {
      min: 3,
      max: 240,
    }),
    audience: requiredString(body.audience, "audience", {
      min: 3,
      max: 240,
    }),
    steps: body.steps.map((step, index) =>
      requiredString(step, `steps[${index}]`, { min: 2, max: 240 }),
    ),
    status: enumValue(body.status, "status", [
      "draft",
      "active",
      "archived",
    ]),
    owner: staffActorName(draft, staffId),
    version: 1,
    updatedAt: now.toISOString(),
  };
  draft.staff.corePlays ??= [];
  draft.staff.corePlays.unshift(play);
  return structuredClone(play);
}

export function createStaffClub(draft, input, now) {
  const body = objectBody(input);
  exactKeys(body, [
    "name",
    "category",
    "description",
    "latestUpdate",
    "contactName",
    "contactRole",
    "contactChannel",
    "membershipOpen",
    "imageUrl",
  ]);
  const name = requiredString(body.name, "name", { min: 2, max: 120 });
  const club = {
    id: randomUUID(),
    name,
    category: requiredString(body.category, "category", {
      min: 2,
      max: 80,
    }),
    description: requiredString(body.description, "description", {
      min: 3,
      max: 500,
    }),
    contactName: requiredString(body.contactName, "contactName", {
      min: 2,
      max: 120,
    }),
    contactRole: requiredString(body.contactRole, "contactRole", {
      min: 2,
      max: 120,
    }),
    contactChannel: requiredString(
      body.contactChannel,
      "contactChannel",
      { min: 3, max: 160 },
    ),
    latestUpdate: requiredString(body.latestUpdate, "latestUpdate", {
      min: 2,
      max: 240,
    }),
    nextActivity: null,
    imageUrl:
      optionalString(body.imageUrl, "imageUrl", { min: 1, max: 500 }) ??
      "/media/clubs/code-collective.jpg",
    imageAlt: `Students participating in ${name}`,
    imageAttribution: "Staff-managed portal image",
    imageSourceUrl: "",
    source: null,
    socialLinks: [],
    longDescription: null,
    meetingSchedule: null,
    membershipOpen: booleanValue(body.membershipOpen, "membershipOpen"),
    events: [],
    version: 1,
    updatedAt: now.toISOString(),
  };
  draft.campusLife.clubs.unshift(club);
  draft.portalProjectionVersion += 1;
  return structuredClone(club);
}

export function updateStaffKnowledgeCard(draft, cardId, input, now) {
  uuidValue(cardId, "cardId");
  const body = objectBody(input);
  exactKeys(body, [
    "expectedVersion",
    "title",
    "summary",
    "body",
    "category",
    "audience",
    "status",
  ]);
  const card = (draft.staff.knowledgeBase ?? []).find(
    (candidate) => candidate.id === cardId,
  );
  if (!card) {
    throw notFound(
      "STAFF_KNOWLEDGE_CARD_NOT_FOUND",
      "The knowledge card was not found",
    );
  }
  const expectedVersion = integerValue(
    body.expectedVersion,
    "expectedVersion",
    1,
    Number.MAX_SAFE_INTEGER,
  );
  if (card.version !== expectedVersion) {
    throw conflict(
      "VERSION_CONFLICT",
      "This knowledge card changed in another staff session",
    );
  }
  card.title = requiredString(body.title, "title", { min: 2, max: 120 });
  card.summary = requiredString(body.summary, "summary", {
    min: 3,
    max: 240,
  });
  card.body = requiredString(body.body, "body", { min: 3, max: 2_000 });
  card.category = requiredString(body.category, "category", {
    min: 2,
    max: 60,
  });
  card.audience = enumValue(body.audience, "audience", [
    "internal",
    "student",
  ]);
  card.status = enumValue(body.status, "status", [
    "draft",
    "published",
    "archived",
  ]);
  card.version += 1;
  card.updatedAt = now.toISOString();
  return structuredClone(card);
}

export function updateStaffCorePlay(draft, playId, input, now) {
  uuidValue(playId, "playId");
  const body = objectBody(input);
  exactKeys(body, [
    "expectedVersion",
    "title",
    "description",
    "trigger",
    "audience",
    "steps",
    "status",
  ]);
  const play = (draft.staff.corePlays ?? []).find(
    (candidate) => candidate.id === playId,
  );
  if (!play) {
    throw notFound("STAFF_CORE_PLAY_NOT_FOUND", "The core play was not found");
  }
  const expectedVersion = integerValue(
    body.expectedVersion,
    "expectedVersion",
    1,
    Number.MAX_SAFE_INTEGER,
  );
  if (play.version !== expectedVersion) {
    throw conflict(
      "VERSION_CONFLICT",
      "This core play changed in another staff session",
    );
  }
  if (
    !Array.isArray(body.steps) ||
    body.steps.length < 1 ||
    body.steps.length > 12
  ) {
    throw badRequest(
      "INVALID_CORE_PLAY_STEPS",
      "A core play must contain 1-12 steps",
    );
  }
  play.title = requiredString(body.title, "title", { min: 2, max: 120 });
  play.description = requiredString(body.description, "description", {
    min: 3,
    max: 500,
  });
  play.trigger = requiredString(body.trigger, "trigger", {
    min: 3,
    max: 240,
  });
  play.audience = requiredString(body.audience, "audience", {
    min: 3,
    max: 240,
  });
  play.steps = body.steps.map((step, index) =>
    requiredString(step, `steps[${index}]`, { min: 2, max: 240 }),
  );
  play.status = enumValue(body.status, "status", [
    "draft",
    "active",
    "archived",
  ]);
  play.version += 1;
  play.updatedAt = now.toISOString();
  return structuredClone(play);
}

export function updateStaffInquiry(
  draft,
  inquiryId,
  input,
  staffId,
  now,
) {
  uuidValue(inquiryId, "inquiryId");
  const body = objectBody(input);
  exactKeys(body, [
    "expectedVersion",
    "status",
    "assigneeId",
    "responseNote",
    "notifyStudent",
  ]);
  const inquiry = (draft.helpRequests ?? []).find(
    (candidate) => candidate.id === inquiryId,
  );
  if (!inquiry) {
    throw notFound("STAFF_INQUIRY_NOT_FOUND", "The inquiry was not found");
  }
  const expectedVersion = integerValue(
    body.expectedVersion,
    "expectedVersion",
    1,
    Number.MAX_SAFE_INTEGER,
  );
  if ((inquiry.version ?? 1) !== expectedVersion) {
    throw conflict(
      "VERSION_CONFLICT",
      "This inquiry changed in another staff session",
    );
  }
  const status = enumValue(body.status, "status", [
    "new",
    "open",
    "waiting_on_student",
    "resolved",
  ]);
  const assigneeId =
    body.assigneeId === undefined
      ? inquiry.assigneeId ?? null
      : body.assigneeId === null
        ? null
        : uuidValue(body.assigneeId, "assigneeId");
  if (assigneeId && !staffMember(draft, assigneeId)) {
    throw notFound("STAFF_MEMBER_NOT_FOUND", "The assignee was not found");
  }
  const responseNote = optionalString(body.responseNote, "responseNote", {
    min: 2,
    max: 1_000,
  });
  const notifyStudent = booleanValue(body.notifyStudent, "notifyStudent");
  inquiry.status = status;
  inquiry.assigneeId = assigneeId;
  inquiry.updatedAt = now.toISOString();
  inquiry.version = (inquiry.version ?? 1) + 1;
  if (responseNote && notifyStudent) {
    draft.messages.push({
      id: randomUUID(),
      subject: `Reply: ${inquiry.subject ?? "Your student support question"}`,
      body: responseNote,
      sentAt: now.toISOString(),
      readAt: null,
      senderName: `${draft.tenant.shortName} Enrollment Team`,
    });
    draft.portalProjectionVersion += 1;
  }
  const matchingWorkItem = draft.staff.workItems.find(
    (item) => item.source?.type === "message" && item.studentId === draft.profile.studentId,
  );
  if (matchingWorkItem && responseNote) {
    addStaffWorkLog(
      draft,
      matchingWorkItem.id,
      "commented",
      `Replied to student inquiry: ${responseNote}`,
      staffActorName(draft, staffId),
      now,
    );
  }
  return staffInquiry(draft, inquiry);
}

export function updateStaffClub(draft, clubId, input, now) {
  uuidValue(clubId, "clubId");
  const body = objectBody(input);
  exactKeys(body, [
    "expectedVersion",
    "name",
    "category",
    "description",
    "latestUpdate",
    "contactName",
    "contactRole",
    "contactChannel",
    "membershipOpen",
  ]);
  const club = draft.campusLife.clubs.find(
    (candidate) => candidate.id === clubId,
  );
  if (!club) {
    throw notFound("STAFF_CLUB_NOT_FOUND", "The club was not found");
  }
  const expectedVersion = integerValue(
    body.expectedVersion,
    "expectedVersion",
    1,
    Number.MAX_SAFE_INTEGER,
  );
  if ((club.version ?? 1) !== expectedVersion) {
    throw conflict(
      "VERSION_CONFLICT",
      "This club changed in another staff session",
    );
  }
  club.name = requiredString(body.name, "name", { min: 2, max: 120 });
  club.category = requiredString(body.category, "category", {
    min: 2,
    max: 80,
  });
  club.description = requiredString(body.description, "description", {
    min: 3,
    max: 500,
  });
  club.latestUpdate = requiredString(body.latestUpdate, "latestUpdate", {
    min: 2,
    max: 240,
  });
  club.contactName = requiredString(body.contactName, "contactName", {
    min: 2,
    max: 120,
  });
  club.contactRole = requiredString(body.contactRole, "contactRole", {
    min: 2,
    max: 120,
  });
  club.contactChannel = requiredString(
    body.contactChannel,
    "contactChannel",
    { min: 3, max: 160 },
  );
  club.membershipOpen = booleanValue(body.membershipOpen, "membershipOpen");
  club.version = (club.version ?? 1) + 1;
  club.updatedAt = now.toISOString();
  draft.portalProjectionVersion += 1;
  return structuredClone(club);
}

export function simulateStaffOutreach(draft, input, staffId, now) {
  const body = objectBody(input);
  exactKeys(body, ["title", "audience", "channel", "requestedCount"]);
  const run = {
    id: randomUUID(),
    title: requiredString(body.title, "title", { min: 2, max: 120 }),
    audience: requiredString(body.audience, "audience", {
      min: 3,
      max: 240,
    }),
    channel: enumValue(body.channel, "channel", ["email", "sms", "voice"]),
    requestedCount: integerValue(
      body.requestedCount,
      "requestedCount",
      1,
      10_000,
    ),
    status: "simulation_only",
    createdBy: staffActorName(draft, staffId),
    createdAt: now.toISOString(),
  };
  draft.staff.outreachRuns ??= [];
  draft.staff.outreachRuns.unshift(run);
  draft.staff.outreachRuns = draft.staff.outreachRuns.slice(0, 20);
  return structuredClone(run);
}

export function previewStaffEdward(input) {
  const body = objectBody(input);
  exactKeys(body, ["message"]);
  const message = requiredString(body.message, "message", {
    min: 2,
    max: 2_000,
  });
  const text = message.toLowerCase();
  const wantsOutreach = /call|email|sms|outreach|contact|message/.test(text);
  const wantsJourney =
    /onboarding|enrollment|checklist|requirement|task/.test(text);
  const wantsData = /show|find|which|who|how many|list|student/.test(text);
  const plan = [
    ...(wantsData
      ? [
          {
            label: "Read the matching student and task records",
            capability: "read_student_data",
            status: "available",
          },
        ]
      : []),
    ...(wantsJourney
      ? [
          {
            label: "Prepare the requested journey changes for staff review",
            capability: "update_journey",
            status: "needs_confirmation",
          },
        ]
      : []),
    ...(wantsOutreach
      ? [
          {
            label: "Draft the outreach content",
            capability: "draft_message",
            status: "needs_confirmation",
          },
          {
            label: "Simulate the external outreach run",
            capability: "launch_outreach",
            status: "simulation_only",
          },
        ]
      : []),
  ];
  if (plan.length === 0) {
    plan.push({
      label: "Read the relevant staff workspace context",
      capability: "read_student_data",
      status: "available",
    });
  }
  return {
    message:
      "I prepared a safe execution plan. I can read current staff data now, but any record change needs explicit confirmation and external email, SMS, or voice outreach remains simulation-only.",
    plan,
    dataSources: [
      "Enrollment action center",
      "Student journey and documents",
      "Knowledge base and core plays",
      "Student inquiries",
    ],
    executionMode: "preview_only",
  };
}

export function updateStaffWorkItem(
  draft,
  workItemId,
  input,
  staffId,
  now,
) {
  uuidValue(workItemId, "workItemId");
  const body = objectBody(input);
  exactKeys(body, [
    "expectedVersion",
    "status",
    "assigneeId",
    "escalated",
    "note",
  ]);
  const expectedVersion = integerValue(
    body.expectedVersion,
    "expectedVersion",
    1,
    Number.MAX_SAFE_INTEGER,
  );
  const item = draft.staff.workItems.find(
    (candidate) => candidate.id === workItemId,
  );
  if (!item) {
    throw notFound("STAFF_WORK_ITEM_NOT_FOUND", "The work item was not found");
  }
  if (item.version !== expectedVersion) {
    throw conflict(
      "VERSION_CONFLICT",
      "This work item changed in another staff session",
    );
  }
  const actorName = staffActorName(draft, staffId);
  let changed = false;
  if (body.status !== undefined) {
    const status = enumValue(body.status, "status", [
      "todo",
      "in_progress",
      "done",
    ]);
    if (status !== item.status) {
      addStaffWorkLog(
        draft,
        item.id,
        "status_changed",
        `Moved from ${item.status.replaceAll("_", " ")} to ${status.replaceAll("_", " ")}.`,
        actorName,
        now,
      );
      item.status = status;
      changed = true;
    }
  }
  if (body.assigneeId !== undefined) {
    const assigneeId =
      body.assigneeId === null
        ? null
        : uuidValue(body.assigneeId, "assigneeId");
    if (assigneeId && !staffMember(draft, assigneeId)) {
      throw notFound("STAFF_MEMBER_NOT_FOUND", "The assignee was not found");
    }
    if (assigneeId !== item.assigneeId) {
      item.assigneeId = assigneeId;
      addStaffWorkLog(
        draft,
        item.id,
        "assigned",
        assigneeId
          ? `Assigned to ${staffActorName(draft, assigneeId)}.`
          : "Removed the assignee.",
        actorName,
        now,
      );
      changed = true;
    }
  }
  if (body.escalated !== undefined) {
    const escalated = booleanValue(body.escalated, "escalated");
    if (escalated !== item.escalated) {
      item.escalated = escalated;
      addStaffWorkLog(
        draft,
        item.id,
        "escalated",
        escalated ? "Marked as escalated." : "Cleared the escalation flag.",
        actorName,
        now,
      );
      changed = true;
    }
  }
  const note = optionalString(body.note, "note", { min: 1, max: 500 });
  if (note) {
    addStaffWorkLog(
      draft,
      item.id,
      "commented",
      note,
      actorName,
      now,
    );
    changed = true;
  }
  if (!changed) {
    throw badRequest(
      "STAFF_WORK_ITEM_NO_CHANGES",
      "Choose a status, assignee, escalation state, or note to update",
    );
  }
  item.version += 1;
  item.updatedAt = now.toISOString();
  return mapStaffWorkItem(draft, item);
}

export function updateStaffStudentPreferences(
  draft,
  studentId,
  input,
  staffId,
  now,
) {
  uuidValue(studentId, "studentId");
  if (studentId !== draft.profile.studentId) {
    throw notFound("STAFF_STUDENT_NOT_FOUND", "The student was not found");
  }
  const body = objectBody(input);
  exactKeys(body, [
    "expectedOnboardingVersion",
    "expectedProfileVersion",
    "communicationPreference",
    "housingPreference",
    "accommodationInterest",
    "residencyVerificationPath",
    "notifyStudent",
    "note",
  ]);
  const expectedOnboardingVersion = integerValue(
    body.expectedOnboardingVersion,
    "expectedOnboardingVersion",
    1,
    Number.MAX_SAFE_INTEGER,
  );
  const expectedProfileVersion = integerValue(
    body.expectedProfileVersion,
    "expectedProfileVersion",
    1,
    Number.MAX_SAFE_INTEGER,
  );
  if (
    draft.onboarding.version !== expectedOnboardingVersion ||
    draft.profile.version !== expectedProfileVersion
  ) {
    throw conflict(
      "VERSION_CONFLICT",
      "The student record changed in another session",
    );
  }
  const communicationPreference = enumValue(
    body.communicationPreference,
    "communicationPreference",
    ["email", "sms"],
  );
  const housingPreference = enumValue(body.housingPreference, "housingPreference", [
    "on_campus",
    "off_campus",
    "commuting",
    "undecided",
    "family",
  ]);
  const accommodationInterest = enumValue(
    body.accommodationInterest,
    "accommodationInterest",
    ["not_now", "housing", "academic", "both"],
  );
  const residencyVerificationPath = enumValue(
    body.residencyVerificationPath,
    "residencyVerificationPath",
    ["home_address_review", "document_upload", "advisor_review"],
  );
  const notifyStudent = booleanValue(body.notifyStudent, "notifyStudent");
  const note = optionalString(body.note, "note", { min: 1, max: 500 });
  draft.profile.communicationPreference = communicationPreference;
  draft.profile.version += 1;
  draft.profile.updatedAt = now.toISOString();
  draft.onboarding.data.communicationPreference = communicationPreference;
  draft.onboarding.data.housingPreference = housingPreference;
  draft.onboarding.data.accommodationInterest = accommodationInterest;
  draft.onboarding.data.residencyVerificationPath =
    residencyVerificationPath;
  if (housingPreference !== "on_campus") {
    delete draft.onboarding.data.housingResidenceOption;
    delete draft.onboarding.data.housingResidencePreferences;
  }
  draft.onboarding.version += 1;
  draft.onboarding.updatedAt = now.toISOString();
  draft.portalProjectionVersion += 1;
  const onboardingItem = draft.staff.workItems.find(
    (item) =>
      item.studentId === studentId && item.source?.type === "onboarding",
  );
  if (onboardingItem) {
    onboardingItem.updatedAt = now.toISOString();
    addStaffWorkLog(
      draft,
      onboardingItem.id,
      "student_preferences_updated",
      note ?? "Updated the student's operational onboarding preferences.",
      staffActorName(draft, staffId),
      now,
    );
  }
  if (notifyStudent) {
    draft.messages.push({
      id: randomUUID(),
      subject: "Your enrollment preferences were updated",
      body:
        note ??
        "Your enrollment team updated your communication, housing, and support follow-up preferences. Review your enrollment page for the latest details.",
      sentAt: now.toISOString(),
      readAt: null,
      senderName: `${draft.tenant.shortName} Enrollment Team`,
    });
  }
  return buildStaffStudentRecord(draft, studentId);
}

export function reviewStaffDocument(
  draft,
  documentId,
  input,
  staffId,
  now,
) {
  uuidValue(documentId, "documentId");
  const body = objectBody(input);
  exactKeys(body, [
    "workItemId",
    "expectedWorkItemVersion",
    "decision",
    "note",
    "notifyStudent",
  ]);
  const workItemId = uuidValue(body.workItemId, "workItemId");
  const expectedWorkItemVersion = integerValue(
    body.expectedWorkItemVersion,
    "expectedWorkItemVersion",
    1,
    Number.MAX_SAFE_INTEGER,
  );
  const decision = enumValue(body.decision, "decision", [
    "accepted",
    "rejected",
  ]);
  const note = requiredString(body.note, "note", { min: 3, max: 500 });
  const notifyStudent = booleanValue(body.notifyStudent, "notifyStudent");
  const document = draft.documents.find(
    (candidate) => candidate.id === documentId,
  );
  if (!document) {
    throw notFound("STAFF_DOCUMENT_NOT_FOUND", "The document was not found");
  }
  const item = draft.staff.workItems.find(
    (candidate) =>
      candidate.id === workItemId &&
      candidate.source?.type === "document" &&
      candidate.source.id === documentId,
  );
  if (!item) {
    throw notFound(
      "STAFF_WORK_ITEM_NOT_FOUND",
      "The document review work item was not found",
    );
  }
  if (item.version !== expectedWorkItemVersion) {
    throw conflict(
      "VERSION_CONFLICT",
      "This document review changed in another staff session",
    );
  }
  if (!["needs_review", "under_review"].includes(document.status)) {
    throw conflict(
      "DOCUMENT_REVIEW_ALREADY_DECIDED",
      "This document already has an official staff decision",
    );
  }
  document.status = decision;
  const requirement = document.requirementId
    ? draft.requirements.find(
        (candidate) => candidate.id === document.requirementId,
      )
    : null;
  if (requirement) {
    requirement.status = decision === "accepted" ? "completed" : "rejected";
    requirement.progressPercent = decision === "accepted" ? 100 : 60;
    if (decision === "accepted") {
      awardRewards(
        draft,
        "requirement_completed",
        requirement.code,
        requirement.id,
        {},
        now,
      );
    }
  }
  if (document.category === "financial_aid") {
    const financialDocument = draft.financials.requiredDocuments.find(
      (candidate) => candidate.code === "verification_worksheet",
    );
    if (financialDocument) {
      financialDocument.status =
        decision === "accepted" ? "received" : "required";
      financialDocument.version = (financialDocument.version ?? 1) + 1;
      financialDocument.updatedAt = now.toISOString();
    }
  }
  item.status = "done";
  item.version += 1;
  item.updatedAt = now.toISOString();
  addStaffWorkLog(
    draft,
    item.id,
    "document_decided",
    `${decision === "accepted" ? "Accepted" : "Requested changes to"} ${document.fileName}: ${note}`,
    staffActorName(draft, staffId),
    now,
  );
  const notification = {
    id: randomUUID(),
    subject:
      decision === "accepted"
        ? `${document.fileName} was accepted`
        : `${document.fileName} needs changes`,
    body: notifyStudent
      ? note
      : decision === "accepted"
        ? "Your document was reviewed and accepted."
        : "Your document was reviewed and needs changes. Open Documents for details.",
    kind: "document_review",
    href: `/documents?document=${documentId}`,
    sentAt: now.toISOString(),
    readAt: null,
    senderName: `${draft.tenant.shortName} Enrollment Team`,
  };
  draft.messages.push(notification);
  draft.portalProjectionVersion += 1;
  refreshJourneyStatus(draft);
  return {
    document: structuredClone(document),
    workItem: mapStaffWorkItem(draft, item),
    notification: structuredClone(notification),
  };
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
  updateDocumentRequirement(draft, document, now);
  draft.portalProjectionVersion += 1;
  return documentResponse(document);
}

/**
 * Creates the durable database record before object storage or AI work starts.
 *
 * `contentStored` is deliberately internal-only.  A client cannot obtain a
 * content URL until the original object is confirmed in storage, while the
 * record itself is already recoverable if the process stops between steps.
 */
export function reserveDocumentUpload(draft, input, now) {
  const body = objectBody(input);
  exactKeys(body, [
    "id",
    "fileName",
    "mimeType",
    "sizeBytes",
    "category",
    "requirementId",
    "uploadBundleId",
    "sha256",
    "storageKey",
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
  const sizeBytes = integerValue(body.sizeBytes, "sizeBytes", 1, 10_485_760);
  let category = enumValue(body.category, "category", [
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
  const uploadBundleId =
    body.uploadBundleId === undefined
      ? undefined
      : uuidValue(body.uploadBundleId, "uploadBundleId");
  if (requirementId) {
    const requirement = draft.requirements.find(
      (candidate) =>
        candidate.id === requirementId &&
        candidate.submissionType === "document",
    );
    if (!requirement) {
      throw notFound(
        "DOCUMENT_REQUIREMENT_NOT_FOUND",
        "The document requirement was not found",
      );
    }
    category = documentCategoryForRequirement(requirement.code) ?? category;
    const activeExtraction = draft.documents.find((candidate) => {
      const belongsToSameBundle =
        uploadBundleId &&
        candidate.uploadBundleId === uploadBundleId;
      return (
        candidate.requirementId === requirementId &&
        candidate.status === "processing" &&
        candidate.extraction?.status === "processing" &&
        !belongsToSameBundle
      );
    });
    if (activeExtraction) {
      throw conflict(
        "DOCUMENT_EXTRACTION_IN_PROGRESS",
        "This requirement already has a document being parsed. Wait for it to finish or fail before uploading another document.",
      );
    }
  }
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

  const document = {
    id,
    ...(requirementId ? { requirementId } : {}),
    ...(uploadBundleId ? { uploadBundleId } : {}),
    fileName,
    mimeType,
    sizeBytes,
    category,
    processingMode: documentProcessingModeForCategory(category),
    status: "placeholder",
    sha256,
    storageKey,
    contentStored: false,
    extraction: null,
    createdAt: now.toISOString(),
  };
  draft.documents.push(document);
  draft.portalProjectionVersion += 1;
  return documentResponse(document);
}

/**
 * This transition is the durable hand-off to the asynchronous extractor.  It
 * happens only after the immutable object write succeeds, and is idempotent
 * so a replayed upload cannot enqueue a second parser job.
 */
export function queueDocumentExtraction(draft, documentId, now) {
  uuidValue(documentId, "documentId");
  const document = draft.documents.find(
    (candidate) => candidate.id === documentId,
  );
  if (!document) {
    throw notFound("STUDENT_DOCUMENT_NOT_FOUND", "The document was not found");
  }
  if (!document.storageKey) {
    throw conflict(
      "DOCUMENT_CONTENT_NOT_AVAILABLE",
      "The original document has not been stored yet",
    );
  }
  if (document.status === "processing") return documentResponse(document);
  if (
    document.status !== "placeholder" &&
    document.status !== "uploaded"
  ) {
    return documentResponse(document);
  }

  document.contentStored = true;
  document.processingMode ??= documentProcessingModeForCategory(
    document.category,
  );
  if (document.processingMode === "manual_review") {
    document.status = "under_review";
    document.extraction = null;
    updateDocumentRequirement(draft, document, now);
    draft.portalProjectionVersion += 1;
    return documentResponse(document);
  }
  document.status = "processing";
  const processingStartedAt = now.toISOString();
  document.extraction = {
    status: "processing",
    documentType: documentTypeForCategory(document.category),
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
    processingStartedAt,
    processingDeadlineAt: new Date(now.getTime() + 90_000).toISOString(),
    processedAt: null,
    verifiedAt: null,
  };
  draft.portalProjectionVersion += 1;
  return documentResponse(document);
}

/** Marks an already-stored, retryable document as queued without reading it. */
export function queueDocumentExtractionRetry(draft, documentId, now) {
  const reference = prepareDocumentExtractionRetry(draft, documentId);
  const document = draft.documents.find(
    (candidate) => candidate.id === reference.id,
  );
  document.status = "processing";
  const processingStartedAt = now.toISOString();
  document.extraction = {
    status: "processing",
    documentType: documentTypeForCategory(document.category),
    summary:
      "The original file is safely stored. Edward is retrying structured extraction.",
    studentName: null,
    institutionName: null,
    issueDate: null,
    academicTerm: null,
    fields: [],
    courses: [],
    warnings: [],
    model: null,
    provider: "local",
    processingStartedAt,
    processingDeadlineAt: new Date(now.getTime() + 90_000).toISOString(),
    processedAt: null,
    verifiedAt: null,
  };
  draft.portalProjectionVersion += 1;
  return { document: documentResponse(document), reference };
}

/**
 * Converts abandoned processing leases into a retryable terminal state.
 * Reads call this before returning document state, so a crashed worker cannot
 * make browsers poll forever.
 */
export function expireStaleDocumentExtractions(draft, now) {
  const nowMs = now.getTime();
  let expired = 0;
  for (const document of draft.documents) {
    if (
      document.status !== "processing" ||
      document.extraction?.status !== "processing"
    ) {
      continue;
    }
    const explicitDeadlineMs = Date.parse(
      document.extraction.processingDeadlineAt ?? "",
    );
    const processingStartedMs = Date.parse(
      document.extraction.processingStartedAt ?? document.createdAt ?? "",
    );
    const deadlineMs = Number.isFinite(explicitDeadlineMs)
      ? explicitDeadlineMs
      : processingStartedMs + 90_000;
    if (!Number.isFinite(deadlineMs) || deadlineMs > nowMs) continue;

    document.status = "uploaded";
    document.extraction = {
      status: "failed",
      documentType: document.extraction.documentType,
      summary:
        "The file is safely stored, but the parsing attempt did not finish before its processing deadline.",
      studentName: null,
      institutionName: null,
      issueDate: null,
      academicTerm: null,
      fields: [],
      courses: [],
      visualRegions: [],
      warnings: [
        "Parsing timed out. Retry the stored document without uploading it again.",
      ],
      model: null,
      provider: "local",
      processedAt: now.toISOString(),
      verifiedAt: null,
      failureCode: "timeout",
      retryable: true,
    };
    expired += 1;
  }
  if (expired > 0) draft.portalProjectionVersion += 1;
  return expired;
}

/**
 * Returns the immutable storage reference needed to retry a failed document
 * parse. The caller reads the bytes and then calls
 * `completeDocumentExtractionRetry` inside the same serialized mutation.
 *
 * The preview store serializes mutations, so this check also prevents a
 * second idempotency key from starting another parser call after a successful
 * retry has already reached a terminal reviewable state.
 */
export function prepareDocumentExtractionRetry(draft, documentId) {
  uuidValue(documentId, "documentId");
  const document = draft.documents.find(
    (candidate) => candidate.id === documentId,
  );
  if (!document) {
    throw notFound("STUDENT_DOCUMENT_NOT_FOUND", "The document was not found");
  }
  if (!document.storageKey || document.contentStored === false) {
    throw conflict(
      "DOCUMENT_CONTENT_NOT_AVAILABLE",
      "The original document is not available for parsing",
    );
  }
  const extraction = document.extraction;
  const retryableFailure =
    extraction?.status === "failed" && extraction.retryable !== false;
  const pendingConfiguration = extraction?.status === "pending_configuration";
  if (!retryableFailure && !pendingConfiguration) {
    throw conflict(
      "DOCUMENT_EXTRACTION_NOT_RETRYABLE",
      "This document is not waiting for a retryable parsing attempt",
    );
  }
  return {
    id: document.id,
    storageKey: document.storageKey,
    fileName: document.fileName,
    mimeType: document.mimeType,
    category: document.category,
    requirementId: document.requirementId,
  };
}

/**
 * Replaces an existing extraction without creating a second document record
 * or a second object-storage reference. The original upload remains the
 * canonical record through all retry attempts.
 */
export function completeDocumentExtractionRetry(
  draft,
  documentId,
  extraction,
  now,
) {
  uuidValue(documentId, "documentId");
  const document = draft.documents.find(
    (candidate) => candidate.id === documentId,
  );
  if (!document) {
    throw notFound("STUDENT_DOCUMENT_NOT_FOUND", "The document was not found");
  }
  document.extraction = validateExtraction(extraction);
  const automaticallyProjectedTranscript =
    document.category === "transcript" &&
    document.extraction.status === "completed" &&
    document.extraction.documentType === "transcript";
  document.status = automaticallyProjectedTranscript
    ? "under_review"
    : document.extraction.status === "completed"
      ? "needs_review"
      : "uploaded";
  if (
    automaticallyProjectedTranscript &&
    Array.isArray(document.extraction.courses)
  ) {
    ingestTranscriptCourses(draft, document, now);
  }
  updateDocumentRequirement(draft, document, now);
  draft.portalProjectionVersion += 1;
  return documentResponse(document);
}

/**
 * One-time recovery for transcript extractions created before transcripts
 * became an automatic, read-only projection. It is idempotent because course
 * ingestion is keyed by source document and the document leaves needs_review.
 */
export function autoProjectCompletedTranscripts(draft, now) {
  let projected = 0;
  for (const document of draft.documents) {
    if (
      document.category !== "transcript" ||
      document.status !== "needs_review" ||
      document.extraction?.status !== "completed" ||
      document.extraction.documentType !== "transcript"
    ) {
      continue;
    }
    document.status = "under_review";
    if (Array.isArray(document.extraction.courses)) {
      ingestTranscriptCourses(draft, document, now);
    }
    updateDocumentRequirement(draft, document, now);
    projected += 1;
  }
  if (projected > 0) draft.portalProjectionVersion += 1;
  return projected;
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

function documentTypeForCategory(category) {
  return (
    {
      transcript: "transcript",
      identity: "identity",
      financial_aid: "financial_aid",
      health: "immunization",
      consent: "ferpa",
      residency: "residency",
    }[category] ?? "other"
  );
}

function documentProcessingModeForCategory(category) {
  if (category === "identity" || category === "transcript") return "agentic";
  if (category === "financial_aid") return "classification_only";
  return "manual_review";
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
  applySafeProfileProjectionFromExtraction(draft, document, accepted, now);
  if (
    document.extraction.documentType === "transcript" &&
    Array.isArray(document.extraction.courses)
  ) {
    ingestTranscriptCourses(draft, document, now);
  }
  draft.portalProjectionVersion += 1;
  return documentResponse(document);
}

function applySafeProfileProjectionFromExtraction(
  state,
  document,
  acceptedFieldKeys,
  now,
) {
  const accepted = new Set(acceptedFieldKeys);
  const projection = {};
  for (const field of document.extraction.fields) {
    if (!accepted.has(field.key)) continue;
    const value = field.value.trim();
    if (!value) continue;
    if (field.key === "preferred_name" && value.length <= 120) {
      projection.preferredName = value;
    } else if (field.key === "pronouns" && value.length <= 80) {
      projection.pronouns = value;
    } else if (
      field.key === "mobile_phone" &&
      /^\+?[0-9 ()-]{7,32}$/.test(value)
    ) {
      projection.mobilePhone = value;
    } else if (
      field.key === "communication_preference" &&
      (value.toLowerCase() === "email" || value.toLowerCase() === "sms")
    ) {
      projection.communicationPreference = value.toLowerCase();
    }
  }
  if (Object.keys(projection).length === 0) return;
  Object.assign(state.profile, projection, {
    version: state.profile.version + 1,
    updatedAt: now.toISOString(),
  });
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
  if (!document || !document.storageKey || document.contentStored === false) {
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
  if (financialDocument) {
    financialDocument.status = "verified";
    financialDocument.version = (financialDocument.version ?? 1) + 1;
    financialDocument.updatedAt = now.toISOString();
  }
  const requirement = draft.requirements.find(
    (candidate) => candidate.code === "enrollment_deposit",
  );
  if (requirement) {
    completeRequirementAndRefreshDependencies(
      draft,
      "enrollment_deposit",
      now,
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
    subject: `Student question about ${topicCode.replaceAll("_", " ")}`,
    message,
    status: "new",
    priority: topicCode === "support" ? "high" : "medium",
    assigneeId: null,
    createdAt: now.toISOString(),
    updatedAt: now.toISOString(),
    version: 1,
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
    const section = event.properties.section;
    awardMatchingRewards(
      draft,
      "activity_event",
      event.eventName,
      typeof section === "string"
        ? `${event.eventName}:${section}`
        : event.eventId,
      event.properties,
      now,
    );
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
  const failureCode =
    status === "failed" && extraction.failureCode !== undefined
      ? enumValue(extraction.failureCode, "extraction.failureCode", [
          "provider_unavailable",
          "unsupported_capability",
          "invalid_response",
          "timeout",
          "unknown",
        ])
      : undefined;
  const retryable =
    (status === "failed" || status === "pending_configuration") &&
    extraction.retryable !== undefined
      ? booleanValue(extraction.retryable, "extraction.retryable")
      : undefined;
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
  const visualRegions = Array.isArray(extraction.visualRegions)
    ? extraction.visualRegions.slice(0, 4).flatMap((candidate, index) => {
        const region = objectBody(candidate);
        if (region.kind !== "profile_photo") return [];
        const x = boundedNormalizedNumber(region.x);
        const y = boundedNormalizedNumber(region.y);
        const width = Math.min(1 - x, boundedNormalizedNumber(region.width));
        const height = Math.min(1 - y, boundedNormalizedNumber(region.height));
        if (width < 0.02 || height < 0.02) return [];
        return [{
          kind: "profile_photo",
          pageNumber:
            region.pageNumber === null || region.pageNumber === undefined
              ? null
              : integerValue(
                  region.pageNumber,
                  `visualRegions[${index}].pageNumber`,
                  1,
                  8,
                ),
          x,
          y,
          width,
          height,
          confidence: boundedNormalizedNumber(region.confidence),
        }];
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
    visualRegions,
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
      "groq",
      "local",
    ]),
    processedAt: nullableBoundedText(extraction.processedAt, 80),
    verifiedAt: null,
    ...(failureCode ? { failureCode } : {}),
    ...(retryable !== undefined ? { retryable } : {}),
  };
}

function boundedNormalizedNumber(value) {
  return typeof value === "number" && Number.isFinite(value)
    ? Math.max(0, Math.min(1, value))
    : 0;
}

function publicProgram(program) {
  return {
    id: program.id,
    code: program.code,
    name: program.name,
    degree: program.degree,
    totalCredits: program.totalCredits,
    description: program.description,
    source: program.source ?? null,
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
    contentStored: _contentStored,
    uploadBundleId: _uploadBundleId,
    sha256,
    extraction,
    ...publicDocument
  } = structuredClone(document);
  return {
    ...publicDocument,
    ...(sha256 ? { sha256 } : {}),
    ...(document.storageKey && document.contentStored !== false
      ? { contentUrl: `/v1/student/documents/${document.id}/content` }
      : {}),
    ...(extraction ? { extraction } : {}),
  };
}

function updateDocumentRequirement(draft, document, now) {
  const manuallyStoredForReview =
    (document.processingMode ??
      documentProcessingModeForCategory(document.category)) ===
      "manual_review" && document.status === "under_review";
  const completedExtraction = document.extraction?.status === "completed";
  if (!manuallyStoredForReview && !completedExtraction) return;
  if (
    completedExtraction &&
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
    if (financialDocument) {
      financialDocument.status = "under_review";
      financialDocument.version = (financialDocument.version ?? 1) + 1;
      financialDocument.updatedAt = now?.toISOString() ?? document.createdAt;
    }
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

function requirementSummary(requirement, state) {
  const reward = state ? rewardForRequirement(state, requirement) : null;
  const storedOrder = Number.isInteger(requirement.order)
    ? requirement.order
    : state
      ? state.requirements.indexOf(requirement) + 1
      : 1;
  return {
    id: requirement.id,
    code: requirement.code,
    title: requirement.title,
    description: requirement.description,
    status: requirement.status,
    blocking: requirement.blocking,
    priority: Number.isInteger(requirement.priority) ? requirement.priority : 0,
    order: Math.max(1, storedOrder),
    dueAt: requirement.dueAt,
    progressPercent: requirement.progressPercent,
    ...(reward ? { reward } : {}),
  };
}

function requirementInteractionType(requirement) {
  if (typeof requirement.interactionType === "string") {
    return requirement.interactionType;
  }
  if (requirement.submissionType === "document") return "upload_file";
  if (requirement.submissionType === "payment") return "payment";
  if (requirement.submissionType === "appointment") return "scheduling";
  if (requirement.submissionType === "none") return "information";
  return "form";
}

function requirementDetailResponse(requirement, state) {
  return {
    ...requirementSummary(requirement, state),
    slug: requirementSlug(requirement.code),
    journeyId: requirement.journeyId ?? ids.journey,
    version: requirement.version ?? 1,
    submissionType: requirement.submissionType,
    flowKind: requirement.flowKind ?? "enrollment",
    interactionType: requirementInteractionType(requirement),
    inputConfig: structuredClone(requirement.inputConfig ?? {}),
    documentCategory: documentCategoryForRequirement(requirement.code),
    responsibleOffice: requirement.responsibleOffice,
    dependencyCodes: [...requirement.dependsOnCodes],
    ...(requirement.code === "immunization_record"
      ? { immunizationPolicy: demoImmunizationPolicy(state) }
      : {}),
  };
}

function demoImmunizationPolicy(state) {
  const tenantName = state.tenant?.name ?? "Aster University";
  const tenantShortName = state.tenant?.shortName ?? "Aster";
  const tenantCode = state.tenant?.slug === "harvard" ? "HARVARD" : "ASTER";
  return {
    id: "70000000-0000-7000-8000-000000000001",
    code: `${tenantCode}-HEALTH-2027`,
    version: 1,
    name: `${tenantName} 2027 student immunization requirements`,
    effectiveFrom: "2027-01-01",
    effectiveUntil: null,
    requirements: [
      {
        id: "71000000-0000-7000-8000-000000000001",
        code: "mmr",
        name: "MMR",
        description: "Two documented MMR doses or qualifying evidence.",
        required: true,
        doseCount: 2,
        validityDays: null,
      },
      {
        id: "71000000-0000-7000-8000-000000000002",
        code: "meningococcal",
        name: "Meningococcal",
        description: "One documented meningococcal dose.",
        required: true,
        doseCount: 1,
        validityDays: null,
      },
      {
        id: "71000000-0000-7000-8000-000000000003",
        code: "covid_19",
        name: "COVID-19",
        description:
          `A documented COVID-19 vaccination is required by the ${tenantShortName} demo tenant policy.`,
        required: true,
        doseCount: 1,
        validityDays: null,
      },
      {
        id: "71000000-0000-7000-8000-000000000004",
        code: "tb_screening",
        name: "Tuberculosis screening",
        description: "A documented tuberculosis screening result.",
        required: true,
        doseCount: null,
        validityDays: null,
      },
    ],
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
      student_id_photo: "identity",
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

function completeRequirementAndRefreshDependencies(
  state,
  code,
  now = new Date(),
) {
  const requirement = state.requirements.find(
    (candidate) => candidate.code === code,
  );
  if (!requirement) return;
  const newlyCompleted = !terminalRequirementStatuses.has(requirement.status);
  requirement.status = "completed";
  requirement.progressPercent = 100;
  if (newlyCompleted) {
    awardMatchingRewards(
      state,
      "requirement_completed",
      code,
      requirement.id,
      {},
      now,
    );
  }
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

export function reconcileAuthoritativeRewards(state, now) {
  let awarded = 0;
  if (state.onboarding.status === "completed") {
    awarded += awardMatchingRewards(
      state,
      "onboarding_completed",
      "onboarding",
      state.profile.studentId,
      {},
      now,
    );
  }
  for (const requirement of state.requirements) {
    if (requirement.status !== "completed") continue;
    awarded += awardMatchingRewards(
      state,
      "requirement_completed",
      requirement.code,
      requirement.id,
      {},
      now,
    );
  }
  return awarded;
}

function awardMatchingRewards(
  state,
  triggerType,
  triggerKey,
  sourceKey,
  properties,
  now,
) {
  if (!state.rewards?.program?.enabled) return 0;
  let awarded = 0;
  for (const rule of state.rewards.rules) {
    if (
      !rule.enabled ||
      rule.triggerType !== triggerType ||
      rule.triggerKey !== triggerKey ||
      !Object.entries(rule.triggerProperties ?? {}).every(
        ([key, value]) => properties[key] === value,
      )
    ) {
      continue;
    }
    const priorAwards = state.rewards.ledger.filter(
      (entry) => entry.ruleId === rule.id,
    );
    if (
      priorAwards.length >= rule.maxAwardsPerStudent ||
      priorAwards.some((entry) => entry.sourceKey === sourceKey)
    ) {
      continue;
    }
    state.rewards.ledger.push({
      id: randomUUID(),
      ruleId: rule.id,
      sourceType: triggerType,
      sourceKey,
      points: rule.points,
      awardedAt: now.toISOString(),
    });
    awarded += rule.points;
  }
  return awarded;
}

function rewardSummary(state) {
  const lifetimePoints = state.rewards.ledger.reduce(
    (total, entry) => total + entry.points,
    0,
  );
  const pointsPerUsd = state.rewards.program.pointsPerUsd;
  return {
    pointName: state.rewards.program.pointName,
    pointsPerUsd,
    lifetimePoints,
    bookstoreCreditCents: Math.floor(
      (lifetimePoints * 100) / pointsPerUsd,
    ),
  };
}

function rewardForRequirement(state, requirement) {
  const rules = state.rewards?.rules?.filter(
    (rule) =>
      rule.enabled &&
      rule.triggerType === "requirement_completed" &&
      rule.triggerKey === requirement.code,
  );
  if (!rules?.length) return null;
  return {
    points: rules.reduce((total, rule) => total + rule.points, 0),
    earned: rules.every((rule) =>
      state.rewards.ledger.some(
        (entry) =>
          entry.ruleId === rule.id && entry.sourceKey === requirement.id,
      ),
    ),
  };
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

function isSkippableOnboardingStep(step) {
  return step === "campus_life" || step === "deposit";
}

function validateOnboardingData(input) {
  const data = objectBody(input);
  const fields = [
    "firstName",
    "lastName",
    "preferredName",
    "personalEmail",
    "mobilePhone",
    "citizenshipStatus",
    "communicationPreference",
    "residencyStatus",
    "residencyVerificationPath",
    "streetAddress",
    "addressLine2",
    "city",
    "stateOrProvince",
    "postalCode",
    "country",
    "supportNeeds",
    "accommodationInterest",
    "housingPreference",
    "housingResidenceOption",
    "housingResidencePreferences",
    "insuranceInterest",
    "housingRoomType",
    "bathroomPreference",
    "roommateMatching",
    "knownRoommateName",
    "knownRoommateEmail",
    "sleepSchedule",
    "studyHabits",
    "roomNoise",
    "cleanliness",
    "guestPreference",
    "temperaturePreference",
    "smokeVapeCompatibility",
    "substanceFreeHousing",
    "genderInclusiveHousing",
    "accessibleHousingInformation",
    "livingLearningCommunities",
    "offCampusStatus",
    "offCampusResources",
    "commuteMode",
    "commuteDuration",
    "commuterResources",
    "campusInterests",
    "socialComfort",
    "firstMonthGoals",
    "emergencyContacts",
    "familyPermissions",
    "signatureFullName",
    "signatureMethod",
    "signatureImageData",
    "signatureConsent",
    "signedDocumentIds",
    "depositChoice",
  ];
  exactKeys(data, fields);
  const result = {};
  for (const field of ["firstName", "lastName", "preferredName"]) {
    if (data[field] !== undefined) {
      result[field] = requiredString(data[field], field, {
        min: 1,
        max: 120,
      });
    }
  }
  if (data.personalEmail !== undefined) {
    const email = requiredString(data.personalEmail, "personalEmail", {
      min: 3,
      max: 254,
    }).toLowerCase();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      throw badRequest("INVALID_FIELD", "personalEmail must be valid");
    }
    result.personalEmail = email;
  }
  if (data.mobilePhone !== undefined) {
    const mobilePhone = requiredString(data.mobilePhone, "mobilePhone", {
      min: 8,
      max: 32,
    }).replace(/[ ()-]/g, "");
    const normalizedPhone = mobilePhone.startsWith("+")
      ? mobilePhone
      : `+${mobilePhone}`;
    if (!/^\+[1-9][0-9]{7,14}$/.test(normalizedPhone)) {
      throw badRequest(
        "INVALID_FIELD",
        "mobilePhone must include a valid country code",
      );
    }
    result.mobilePhone = normalizedPhone;
  }
  for (const field of [
    "substanceFreeHousing",
    "genderInclusiveHousing",
    "accessibleHousingInformation",
    "signatureConsent",
  ]) {
    if (data[field] !== undefined) {
      result[field] = booleanValue(data[field], field);
    }
  }
  if (data.citizenshipStatus !== undefined) {
    result.citizenshipStatus = enumValue(
      data.citizenshipStatus,
      "citizenshipStatus",
      [
        "us_citizen",
        "permanent_resident",
        "eligible_noncitizen",
        "international",
      ],
    );
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
  if (data.residencyVerificationPath !== undefined) {
    result.residencyVerificationPath = enumValue(
      data.residencyVerificationPath,
      "residencyVerificationPath",
      ["home_address_review", "document_upload", "advisor_review"],
    );
  }
  if (data.accommodationInterest !== undefined) {
    result.accommodationInterest = enumValue(
      data.accommodationInterest,
      "accommodationInterest",
      ["not_now", "housing", "academic", "both"],
    );
  }
  if (data.housingPreference !== undefined) {
    result.housingPreference = enumValue(
      data.housingPreference,
      "housingPreference",
      ["on_campus", "off_campus", "commuting", "undecided", "family"],
    );
  }
  if (data.housingResidenceOption !== undefined) {
    result.housingResidenceOption =
      data.housingResidenceOption === null
        ? null
        : enumValue(
            data.housingResidenceOption,
            "housingResidenceOption",
            ["aster_residence_hall", "aster_apartments", "student_village"],
          );
  }
  if (data.housingResidencePreferences !== undefined) {
    if (
      !Array.isArray(data.housingResidencePreferences) ||
      data.housingResidencePreferences.length > 3
    ) {
      throw badRequest(
        "INVALID_FIELD",
        "housingResidencePreferences must contain at most three residences",
      );
    }
    result.housingResidencePreferences = data.housingResidencePreferences.map(
      (value, index) =>
        enumValue(
          value,
          `housingResidencePreferences[${index}]`,
          ["aster_residence_hall", "aster_apartments", "student_village"],
        ),
    );
    if (
      new Set(result.housingResidencePreferences).size !==
      result.housingResidencePreferences.length
    ) {
      throw badRequest(
        "INVALID_FIELD",
        "housingResidencePreferences cannot contain duplicates",
      );
    }
  }
  if (data.insuranceInterest !== undefined) {
    result.insuranceInterest = enumValue(
      data.insuranceInterest,
      "insuranceInterest",
      ["not_now", "learn_more", "tuition", "housing", "both"],
    );
  }
  const boundedStrings = {
    streetAddress: 180,
    addressLine2: 180,
    city: 120,
    stateOrProvince: 120,
    postalCode: 32,
    country: 120,
    housingRoomType: 80,
    bathroomPreference: 80,
    roommateMatching: 80,
    knownRoommateName: 160,
    sleepSchedule: 80,
    studyHabits: 80,
    roomNoise: 80,
    cleanliness: 80,
    guestPreference: 80,
    temperaturePreference: 80,
    smokeVapeCompatibility: 80,
    offCampusStatus: 80,
    commuteMode: 80,
    commuteDuration: 80,
    socialComfort: 80,
    signatureFullName: 240,
    signatureImageData: 100000,
  };
  for (const [field, max] of Object.entries(boundedStrings)) {
    if (data[field] !== undefined) {
      result[field] = requiredString(data[field], field, { min: 1, max });
    }
  }
  if (data.signatureMethod !== undefined) {
    result.signatureMethod = enumValue(
      data.signatureMethod,
      "signatureMethod",
      ["typed", "drawn"],
    );
  }
  if (data.knownRoommateEmail !== undefined) {
    const email = requiredString(
      data.knownRoommateEmail,
      "knownRoommateEmail",
      { min: 3, max: 254 },
    ).toLowerCase();
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      throw badRequest(
        "INVALID_FIELD",
        "knownRoommateEmail must be valid",
      );
    }
    result.knownRoommateEmail = email;
  }
  for (const field of [
    "supportNeeds",
    "livingLearningCommunities",
    "offCampusResources",
    "commuterResources",
    "campusInterests",
    "firstMonthGoals",
    "signedDocumentIds",
  ]) {
    if (data[field] === undefined) continue;
    if (!Array.isArray(data[field])) {
      throw badRequest("INVALID_FIELD", `${field} must be an array`);
    }
    if (data[field].length > 12) {
      throw badRequest(
        "INVALID_FIELD",
        `${field} cannot contain more than 12 values`,
      );
    }
    result[field] = data[field].map((value, index) =>
      requiredString(value, `${field}[${index}]`, { min: 1, max: 80 }),
    );
  }
  if (data.emergencyContacts !== undefined) {
    if (
      !Array.isArray(data.emergencyContacts) ||
      data.emergencyContacts.length > 4
    ) {
      throw badRequest(
        "INVALID_FIELD",
        "emergencyContacts must contain at most four contacts",
      );
    }
    result.emergencyContacts = data.emergencyContacts.map(
      (candidate, index) => {
        const contact = objectBody(candidate);
        exactKeys(contact, ["fullName", "relationship", "mobilePhone"]);
        const phone = requiredString(
          contact.mobilePhone,
          `emergencyContacts[${index}].mobilePhone`,
          { min: 8, max: 32 },
        ).replace(/[ ()-]/g, "");
        const normalizedPhone = phone.startsWith("+") ? phone : `+${phone}`;
        if (!/^\+[1-9][0-9]{7,14}$/.test(normalizedPhone)) {
          throw badRequest(
            "INVALID_FIELD",
            `emergencyContacts[${index}].mobilePhone must include a valid country code`,
          );
        }
        return {
          fullName: requiredString(
            contact.fullName,
            `emergencyContacts[${index}].fullName`,
            { min: 1, max: 160 },
          ),
          relationship: enumValue(
            contact.relationship,
            `emergencyContacts[${index}].relationship`,
            [
              "parent",
              "guardian",
              "partner",
              "sibling",
              "relative",
              "friend",
              "other",
            ],
          ),
          mobilePhone: normalizedPhone,
        };
      },
    );
  }
  if (data.familyPermissions !== undefined) {
    if (
      !Array.isArray(data.familyPermissions) ||
      data.familyPermissions.length > 4
    ) {
      throw badRequest(
        "INVALID_FIELD",
        "familyPermissions must contain at most four people",
      );
    }
    result.familyPermissions = data.familyPermissions.map(
      (candidate, index) => {
        const permission = objectBody(candidate);
        exactKeys(permission, [
          "fullName",
          "relationship",
          "email",
          "scopes",
          "purpose",
          "expires",
        ]);
        const email = requiredString(
          permission.email,
          `familyPermissions[${index}].email`,
          { min: 3, max: 254 },
        ).toLowerCase();
        if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
          throw badRequest(
            "INVALID_FIELD",
            `familyPermissions[${index}].email must be valid`,
          );
        }
        if (
          !Array.isArray(permission.scopes) ||
          permission.scopes.length > 7
        ) {
          throw badRequest(
            "INVALID_FIELD",
            `familyPermissions[${index}].scopes must contain at most seven values`,
          );
        }
        return {
          fullName: requiredString(
            permission.fullName,
            `familyPermissions[${index}].fullName`,
            { min: 1, max: 160 },
          ),
          relationship: enumValue(
            permission.relationship,
            `familyPermissions[${index}].relationship`,
            ["parent", "guardian", "partner", "sponsor", "other"],
          ),
          email,
          scopes: permission.scopes.map((value, scopeIndex) =>
            requiredString(
              value,
              `familyPermissions[${index}].scopes[${scopeIndex}]`,
              { min: 1, max: 80 },
            ),
          ),
          purpose: enumValue(
            permission.purpose,
            `familyPermissions[${index}].purpose`,
            [
              "education_and_expenses",
              "academic_planning",
              "billing_and_aid",
              "other",
            ],
          ),
          expires: enumValue(
            permission.expires,
            `familyPermissions[${index}].expires`,
            ["end_first_year", "end_enrollment", "registrar_date"],
          ),
        };
      },
    );
  }
  if (data.depositChoice !== undefined) {
    result.depositChoice = enumValue(
      data.depositChoice,
      "depositChoice",
      ["pay_now", "pay_later", "waiver_or_deferral"],
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
      !data.firstName ||
      !data.lastName ||
      !data.preferredName ||
      !data.personalEmail ||
      !data.mobilePhone ||
      !data.citizenshipStatus ||
      !data.streetAddress ||
      !data.city ||
      !data.stateOrProvince ||
      !data.postalCode ||
      !data.country ||
      !data.communicationPreference ||
      !data.residencyStatus ||
      !data.residencyVerificationPath
    ) {
      invalid(
        "Enter your legal and preferred name, personal contact details, citizenship status, permanent home address, and residency review path",
      );
    }
    return;
  }
  if (step === "housing") {
    if (!data.housingPreference) invalid("Choose a housing preference");
    return;
  }
  if (step === "campus_life") return;
  if (step === "emergency_contacts") {
    if (!data.emergencyContacts?.length) {
      invalid("Add at least one emergency contact");
    }
    return;
  }
  if (step === "family_permissions") return;
  if (step === "review_and_sign") {
    if (
      !data.signatureFullName ||
      !["typed", "drawn"].includes(data.signatureMethod) ||
      !data.signatureConsent ||
      !Array.isArray(data.signedDocumentIds) ||
      data.signedDocumentIds.length === 0 ||
      (data.signatureMethod === "drawn" && !data.signatureImageData)
    ) {
      invalid(
        "Review the document packet, choose a signature method, and provide your electronic signature",
      );
    }
    return;
  }
  if (step === "deposit") {
    if (!data.depositChoice) {
      invalid("Choose how you would like to handle the enrollment deposit");
    }
    if (
      data.depositChoice === "pay_now" &&
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
    demoStudentId: state.profile.studentId,
  };
}
