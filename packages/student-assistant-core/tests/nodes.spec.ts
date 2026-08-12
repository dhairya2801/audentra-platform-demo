import { describe, expect, it } from "vitest";
import {
  classifyRequestDeterministically,
  deriveEnrollmentBlockers,
  deriveFinancialAidResult,
  housingRequirementState,
  derivePrioritizedAction,
  deriveRequirements,
  deriveStudentDeadlines,
  normalizeRequestNode,
  selectToolReadsNode,
  validateModelToolPlan,
  type GroundedRequirement,
  type AidChecklistRead,
  type NormalizedStudentDeadlineSource,
  type StudentDeadlineWindow,
} from "../src/index";
import { studentAssistantEvaluationFixtures } from "../fixtures/evaluation-fixtures";

describe("student assistant deterministic nodes", () => {
  it("validates model tool plans against request-domain capabilities", () => {
    expect(
      validateModelToolPlan({
        requestType: "housing_next_action",
        confidence: 0.93,
        requirementReference: null,
        toolNames: ["getStudentHousingStatus", "getStudentDeadlines"],
        housingEntity: "housing_plan",
      }),
    ).toMatchObject({
      classification: {
        requestType: "housing_next_action",
        source: "model",
        housingEntity: "housing_plan",
      },
      toolNames: ["getStudentHousingStatus", "getStudentDeadlines"],
    });
    expect(
      validateModelToolPlan({
        requestType: "housing_next_action",
        confidence: 0.93,
        requirementReference: null,
        toolNames: ["getFinancialAidStatus"],
      }),
    ).toBeNull();
    expect(
      validateModelToolPlan({
        requestType: "unsupported_or_out_of_scope",
        confidence: 0.8,
        requirementReference: null,
        toolNames: ["getStudentProfile"],
      }),
    ).toBeNull();
  });

  it.each([
    ["What financial-aid steps do I have left?", "aid_remaining_steps", "financial_aid"],
    ["Why is my financial aid incomplete?", "aid_incomplete_reason", "financial_aid"],
    ["What financial-aid documents are missing?", "aid_missing_documents", "financial_aid"],
    ["Is my verification still pending?", "aid_verification_status", "financial_aid_verification"],
    ["When is the verification worksheet due?", "aid_deadlines", "verification_worksheet"],
    ["Has my award been accepted?", "aid_award_acceptance_status", "award_acceptance"],
    ["Who should I contact about financial aid?", "aid_support", "financial_aid"],
  ] as const)(
    "classifies financial-aid request %s deterministically",
    (question, requestType, entity) => {
      const result = classifyRequestDeterministically(
        normalizeRequestNode(question, { history: [] }),
      );
      expect(result).toMatchObject({ requestType, financialAidEntity: entity });
    },
  );

  it("derives aid completion, missing documents, verification, deadlines, and next action in code", () => {
    const read: AidChecklistRead = {
      academicYear: "2027–28",
      overallStatus: "incomplete",
      highLevelVerificationStatus: "in_progress",
      unavailableSources: [],
      items: [
        aidRequirement("fafsa", "fafsa", "satisfied", null),
        aidRequirement(
          "verification_worksheet",
          "verification_document",
          "action_required",
          "2027-08-03T23:59:59.000Z",
        ),
        aidRequirement(
          "award_acceptance",
          "award_acceptance",
          "not_started",
          "2027-08-10T23:59:59.000Z",
        ),
      ],
      awards: [
        {
          awardId: "award-accepted",
          awardLabel: "Institutional grant",
          awardType: "grant",
          status: "accepted",
          requiresAction: false,
          academicYear: "2027–28",
          lastVerifiedAt: "2027-08-01T00:00:00.000Z",
          sourceVersion: "1",
        },
      ],
    };
    const result = deriveFinancialAidResult({
      read,
      receiptId: "receipt-aid",
      receiptObservedAt: "2027-08-01T00:00:00.000Z",
      receiptSourceVersion: "projection:1",
      institutionalTimeZone: "UTC",
      now: new Date("2027-08-01T12:00:00.000Z"),
      window: "all",
      entity: "verification_worksheet",
    });

    expect(result.completedRequirements).toEqual([]);
    expect(result.remainingRequirements.map((item) => item.code)).toEqual([
      "verification_worksheet",
    ]);
    expect(result.missingDocuments.map((item) => item.requirementCode)).toEqual([
      "verification_worksheet",
    ]);
    expect(result.verificationStatus).toBe("action_required");
    expect(result.deadlines.map((item) => item.requirementCode)).toEqual([
      "verification_worksheet",
    ]);
    expect(result.nextAction).toMatchObject({
      id: "aid-verification_worksheet",
      reasonCode: "submit_required_document",
    });
  });
  it.each(studentAssistantEvaluationFixtures)(
    "classifies evaluation fixture $id without open-ended routing",
    (fixture) => {
      const normalized = normalizeRequestNode(fixture.question, {
        history: "history" in fixture ? fixture.history : [],
      });
      expect(classifyRequestDeterministically(normalized)?.requestType).toBe(
        fixture.expectedRequestType,
      );
      if ("expectedDeadlineWindow" in fixture) {
        expect(
          classifyRequestDeterministically(normalized)?.deadlineWindow,
        ).toBe(fixture.expectedDeadlineWindow);
      }
      if (fixture.id === "registration-blocked") {
        expect(
          classifyRequestDeterministically(normalized)?.registrationQuestion,
        ).toBe(true);
      }
    },
  );

  it.each([
    ["Do I have a housing assignment?", "housing_status", "housing_assignment"],
    ["Am I on a housing waitlist?", "housing_status", "housing_waitlist"],
    ["Has my housing deposit been received?", "housing_status", "housing_deposit"],
    ["Did I provide roommate preferences?", "housing_status", "roommate_preferences"],
    ["What is my accessible housing accommodation status?", "housing_support", "housing_accommodation"],
  ] as const)(
    "classifies bounded housing entity request %s",
    (question, requestType, housingEntity) => {
      expect(
        classifyRequestDeterministically(
          normalizeRequestNode(question, { history: [] }),
        ),
      ).toMatchObject({ requestType, housingEntity });
    },
  );

  it("does not mistake an unqualified enrollment deposit request for a housing write", () => {
    expect(
      normalizeRequestNode(
        "Help me pay my deposit, upload a transcript, and make an appointment.",
        { history: [] },
      ).isMutationRequest,
    ).toBe(false);
    expect(
      normalizeRequestNode("Pay my housing deposit for me.", { history: [] })
        .isMutationRequest,
    ).toBe(true);
  });

  it.each([
    [false, "ready", "incomplete"],
    [true, "completed", "complete"],
    [true, "ready", "conflicting"],
    [false, "completed", "conflicting"],
    [false, "waived", "waived"],
    [false, "not_applicable", "not_applicable"],
    [false, null, "unknown"],
  ] as const)(
    "derives housing plan=%s requirement=%s as %s",
    (hasPlan, status, expected) => {
      expect(housingRequirementState(hasPlan, status)).toBe(expected);
    },
  );

  it("bounds and normalizes conversation history as untrusted text", () => {
    const normalized = normalizeRequestNode(
      "  What\u0000   about those?  ",
      {
        history: Array.from({ length: 9 }, (_, index) => ({
          role: index % 2 === 0 ? ("user" as const) : ("assistant" as const),
          content: `message ${index} ${"x".repeat(1_500)}`,
        })),
        pageContext: { path: "/enrollment", label: "Enrollment" },
      },
    );

    expect(normalized.text).toBe("What about those?");
    expect(normalized.history).toHaveLength(6);
    expect(normalized.history[0]?.content.startsWith("message 3")).toBe(true);
    expect(normalized.history.every((item) => item.content.length <= 1_200)).toBe(
      true,
    );
    expect(normalized.isFollowUp).toBe(true);
  });

  it("selects the minimum allowlisted reads for clear intents", () => {
    expect(selectToolReadsNode("remaining_steps")).toEqual([
      "getOnboardingChecklist",
    ]);
    expect(selectToolReadsNode("missing_documents")).toEqual([
      "getOnboardingChecklist",
      "getDocumentStatuses",
    ]);
    expect(selectToolReadsNode("next_action")).toEqual([
      "getOnboardingChecklist",
      "getEnrollmentHolds",
      "getStudentDeadlines",
    ]);
    expect(selectToolReadsNode("explain_requirement")).toEqual([
      "getOnboardingChecklist",
      "retrieveApprovedPolicy",
    ]);
    expect(selectToolReadsNode("unsupported_or_out_of_scope")).toEqual([]);
  });

  it("conservatively keeps a conflicting requirement incomplete", () => {
    const base = {
      id: "requirement-transcript",
      code: "official_transcript",
      title: "Final transcript",
      description: "Submit a final transcript.",
      blocking: true,
      dueAt: "2026-08-10T00:00:00.000Z",
      progressPercent: 100,
    } as const;
    const result = deriveRequirements([
      {
        requirement: { ...base, status: "completed" },
        receiptId: "receipt-a",
        source: "getOnboardingChecklist",
        order: 0,
      },
      {
        requirement: { ...base, status: "ready", progressPercent: 0 },
        receiptId: "receipt-b",
        source: "getOnboardingChecklist",
        order: 0,
      },
    ]);

    expect(result.completed).toEqual([]);
    expect(result.remaining.map((step) => step.code)).toEqual([
      "official_transcript",
    ]);
    expect(result.unavailableData).toEqual([
      {
        source: "getOnboardingChecklist",
        reason: "conflicting_data",
        retryable: false,
      },
    ]);
  });

  it("classifies date-only and instant deadlines with an institutional clock", () => {
    const result = deriveStudentDeadlines({
      items: [
        deadline("date-today", "2026-03-08", "date"),
        deadline("instant-before-midnight", "2026-03-08T04:59:59.000Z"),
        deadline("instant-after-midnight", "2026-03-08T05:00:01.000Z"),
        deadline("seven-days", "2026-03-15T16:00:00.000Z"),
        deadline("eight-days", "2026-03-16T16:00:00.000Z"),
        deadline("completed-future", "2026-03-20T16:00:00.000Z", "instant", "satisfied"),
        deadline("missing-date", null),
        deadline("invalid-date", "not-a-date"),
      ],
      receiptId: "receipt-deadlines",
      receiptObservedAt: "2026-03-08T15:00:00.000Z",
      receiptSourceVersion: "fixture-v1",
      institutionalTimeZone: "America/New_York",
      now: new Date("2026-03-08T15:00:00.000Z"),
      window: "all",
    });

    expect(Object.fromEntries(result.allOutstanding.map((item) => [item.id, item.urgency]))).toMatchObject({
      "date-today": "due_today",
      "instant-before-midnight": "overdue",
      "instant-after-midnight": "overdue",
      "seven-days": "due_within_7_days",
      "eight-days": "upcoming",
      "missing-date": "unknown_date",
      "invalid-date": "unknown_date",
    });
    expect(result.allOutstanding.map((item) => item.id)).not.toContain(
      "completed-future",
    );
    expect(result.bucketCounts.completedOrSatisfied).toBe(1);
    expect(result.bucketCounts.unknownDate).toBe(2);
  });

  it("keeps a date-only deadline due for the full institutional calendar day", () => {
    const beforeLocalMidnight = deriveStudentDeadlines({
      items: [deadline("offer", "2026-11-01", "date")],
      receiptId: "receipt-deadlines",
      receiptObservedAt: null,
      receiptSourceVersion: null,
      institutionalTimeZone: "America/New_York",
      now: new Date("2026-11-02T04:30:00.000Z"),
      window: "all",
    });
    const afterLocalMidnight = deriveStudentDeadlines({
      items: [deadline("offer", "2026-11-01", "date")],
      receiptId: "receipt-deadlines",
      receiptObservedAt: null,
      receiptSourceVersion: null,
      institutionalTimeZone: "America/New_York",
      now: new Date("2026-11-02T05:30:00.000Z"),
      window: "all",
    });

    expect(beforeLocalMidnight.allOutstanding[0]?.urgency).toBe("due_today");
    expect(afterLocalMidnight.allOutstanding[0]?.urgency).toBe("overdue");
  });

  it("filters today, calendar-week, upcoming, and overdue windows deterministically", () => {
    const items = [
      deadline("monday-overdue", "2026-08-03T12:00:00.000Z"),
      deadline("tuesday-today", "2026-08-04T20:00:00.000Z"),
      deadline("sunday", "2026-08-09T20:00:00.000Z"),
      deadline("next-monday", "2026-08-10T20:00:00.000Z"),
    ];
    const visibleIds = (window: StudentDeadlineWindow) =>
      deriveStudentDeadlines({
        items,
        receiptId: "receipt-deadlines",
        receiptObservedAt: null,
        receiptSourceVersion: null,
        institutionalTimeZone: "UTC",
        now: new Date("2026-08-04T16:00:00.000Z"),
        window,
      }).visible.map((item) => item.id);

    expect(visibleIds("today")).toEqual(["tuesday-today"]);
    expect(visibleIds("this_week")).toEqual([
      "monday-overdue",
      "tuesday-today",
      "sunday",
    ]);
    expect(visibleIds("upcoming")).toEqual([
      "tuesday-today",
      "sunday",
      "next-monday",
    ]);
    expect(visibleIds("overdue")).toEqual(["monday-overdue"]);
  });

  it("reports missing timezone and conflicting dates without choosing a date", () => {
    const item = deadline("same", "2026-08-04T12:00:00.000Z");
    const result = deriveStudentDeadlines({
      items: [item, { ...item, dueAt: "2026-08-05T12:00:00.000Z" }],
      receiptId: "receipt-deadlines",
      receiptObservedAt: null,
      receiptSourceVersion: null,
      institutionalTimeZone: null,
      now: new Date("2026-08-04T12:00:00.000Z"),
      window: "all",
    });

    expect(result.allOutstanding[0]).toMatchObject({
      id: "same",
      dueAt: null,
      urgency: "unknown_date",
    });
    expect(result.unavailableData.map((item) => item.reason)).toEqual([
      "incomplete",
      "conflicting_data",
    ]);
  });

  it("distinguishes official holds, prerequisite blockers, and ordinary incomplete work", () => {
    const result = deriveEnrollmentBlockers(
      {
        journey: {
          id: "journey-1",
          status: "on_hold",
          supportRoute: "/help",
          lastVerifiedAt: null,
          sourceVersion: null,
          domain: "enrollment",
        },
        requirements: [
          blockerRequirement("transcript", "Official transcript", "ready", []),
          blockerRequirement(
            "orientation",
            "Orientation",
            "blocked",
            ["transcript"],
          ),
          blockerRequirement("housing", "Housing preference", "ready", []),
        ],
        academicPlan: [],
        financialActions: [],
        unavailableSources: [],
      },
      "receipt-holds",
    );

    expect(result.officialHolds).toHaveLength(1);
    expect(result.officialHolds[0]?.studentSafeReason).toBeNull();
    expect(result.derivedBlockers).toEqual([
      expect.objectContaining({
        type: "requirement_dependency",
        studentSafeReason:
          "Orientation is blocked until Official transcript is completed.",
      }),
    ]);
    expect(JSON.stringify(result)).not.toContain("Housing preference");
  });

  it("prioritizes an actionable prerequisite before its blocked child", () => {
    const transcript = groundedRequirement("transcript", "Official transcript", "ready");
    const orientation = groundedRequirement("orientation", "Orientation", "blocked");
    const result = derivePrioritizedAction({
      requirements: [orientation, transcript],
      holdRead: {
        journey: null,
        requirements: [
          blockerRequirement("transcript", "Official transcript", "ready", []),
          blockerRequirement("orientation", "Orientation", "blocked", ["transcript"]),
        ],
        academicPlan: [],
        financialActions: [],
        unavailableSources: [],
      },
      officialHolds: [],
      deadlines: [],
    });

    expect(result.action).toMatchObject({
      label: "Official transcript",
      reasonCode: "dependency_prerequisite",
    });
  });

  it("prioritizes support for an authoritative hold before derived work", () => {
    const blockerResult = deriveEnrollmentBlockers(
      {
        journey: {
          id: "journey-1",
          status: "on_hold",
          supportRoute: "/help",
          lastVerifiedAt: "2026-08-04T12:00:00.000Z",
          sourceVersion: "journey-v2",
          domain: "enrollment",
        },
        requirements: [],
        academicPlan: [],
        financialActions: [],
        unavailableSources: [],
      },
      "receipt-holds",
    );
    const result = derivePrioritizedAction({
      requirements: [groundedRequirement("transcript", "Official transcript", "ready")],
      holdRead: null,
      officialHolds: blockerResult.officialHolds,
      deadlines: [],
    });

    expect(result).toMatchObject({
      action: {
        kind: "contact_support",
        reasonCode: "official_hold_support",
        navigationRoute: "/help",
      },
      resultCode: "official_hold_support",
      dependencyCycle: false,
    });
  });

  it("fails safely instead of guessing a priority through a dependency cycle", () => {
    const result = derivePrioritizedAction({
      requirements: [
        groundedRequirement("transcript", "Official transcript", "blocked"),
        groundedRequirement("orientation", "Orientation", "blocked"),
      ],
      holdRead: {
        journey: null,
        requirements: [
          blockerRequirement("transcript", "Official transcript", "blocked", ["orientation"]),
          blockerRequirement("orientation", "Orientation", "blocked", ["transcript"]),
        ],
        academicPlan: [],
        financialActions: [],
        unavailableSources: [],
      },
      officialHolds: [],
      deadlines: [],
    });

    expect(result).toEqual({
      action: null,
      resultCode: "dependency_cycle",
      dependencyCycle: true,
    });
  });
});

