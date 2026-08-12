import {
  SYNTHETIC_AMOUNTS,
  SYNTHETIC_CALENDAR,
  SYNTHETIC_TERM,
  searchSyntheticPolicies,
  syntheticCalendar,
} from "./synthetic-university.js";
import {
  buildDashboard,
  buildStudentAcademics,
  buildStudentFinancials,
  getHelpTopics,
  housingPlanResponse,
  listDocuments,
  listRequirements,
  profileResponse,
} from "./domain.js";
import { normalizeFinancialAidRead } from "@vv/student-assistant-core";

/**
 * Read-only shared-graph adapters for the development preview.
 *
 * The hosting route has already selected an account-specific JsonStateStore.
 * Every tool nevertheless re-validates the graph's trusted identity against
 * that store, and takes a new snapshot so website writes are visible on the
 * next assistant turn.
 */
export function createPreviewStudentAssistantTools({
  store,
  clock = () => new Date(),
}) {
  return {
    async getStudentProfile(context) {
      return readAvailable(store, context, clock, (state) =>
        profileResponse(state),
      );
    },

    async getOnboardingChecklist(context) {
      return readAcceptedStudent(store, context, clock, (state) => {
        const checklist = listRequirements(state);
        const items = checklist.items.slice(0, 64);
        return { items, total: items.length };
      });
    },

    async getDocumentStatuses(context) {
      return readAcceptedStudent(store, context, clock, (state) => {
        const documents = listDocuments(state);
        const items = documents.items.slice(0, 64);
        return { items, total: items.length };
      });
    },

    async getEnrollmentHolds(context) {
      return readAcceptedStudent(store, context, clock, (state) =>
        normalizedEnrollmentHolds(state, clock),
      );
    },

    async getStudentDeadlines(context) {
      return readAvailable(store, context, clock, (state) => {
        if (!state.offer) return unavailable("not_found", false);
        return normalizedStudentDeadlines(state, clock);
      });
    },

    async getSupportOptions(context) {
      return readAvailable(store, context, clock, (state) => {
        const help = getHelpTopics();
        return {
          articles: help.articles.slice(0, 6),
          support: {
            ...help.support,
            email: state.tenant?.supportEmail ?? help.support.email,
          },
        };
      });
    },

    async retrieveApprovedPolicy(context, request) {
      return readAcceptedStudent(store, context, clock, (state) => {
        const requirement = listRequirements(state).items.find(
          (candidate) => candidate.code === request.requirementCode,
        );
        return approvedPolicyFor(requirement) ??
          unavailable("not_configured", false);
      });
    },

    async getFinancialAidStatus(context) {
      return readAidState(store, context, clock, (state) =>
        normalizedFinancialAidStatus(state, clock),
      );
    },

    async getFinancialAidSupportOptions(context) {
      return readAidState(store, context, clock, (state) =>
        normalizedFinancialAidSupport(state),
      );
    },

    async retrieveApprovedFinancialAidPolicy(context, request) {
      return readAidState(store, context, clock, (state) => {
        const policy = (state.financialAidPolicies ?? []).find(
          (candidate) =>
            candidate.status === "published" &&
            candidate.requirementCode === request.requirementCode &&
            candidate.topic === request.topic &&
            (!candidate.academicYear ||
              candidate.academicYear === state.financials.academicYear) &&
            Date.parse(candidate.effectiveFrom) <= clock().getTime() &&
            (!candidate.effectiveUntil ||
              Date.parse(candidate.effectiveUntil) >= clock().getTime()),
        );
        return policy
          ? normalizedApprovedAidPolicy(policy)
          : unavailable("not_configured", false);
      });
    },

    async getStudentHousingStatus(context) {
      return readAcceptedStudent(store, context, clock, (state) =>
        normalizedHousingStatus(state),
      );
    },

    async getFinancialAidSummary(context) {
      return readAidState(store, context, clock, (state) =>
        syntheticAidSummary(state, clock),
      );
    },

    async getAidDisbursements(context) {
      return readAidState(store, context, clock, (state) =>
        syntheticAidDisbursements(state, clock),
      );
    },

    async getStudentHousingEligibility(context) {
      return readAcceptedStudent(store, context, clock, (state) =>
        syntheticHousingEligibility(state, clock),
      );
    },

    async getRegistrationStatus(context) {
      return readAcceptedStudent(store, context, clock, (state) =>
        syntheticRegistrationStatus(state, clock),
      );
    },

    async getStudentAccountSummary(context) {
      return readAcceptedStudent(store, context, clock, (state) =>
        syntheticAccountSummary(state),
      );
    },

    async getAcademicCalendar(context) {
      return readAvailable(store, context, clock, () => syntheticCalendar());
    },

    async getStudentAppointments(context) {
      return readAcceptedStudent(store, context, clock, (state) =>
        syntheticAppointments(state),
      );
    },

    async searchApprovedPolicies(context, request) {
      return readAvailable(store, context, clock, () => ({
        matches: searchSyntheticPolicies(request?.topic ?? ""),
      }));
    },

    async getHousingOptions(context) {
      return readAcceptedStudent(store, context, clock, (state) => ({
        items: housingPlanResponse(state).residences.slice(0, 32).map((residence) => ({
          code: residence.value,
          name: boundedText(residence.name, 180),
          description: boundedText(residence.description, 600),
          amenities: residence.amenities.slice(0, 16).map((item) => boundedText(item, 120)),
          listingState: "listed",
          synthetic: true,
        })),
      }));
    },
  };
}

