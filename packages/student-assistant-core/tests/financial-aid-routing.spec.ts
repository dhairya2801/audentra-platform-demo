import { describe, expect, it } from "vitest";
import {
  classifyRequestDeterministically,
  normalizeRequestNode,
  selectToolReadsNode,
} from "../src/index";

const classify = (message: string) =>
  classifyRequestDeterministically(normalizeRequestNode(message, { history: [] }));

/**
 * The financial-aid question families from the brief, each routed to the read
 * that can actually answer it. These are phrasing-level assertions on purpose:
 * the failure mode being guarded against is a rule widening until it swallows a
 * neighbouring question, which is exactly what a single representative question
 * per family would not catch.
 */
describe("financial aid routing", () => {
  const cases: Array<[string, string]> = [
    ["What financial aid do I have?", "aid_summary"],
    ["How much aid am I receiving?", "aid_summary"],
    ["Give me a summary of my award package", "aid_summary"],
    ["What's my total aid?", "aid_summary"],

    ["Which grants have I accepted?", "aid_award_acceptance_status"],
    ["Which loans have I declined?", "aid_award_acceptance_status"],
    ["Has my award been accepted?", "aid_award_acceptance_status"],

    ["Has my FAFSA been received?", "aid_application_status"],
    ["Was my FAFSA selected for verification?", "aid_application_status"],
    ["What's the status of my FAFSA?", "aid_application_status"],

    ["What financial aid requirements am I missing?", "aid_missing_documents"],
    ["Do I need to submit verification documents?", "aid_missing_documents"],
    ["What financial-aid documents are missing?", "aid_missing_documents"],

    ["Is my verification still pending?", "aid_verification_status"],
    ["What is the status of my verification worksheet?", "aid_verification_status"],

    ["Why hasn't my aid been disbursed?", "aid_disbursement"],
    ["When will my aid be disbursed?", "aid_disbursement"],
    ["When does my aid get paid out?", "aid_disbursement"],

    ["Does my aid cover my tuition?", "aid_coverage"],
    ["How much will I still owe?", "aid_coverage"],
    ["What's my remaining balance after aid?", "aid_coverage"],
    ["Am I getting a refund?", "aid_coverage"],

    ["Why is my financial aid incomplete?", "aid_incomplete_reason"],
    ["What financial-aid steps do I have left?", "aid_remaining_steps"],
    ["When is the verification worksheet due?", "aid_deadlines"],
    ["Who should I contact about financial aid?", "aid_support"],
  ];

  it.each(cases)("routes %s", (question, expected) => {
    expect(classify(question)?.requestType).toBe(expected);
  });

  it("keeps a plain billing question with billing rather than claiming it for aid", () => {
    // "How much do I owe?" is answerable from the account alone. Only the
    // after-aid phrasings need both records.
    expect(classify("How much do I owe?")?.requestType).not.toBe("aid_coverage");
  });

  it("reads both the aid record and the bill for a coverage question", () => {
    // The cross-domain case from the brief: unanswerable from either alone.
    expect(selectToolReadsNode("aid_coverage")).toEqual([
      "getFinancialAidSummary",
      "getStudentAccountSummary",
    ]);
  });

  it("does not ask for aid reads when answering a greeting", () => {
    expect(selectToolReadsNode("greeting")).toEqual(["getStudentProfile"]);
    expect(selectToolReadsNode("capability_overview")).toEqual([]);
  });

  it("does not promise an amount nobody has decided yet", () => {
    // Eligibility and hypothetical amounts stay gated; the coverage rule above
    // must not have opened a path around that.
    expect(classify("How much aid will I qualify for?")?.requirementReference).toBe(
      "award_amount_unavailable",
    );
    expect(classify("Am I eligible for a Pell Grant?")?.requirementReference).toBe(
      "eligibility_unavailable",
    );
  });
});
