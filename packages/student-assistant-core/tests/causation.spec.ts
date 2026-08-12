import { describe, expect, it } from "vitest";
import {
  buildCausalGuards,
  detectInventedCausation,
  guardGroundedAnswer,
} from "../src/index";

/**
 * The failure this guards against is the most serious the assistant can
 * produce: a confident, specific, wrong reason. A student acts on it, and the
 * model judge does not catch it — during the previous run's calibration it
 * scored three such answers "good".
 *
 * These assertions are generated from gate data rather than hand-written per
 * falsehood, so a new gate is covered without a new test.
 */
const registrationGates = [
  { code: "account_balance", satisfied: false },
  { code: "immunization_cleared", satisfied: false },
  { code: "advising_complete", satisfied: false },
  { code: "final_transcript", satisfied: true },
];

const guards = buildCausalGuards({ registrationGates });

describe("invented causation", () => {
  it("rejects a cause that is not an open gate of the outcome", () => {
    // The canonical failure: aid completeness is not a registration gate.
    expect(
      detectInventedCausation(
        "you cannot register because your financial aid is incomplete.",
        guards,
      ),
    ).toBe("financial_aid");
  });

  it("rejects a cause that is a gate but a satisfied one", () => {
    // The transcript gate is closed, so it cannot be why registration is blocked.
    expect(
      detectInventedCausation(
        "registration is blocked because your transcript has not been received.",
        guards,
      ),
    ).toBe("transcript");
  });

  it("accepts a cause the gate data actually supports", () => {
    expect(
      detectInventedCausation(
        "you cannot register because your immunisation record has not been cleared.",
        guards,
      ),
    ).toBeNull();
    expect(
      detectInventedCausation(
        "registration is blocked because of your past-due balance.",
        guards,
      ),
    ).toBeNull();
  });

  it("leaves two true statements alone when neither claims to cause the other", () => {
    // "Also incomplete" is a fact, not a cause, and must not be rejected.
    expect(
      detectInventedCausation(
        "you cannot register yet. your financial aid is also incomplete.",
        guards,
      ),
    ).toBeNull();
  });

  it("does not fire on a capability that was never read", () => {
    // No housing read means no housing gates, so no housing claim is checkable
    // and none is rejected. The guard never invents an objection either.
    expect(
      detectInventedCausation(
        "you cannot apply for housing because your deposit has not posted.",
        buildCausalGuards({ registrationGates }),
      ),
    ).toBeNull();
  });

  it("covers other gated outcomes from the same data", () => {
    const housingGuards = buildCausalGuards({
      housingGates: [
        { code: "enrollment_deposit_posted", satisfied: false },
        { code: "housing_preference_selected", satisfied: true },
      ],
    });
    expect(
      detectInventedCausation(
        "you cannot apply for housing because you have not recorded a housing plan preference.",
        housingGuards,
      ),
    ).toBe("housing");
    expect(
      detectInventedCausation(
        "you cannot apply for housing because your enrollment deposit has not posted.",
        housingGuards,
      ),
    ).toBeNull();
  });

  it("refuses the whole written answer, so the deterministic message is used instead", () => {
    const result = guardGroundedAnswer({
      answer:
        "You cannot register because your financial aid is incomplete. Contact the office.",
      evidenceTexts: ["Registration is blocked."],
      causalGuards: guards,
    });
    expect(result.accepted).toBe(false);
    expect(result.reasonCode).toBe("invented_causation");
  });

  it("still accepts a well-grounded answer with no causal claim at all", () => {
    const result = guardGroundedAnswer({
      answer: "Your immunisation record is still being reviewed.",
      evidenceTexts: ["Your immunisation record is still being reviewed."],
      causalGuards: guards,
    });
    expect(result.accepted).toBe(true);
  });
});