export function trustedStudentAssistantIdentity(store) {
  const state = store.snapshot();
  const identity = state.auth?.demoIdentity;
  if (
    !identity ||
    typeof identity.tenantId !== "string" ||
    typeof identity.studentId !== "string" ||
    identity.studentId !== state.profile?.studentId
  ) {
    return null;
  }
  return {
    tenantId: identity.tenantId,
    studentId: identity.studentId,
  };
}

/*
 * Aid reads are deliberately not gated on an accepted offer: an admitted
 * student has an aid package to ask about before they accept, and a student
 * with no aid on file deserves "nothing on file yet, here is how to start"
 * rather than a failed read. "No data" is a valid empty state; "unavailable"
 * is reserved for reads that actually could not be performed.
 */
function readAidState(store, context, clock, project) {
  return readAvailable(store, context, clock, (state) => {
    if (!state.financials || typeof state.financials !== "object") {
      return unavailable("not_found", false);
    }
    return project(state);
  });
}

function readAcceptedStudent(store, context, clock, project) {
  return readAvailable(store, context, clock, (state) => {
    if (
      state.offer?.status !== "accepted" ||
      !state.journey ||
      !Array.isArray(state.requirements)
    ) {
      return unavailable("not_found", false);
    }
    return project(state);
  });
}

function readAvailable(store, context, clock, project) {
  const state = store.snapshot();
  if (!matchesTrustedContext(state, context)) {
    return unavailable("forbidden", false);
  }
  const projected = project(state);
  if (isUnavailable(projected)) return projected;
  return {
    status: "available",
    data: structuredClone(projected),
    observedAt: clock().toISOString(),
    sourceVersion: String(state.fixture?.revision ?? 0),
  };
}

function matchesTrustedContext(state, context) {
  const identity = state.auth?.demoIdentity;
  if (
    !context ||
    !identity ||
    context.tenantId !== identity.tenantId ||
    context.studentId !== identity.studentId ||
    state.profile?.studentId !== identity.studentId
  ) {
    return false;
  }
  return (state.assistantConversations ?? []).some(
    (conversation) => conversation.id === context.conversationId,
  );
}

function unavailable(reason, retryable) {
  return { status: "unavailable", reason, retryable };
}

function isUnavailable(value) {
  return value?.status === "unavailable" && typeof value.reason === "string";
}

function approvedPolicyFor(requirement) {
  const policy = requirement?.immunizationPolicy;
  if (!policy || requirement.code !== "immunization_record") return null;
  return {
    id: policy.id,
    requirementCode: requirement.code,
    title: policy.name,
    text: policy.requirements
      .slice(0, 32)
      .map((rule) => `${rule.name}: ${rule.description}`)
      .join(" ")
      .slice(0, 1_200),
    version: `${policy.code}:v${policy.version}`,
    effectiveFrom: policy.effectiveFrom,
    effectiveUntil: policy.effectiveUntil,
  };
}

function normalizedFinancialAidStatus(state, clock) {
  const financials = withFinancialFreshnessDefaults(
    buildStudentFinancials(state, clock),
    state,
  );
  return normalizeFinancialAidRead({
    financials,
    highLevelVerificationStatus:
      listRequirements(state).items.find(
        (item) => item.code === "financial_aid_verification",
      )?.status ?? null,
    unavailableSources: [],
  });
}

/**
 * A state file persisted before financial records carried version/updatedAt
 * would crash the aid normalizer. Repair on read with the fixture's own
 * timestamp so the answer stays honest about when the record was last written.
 */
function withFinancialFreshnessDefaults(financials, state) {
  const fallbackUpdatedAt =
    state.fixture?.updatedAt ?? state.fixture?.seededAt ?? "1970-01-01T00:00:00.000Z";
  return {
    ...financials,
    awards: financials.awards.map((award) => ({
      ...award,
      updatedAt: award.updatedAt ?? fallbackUpdatedAt,
    })),
    requiredDocuments: financials.requiredDocuments.map((item) => ({
      ...item,
      version: item.version ?? 1,
      updatedAt: item.updatedAt ?? fallbackUpdatedAt,
    })),
  };
}

function normalizedHousingStatus(state) {
  const plan = housingPlanResponse(state);
  const requirement = listRequirements(state).items.find(
    (item) => item.code === "housing_preference",
  );
  const data = state.onboarding.data;
  const roommatePreferenceState =
    plan.preference !== "on_campus"
      ? "not_applicable"
      : !data.roommateMatching
        ? "not_provided"
        : data.roommateMatching !== "known_roommate"
          ? "provided"
          : data.knownRoommateName && data.knownRoommateEmail
            ? "provided"
            : data.knownRoommateName || data.knownRoommateEmail
              ? "partially_provided"
              : "not_provided";
  const offCampusSearchStatus = [
    "lease_signed",
    "actively_looking",
    "need_roommates",
    "living_with_family",
  ].includes(data.offCampusStatus)
    ? data.offCampusStatus
    : null;
  return {
    plan: {
      preference: plan.preference,
      residencePreference: plan.residenceOption,
      updatedAt: boundedText(plan.updatedAt, 40),
      version: boundedInteger(plan.version, 0, Number.MAX_SAFE_INTEGER),
    },
    requirement: requirement
      ? {
          id: boundedText(requirement.id, 120),
          code: "housing_preference",
          title: boundedText(requirement.title, 180),
          status: requirement.status,
          dueAt: requirement.dueAt,
          progressPercent: boundedInteger(requirement.progressPercent, 0, 100),
          blocking: requirement.blocking === true,
          dependencyCodes: (requirement.dependencyCodes ?? []).slice(0, 16).map((code) => boundedText(code, 120)),
          responsibleOffice: boundedText(requirement.responsibleOffice, 180),
          supportRoute: `/enrollment/requirements/${boundedText(requirement.slug, 120)}`,
        }
      : null,
    supplementalSignals: {
      roommatePreferenceState,
      mealPlanInterest:
        plan.preference === "commuting"
          ? (data.commuterResources?.includes("meal_plan") ?? false)
          : null,
      offCampusSearchStatus:
        plan.preference === "off_campus" ? offCampusSearchStatus : null,
    },
  };
}

