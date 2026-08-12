import type {
  RequirementStatus,
  StudentFinancials,
} from "@vv/contracts";
import type {
  AidChecklistRead,
  AidVerificationStatus,
  FinancialAidEntity,
  FinancialAidNextAction,
  FinancialAidResult,
  MissingAidDocument,
  NormalizedAidRequirement,
  StudentAssistantUnavailableData,
  StudentDeadlineWindow,
} from "./contracts";
import { deriveStudentDeadlines } from "./deterministic";

const satisfiedRequirementStatuses = new Set(["satisfied"]);
const blockedRequirementStatuses = new Set(["submitted", "under_review"]);
const readyRequirementStatuses = new Set([
  "not_started",
  "action_required",
  "rejected",
]);

export function normalizeFinancialAidRead(input: {
  financials: Pick<
    StudentFinancials,
    "academicYear" | "requiredDocuments" | "awards"
  >;
  highLevelVerificationStatus: RequirementStatus | null;
  unavailableSources?: AidChecklistRead["unavailableSources"];
}): AidChecklistRead {
  const academicYear = input.financials.academicYear.slice(0, 20);
  const items = input.financials.requiredDocuments.slice(0, 64).map((item) => ({
    id: item.id.slice(0, 80),
    code: item.code.slice(0, 120),
    kind:
      item.code === "fafsa"
        ? ("fafsa" as const)
        : item.code === "verification_worksheet"
          ? ("verification_document" as const)
          : item.code === "award_acceptance"
            ? ("award_acceptance" as const)
            : ("institutional_form" as const),
    label: item.title.slice(0, 180),
    status: item.status === "verified" ? ("satisfied" as const) : item.status,
    dueAt: validIsoDate(item.dueAt),
    navigationRoute: safePortalRoute(item.href, "/financials"),
    policyTopic: item.code.slice(0, 120),
    authoritativeDocumentState:
      item.status === "verified"
        ? ("accepted" as const)
        : item.status === "under_review"
          ? ("under_review" as const)
          : item.status === "submitted"
            ? ("uploaded" as const)
            : ("none" as const),
    lastVerifiedAt: validIsoDate(item.updatedAt),
    sourceVersion: `version:${Math.max(1, Math.trunc(item.version))}`,
  }));
  const awards = input.financials.awards.slice(0, 64).map((award) => {
    const partiallyAccepted =
      award.type === "loan" &&
      award.status === "accepted" &&
      award.acceptedAmountCents > 0 &&
      award.acceptedAmountCents < award.offeredAmountCents;
    return {
      awardId: award.id.slice(0, 80),
      awardLabel: award.name.slice(0, 180),
      awardType: award.type,
      status: partiallyAccepted ? ("pending" as const) : award.status,
      requiresAction: partiallyAccepted || award.requiresAction === true,
      academicYear,
      lastVerifiedAt: validIsoDate(award.updatedAt),
      sourceVersion: `updated:${award.updatedAt.slice(0, 40)}`,
    };
  });
  return {
    academicYear,
    overallStatus:
      items.every((item) => item.status === "satisfied") &&
      !awards.some(
        (award) =>
          award.requiresAction &&
          award.status !== "accepted" &&
          award.status !== "declined",
      )
        ? "complete"
        : "incomplete",
    items,
    awards,
    highLevelVerificationStatus: input.highLevelVerificationStatus,
    unavailableSources: input.unavailableSources ?? [],
  };
}

