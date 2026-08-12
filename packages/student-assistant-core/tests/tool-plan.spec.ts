import { describe, expect, it } from "vitest";
import { validateModelToolPlan } from "../src/model";

const basePlan = {
  requestType: "housing_status",
  additionalRequestTypes: [] as string[],
  confidence: 0.92,
  requirementReference: null,
  toolNames: ["getStudentHousingStatus"],
  deadlineWindow: null,
  requestedEntity: null,
  financialAidEntity: null,
  housingEntity: null,
  blockerScope: null,
  priorityExplanationRequested: false,
  registrationQuestion: false,
};

describe("model tool plan validation", () => {
  it("lets any intent draw on shared context reads for causal questions", () => {
    const plan = validateModelToolPlan({
      ...basePlan,
      toolNames: [
        "getStudentHousingStatus",
        "getOnboardingChecklist",
        "getEnrollmentHolds",
      ],
    });

    expect(plan).not.toBeNull();
    expect(plan?.toolNames).toEqual([
      "getStudentHousingStatus",
      "getOnboardingChecklist",
      "getEnrollmentHolds",
    ]);
  });

  it("drops an out-of-scope read instead of discarding the whole plan", () => {
    // A sound housing plan that also proposes an unrelated aid read keeps its
    // housing evidence; previously the entire plan was thrown away.
    const plan = validateModelToolPlan({
      ...basePlan,
      toolNames: ["getStudentHousingStatus", "getFinancialAidStatus"],
    });

    expect(plan).not.toBeNull();
    expect(plan?.toolNames).toEqual(["getStudentHousingStatus"]);
  });

  it("still rejects a plan that carries no evidence for its own intent", () => {
    expect(
      validateModelToolPlan({
        ...basePlan,
        requestType: "remaining_steps",
        toolNames: ["getFinancialAidStatus"],
      }),
    ).toBeNull();
  });

  it("adds reads an intent cannot be answered without", () => {
    const plan = validateModelToolPlan({
      ...basePlan,
      requestType: "missing_documents",
      toolNames: ["getOnboardingChecklist"],
    });

    expect(plan?.toolNames).toContain("getDocumentStatuses");
  });

  it("carries up to two secondary intents for one multi-part question", () => {
    const plan = validateModelToolPlan({
      ...basePlan,
      requestType: "remaining_steps",
      additionalRequestTypes: ["aid_status", "housing_status"],
      toolNames: [
        "getOnboardingChecklist",
        "getFinancialAidStatus",
        "getStudentHousingStatus",
      ],
    });

    expect(plan?.additionalClassifications.map((item) => item.requestType)).toEqual([
      "aid_status",
      "housing_status",
    ]);
  });

  it("rejects a plan that bolts a fourth domain onto one question", () => {
    // Each extra intent is another paragraph of state in the reply, and the
    // planner used the third slot to attach domains nobody asked about.
    expect(
      validateModelToolPlan({
        ...basePlan,
        requestType: "remaining_steps",
        additionalRequestTypes: ["aid_status", "housing_status", "deadlines"],
        toolNames: ["getOnboardingChecklist"],
      }),
    ).toBeNull();
  });

  it("keeps out-of-scope plans toolless", () => {
    expect(
      validateModelToolPlan({
        ...basePlan,
        requestType: "unsupported_or_out_of_scope",
        toolNames: ["getOnboardingChecklist"],
      }),
    ).toBeNull();
  });

  it("rejects an unknown tool name outright", () => {
    expect(
      validateModelToolPlan({ ...basePlan, toolNames: ["dropStudentRecord"] }),
    ).toBeNull();
  });

  it("rejects a low-confidence plan", () => {
    expect(validateModelToolPlan({ ...basePlan, confidence: 0.3 })).toBeNull();
  });
});