function normalizedFinancialAidSupport(state) {
  const configured = state.financialAidSupport ?? null;
  const help = getHelpTopics();
  return {
    financialAidSpecificConfigured: configured !== null,
    options: [
      ...(configured?.email
        ? [{ kind: "email", label: "Financial Aid email (synthetic demo)", value: boundedText(configured.email, 254) }]
        : []),
      ...(configured?.phone
        ? [{ kind: "phone", label: "Financial Aid phone (synthetic demo)", value: boundedText(configured.phone, 40) }]
        : []),
      ...(configured?.hours
        ? [{ kind: "hours", label: "Financial Aid hours (synthetic demo)", value: boundedText(configured.hours, 180) }]
        : []),
      ...(!configured
        ? [{ kind: "email", label: "Generic enrollment support email", value: boundedText(state.tenant?.supportEmail ?? help.support.email, 254) }]
        : []),
      { kind: "route", label: "Schedule financial-aid help", href: boundedPath(configured?.appointmentRoute ?? "/appointments", "/appointments") },
      { kind: "route", label: "View financial aid", href: "/financials" },
      { kind: "route", label: "Manage requested documents", href: "/documents" },
    ].slice(0, 12),
  };
}

function normalizedApprovedAidPolicy(policy) {
  const citationUrl = boundedText(policy.citationUrl, 1_000);
  if (!citationUrl.startsWith("https://")) {
    return unavailable("incomplete", false);
  }
  return {
    policyId: boundedText(policy.id, 80),
    topic: boundedText(policy.topic, 120),
    requirementCode: boundedText(policy.requirementCode, 120),
    title: boundedText(policy.title, 180),
    sourceOwner: boundedText(policy.sourceOwner, 180),
    version: `v${boundedInteger(policy.version, 1, 1_000_000)}`,
    effectiveFrom: boundedText(policy.effectiveFrom, 10),
    effectiveUntil: policy.effectiveUntil
      ? boundedText(policy.effectiveUntil, 10)
      : null,
    sectionId: boundedText(policy.sectionId, 120),
    studentVisibleText: boundedText(policy.studentVisibleText, 1_200),
    citationLabel: boundedText(policy.citationLabel, 180),
    citationUrl,
    synthetic: policy.synthetic === true,
  };
}

function normalizedStudentDeadlines(state, clock) {
  const dashboard = buildDashboard(state, clock);
  const requirements = Array.isArray(state.requirements)
    ? listRequirements(state).items.slice(0, 64)
    : [];
  const financials = buildStudentFinancials(state, clock);
  const appointments = Array.isArray(state.appointments)
    ? state.appointments.slice(0, 64)
    : [];
  return {
    items: [
      {
        id: boundedText(dashboard.offer.id, 80),
        label: "Admission offer response",
        kind: "offer_response",
        dueAt: boundedDateOnly(dashboard.offer.responseDeadline),
        duePrecision: "date",
        sourceStatus: dashboard.offer.status,
        requirementId: null,
        requirementCode: null,
        source: "admission_offer",
        completionState:
          dashboard.offer.status === "offered" ? "outstanding" : "satisfied",
        currentlyBlocking: false,
        blockingRequirement: false,
        hardOrRecommended: null,
        dependencyCodes: [],
        resolutionOwner: null,
        navigationRoute: "/dashboard",
        sourceOrder: 0,
        lastVerifiedAt: validTimestamp(dashboard.generatedAt),
        sourceVersion: `projection:${boundedInteger(
          dashboard.projectionVersion,
          0,
          Number.MAX_SAFE_INTEGER,
        )}`,
      },
      ...requirements.map((requirement, sourceOrder) => ({
        id: boundedText(requirement.id, 80),
        label: boundedText(requirement.title, 180),
        kind: requirementDeadlineKind(requirement),
        dueAt: boundedNullableDate(requirement.dueAt),
        duePrecision: "instant",
        sourceStatus: requirement.status,
        requirementId: boundedText(requirement.id, 80),
        requirementCode: boundedText(requirement.code, 120),
        source: "student_requirement",
        completionState: ["completed", "waived", "not_applicable"].includes(
          requirement.status,
        )
          ? "satisfied"
          : "outstanding",
        currentlyBlocking: requirement.status === "blocked",
        blockingRequirement: requirement.blocking === true,
        hardOrRecommended: null,
        dependencyCodes: requirement.dependencyCodes
          .slice(0, 16)
          .map((code) => boundedText(code, 120)),
        resolutionOwner: boundedText(requirement.responsibleOffice, 160),
        navigationRoute: `/enrollment/requirements/${boundedText(
          requirement.slug,
          100,
        )}`,
        sourceOrder: sourceOrder + 100,
        lastVerifiedAt: null,
        sourceVersion: null,
      })),
      ...financials.requiredDocuments.slice(0, 64).map((item, sourceOrder) => ({
        id: boundedText(item.id, 80),
        label: boundedText(item.title, 180),
        kind: "financial_aid",
        dueAt: boundedNullableDate(item.dueAt),
        duePrecision: "instant",
        sourceStatus: item.status,
        requirementId: null,
        requirementCode: boundedText(item.code, 120),
        source: "financial_document_requirement",
        completionState: item.status === "verified" ? "satisfied" : "outstanding",
        currentlyBlocking: false,
        blockingRequirement: false,
        hardOrRecommended: null,
        dependencyCodes: [],
        resolutionOwner: null,
        navigationRoute: boundedPath(item.href, "/financials"),
        sourceOrder: sourceOrder + 200,
        lastVerifiedAt: validTimestamp(financials.generatedAt),
        sourceVersion: null,
      })),
      ...appointments.map((item, sourceOrder) => ({
        id: boundedText(item.id, 80),
        label: `${appointmentLabel(item.type)} appointment`,
        kind: "appointment",
        dueAt: boundedNullableDate(item.startsAt),
        duePrecision: "instant",
        sourceStatus: item.status,
        requirementId: null,
        requirementCode: null,
        source: "student_appointment",
        completionState:
          item.status === "scheduled" ? "outstanding" : "satisfied",
        currentlyBlocking: false,
        blockingRequirement: false,
        hardOrRecommended: null,
        dependencyCodes: [],
        resolutionOwner: null,
        navigationRoute: "/appointments",
        sourceOrder: sourceOrder + 300,
        lastVerifiedAt: validTimestamp(item.createdAt),
        sourceVersion: null,
      })),
    ].slice(0, 256),
    unavailableSources: [],
  };
}

