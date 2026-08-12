import { describe, expect, it, vi } from "vitest";
import { createStudentAssistantGraph } from "../src/graph";
import type { StudentAssistantInput, StudentAssistantTools } from "../src/contracts";
import type { StudentAssistantModel } from "../src/model";
import { createFakeTools } from "./support/fake-tools";

function request(message: string): StudentAssistantInput {
  return {
    message,
    context: {
      tenantId: "tenant-authoritative",
      studentId: "student-authoritative",
      conversationId: "conversation-authoritative",
      inputMode: "text",
      history: [],
      pageContext: { path: "/enrollment", label: "My Enrollment" },
    },
  };
}

function planningModel(
  write: NonNullable<StudentAssistantModel["writeGroundedAnswer"]>,
  overrides: Partial<{
    requestType: string;
    additionalRequestTypes: string[];
    toolNames: string[];
  }> = {},
): StudentAssistantModel {
  return {
    planToolReads: async () =>
      ({
        requestType: overrides.requestType ?? "remaining_steps",
        additionalRequestTypes: overrides.additionalRequestTypes ?? ["aid_status"],
        confidence: 0.93,
        requirementReference: null,
        toolNames: overrides.toolNames ?? [
          "getOnboardingChecklist",
          "getFinancialAidStatus",
        ],
        deadlineWindow: null,
        requestedEntity: null,
        financialAidEntity: null,
        housingEntity: null,
        blockerScope: null,
        priorityExplanationRequested: false,
        registrationQuestion: false,
      }) as Awaited<ReturnType<NonNullable<StudentAssistantModel["planToolReads"]>>>,
    classifyRequest: async () => ({
      requestType: "remaining_steps",
      confidence: 0.9,
      requirementReference: null,
    }),
    composeGroundedResponse: async ({ facts }) => ({
      factIds: facts.map((fact) => fact.id),
      tone: "concise",
    }),
    writeGroundedAnswer: vi.fn(write),
  };
}

async function run(
  model: StudentAssistantModel,
  message = "What's left on my checklist and where does my aid stand?",
  tools: StudentAssistantTools = createFakeTools(),
) {
  return createStudentAssistantGraph({ tools, model }).execute(request(message));
}

describe("written grounded answers", () => {
  it("uses the model's prose when it survives the claim guard", async () => {
    const response = await run(
      planningModel(async () => ({
        answer:
          "Your checklist still has open items, and your financial aid is not complete yet. Start with the outstanding verification step.",
      })),
    );

    expect(response.message).toBe(
      "Your checklist still has open items, and your financial aid is not complete yet. Start with the outstanding verification step.",
    );
    expect(response.safeFailure).toBeNull();
  });

  it("sees evidence from every planned domain, not just the primary intent", async () => {
    const seen: string[][] = [];
    const response = await run(
      planningModel(async (input) => {
        seen.push(input.facts.map((fact) => fact.id));
        return { answer: "Both your checklist and your aid record have open items." };
      }),
    );

    const factIds = seen[0] ?? [];
    expect(factIds.some((id) => id.startsWith("remaining:"))).toBe(true);
    expect(factIds.some((id) => id.includes("aid"))).toBe(true);
    expect(response.message).toContain("aid");
  });

  it("supplies cross-domain supporting facts the primary intent did not ask for", async () => {
    let relevance: string[] = [];
    await run(
      planningModel(async (input) => {
        relevance = input.facts.map((fact) => fact.relevance);
        return { answer: "Your checklist has open items." };
      }),
    );

    expect(relevance).toContain("primary");
    expect(relevance).toContain("supporting");
  });

  it("falls back to the deterministic message when the answer invents a date", async () => {
    const response = await run(
      planningModel(async () => ({
        answer: "Your verification worksheet is due 2031-01-31, so submit it now.",
      })),
    );

    expect(response.message).not.toContain("2031");
    expect(response.safeFailure?.compositionFallback).toBe(true);
    expect(response.safeFailure?.codes).toContain(
      "written_answer_rejected:ungrounded_date",
    );
  });

  it("falls back when the answer claims Edward changed a record", async () => {
    const response = await run(
      planningModel(async () => ({
        answer: "I've submitted your verification worksheet for you.",
      })),
    );

    expect(response.message).not.toContain("I've submitted");
    expect(response.safeFailure?.codes).toContain(
      "written_answer_rejected:claimed_write",
    );
  });

  it("falls back when the writer throws", async () => {
    const response = await run(
      planningModel(async () => {
        throw new Error("provider exploded");
      }),
    );

    expect(response.message.length).toBeGreaterThan(0);
    expect(response.safeFailure?.compositionFallback).toBe(true);
  });

  it("never asks the model to write a refusal for a mutation request", async () => {
    const model = planningModel(async () => ({
      answer: "Sure, consider it handled.",
    }));

    const response = await run(model, "Pay my enrollment deposit for me.");

    expect(model.writeGroundedAnswer).not.toHaveBeenCalled();
    expect(response.message).not.toContain("consider it handled");
  });

  it("does not repeat the same fact list once per requested intent", async () => {
    // Deterministic path only: no writer bound, three intents planned.
    const model: StudentAssistantModel = {
      planToolReads: async () =>
        ({
          requestType: "onboarding_status",
          additionalRequestTypes: ["deadlines", "holds_and_blockers"],
          confidence: 0.93,
          requirementReference: null,
          toolNames: [
            "getStudentProfile",
            "getOnboardingChecklist",
            "getStudentDeadlines",
            "getEnrollmentHolds",
          ],
          deadlineWindow: "all",
          requestedEntity: null,
          financialAidEntity: null,
          housingEntity: null,
          blockerScope: "enrollment",
          priorityExplanationRequested: false,
          registrationQuestion: false,
        }) as Awaited<
          ReturnType<NonNullable<StudentAssistantModel["planToolReads"]>>
        >,
      classifyRequest: async () => ({
        requestType: "onboarding_status",
        confidence: 0.9,
        requirementReference: null,
      }),
      composeGroundedResponse: async () => ({ factIds: [], tone: "concise" }),
    };

    const response = await run(model, "Summarise where I stand.");

    // Before the fix each requested intent re-rendered the whole merged fact
    // list behind its own lead-in, so any repeated deadline sentence appeared
    // once per intent.
    const sentences = response.message
      .split(/(?<=\.)\s+/)
      .map((sentence) => sentence.trim())
      .filter(Boolean);
    const duplicates = sentences.filter(
      (sentence, index) => sentences.indexOf(sentence) !== index,
    );
    expect(duplicates).toEqual([]);
  });
});

