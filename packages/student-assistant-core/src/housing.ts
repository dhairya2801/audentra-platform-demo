import type { RequirementStatus } from "@vv/contracts";
import type {
  HousingNextAction,
  HousingOptionsRead,
  HousingPlanRequirementState,
  HousingResult,
  StudentAssistantUnavailableData,
  StudentDeadline,
  StudentHousingStatusRead,
  StudentSupportOption,
} from "./contracts";

export interface DeriveHousingResultInput {
  status: StudentHousingStatusRead | null;
  statusReceiptId: string | null;
  options: HousingOptionsRead | null;
  optionsReceiptId: string | null;
  deadlines: readonly StudentDeadline[];
  supportOptions: readonly StudentSupportOption[];
  unavailableData: readonly StudentAssistantUnavailableData[];
}

export function deriveHousingResult(
  input: DeriveHousingResultInput,
): HousingResult {
  const requirementState = input.status
    ? housingRequirementState(
        input.status.plan.preference !== null,
        input.status.requirement?.status ?? null,
      )
    : "unknown";
  const route = input.status?.requirement?.supportRoute ?? null;
  const remainingSteps = input.status && input.statusReceiptId
    ? housingRemainingSteps(
        input.status,
        requirementState,
        input.statusReceiptId,
        route,
      )
    : [];
  const nextAction = input.status && input.statusReceiptId
    ? housingNextAction(
        input.status,
        requirementState,
        remainingSteps,
        input.statusReceiptId,
        route,
      )
    : {
        label: "Try again or use general support because housing status could not be verified",
        reasonCode: "data_unavailable" as const,
        navigationRoute: null,
        contextReceiptIds: [],
      };
  const contextReceiptIds = [
    ...(input.statusReceiptId ? [input.statusReceiptId] : []),
    ...(input.optionsReceiptId ? [input.optionsReceiptId] : []),
    ...input.deadlines.flatMap((deadline) => deadline.contextReceiptIds),
    ...input.supportOptions.flatMap((option) => option.contextReceiptIds),
  ];
  return {
    applicationStatus: "unavailable",
    planRequirementState: requirementState,
    planStatus: !input.status
      ? "unknown"
      : requirementState === "conflicting"
        ? "conflicting"
        : input.status.plan.preference
          ? "selected"
          : "not_selected",
    housingOptionType: input.status?.plan.preference ?? null,
    residencePreferenceState:
      input.status?.plan.preference !== "on_campus"
        ? "not_applicable"
        : input.status.plan.residencePreference
          ? "selected"
          : "not_selected",
    residencePreference: input.status?.plan.residencePreference ?? null,
    depositStatus: "unavailable",
    agreementStatus: "unavailable",
    assignmentStatus: "unavailable",
    waitlistStatus: "unavailable",
    roommatePreferenceState:
      input.status?.supplementalSignals.roommatePreferenceState ?? "not_applicable",
    mealPlanStatus: "unavailable",
    remainingSteps,
    deadlines: [...input.deadlines],
    options: input.options?.items ?? [],
    nextAction,
    supportOptions: [
      ...(input.status?.requirement?.responsibleOffice
        ? [
            {
              kind: "office" as const,
              label: "Responsible office",
              value: input.status.requirement.responsibleOffice,
            },
          ]
        : []),
      ...(route
        ? [
            {
              kind: "route" as const,
              label: "Housing requirement",
              href: route,
            },
          ]
        : []),
      ...input.supportOptions.flatMap((option) =>
        option.kind === "article"
          ? []
          : [
              {
                kind: option.kind,
                label: `General support ${option.kind}`,
                value: option.value,
              },
            ],
      ),
    ],
    unavailableData: [...input.unavailableData],
    contextReceiptIds: [...new Set(contextReceiptIds)],
  };
}

export function housingRequirementState(
  hasPlan: boolean,
  status: RequirementStatus | null,
): HousingPlanRequirementState {
  if (status === null) return "unknown";
  if (status === "waived") return "waived";
  if (status === "not_applicable") return "not_applicable";
  if (status === "completed") return hasPlan ? "complete" : "conflicting";
  return hasPlan ? "conflicting" : "incomplete";
}

function housingRemainingSteps(
  status: StudentHousingStatusRead,
  requirementState: HousingPlanRequirementState,
  receiptId: string,
  route: string | null,
): HousingResult["remainingSteps"] {
  if (
    requirementState === "complete" ||
    requirementState === "waived" ||
    requirementState === "not_applicable"
  ) {
    return [];
  }
  if (!status.plan.preference) {
    return [
      {
        code: "select_housing_plan",
        label: "Select your housing plan preference",
        blocked: status.requirement?.status === "blocked",
        navigationRoute: route,
        contextReceiptIds: [receiptId],
      },
    ];
  }
  return [
    {
      code: "complete_housing_requirement",
      label: status.requirement?.title ?? "Complete the housing requirement",
      blocked: status.requirement?.status === "blocked",
      navigationRoute: route,
      contextReceiptIds: [receiptId],
    },
  ];
}

function housingNextAction(
  status: StudentHousingStatusRead,
  requirementState: HousingPlanRequirementState,
  remainingSteps: HousingResult["remainingSteps"],
  receiptId: string,
  route: string | null,
): HousingNextAction {
  if (requirementState === "unknown") {
    return {
      label: "Contact general support because the housing requirement could not be verified",
      reasonCode: "data_unavailable",
      navigationRoute: route,
      contextReceiptIds: [receiptId],
    };
  }
  if (remainingSteps[0]) {
    const conflictingRecord = requirementState === "conflicting";
    return {
      label: conflictingRecord
        ? "Contact Housing & Residence Life to resolve the conflicting housing record"
        : remainingSteps[0].label,
      reasonCode: conflictingRecord
        ? "resolve_conflict"
        : status.plan.preference
          ? "complete_requirement"
          : "select_plan",
      navigationRoute: route,
      contextReceiptIds: [receiptId],
    };
  }
  return {
    label: "No housing checklist action is currently required",
    reasonCode: "no_action",
    navigationRoute: route,
    contextReceiptIds: [receiptId],
  };
}