function normalizedEnrollmentHolds(state, clock) {
  const dashboard = buildDashboard(state, clock);
  const requirements = listRequirements(state).items.slice(0, 64);
  const academics = buildStudentAcademics(state, clock);
  const financials = buildStudentFinancials(state, clock);
  return {
    journey: state.journey
      ? {
          id: boundedText(dashboard.journey.id ?? "journey-status", 80),
          status: boundedText(dashboard.journey.status, 80),
          supportRoute: "/help",
          lastVerifiedAt: validTimestamp(dashboard.generatedAt),
          sourceVersion: `projection:${boundedInteger(
            dashboard.projectionVersion,
            0,
            Number.MAX_SAFE_INTEGER,
          )}`,
          domain: "enrollment",
        }
      : null,
    requirements: requirements.map((requirement, sourceOrder) => ({
      id: boundedText(requirement.id, 80),
      code: boundedText(requirement.code, 120),
      label: boundedText(requirement.title, 180),
      description: boundedText(requirement.description, 600),
      status: requirement.status,
      blockingRequirement: requirement.blocking === true,
      dueAt: boundedNullableDate(requirement.dueAt),
      progressPercent: boundedInteger(requirement.progressPercent, 0, 100),
      slug: boundedText(requirement.slug, 100),
      dependencyCodes: requirement.dependencyCodes
        .slice(0, 16)
        .map((code) => boundedText(code, 120)),
      resolutionOwner: boundedText(requirement.responsibleOffice, 160),
      submissionType: boundedText(requirement.submissionType, 80),
      supportRoute: `/enrollment/requirements/${boundedText(
        requirement.slug,
        100,
      )}`,
      sourceOrder,
      lastVerifiedAt: null,
      sourceVersion: null,
      domain: "enrollment",
    })),
    academicPlan: academics.plan.slice(0, 64).map((item, sourceOrder) => ({
      id: boundedText(item.course.id, 80),
      courseCode: boundedText(item.course.code, 80),
      label: boundedText(item.course.title, 180),
      status: boundedText(item.status, 80),
      missingPrerequisiteCodes: item.missingPrerequisiteCodes
        .slice(0, 16)
        .map((code) => boundedText(code, 80)),
      supportRoute: "/classrooms",
      sourceOrder,
      lastVerifiedAt: validTimestamp(academics.generatedAt),
      sourceVersion: boundedText(academics.catalogVersion, 120),
      domain: "course_registration",
    })),
    financialActions: financials.requiredDocuments
      .slice(0, 64)
      .map((item, sourceOrder) => ({
        id: boundedText(item.id, 80),
        code: boundedText(item.code, 120),
        label: boundedText(item.title, 180),
        status: boundedText(item.status, 80),
        supportRoute: boundedPath(item.href, "/financials"),
        sourceOrder,
        lastVerifiedAt: validTimestamp(financials.generatedAt),
        sourceVersion: null,
        domain: "financial_aid",
      })),
    unavailableSources: [],
  };
}

function requirementDeadlineKind(requirement) {
  if (requirement.code === "profile_verification") return "profile";
  if (requirement.code === "financial_aid_verification") return "financial_aid";
  if (requirement.code === "housing_preference") return "housing";
  if (requirement.code === "enrollment_deposit") return "enrollment_deposit";
  if (requirement.code === "orientation_registration") return "orientation";
  if (requirement.submissionType === "document") return "document";
  return "other_requirement";
}

function appointmentLabel(type) {
  if (type === "financial_aid") return "Financial aid";
  if (type === "enrollment_support") return "Enrollment support";
  return "Admissions counseling";
}

function boundedText(value, maximum) {
  return String(value ?? "")
    .normalize("NFKC")
    .replace(/[\u0000-\u001f\u007f]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, maximum);
}

function boundedInteger(value, minimum, maximum) {
  return Number.isFinite(value)
    ? Math.max(minimum, Math.min(maximum, Math.round(value)))
    : minimum;
}

