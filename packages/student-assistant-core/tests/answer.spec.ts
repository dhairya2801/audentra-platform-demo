import { describe, expect, it } from "vitest";
import { guardGroundedAnswer } from "../src/answer";

const evidence = [
  "Complete financial-aid verification is due 2026-08-14.",
  "Pay your enrollment deposit is already complete.",
  "Checklist totals: 1 complete, 7 remaining, 0 blocked.",
  "Housing & Residence Life can be reached at housing@aster.example.edu.",
];

const guard = (answer: string) =>
  guardGroundedAnswer({ answer, evidenceTexts: evidence });

describe("grounded answer guard", () => {
  it("accepts a written answer that only restates supplied evidence", () => {
    const result = guard(
      "Your enrollment deposit is already posted, so that is not what is holding you up. Financial-aid verification is still outstanding and is due 2026-08-14. Complete that next and 7 items will remain on your checklist.",
    );
    expect(result.accepted).toBe(true);
    expect(result.reasonCode).toBeNull();
  });

  it("accepts an ISO evidence date restated in prose form", () => {
    expect(guard("Verification is due August 14, 2026.").accepted).toBe(true);
    expect(guard("Verification is due Aug 14.").accepted).toBe(true);
  });

  it("rejects a date that appears nowhere in the evidence", () => {
    const result = guard("Your verification is due 2026-09-01.");
    expect(result.accepted).toBe(false);
    expect(result.reasonCode).toBe("ungrounded_date");
  });

  it("rejects a prose date that contradicts the evidence date", () => {
    expect(guard("It is due September 1, 2026.").reasonCode).toBe(
      "ungrounded_date",
    );
  });

  it("rejects an invented monetary amount", () => {
    const result = guard("You still owe $450 before you can register.");
    expect(result.accepted).toBe(false);
    expect(result.reasonCode).toBe("ungrounded_number");
  });

  it("rejects an invented contact detail", () => {
    expect(guard("Email registrar@aster.example.edu for help.").reasonCode).toBe(
      "ungrounded_contact",
    );
    expect(guard("Call 555-123-9000 to resolve it.").reasonCode).toBe(
      "ungrounded_contact",
    );
  });

  it("allows a contact detail that is present in the evidence", () => {
    expect(
      guard("You can reach housing@aster.example.edu about the requirement.")
        .accepted,
    ).toBe(true);
  });

  it("rejects any claim that Edward performed a write", () => {
    for (const answer of [
      "I've submitted your verification worksheet.",
      "I updated your housing preference for you.",
      "I'll pay the enrollment deposit now.",
      "I have cancelled that requirement.",
      "I can go ahead and register you.",
    ]) {
      const result = guard(answer);
      expect(result.accepted, answer).toBe(false);
      expect(result.reasonCode, answer).toBe("claimed_write");
    }
  });

  it("still allows describing what the student should do", () => {
    expect(
      guard("Submit your verification worksheet next; I can't submit it for you.")
        .accepted,
    ).toBe(true);
  });

  it("allows small counts the model derived from the evidence list", () => {
    expect(guard("You have 7 outstanding items and 1 completed step.").accepted).toBe(
      true,
    );
  });

  it("rejects leaked internal identifiers", () => {
    expect(guard("See receipt-3 for details.").reasonCode).toBe(
      "leaked_identifier",
    );
    expect(
      guard("Requirement 00000000-0000-7000-8000-000000000605 is open.")
        .reasonCode,
    ).toBe("leaked_identifier");
  });

  it("rejects empty and oversized answers", () => {
    expect(guard("   ").reasonCode).toBe("empty");
    expect(guard("word ".repeat(400)).reasonCode).toBe("too_long");
  });
});

describe("numeric grounding is compared by value", () => {
  const money = ["Your balance is $500.00 and tuition is $18400.00."];
  const check = (answer: string) =>
    guardGroundedAnswer({ answer, evidenceTexts: money });

  it("accepts a natural rendering of an evidence amount", () => {
    expect(check("You owe $500.").accepted).toBe(true);
    expect(check("You owe $18,400 in tuition.").accepted).toBe(true);
    expect(check("Your balance is $500.00.").accepted).toBe(true);
  });

  it("still rejects an amount that is not in the evidence", () => {
    expect(check("You owe $450.").reasonCode).toBe("ungrounded_number");
    expect(check("Tuition is $18,500.").reasonCode).toBe("ungrounded_number");
  });
});