describe("scope and ambiguity regressions found by evaluation", () => {
  it("does not ask which registration is meant when the student never raised it", async () => {
    let asked: string[] = [];
    const model = planningModel(
      async (input) => {
        asked = input.requestTypes;
        return { answer: "You have no official holds on your record right now." };
      },
      {
        requestType: "holds_and_blockers",
        additionalRequestTypes: ["deadlines"],
        toolNames: ["getEnrollmentHolds", "getStudentDeadlines"],
      },
    );
    // The planner marks this ambiguous even though the student said nothing
    // about registering.
    (model as { planToolReads?: unknown }).planToolReads = async () =>
      ({
        requestType: "holds_and_blockers",
        additionalRequestTypes: ["deadlines"],
        confidence: 0.9,
        requirementReference: null,
        toolNames: ["getEnrollmentHolds", "getStudentDeadlines"],
        deadlineWindow: "all",
        requestedEntity: null,
        financialAidEntity: null,
        housingEntity: null,
        blockerScope: "registration_ambiguous",
        priorityExplanationRequested: false,
        registrationQuestion: false,
      }) as never;

    const response = await run(
      model,
      "Do I have any blockers, and what deadlines are coming up?",
    );

    expect(response.message).not.toContain("Do you mean course registration");
    expect(asked.length).toBeGreaterThan(0);
  });

  it("treats a report of a past action as a question, not a write request", async () => {
    const model = planningModel(async () => ({
      answer: "Your transcript has not been received yet, so the step stays open.",
    }));

    const response = await run(
      model,
      "I uploaded my transcript yesterday. Why does my checklist still say incomplete?",
    );

    expect(response.requestType).not.toBe("unsupported_or_out_of_scope");
    expect(model.writeGroundedAnswer).toHaveBeenCalled();
  });

  it("still refuses when the student asks Edward to do the upload", async () => {
    const model = planningModel(async () => ({ answer: "Done." }));

    const response = await run(model, "Can you upload my transcript document for me?");

    expect(response.requestType).toBe("unsupported_or_out_of_scope");
    expect(model.writeGroundedAnswer).not.toHaveBeenCalled();
  });
});

describe("recovery from an invented cause", () => {
  /** A registration read, so the gate data the causal guard uses exists. */
  const registrationTools = (): StudentAssistantTools => ({
    ...createFakeTools(),
    async getRegistrationStatus() {
      return {
        status: "available",
        observedAt: "2026-08-03T12:00:00.000Z",
        sourceVersion: "1",
        data: {
          termCode: "2026FA",
          termName: "Fall 2026",
          state: "blocked",
          registrationWindow: { opensAt: null, closesAt: null, open: true },
          gates: [
            {
              code: "immunization_cleared",
              label: "Immunisation record",
              satisfied: false,
              reason: "Student Health Services must clear your immunisation record.",
              resolutionOwner: "Student Health Services",
              navigationRoute: null,
              relatedRequirementCode: "immunization_record",
            },
          ],
          registeredCreditCount: 0,
          registeredCourseCount: 0,
          minimumCredits: 12,
          maximumCredits: 18,
          advisingRequired: true,
          advisingHoldCleared: true,
          synthetic: true,
        },
      };
    },
  });

  const registrationPlan = (
    write: NonNullable<StudentAssistantModel["writeGroundedAnswer"]>,
  ) =>
    planningModel(write, {
      requestType: "registration_status",
      additionalRequestTypes: [],
      toolNames: ["getRegistrationStatus"],
    });

  it("asks the writer again rather than falling back to raw state", async () => {
    // The guard doing its job should not cost the student a readable answer.
    const answers = [
      "You cannot register because your financial aid is incomplete.",
      "You cannot register yet. Your immunisation record has not been cleared.",
    ];
    const seen: string[] = [];

    const response = await run(
      registrationPlan(async (input) => {
        seen.push(input.normalizedMessage);
        return { answer: answers.shift() ?? "" };
      }),
      "Can I register even though my financial aid isn't complete?",
      registrationTools(),
    );

    expect(seen).toHaveLength(2);
    expect(seen[1]).toContain("Correction:");
    expect(response.message).toContain("immunisation record");
    expect(response.message).not.toContain("because your financial aid is incomplete");
  });

  it("gives up after one retry rather than looping", async () => {
    let calls = 0;
    await run(
      registrationPlan(async () => {
        calls += 1;
        return {
          answer: "You cannot register because your financial aid is incomplete.",
        };
      }),
      "Can I register even though my financial aid isn't complete?",
      registrationTools(),
    );
    expect(calls).toBe(2);
  });
});