function boundedDateOnly(value) {
  const bounded = boundedText(value, 80);
  return /^\d{4}-\d{2}-\d{2}/.test(bounded) ? bounded.slice(0, 10) : bounded;
}

function boundedNullableDate(value) {
  return value === null || value === undefined ? null : boundedText(value, 80);
}

function validTimestamp(value) {
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? new Date(timestamp).toISOString() : null;
}

function boundedPath(value, fallback) {
  const path = boundedText(value, 240);
  return path.startsWith("/") && !path.startsWith("//") ? path : fallback;
}

/* -------------------------------------------------------------------------
 * Broader university capabilities, derived from the demo store plus the
 * synthetic university dataset. Everything below is fictional demo data; see
 * `synthetic-university.js`. A real deployment replaces these derivations with
 * reads against the registrar, bursar, and housing systems.
 * ----------------------------------------------------------------------- */

/** Look up one onboarding requirement by code. */
function requirementByCode(state, code) {
  return listRequirements(state).items.find((item) => item.code === code) ?? null;
}

function isSatisfied(requirement) {
  return requirement?.status === "completed" || requirement?.status === "waived";
}

function calendarEvent(code) {
  return SYNTHETIC_CALENDAR.find((event) => event.code === code) ?? null;
}

function windowFrom(openCode, closeCode, now) {
  const opens = calendarEvent(openCode);
  const closes = calendarEvent(closeCode);
  const opensAt = opens?.startsAt ?? null;
  const closesAt = closes?.startsAt ?? null;
  const at = now.getTime();
  const open =
    opensAt === null && closesAt === null
      ? null
      : (opensAt === null || Date.parse(opensAt) <= at) &&
        (closesAt === null || Date.parse(closesAt) >= at);
  return { opensAt, closesAt, open };
}

/**
 * The deposit is the hinge of several other gates, so its state is derived once
 * here. "Posted" is deliberately distinct from "the student says they paid":
 * only a cleared payment opens the gates that depend on it.
 */
function depositState(state) {
  const requirement = requirementByCode(state, "enrollment_deposit");
  const pending = (state.payments ?? []).find(
    (payment) =>
      payment?.appliesToChargeCode === "enrollment_deposit" &&
      payment?.state === "pending",
  );
  if (isSatisfied(requirement)) return "posted";
  return pending ? "pending" : "unpaid";
}

function syntheticAccountSummary(state) {
  const deposit = depositState(state);
  const charges = [
    {
      code: "enrollment_deposit",
      label: "Enrollment deposit",
      amountUsd: SYNTHETIC_AMOUNTS.enrollmentDepositUsd,
      dueAt: requirementByCode(state, "enrollment_deposit")?.dueAt ?? null,
      state: deposit === "posted" ? "paid" : deposit === "pending" ? "pending" : "outstanding",
    },
    {
      code: "tuition",
      label: "Fall 2026 tuition",
      amountUsd: SYNTHETIC_AMOUNTS.tuitionUsd,
      dueAt: calendarEvent("tuition_due")?.startsAt ?? null,
      state: "outstanding",
    },
    {
      code: "orientation_fee",
      label: "Orientation fee",
      amountUsd: SYNTHETIC_AMOUNTS.orientationFeeUsd,
      dueAt: calendarEvent("orientation")?.startsAt ?? null,
      state: "outstanding",
    },
  ];
  const payments = [];
  if (deposit === "posted") {
    payments.push({
      id: "payment-enrollment-deposit",
      label: "Enrollment deposit",
      amountUsd: SYNTHETIC_AMOUNTS.enrollmentDepositUsd,
      state: "posted",
      postedAt: requirementByCode(state, "enrollment_deposit")?.completedAt ?? null,
      appliesToChargeCode: "enrollment_deposit",
    });
  }
  for (const payment of state.payments ?? []) {
    payments.push({
      id: boundedText(String(payment.id ?? "payment"), 80),
      label: boundedText(String(payment.label ?? "Payment"), 120),
      amountUsd: Number(payment.amountUsd ?? 0),
      state: ["posted", "pending", "failed"].includes(payment.state)
        ? payment.state
        : "pending",
      postedAt: boundedNullableDate(payment.postedAt),
      appliesToChargeCode: payment.appliesToChargeCode ?? null,
    });
  }
  const outstanding = charges
    .filter((charge) => charge.state === "outstanding")
    .reduce((total, charge) => total + charge.amountUsd, 0);
  // Only the deposit is past its due date in the demo dataset; tuition is not.
  const pastDue = deposit === "unpaid" ? SYNTHETIC_AMOUNTS.enrollmentDepositUsd : 0;
  return {
    currency: "USD",
    balanceUsd: outstanding,
    pastDueUsd: pastDue,
    charges,
    payments,
    paymentPlanEnrolled: false,
    blocksRegistration: pastDue > 250,
    nextPaymentDueAt: calendarEvent("tuition_due")?.startsAt ?? null,
    synthetic: true,
  };
}