export function deriveFinancialAidResult(input: {
  read: AidChecklistRead;
  receiptId: string;
  receiptObservedAt: string | null;
  receiptSourceVersion: string | null;
  institutionalTimeZone: string | null | undefined;
  now: Date;
  window: StudentDeadlineWindow;
  entity: FinancialAidEntity | null;
}): FinancialAidResult {
  const allCompletedRequirements = input.read.items.filter((item) =>
    satisfiedRequirementStatuses.has(item.status),
  );
  const allRemainingRequirements = input.read.items.filter(
    (item) => !satisfiedRequirementStatuses.has(item.status),
  );
  const completedRequirements = allCompletedRequirements.filter((item) =>
    financialAidRequirementMatchesEntity(item, input.entity),
  );
  const remainingRequirements = allRemainingRequirements.filter((item) =>
    financialAidRequirementMatchesEntity(item, input.entity),
  );
  const blockedRequirements = remainingRequirements.filter((item) =>
    blockedRequirementStatuses.has(item.status),
  );
  const readyRequirements = remainingRequirements.filter((item) =>
    readyRequirementStatuses.has(item.status),
  );
  const missingDocuments = deriveMissingAidDocuments(
    readyRequirements,
    input.receiptId,
  );
  const verificationStatus = deriveAidVerificationStatus(input.read);
  const unavailableData = unavailableFinancialAidData(input.read);

  const highLevelComplete = ["completed", "waived", "not_applicable"].includes(
    input.read.highLevelVerificationStatus ?? "",
  );
  if (
    highLevelComplete &&
    input.read.items.some(
      (item) =>
        item.kind === "verification_document" && item.status !== "satisfied",
    )
  ) {
    unavailableData.push({
      source: "getFinancialAidStatus",
      reason: "conflicting_data",
      retryable: false,
    });
  }

  const deadlines = deriveStudentDeadlines({
    items: input.read.items.map((item, sourceOrder) => ({
      id: item.id,
      label: item.label,
      kind: "financial_aid" as const,
      dueAt: item.dueAt,
      duePrecision: "instant" as const,
      sourceStatus: item.status,
      requirementId: item.id,
      requirementCode: item.code,
      source: "financial_document_requirement" as const,
      completionState:
        item.status === "satisfied" ? ("satisfied" as const) : ("outstanding" as const),
      currentlyBlocking: blockedRequirementStatuses.has(item.status),
      blockingRequirement: false,
      hardOrRecommended: null,
      dependencyCodes: [],
      resolutionOwner: null,
      navigationRoute: item.navigationRoute,
      sourceOrder,
      lastVerifiedAt: item.lastVerifiedAt,
      sourceVersion: item.sourceVersion,
    })),
    receiptId: input.receiptId,
    receiptObservedAt: input.receiptObservedAt,
    receiptSourceVersion: input.receiptSourceVersion,
    institutionalTimeZone: input.institutionalTimeZone,
    now: input.now,
    window: input.window,
  }).visible.filter((deadline) => aidDeadlineMatchesEntity(deadline.requirementCode, input.entity));

  const nextAction = deriveFinancialAidNextAction({
    readyRequirements,
    blockedRequirements,
    awards: input.read.awards,
    receiptId: input.receiptId,
    unavailable: unavailableData.length > 0 && input.read.items.length === 0,
  });
  const status = deriveOverallAidStatus(input.read, allRemainingRequirements);

  return {
    status,
    completedRequirements,
    remainingRequirements,
    blockedRequirements,
    readyRequirements,
    missingDocuments,
    verificationStatus,
    awardAcceptanceStatuses: input.read.awards,
    deadlines,
    nextAction,
    supportOptions: [],
    policyExplanation: null,
    policyContextReceiptId: null,
    unavailableData: deduplicateUnavailable(unavailableData),
    contextReceiptIds: [input.receiptId],
  };
}

export function financialAidRequirementMatchesEntity(
  requirement: NormalizedAidRequirement,
  entity: FinancialAidEntity | null,
): boolean {
  if (!entity || entity === "financial_aid") return true;
  if (entity === "requested_financial_aid_documents") {
    return requirement.kind !== "award_acceptance";
  }
  if (entity === "financial_aid_verification") {
    return requirement.kind === "verification_document";
  }
  return requirement.code === entity;
}

function deriveMissingAidDocuments(
  requirements: readonly NormalizedAidRequirement[],
  receiptId: string,
): MissingAidDocument[] {
  return requirements
    .filter(
      (item) =>
        item.kind !== "award_acceptance" &&
        item.authoritativeDocumentState !== "accepted",
    )
    .map((item) => ({
      requirementId: item.id,
      requirementCode: item.code,
      label: item.label,
      requirementStatus: item.status,
      authoritativeDocumentState: item.authoritativeDocumentState,
      dueAt: item.dueAt,
      navigationRoute: item.navigationRoute,
      contextReceiptIds: [receiptId],
    }));
}

