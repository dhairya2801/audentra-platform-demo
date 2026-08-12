import { describe, expect, it } from "vitest";
import {
  assistantBlockTypes,
  buildResponseBlocks,
  renderBlocksAsText,
  type AssistantBlock,
  type DerivedStudentState,
  type RequestClassification,
} from "../src/index";

function classification(
  requestType: RequestClassification["requestType"],
): RequestClassification {
  return {
    requestType,
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
    registrationQuestion: false,
  };
}

function documentState(
  code: string,
  title: string,
  submissionState: DerivedStudentState["documentStates"][number]["submissionState"],
) {
  return {
    requirementId: `requirement-${code}`,
    requirementCode: code,
    title,
    category: null,
    submissionState,
    requirementStatus: "ready" as const,
    owner:
      submissionState === "UNDER_REVIEW" || submissionState === "UPLOADED"
        ? ("university" as const)
        : submissionState === "ACCEPTED" || submissionState === "WAIVED"
          ? ("nobody" as const)
          : ("student" as const),
    dueAt: "2026-08-14T00:00:00.000Z",
    fileName: null,
    submittedAt: null,
    responsibleOffice: null,
    navigationRoute: `/enrollment/requirements/${code}`,
    contextReceiptIds: ["receipt-1"],
  };
}

function derived(
  overrides: Partial<DerivedStudentState> = {},
): DerivedStudentState {
  return {
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
    capabilitySummary: {} as DerivedStudentState["capabilitySummary"],
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
    registration: null,
    account: null,
    calendar: null,
    appointments: null,
    policyMatches: null,
    capabilityReceiptIds: {},
    ...overrides,
  };
}

const build = (
  requestType: RequestClassification["requestType"],
  state: DerivedStudentState,
  message = "An answer.",
  authored = false,
): AssistantBlock[] =>
  buildResponseBlocks({
    classification: classification(requestType),
    derived: state,
    message,
    authored,
  });

describe("response blocks", () => {
  it("uses prose alone for a greeting", () => {
    const blocks = build("greeting", derived(), "Hi Alex! I'm Edward.");
    expect(blocks).toEqual([
      { type: "text", text: "Hi Alex! I'm Edward.", fallbackText: "Hi Alex! I'm Edward." },
    ]);
  });

  it("uses prose alone for a single fact", () => {
    const blocks = build(
      "document_status",
      derived({ documentStates: [documentState("official_transcript", "Transcript", "UNDER_REVIEW")] }),
      "Your transcript is under review.",
    );
    expect(blocks.map((block) => block.type)).toEqual(["text"]);
  });

  it("uses a table once several documents share the same columns", () => {
    const blocks = build(
      "missing_documents",
      derived({
        documentStates: [
          documentState("official_transcript", "Official transcript", "UNDER_REVIEW"),
          documentState("immunization_record", "Immunisation record", "NOT_SUBMITTED"),
          documentState("identity_document", "Identity document", "ACCEPTED"),
        ],
      }),
    );
    const table = blocks.find((block) => block.type === "table");
    expect(table).toBeDefined();
    if (table?.type !== "table") throw new Error("expected a table");
    expect(table.columns.map((column) => column.label)).toEqual([
      "Document",
      "Status",
      "Due",
    ]);
    expect(table.rows).toHaveLength(3);
    // The distinction the brief insists on is visible as data, not prose.
    expect(table.rows.map((row) => row.status)).toEqual([
      "Under review",
      "Not submitted",
      "Accepted",
    ]);
  });

  it("leads with a summary rather than repeating the detail it is about to tabulate", () => {
    const blocks = build(
      "missing_documents",
      derived({
        documentStates: [
          documentState("official_transcript", "Official transcript", "UNDER_REVIEW"),
          documentState("immunization_record", "Immunisation record", "NOT_SUBMITTED"),
          documentState("identity_document", "Identity document", "NOT_SUBMITTED"),
        ],
      }),
      "Transcript: under review. Immunisation record: not submitted yet. Identity document: not submitted yet.",
    );
    const lead = blocks[0];
    if (lead?.type !== "text") throw new Error("expected a leading text block");
    expect(lead.text).toBe(
      "2 documents still need your attention and 1 is already with a reviewer.",
    );
  });

  it("keeps an authored answer intact and puts the detail beneath it", () => {
    const authored =
      "Your transcript is with the registrar; two other documents still need you.";
    const blocks = build(
      "missing_documents",
      derived({
        documentStates: [
          documentState("official_transcript", "Official transcript", "UNDER_REVIEW"),
          documentState("immunization_record", "Immunisation record", "NOT_SUBMITTED"),
          documentState("identity_document", "Identity document", "NOT_SUBMITTED"),
        ],
      }),
      authored,
      true,
    );
    expect(blocks[0]).toEqual({ type: "text", text: authored, fallbackText: authored });
    expect(blocks.some((block) => block.type === "table")).toBe(true);
  });

  it("orders next steps and marks who has to act", () => {
    const blocks = build(
      "missing_documents",
      derived({
        documentStates: [
          documentState("immunization_record", "Immunisation record", "NOT_SUBMITTED"),
          documentState("official_transcript", "Official transcript", "NEEDS_RESUBMISSION"),
        ],
      }),
    );
    const steps = blocks.find((block) => block.type === "next_steps");
    if (steps?.type !== "next_steps") throw new Error("expected next steps");
    expect(steps.items).toHaveLength(2);
    expect(steps.items.every((item) => item.owner === "student")).toBe(true);
    expect(steps.items[0]?.href).toBe(
      "/enrollment/requirements/immunization_record",
    );
  });

  it("gives every block a plain-text rendering so an unknown type is still readable", () => {
    const blocks = build(
      "missing_documents",
      derived({
        documentStates: [
          documentState("official_transcript", "Official transcript", "UNDER_REVIEW"),
          documentState("immunization_record", "Immunisation record", "NOT_SUBMITTED"),
        ],
      }),
    );
    for (const block of blocks) {
      expect(block.fallbackText.length).toBeGreaterThan(0);
      expect(assistantBlockTypes).toContain(block.type);
    }
    expect(renderBlocksAsText(blocks)).toContain("Official transcript");
  });

  it("never emits markup or raw JSON in any block's text", () => {
    const blocks = build(
      "missing_documents",
      derived({
        documentStates: [
          documentState("official_transcript", "Official transcript", "UNDER_REVIEW"),
          documentState("immunization_record", "Immunisation record", "NOT_SUBMITTED"),
        ],
      }),
    );
    const text = renderBlocksAsText(blocks);
    expect(text).not.toMatch(/[<>]|\|{2,}|\*\*|^\s*[{[]/m);
    expect(() => JSON.parse(text)).toThrow();
  });

  it("returns no blocks for an empty message rather than an empty bubble", () => {
    expect(build("missing_documents", derived(), "")).toEqual([]);
  });
});