function syntheticHousingEligibility(state, clock) {
  const now = clock();
  const deposit = depositState(state);
  const window = windowFrom(
    "housing_application_opens",
    "housing_application_closes",
    now,
  );
  const gates = [
    {
      code: "enrollment_deposit_posted",
      label: "Enrollment deposit",
      satisfied: deposit === "posted",
      reason:
        deposit === "posted"
          ? "Your enrollment deposit has posted to your student account."
          : deposit === "pending"
            ? "A deposit payment is recorded but has not posted yet, and housing opens only once it clears."
            : "The housing application opens once your enrollment deposit has posted.",
      resolutionOwner: deposit === "posted" ? null : "Office of Student Accounts",
      navigationRoute: "/financials",
      relatedRequirementCode: "enrollment_deposit",
    },
    {
      code: "housing_preference_selected",
      label: "Housing plan preference",
      satisfied: isSatisfied(requirementByCode(state, "housing_preference")),
      reason: isSatisfied(requirementByCode(state, "housing_preference"))
        ? "You have recorded a housing plan preference."
        : "You have not recorded a housing plan preference yet.",
      resolutionOwner: "Housing & Residence Life",
      navigationRoute: "/enrollment/requirements/housing-preference",
      relatedRequirementCode: "housing_preference",
    },
  ];
  const blocking = gates.filter((gate) => !gate.satisfied);
  const assignment = state.housingAssignment ?? null;
  return {
    state:
      window.open === false && window.closesAt && Date.parse(window.closesAt) < now.getTime()
        ? "closed"
        : window.open === false
          ? "not_yet_open"
          : blocking.length > 0
            ? "blocked"
            : "eligible",
    applicationWindow: window,
    gates,
    assignment: {
      state: assignment?.state ?? "not_assigned",
      residenceName: assignment?.residenceName ?? null,
      roomLabel: assignment?.roomLabel ?? null,
      moveInAt: assignment?.moveInAt ?? calendarEvent("housing_move_in")?.startsAt ?? null,
    },
    applicablePolicyCodes: [
      "housing_application_eligibility",
      "housing_first_year_residency",
    ],
    synthetic: true,
  };
}

function syntheticRegistrationStatus(state, clock) {
  const now = clock();
  const account = syntheticAccountSummary(state);
  const immunization = requirementByCode(state, "immunization_record");
  const transcript = requirementByCode(state, "official_transcript");
  const advisingDone = (state.appointments ?? []).some(
    (appointment) =>
      appointment?.type === "advising" && appointment?.status === "completed",
  );
  const gates = [
    {
      code: "account_balance",
      label: "Student account balance",
      satisfied: !account.blocksRegistration,
      reason: account.blocksRegistration
        ? `A past-due balance over $250 places a billing hold that prevents registration. $${account.pastDueUsd.toFixed(2)} is past due.`
        : "Your account has no past-due balance large enough to block registration.",
      resolutionOwner: "Office of Student Accounts",
      navigationRoute: "/financials",
      relatedRequirementCode: "enrollment_deposit",
    },
    {
      code: "immunization_cleared",
      label: "Immunisation record",
      satisfied: isSatisfied(immunization),
      reason: isSatisfied(immunization)
        ? "Student Health Services has cleared your immunisation record."
        : "Student Health Services must review and clear your immunisation record before you can register. A submitted record that has not been reviewed does not clear this gate.",
      resolutionOwner: "Student Health Services",
      navigationRoute: "/enrollment/requirements/immunization-record",
      relatedRequirementCode: "immunization_record",
    },
    {
      code: "advising_complete",
      label: "Advising meeting",
      satisfied: advisingDone,
      reason: advisingDone
        ? "Your required advising meeting is complete."
        : "New students meet an academic adviser once before registering; no completed advising meeting is on your record.",
      resolutionOwner: "Academic Advising",
      navigationRoute: "/appointments",
      relatedRequirementCode: null,
    },
    {
      code: "final_transcript",
      label: "Final official transcript",
      satisfied: isSatisfied(transcript),
      // The unsatisfied wording has to hold for a transcript that arrived and is
      // still with the registrar, not only for one that was never sent. Saying
      // "has not been received" there would be the very contradiction the
      // document projection exists to prevent -- and it is a fact, so no guard
      // downstream would catch it. The immunisation gate below was already
      // worded this way; this one was not.
      reason: isSatisfied(transcript)
        ? "Your final official transcript has been received and accepted."
        : "Your final official transcript has not been accepted yet. A transcript that has been submitted but not yet reviewed does not clear this gate. It blocks registration after the add/drop deadline.",
      resolutionOwner: "Office of Admissions",
      navigationRoute: "/enrollment/requirements/official-transcript",
      relatedRequirementCode: "official_transcript",
    },
  ];
  const window = windowFrom("registration_opens_new_students", "registration_closes", now);
  const blocking = gates.filter((gate) => !gate.satisfied);
  return {
    termCode: SYNTHETIC_TERM.code,
    termName: SYNTHETIC_TERM.name,
    state:
      blocking.length > 0
        ? "blocked"
        : window.open === false && window.opensAt && Date.parse(window.opensAt) > now.getTime()
          ? "not_yet_open"
          : window.open === false
            ? "closed"
            : "eligible",
    registrationWindow: window,
    gates,
    registeredCreditCount: 0,
    registeredCourseCount: 0,
    minimumCredits: 12,
    maximumCredits: 18,
    advisingRequired: true,
    advisingHoldCleared: advisingDone,
    synthetic: true,
  };
}

function syntheticAppointments(state) {
  const scheduled = (state.appointments ?? []).slice(0, 16).map((appointment, index) => ({
    id: boundedText(String(appointment.id ?? `appointment-${index}`), 80),
    kind: ["advising", "orientation", "financial_aid", "housing", "international"].includes(
      appointment.type,
    )
      ? appointment.type
      : "advising",
    label: boundedText(appointmentLabel(appointment.type), 160),
    startsAt: validTimestamp(appointment.startsAt)
      ? appointment.startsAt
      : new Date(0).toISOString(),
    endsAt: boundedNullableDate(appointment.endsAt),
    location: appointment.location ? boundedText(String(appointment.location), 160) : null,
    state: ["scheduled", "completed", "cancelled", "no_show"].includes(appointment.status)
      ? appointment.status
      : "scheduled",
    withWhom: appointment.withWhom ? boundedText(String(appointment.withWhom), 160) : null,
  }));
  return {
    scheduled,
    bookingRoutes: [
      { kind: "advising", label: "Academic advising appointment", href: "/appointments" },
      { kind: "financial_aid", label: "Financial aid appointment", href: "/appointments" },
    ],
    synthetic: true,
  };
}