function deriveAidVerificationStatus(read: AidChecklistRead): AidVerificationStatus {
  const worksheet = read.items.find(
    (item) => item.code === "verification_worksheet",
  );
  if (!worksheet) {
    return read.highLevelVerificationStatus === "not_applicable"
      ? "not_required"
      : "unknown";
  }
  const mapped: Record<NormalizedAidRequirement["status"], AidVerificationStatus> = {
    not_started: "action_required",
    action_required: "action_required",
    submitted: "submitted",
    under_review: "under_review",
    satisfied: "verified",
    rejected: "needs_correction",
    unknown: "unknown",
  };
  return mapped[worksheet.status];
}

function deriveOverallAidStatus(
  read: AidChecklistRead,
  remaining: readonly NormalizedAidRequirement[],
): FinancialAidResult["status"] {
  if (read.overallStatus === "unknown" || read.items.length === 0) return "unknown";
  if (
    remaining.length === 0 &&
    !read.awards.some(
      (award) => award.requiresAction && award.status !== "accepted" && award.status !== "declined",
    )
  ) {
    return "complete";
  }
  return "incomplete";
}

function deriveFinancialAidNextAction(input: {
  readyRequirements: readonly NormalizedAidRequirement[];
  blockedRequirements: readonly NormalizedAidRequirement[];
  awards: AidChecklistRead["awards"];
  receiptId: string;
  unavailable: boolean;
}): FinancialAidNextAction | null {
  if (input.unavailable) {
    return {
      id: "financial-aid-data-unavailable",
      label: "Use Financial Aid support",
      reasonCode: "data_unavailable",
      navigationRoute: "/appointments",
      contextReceiptIds: [input.receiptId],
    };
  }
  const rejected = input.readyRequirements.find((item) => item.status === "rejected");
  if (rejected) return requirementAction(rejected, "correct_rejected_document", input.receiptId);
  const required = input.readyRequirements.find(
    (item) => item.kind !== "award_acceptance",
  );
  if (required) return requirementAction(required, "submit_required_document", input.receiptId);
  const awardRequirement = input.readyRequirements.find(
    (item) => item.kind === "award_acceptance",
  );
  const actionableAward = input.awards.find(
    (award) =>
      award.requiresAction && award.status !== "accepted" && award.status !== "declined",
  );
  if (awardRequirement || actionableAward) {
    return {
      id: awardRequirement?.id ?? actionableAward!.awardId,
      label: awardRequirement?.label ?? `Review ${actionableAward!.awardLabel}`,
      reasonCode: "accept_or_decline_award",
      navigationRoute: awardRequirement?.navigationRoute ?? "/financials",
      contextReceiptIds: [input.receiptId],
    };
  }
  const review = input.blockedRequirements[0];
  if (review) {
    return requirementAction(review, "await_verification_review", input.receiptId);
  }
  return null;
}

function requirementAction(
  requirement: NormalizedAidRequirement,
  reasonCode: FinancialAidNextAction["reasonCode"],
  receiptId: string,
): FinancialAidNextAction {
  return {
    id: requirement.id,
    label: requirement.label,
    reasonCode,
    navigationRoute: requirement.navigationRoute,
    contextReceiptIds: [receiptId],
  };
}

function aidDeadlineMatchesEntity(
  requirementCode: string | null,
  entity: FinancialAidEntity | null,
): boolean {
  if (!entity || entity === "financial_aid" || entity === "requested_financial_aid_documents") {
    return true;
  }
  if (entity === "financial_aid_verification") {
    return requirementCode === "verification_worksheet";
  }
  return requirementCode === entity;
}

function unavailableFinancialAidData(
  read: AidChecklistRead,
): StudentAssistantUnavailableData[] {
  return read.unavailableSources.map((source) => ({
    source: "getFinancialAidStatus",
    reason: source.reason,
    retryable: source.retryable,
  }));
}

function deduplicateUnavailable(
  values: readonly StudentAssistantUnavailableData[],
): StudentAssistantUnavailableData[] {
  return [...new Map(values.map((item) => [`${item.source}:${item.reason}`, item])).values()];
}

function validIsoDate(value: string | null): string | null {
  if (!value) return null;
  return Number.isFinite(Date.parse(value)) ? value.slice(0, 40) : null;
}

function safePortalRoute(value: string, fallback: string): string {
  return value.startsWith("/") && !value.startsWith("//")
    ? value.slice(0, 240)
    : fallback;
}