describe("policy questions", () => {
  it("does not answer a question about a category of students with this student's blockers", async () => {
    const { buildEvidenceBundle } = await import("../src/answer");
    const derived = {
      completedSteps: [
        {
          id: "r1",
          code: "enrollment_deposit",
          title: "Enrollment deposit",
          description: "",
          status: "completed" as const,
          blocking: true,
          dueAt: null,
          progressPercent: 100,
          slug: "enrollment-deposit",
          contextReceiptIds: ["receipt-1"],
        },
      ],
      remainingSteps: [],
      awaitingReviewSteps: [],
      documentStates: [],
      blockedSteps: [],
      officialHolds: [],
      derivedBlockers: [],
      incompleteNonBlockingRequirements: [],
      nonBlockingActions: [],
      missingDocuments: [],
      deadlines: [],
      supportOptions: [],
      unavailableData: [],
      capabilityReceiptIds: {},
    } as unknown as import("../src/state").DerivedStudentState;

    const personal = buildEvidenceBundle({
      classifications: [
        { requestType: "remaining_steps" } as never,
      ],
      derived,
    });
    const policy = buildEvidenceBundle({
      classifications: [{ requestType: "policy_lookup" } as never],
      derived,
    });

    expect(personal.supportingFacts.length).toBeGreaterThan(0);
    expect(policy.supportingFacts).toEqual([]);
  });
});

describe("the reply a refused answer falls back to", () => {
  it("states the outcome and the open gates instead of reciting every reason", async () => {
    const { buildGroundedFacts, composeGroundedAnswerNode } = await import(
      "../src/composition"
    );
    void buildGroundedFacts;
    const derived = {
      profile: null,
      completedSteps: [],
      remainingSteps: [],
      awaitingReviewSteps: [],
      documentStates: [],
      blockedSteps: [],
      officialHolds: [],
      derivedBlockers: [],
      incompleteNonBlockingRequirements: [],
      nonBlockingActions: [],
      missingDocuments: [],
      deadlines: [],
      prioritizedAction: null,
      priorityEvidence: null,
      holdReceiptId: null,
      registrationEligibility: null,
      capabilitySummary: {},
      supportOptions: [],
      suggestedActions: [],
      unavailableData: [],
      nextStep: null,
      policy: null,
      policyReceiptId: null,
      financialAid: null,
      housing: null,
      aidSummary: null,
      aidDisbursements: null,
      housingEligibility: null,
      registration: {
        termCode: "2026FA",
        termName: "Fall 2026",
        state: "blocked",
        registrationWindow: { opensAt: null, closesAt: null, open: true },
        gates: [
          {
            code: "account_balance",
            label: "Student account balance",
            satisfied: false,
            reason: "A past-due balance over $250 places a billing hold.",
            resolutionOwner: "Office of Student Accounts",
            navigationRoute: "/financials",
            relatedRequirementCode: null,
          },
          {
            code: "advising_complete",
            label: "Advising meeting",
            satisfied: false,
            reason: "New students meet an adviser once before registering.",
            resolutionOwner: "Academic Advising",
            navigationRoute: null,
            relatedRequirementCode: null,
          },
          {
            code: "final_transcript",
            label: "Final official transcript",
            satisfied: true,
            reason: "Received.",
            resolutionOwner: null,
            navigationRoute: null,
            relatedRequirementCode: null,
          },
        ],
        registeredCreditCount: 0,
        registeredCourseCount: 0,
        minimumCredits: 12,
        maximumCredits: 18,
        advisingRequired: true,
        advisingHoldCleared: false,
        synthetic: true,
      },
      account: null,
      calendar: null,
      appointments: null,
      policyMatches: null,
      capabilityReceiptIds: { getRegistrationStatus: ["receipt-1"] },
    } as unknown as import("../src/state").DerivedStudentState;

    const draft = await composeGroundedAnswerNode({
      classification: {
        requestType: "registration_status",
        confidence: 1,
        source: "deterministic",
        requirementReference: null,
        deadlineWindow: null,
        requestedEntity: null,
        financialAidEntity: null,
        housingEntity: null,
        deadlineScope: null,
        blockerScope: null,
        blockerTarget: null,
        priorityExplanationRequested: false,
        registrationQuestion: true,
      },
      normalized: {
        text: "Can I register?",
        resolvedText: "Can I register?",
        comparableText: "can i register?",
        history: [],
        pagePath: null,
        pageLabel: null,
        isFollowUp: false,
        isMutationRequest: false,
        containsSensitiveFinancialData: false,
      },
      derived,
    });

    // Short, names only the open gates, and gives one place to start.
    expect(draft.fallbackMessage).toContain("You can't register for Fall 2026 yet");
    expect(draft.fallbackMessage).toContain("student account balance");
    expect(draft.fallbackMessage).toContain("advising meeting");
    expect(draft.fallbackMessage).not.toContain("Final official transcript");
    expect(draft.fallbackMessage).toContain("Office of Student Accounts");
    expect(draft.fallbackMessage.length).toBeLessThan(400);
  });
});