/* -------------------------------------------------------------------------
 * Financial aid, derived from the demo store plus the synthetic dataset.
 *
 * These two reads exist because the pre-existing aid read answers only "what is
 * outstanding?". Students also ask how much aid they have, whether their FAFSA
 * arrived, when the money actually moves, and whether any of it covers the
 * bill. Splitting those four families across two reads -- one for the package,
 * one for the money movement -- keeps the tool surface small enough that the
 * planner can choose between them.
 * ----------------------------------------------------------------------- */

const AID_DISBURSEMENT_SCHEDULE = [
  { termCode: "2026FA", code: "aid_disbursement_fall", share: 0.5 },
  { termCode: "2027SP", code: "aid_disbursement_spring", share: 0.5 },
];

function centsToUsd(cents) {
  return Math.round(Number(cents ?? 0)) / 100;
}

/** Work-study is earned by working; it never credits the bill up front. */
function awardAppliesToBill(award) {
  return award?.type !== "work_study";
}

function aidRequirementByCode(state, code) {
  return (state.financials?.requiredDocuments ?? []).find(
    (item) => item.code === code,
  ) ?? null;
}

/**
 * FAFSA state, read from the aid document record rather than inferred. The
 * distinction between "received" and "selected for verification" is the whole
 * answer to several of the questions students ask, so it is never collapsed.
 */
function fafsaStatusFrom(state) {
  const fafsa = aidRequirementByCode(state, "fafsa");
  if (!fafsa) return { status: "unknown", receivedAt: null };
  const verification = aidRequirementByCode(state, "verification_worksheet");
  const receivedAt = validTimestamp(fafsa.updatedAt);
  switch (fafsa.status) {
    case "not_started":
    case "action_required":
      return { status: "not_received", receivedAt: null };
    case "rejected":
      return { status: "rejected", receivedAt };
    case "verified":
    case "submitted":
    case "under_review": {
      if (!verification) return { status: "received", receivedAt };
      if (verification.status === "verified") {
        return { status: "verification_complete", receivedAt };
      }
      return { status: "selected_for_verification", receivedAt };
    }
    default:
      return { status: "unknown", receivedAt };
  }
}

/**
 * An aid package is only "finalized" once nothing is outstanding and no award
 * still needs a decision. Saying otherwise turns an estimate into a promise.
 */
function aidPackageState(state, awards, fafsaStatus) {
  if (awards.length === 0) {
    return fafsaStatus === "not_received" ? "no_application" : "not_packaged";
  }
  const outstanding = (state.financials?.requiredDocuments ?? []).filter(
    (item) => item.status !== "verified" && item.status !== "waived",
  );
  const undecided = awards.some((award) => award.requiresAction);
  return outstanding.length > 0 || undecided ? "estimated" : "finalized";
}