function deadline(
  id: string,
  dueAt: string | null,
  duePrecision: "date" | "instant" = "instant",
  completionState: "outstanding" | "satisfied" = "outstanding",
): NormalizedStudentDeadlineSource {
  return {
    id,
    label: id,
    kind: "other_requirement",
    dueAt,
    duePrecision,
    sourceStatus: completionState === "satisfied" ? "completed" : "ready",
    requirementId: id,
    requirementCode: id,
    source: "student_requirement",
    completionState,
    currentlyBlocking: false,
    blockingRequirement: true,
    hardOrRecommended: null,
    dependencyCodes: [],
    resolutionOwner: null,
    navigationRoute: null,
    sourceOrder: 0,
    lastVerifiedAt: null,
    sourceVersion: null,
  };
}

function blockerRequirement(
  code: string,
  label: string,
  status: "ready" | "blocked",
  dependencyCodes: string[],
) {
  return {
    id: `requirement-${code}`,
    code,
    label,
    description: label,
    status,
    blockingRequirement: true,
    dueAt: null,
    progressPercent: 0,
    slug: code,
    dependencyCodes,
    resolutionOwner: "Admissions",
    submissionType: "form",
    supportRoute: `/enrollment/requirements/${code}`,
    sourceOrder: 0,
    lastVerifiedAt: null,
    sourceVersion: null,
    domain: "enrollment",
  } as const;
}

function groundedRequirement(
  code: string,
  title: string,
  status: "ready" | "blocked",
): GroundedRequirement {
  return {
    id: `requirement-${code}`,
    code,
    title,
    description: title,
    status,
    blocking: true,
    dueAt: null,
    progressPercent: 0,
    slug: code,
    contextReceiptIds: ["receipt-holds"],
  };
}

function aidRequirement(
  code: string,
  kind: AidChecklistRead["items"][number]["kind"],
  status: AidChecklistRead["items"][number]["status"],
  dueAt: string | null,
): AidChecklistRead["items"][number] {
  return {
    id: `aid-${code}`,
    code,
    kind,
    label: code.replaceAll("_", " "),
    status,
    dueAt,
    navigationRoute: code === "award_acceptance" ? "/financials" : "/documents",
    policyTopic: code,
    authoritativeDocumentState:
      status === "satisfied" ? "accepted" : status === "under_review" ? "under_review" : "none",
    lastVerifiedAt: "2027-08-01T00:00:00.000Z",
    sourceVersion: "1",
  };
}
