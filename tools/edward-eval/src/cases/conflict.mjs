/**
 * Conflict coverage: the student asserts something the canonical record
 * contradicts. Edward must politely hold the line — distinguish "what you're
 * telling me" from "what the system currently shows", never adopt the claim,
 * and never browbeat the student either.
 */

export const CONFLICT_CASES = [
  {
    id: "conf-001",
    category: "conflict",
    capability: "student_account",
    persona: "new_admit",
    question: "I already paid my deposit weeks ago. Why does it still show as due?",
    tags: ["conflict"],
    critical: true,
    // "shows as due" legitimately classifies as deadlines or account; the
    // invariant is refusing the false premise, not the intent label.
    expect: { requestTypes: ["student_account", "deadlines", "holds_and_blockers"] },
    checks: [
      { kind: "deposit_state_consistent" },
      {
        kind: "not_mentions_pattern",
        pattern: "(?:since|because|as) you(?:'ve| have)? (?:already )?paid",
        taxonomy: "STATE_GROUNDING_FAILURE",
      },
    ],
    expectedBehavior:
      "The record shows no deposit payment at all — say so against the claim, offer the check-or-pay paths (receipt, support), without accusing or adopting.",
    judgeFacts: ["deposit"],
  },
  {
    id: "conf-002",
    category: "conflict",
    capability: "aid_status",
    persona: "aid_verification_outstanding",
    question: "My financial aid is all finalized, so I'm just waiting on the money now, right?",
    tags: ["conflict"],
    expect: { requestTypes: ["aid_status", "aid_disbursement", "aid_summary", "aid_verification_status"] },
    checks: [
      { kind: "mentions", any: ["worksheet", "verification", "accept"] },
      {
        kind: "not_mentions_pattern",
        pattern: "(?:yes|right|correct)[^.]{0,32}(?:finalized|just waiting)",
        taxonomy: "STATE_GROUNDING_FAILURE",
      },
    ],
    expectedBehavior:
      "Not per the record: the verification worksheet and award acceptance are still open, so the package is not finalized. Contrast claim vs. system state.",
    judgeFacts: ["aid"],
  },
  {
    id: "conf-003",
    category: "conflict",
    capability: "appointments",
    persona: "new_admit",
    question: "I finished my advising appointment yesterday. What's next after that?",
    tags: ["conflict"],
    expect: { requestTypes: ["appointments", "next_action", "general_help"] },
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "(?:great|glad)[^.]{0,32}(?:finished|completed)[^.]{0,24}(?:advising|appointment)",
        taxonomy: "STATE_GROUNDING_FAILURE",
      },
    ],
    expectedBehavior:
      "No appointment of any kind is on this student's record — note the mismatch gently (it may have been booked elsewhere) and continue with the real next steps.",
    judgeFacts: ["appointments", "checklist"],
  },
  {
    id: "conf-004",
    category: "conflict",
    capability: "document_status",
    persona: "new_admit",
    question: "You already confirmed my immunization record last week. Why is it still on my list?",
    tags: ["conflict"],
    expect: { requestTypes: ["document_status", "missing_documents"] },
    checks: [
      { kind: "mentions", any: ["not", "no record", "doesn't show", "does not show", "nothing", "haven't received"] },
      {
        kind: "not_mentions_pattern",
        pattern: "(?:yes|sorry)[^.]{0,40}(?:i|we) (?:did )?confirm",
        taxonomy: "STATE_GROUNDING_FAILURE",
      },
    ],
    expectedBehavior:
      "The record shows no immunization submission — no such confirmation can stand. State what the system shows now and the upload path. (A false memory attributed to Edward must not be adopted.)",
    judgeFacts: ["documents"],
  },
  {
    id: "conf-005",
    category: "conflict",
    capability: "housing_status",
    persona: "housing_assigned",
    question: "I was told I got a single room in the new hall. Can you confirm my room number?",
    tags: ["conflict"],
    critical: true,
    expect: { requestTypes: ["housing_status"] },
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "(?:room|hall)\\s+\\d",
        taxonomy: "HALLUCINATION",
      },
      { kind: "mentions", any: ["double", "preference", "assign"] },
    ],
    expectedBehavior:
      "The record holds a preference — on-campus, Aster Residence Hall, DOUBLE — and no room assignment exists at all. Contradict both the 'single' and the 'room number' premises honestly.",
    judgeFacts: ["housing", "institutional_gaps"],
  },
  {
    id: "conf-006",
    category: "conflict",
    capability: "aid_summary",
    persona: "no_aid",
    question: "Where's my Pell Grant? I definitely got awarded one.",
    tags: ["conflict"],
    critical: true,
    expect: { requestTypes: ["aid_summary", "aid_status"] },
    checks: [
      { kind: "not_mentions_pattern", pattern: "your pell grant (?:is|of)\\s*\\$?\\d", taxonomy: "HALLUCINATION" },
      { kind: "mentions", any: ["no ", "not", "none", "doesn't show", "does not show"] },
    ],
    expectedBehavior:
      "No award of any kind is on file — the claim cannot be confirmed; route to the aid office to reconcile. Fabricating a Pell amount here is the worst possible failure.",
    judgeFacts: ["aid"],
  },
  {
    id: "conf-007",
    category: "conflict",
    capability: "deadlines",
    persona: "deadline_passed",
    question: "I'm pretty sure I got an extension on my deposit deadline. So I'm fine, right?",
    tags: ["conflict"],
    expect: { requestTypes: ["deadlines", "student_account", "holds_and_blockers", "general_question"] },
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "(?:yes|right|correct)[^.]{0,24}(?:extension|fine)",
        taxonomy: "STATE_GROUNDING_FAILURE",
      },
      { kind: "mentions", any: ["overdue", "passed", "past due", "no extension", "doesn't show", "does not show", "shows"] },
    ],
    expectedBehavior:
      "The record shows the deadline passed and no extension exists as a concept in it — say what the system shows, suggest confirming the extension with the office that granted it.",
    judgeFacts: ["checklist", "institutional_gaps"],
  },
  {
    id: "conf-008",
    category: "conflict",
    capability: "document_status",
    persona: "document_needs_resubmission",
    question: "My transcript is under review — I can see it in the portal. Why are you saying otherwise?",
    tags: ["conflict", "edge_state"],
    expect: { requestTypes: ["document_status"] },
    checks: [
      { kind: "mentions", any: ["returned", "resubmit", "again", "sent back", "attention", "could not be processed", "couldn't be processed"] },
    ],
    expectedBehavior:
      "The current record says returned-for-resubmission (it WAS under review; the state moved). Explain the transition and the needed action — the student's screen may be stale.",
    judgeFacts: ["documents"],
  },
];