function syntheticAidSummary(state, clock) {
  const financials = buildStudentFinancials(state, clock);
  const fafsa = fafsaStatusFrom(state);
  const awards = (financials.awards ?? []).map((award) => ({
    id: boundedText(award.id, 80),
    name: boundedText(award.name, 180),
    awardType: ["grant", "scholarship", "loan", "work_study"].includes(award.type)
      ? award.type
      : "grant",
    source: award.source ? boundedText(award.source, 80) : null,
    offeredUsd: centsToUsd(award.offeredAmountCents),
    acceptedUsd: centsToUsd(award.acceptedAmountCents),
    status: ["offered", "accepted", "declined", "pending", "cancelled"].includes(
      award.status,
    )
      ? award.status
      : "unknown",
    requiresAction: award.requiresAction === true,
    appliesToBill: awardAppliesToBill(award),
  }));

  const totals = {
    offeredUsd: awards.reduce((sum, award) => sum + award.offeredUsd, 0),
    acceptedUsd: awards.reduce((sum, award) => sum + award.acceptedUsd, 0),
    declinedUsd: awards
      .filter((award) => award.status === "declined")
      .reduce((sum, award) => sum + award.offeredUsd, 0),
    pendingUsd: awards
      .filter((award) => ["offered", "pending"].includes(award.status))
      .reduce((sum, award) => sum + award.offeredUsd, 0),
  };

  // Only aid that credits the bill reduces what the student owes. Counting
  // work-study here would tell someone their bill is covered by money they
  // have not earned yet.
  const aidAppliedUsd = awards
    .filter((award) => award.appliesToBill && award.status === "accepted")
    .reduce((sum, award) => sum + award.acceptedUsd, 0);
  const costOfAttendanceUsd = centsToUsd(financials.costOfAttendanceCents);
  const paymentsUsd = centsToUsd(financials.paymentsCents);
  const remainingBalanceUsd = Math.max(
    0,
    Number((costOfAttendanceUsd - aidAppliedUsd - paymentsUsd).toFixed(2)),
  );
  const estimatedRefundUsd = Math.max(
    0,
    Number((aidAppliedUsd + paymentsUsd - costOfAttendanceUsd).toFixed(2)),
  );

  const packageState = aidPackageState(state, awards, fafsa.status);
  const verification = aidRequirementByCode(state, "verification_worksheet");
  const gates = [
    {
      code: "fafsa_received",
      label: "FAFSA on file",
      satisfied: !["not_received", "rejected", "unknown"].includes(fafsa.status),
      reason:
        fafsa.status === "not_received"
          ? "No FAFSA has been received for this aid year, and it is what starts your aid package."
          : fafsa.status === "rejected"
            ? "Your FAFSA was rejected and has to be corrected before it can be used."
            : "A FAFSA is on file for this aid year.",
      resolutionOwner: "Financial Aid",
      navigationRoute: "/financials",
      relatedRequirementCode: "fafsa",
    },
    // Only surfaced while unsatisfied so an accepted student's summary is
    // unchanged; before acceptance it names the step that finalizes a package.
    ...(state.offer?.status !== "accepted"
      ? [
          {
            code: "offer_accepted",
            label: "Enrollment offer accepted",
            satisfied: false,
            reason:
              "Your aid package is finalized after you accept your enrollment offer.",
            resolutionOwner: "Admissions",
            navigationRoute: "/enrollment",
            relatedRequirementCode: null,
          },
        ]
      : []),
    ...(verification
      ? [
          {
            code: "verification_complete",
            label: "Verification",
            satisfied: verification.status === "verified" || verification.status === "waived",
            reason:
              verification.status === "verified" || verification.status === "waived"
                ? "Verification is complete."
                : verification.status === "under_review" || verification.status === "submitted"
                  ? "Your verification worksheet has been submitted and is with Financial Aid for review."
                  : "Financial Aid still needs your signed verification worksheet before your aid can be finalized.",
            resolutionOwner: "Financial Aid",
            navigationRoute: "/documents",
            relatedRequirementCode: "verification_worksheet",
          },
        ]
      : []),
    ...(awards.some((award) => award.requiresAction)
      ? [
          {
            code: "award_decisions",
            label: "Award decisions",
            satisfied: false,
            reason:
              "One or more awards are still offered and need you to accept or decline them.",
            resolutionOwner: "Financial Aid",
            navigationRoute: "/financials",
            relatedRequirementCode: "award_acceptance",
          },
        ]
      : []),
  ];

  return {
    aidYear: financials.academicYear ? boundedText(financials.academicYear, 40) : null,
    fafsaStatus: fafsa.status,
    fafsaReceivedAt: fafsa.receivedAt,
    packageState,
    awards,
    totals,
    coverage: {
      costOfAttendanceUsd,
      aidAppliedUsd,
      remainingBalanceUsd,
      coversFullCost: remainingBalanceUsd === 0,
      estimatedRefundUsd,
      includesEstimatedAid: packageState === "estimated",
    },
    gates,
    sapStatus: ["meeting", "warning", "probation", "suspension"].includes(
      financials.sap?.status,
    )
      ? financials.sap.status
      : "unknown",
    synthetic: true,
  };
}

/**
 * Disbursement is where "my aid is approved" and "my aid is in my account"
 * come apart, which is exactly the gap students write in about. Nothing is
 * reported as scheduled unless the award behind it was actually accepted.
 */
function syntheticAidDisbursements(state, clock) {
  const summary = syntheticAidSummary(state, clock);
  const now = clock();
  const creditable = summary.awards.filter(
    (award) => award.appliesToBill && award.status === "accepted",
  );
  const gates = summary.gates.filter((gate) => !gate.satisfied);
  const enrollmentGate = {
    code: "enrollment_confirmed",
    label: "Enrollment confirmed",
    satisfied: depositState(state) === "posted",
    reason:
      depositState(state) === "posted"
        ? "Your enrollment is confirmed, so aid can pay out against your account."
        : "Aid pays out only after your enrollment deposit posts and confirms your place.",
    resolutionOwner: "Office of Student Accounts",
    navigationRoute: "/financials",
    relatedRequirementCode: "enrollment_deposit",
  };
  const allGates = [...gates, ...(enrollmentGate.satisfied ? [] : [enrollmentGate])];

  const items = creditable.flatMap((award) =>
    AID_DISBURSEMENT_SCHEDULE.map((term) => {
      const scheduledFor = calendarEvent(term.code)?.startsAt ?? null;
      const disbursed =
        allGates.length === 0 &&
        scheduledFor !== null &&
        Date.parse(scheduledFor) <= now.getTime();
      return {
        id: `${award.id}:${term.termCode}`,
        awardId: award.id,
        awardName: award.name,
        amountUsd: Number((award.acceptedUsd * term.share).toFixed(2)),
        termCode: term.termCode,
        scheduledFor,
        disbursedAt: disbursed ? scheduledFor : null,
        state: disbursed ? "disbursed" : allGates.length > 0 ? "held" : "scheduled",
        holdReasons: allGates.map((gate) => gate.reason),
      };
    }),
  );

  const scheduled = items.filter((item) => item.state !== "disbursed");
  return {
    aidYear: summary.aidYear,
    items,
    totalDisbursedUsd: Number(
      items
        .filter((item) => item.state === "disbursed")
        .reduce((sum, item) => sum + item.amountUsd, 0)
        .toFixed(2),
    ),
    totalScheduledUsd: Number(
      scheduled.reduce((sum, item) => sum + item.amountUsd, 0).toFixed(2),
    ),
    nextScheduledFor:
      scheduled
        .map((item) => item.scheduledFor)
        .filter((value) => value !== null)
        .sort()[0] ?? null,
    gates: allGates,
    firstDisbursementDate: calendarEvent("aid_disbursement_fall")?.startsAt ?? null,
    synthetic: true,
  };
}
