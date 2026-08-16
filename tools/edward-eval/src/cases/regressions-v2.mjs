/**
 * Permanent regression cases pinning the v2 improvement round. Each case
 * represents a fixed failure CLASS (not just an instance); `critical` ones
 * gate CI because the invariant now holds and must never regress.
 */

export const REGRESSION_V2_CASES = [
  {
    id: "rgr-001",
    category: "financial_aid",
    capability: "aid_summary",
    persona: "aid_verification_outstanding",
    question: "How much is my Aster Achievement Scholarship worth?",
    critical: true,
    expect: { requestTypes: ["aid_summary", "aid_status"] },
    checks: [
      { kind: "mentions", any: ["8,000", "8000"] },
      {
        kind: "not_mentions_pattern",
        pattern: "scholarship[^.]{0,40}\\$0\\b|\\$0[^.]{0,24}scholarship",
        taxonomy: "HALLUCINATION",
      },
    ],
    expectedBehavior:
      "The scholarship is $8,000 (accepted). Never a fabricated $0 — amounts must come from award evidence.",
    judgeFacts: ["aid"],
  },
  {
    id: "rgr-002",
    category: "registration",
    capability: "registration_status",
    persona: "deposit_posted",
    question: "Is there a hold blocking my registration?",
    critical: true,
    expect: { requestTypes: ["registration_status", "holds_and_blockers"] },
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "(?:there(?:'s| is) (?:a|an) |you (?:do )?have (?:a|an) )[^.]{0,16}hold",
        taxonomy: "HALLUCINATION",
      },
      {
        kind: "mentions",
        any: ["no hold", "not a hold", "no official", "hold system", "no registrar", "blockers", "blocker"],
      },
    ],
    expectedBehavior:
      "No hold system exists — correct the vocabulary (no official hold) and name the real blockers (the open documents).",
    judgeFacts: ["registration", "institutional_gaps"],
  },
  {
    id: "rgr-003",
    category: "documents",
    capability: "document_status",
    persona: "new_admit",
    question: "How do I change which file I uploaded for my identity document?",
    expect: { requestTypes: ["document_status", "missing_documents", "general_question"] },
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "i can'?t make (?:that|changes)",
        taxonomy: "INTENT_FAILURE",
      },
    ],
    expectedBehavior:
      "A how-do-I question, not a write request — answer with the Documents/checklist path (nothing is uploaded yet, so note that too). Never the read-only refusal boilerplate.",
    judgeFacts: ["documents"],
  },
  {
    id: "rgr-004",
    category: "conversational",
    capability: "assistant_identity",
    persona: "new_admit",
    question: "Wait, are you an actual human?",
    critical: true,
    judged: false,
    tags: ["no_tool"],
    expect: { requestTypes: ["assistant_identity"], maxTools: 0 },
    checks: [
      { kind: "mentions", any: ["ai", "assistant", "not a person", "no"] },
      { kind: "not_mentions", all: ["i am a real person", "yes, i'm human", "i'm a human"] },
      { kind: "max_sentences", max: 4 },
    ],
    note: "Identity honesty is deterministic now: no model call, no reads, no role-play.",
  },
  {
    id: "rgr-005",
    category: "conversational",
    capability: "conversational_ack",
    persona: "deadline_passed",
    question: "great, thanks so much!",
    critical: true,
    judged: false,
    tags: ["no_tool"],
    expect: { requestTypes: ["conversational_ack"], maxTools: 0 },
    checks: [
      { kind: "max_sentences", max: 3 },
      { kind: "block_types", none: ["table", "bullet_list", "next_steps"] },
    ],
    note: "Gratitude reads nothing and lectures about nothing — even for a student with overdue items.",
  },
  {
    id: "rgr-006",
    category: "deposit_account",
    capability: "student_account",
    persona: "payment_pending",
    question: "Do I still need to pay my enrollment deposit?",
    critical: true,
    expect: { requestTypes: ["student_account"] },
    checks: [{ kind: "deposit_state_consistent" }],
    expectedBehavior:
      "No new payment needed: one is already submitted and pending. 'Pending' must never collapse into 'unpaid — go pay'.",
    judgeFacts: ["deposit"],
  },
  {
    id: "rgr-007",
    category: "cross_domain",
    capability: "multi_domain_summary",
    persona: "aid_verification_outstanding",
    question: "Give me an overview of my documents, financial aid, and housing — everything in one place.",
    tags: ["cross_domain"],
    expect: {},
    checks: [
      { kind: "mentions", any: ["identity", "transcript", "immuniz", "immunis", "document"] },
      { kind: "mentions", any: ["worksheet", "verification", "aid"] },
      { kind: "mentions", any: ["housing"] },
    ],
    expectedBehavior:
      "All three named domains answered: open documents, the aid worksheet state, and the housing step (blocked behind the deposit).",
    judgeFacts: ["documents", "aid", "housing", "deposit"],
  },
  {
    id: "rgr-008",
    category: "campus_life",
    capability: "campus_life",
    persona: "new_admit",
    question: "What should I get involved in this semester?",
    tags: ["recommendation"],
    expect: { requestTypes: ["campus_life"], requiredTools: ["getCampusLife"] },
    checks: [{ kind: "mentions_any_fact", fact: "clubNames", min: 1 }],
    expectedBehavior:
      "Campus-life content (clubs/events), not a checklist recitation — involvement questions route to campus life without the word 'club'.",
    judgeFacts: ["campus"],
  },
  {
    id: "rgr-009",
    category: "housing",
    capability: "housing_eligibility",
    persona: "new_admit",
    question: "The housing page seems locked for me. What gives?",
    tags: ["cross_domain"],
    expect: { requestTypes: ["housing_eligibility", "housing_status"] },
    checks: [
      { kind: "mentions", any: ["deposit"] },
      {
        kind: "not_mentions_pattern",
        pattern: "because (?:you haven'?t|your housing preference (?:is|was)) (?:not )?selected",
        taxonomy: "CROSS_DOMAIN_REASONING_FAILURE",
      },
    ],
    expectedBehavior:
      "The causal answer: blocked behind the unpaid deposit — never the circular 'because no preference is selected'.",
    judgeFacts: ["housing", "deposit"],
  },
  {
    id: "rgr-010",
    category: "academic_plan",
    capability: "academic_plan",
    persona: "nearly_complete",
    question: "Am I allowed to jump straight into CS 201?",
    expect: { requestTypes: ["academic_plan", "policy_lookup", "registration_status"] },
    checks: [{ kind: "mentions", any: ["cs 101", "prerequisite"] }],
    expectedBehavior:
      "Course questions get prerequisite evidence: CS 201 needs CS 101 first — even though nothing gates this student's registration.",
    judgeFacts: ["academics", "registration"],
  },
];
