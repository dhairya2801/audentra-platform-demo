/**
 * Cross-domain coverage: realistic questions whose correct answer needs 2–4
 * domains, with tool expectations derived from the actual domain model (the
 * gate list and the dependency-read table in planner.py), not intuition.
 *
 * Key domain truths:
 *  - Aid completeness is NOT a registration gate.
 *  - Housing is gated by the deposit (housing step blocked until posted).
 *  - Registration gates = unpaid deposit + open blocking requirements.
 *  - A pending payment is not a posted deposit.
 */

export const CROSS_DOMAIN_CASES = [
  {
    id: "xd-001",
    category: "cross_domain",
    capability: "registration_status",
    persona: "deposit_posted",
    question: "I paid my deposit and submitted my FAFSA. Why can't I register?",
    tags: ["cross_domain"],
    expect: {
      requestTypes: ["registration_status"],
      requiredTools: ["getRegistrationStatus"],
    },
    checks: [
      { kind: "mentions", any: ["identity", "transcript", "immuniz", "immunis"] },
      { kind: "no_false_causation" },
    ],
    expectedBehavior:
      "Both premises are true and neither is the blocker: the document gates (identity, transcript, immunization) are. Confirm what's done, name what's left.",
    judgeFacts: ["registration", "deposit", "aid"],
  },
  {
    id: "xd-002",
    category: "cross_domain",
    capability: "enrollment_documents",
    persona: "transcript_under_review",
    question: "I uploaded my transcript. Is anything else stopping me from enrolling?",
    tags: ["cross_domain"],
    expect: {
      requestTypes: ["holds_and_blockers", "remaining_steps", "document_status", "registration_status", "missing_documents", "onboarding_status"],
    },
    checks: [
      { kind: "mentions", any: ["deposit"] },
      { kind: "mentions", any: ["identity", "immuniz", "immunis"] },
    ],
    expectedBehavior:
      "Yes: the deposit, identity document, and immunization record are still open; the transcript itself is under review and needs nothing from the student.",
    judgeFacts: ["registration", "documents", "deposit"],
  },
  {
    id: "xd-003",
    category: "cross_domain",
    capability: "housing_eligibility",
    persona: "new_admit",
    question: "Can I apply for housing yet, or do I have to finish something else first?",
    tags: ["cross_domain"],
    expect: {
      requestTypes: ["housing_eligibility"],
      requiredTools: ["getStudentHousingEligibility"],
    },
    checks: [{ kind: "mentions", any: ["deposit"] }],
    expectedBehavior:
      "Something else first: the enrollment deposit — housing opens once it posts. The cross-domain link is the answer.",
    judgeFacts: ["housing", "deposit"],
  },
  {
    id: "xd-004",
    category: "cross_domain",
    capability: "aid_coverage",
    persona: "aid_verification_outstanding",
    question: "Does my aid cover my bill, and is anything holding the aid itself up?",
    tags: ["cross_domain"],
    expect: {
      requestTypes: ["aid_coverage", "aid_status", "aid_incomplete_reason", "aid_summary"],
      anyOfTools: [["getFinancialAidSummary", "getFinancialAidStatus"]],
    },
    checks: [
      { kind: "mentions", any: ["$"] },
      { kind: "mentions", any: ["verification", "worksheet", "accept"] },
    ],
    expectedBehavior:
      "Two halves, both answered: accepted aid ($15,395) against the balance ($17,005 remains), AND the open conditions (worksheet, award acceptance).",
    judgeFacts: ["aid", "account"],
  },
  {
    id: "xd-005",
    category: "cross_domain",
    capability: "registration_status",
    persona: "payment_pending",
    question: "My deposit payment is processing. What does that mean for housing and registration?",
    tags: ["cross_domain"],
    expect: {
      requestTypes: ["registration_status", "housing_eligibility", "student_account", "housing_status", "holds_and_blockers"],
    },
    checks: [
      { kind: "deposit_state_consistent" },
      { kind: "mentions", any: ["housing"] },
      { kind: "mentions", any: ["regist"] },
    ],
    expectedBehavior:
      "Until it posts, both stay gated: housing remains blocked and the deposit gate remains on registration (documents besides). Once posted, housing opens; documents still gate registration.",
    judgeFacts: ["registration", "housing", "deposit"],
  },
  {
    id: "xd-006",
    category: "cross_domain",
    capability: "next_action",
    persona: "new_admit",
    question: "I want to sort out housing and financial aid this week. Where do I start?",
    tags: ["cross_domain"],
    expect: {},
    checks: [
      { kind: "mentions", any: ["deposit"] },
      { kind: "mentions", any: ["worksheet", "verification"] },
    ],
    expectedBehavior:
      "A joined-up plan: pay the deposit (unlocks housing), then the verification worksheet (unblocks aid). Both domains, correct ordering logic.",
    judgeFacts: ["housing", "aid", "deposit", "checklist"],
  },
  {
    id: "xd-007",
    category: "cross_domain",
    capability: "aid_disbursement",
    persona: "aid_ready_to_disburse",
    question: "Everything on my aid is done — so when does the money show up, and does my balance change?",
    tags: ["cross_domain"],
    expect: {
      requestTypes: ["aid_disbursement", "aid_coverage", "student_account"],
      requiredTools: ["getAidDisbursements"],
    },
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "(?:arrives?|disbursed?|paid out)[^.]{0,24}(?:on|by) (?:january|february|march|april|may|june|july|august|september|october|november|december)",
        taxonomy: "HALLUCINATION",
      },
    ],
    expectedBehavior:
      "The aid side is genuinely complete, but no disbursement schedule exists — no date can be given; the balance question answered from the account record; route for timing.",
    judgeFacts: ["aid", "account", "institutional_gaps"],
  },
  {
    id: "xd-008",
    category: "cross_domain",
    capability: "holds_and_blockers",
    persona: "deadline_passed",
    question: "Between my overdue items, housing, and aid — what's the full picture of where I'm stuck?",
    tags: ["cross_domain"],
    expect: {},
    checks: [
      { kind: "mentions", any: ["overdue", "passed", "missed", "past due", "late"] },
      { kind: "mentions", any: ["deposit"] },
      { kind: "mentions", any: ["housing"] },
    ],
    expectedBehavior:
      "A 3-domain synthesis: overdue deposit/identity/transcript; housing blocked behind the deposit; aid waiting on the worksheet. Organized, complete, no invention.",
    judgeFacts: ["checklist", "housing", "aid", "deposit"],
  },
  {
    id: "xd-009",
    category: "cross_domain",
    capability: "registration_status",
    persona: "nearly_complete",
    question: "If I pick my housing today, is there anything at all left before I can register?",
    tags: ["cross_domain", "edge_state"],
    expect: { requestTypes: ["registration_status", "housing_status", "housing_eligibility", "holds_and_blockers"] },
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "(?:housing|preference)[^.]{0,48}(?:blocks?|required for|needed for|before you can) regist",
        taxonomy: "CROSS_DOMAIN_REASONING_FAILURE",
      },
    ],
    expectedBehavior:
      "Housing choice does not gate registration at all — nothing blocks registration for this student either way. Decoupling the two domains correctly is the test.",
    judgeFacts: ["registration", "housing"],
  },
  {
    id: "xd-010",
    category: "cross_domain",
    capability: "student_account",
    persona: "fafsa_missing",
    question: "My balance looks huge and I have no aid. What's the connection and what do I do?",
    tags: ["cross_domain"],
    expect: {},
    checks: [{ kind: "mentions", any: ["fafsa"] }],
    expectedBehavior:
      "The connection: no FAFSA → no aid package → nothing offsetting the balance. Filing the FAFSA is the concrete first step.",
    judgeFacts: ["account", "aid"],
  },
  {
    id: "xd-011",
    category: "cross_domain",
    capability: "housing_eligibility",
    persona: "aid_verification_outstanding",
    question: "Does my unfinished aid verification stop me from applying for housing?",
    tags: ["cross_domain"],
    expect: { requestTypes: ["housing_eligibility", "housing_status", "aid_verification_status", "registration_status"] },
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "(?:^yes|verification (?:does |will )?(?:stops?|blocks?|prevents?)\\b)",
        taxonomy: "CROSS_DOMAIN_REASONING_FAILURE",
      },
      { kind: "mentions", any: ["deposit"] },
    ],
    expectedBehavior:
      "No — aid verification has nothing to do with housing; the deposit is what actually gates it. Correct the assumed link, name the real one.",
    judgeFacts: ["housing", "aid", "deposit"],
  },
  {
    id: "xd-012",
    category: "cross_domain",
    capability: "appointments",
    persona: "advising_booked",
    question: "Before my advising session, can you summarize my documents, money, and housing situation?",
    tags: ["cross_domain"],
    expect: {},
    checks: [
      { kind: "mentions", any: ["identity", "transcript", "immuniz", "immunis", "document"] },
      { kind: "mentions", any: ["housing", "preference"] },
    ],
    expectedBehavior:
      "A grounded 3-domain briefing: documents still open, deposit posted with balances as recorded, housing step open awaiting a preference. Organized for the meeting.",
    judgeFacts: ["documents", "account", "housing", "deposit", "appointments"],
  },
  {
    id: "xd-013",
    category: "cross_domain",
    capability: "registration_status",
    persona: "aid_finalized",
    question: "My aid is 100% finalized. Doesn't that mean I'm cleared for classes?",
    tags: ["cross_domain", "conflict"],
    expect: {
      requestTypes: ["registration_status"],
      requiredTools: ["getRegistrationStatus"],
    },
    checks: [
      { kind: "mentions", any: ["deposit"] },
      { kind: "no_false_causation" },
    ],
    expectedBehavior:
      "No — aid was never the gate. The deposit and the three documents are still open and they, not aid, block registration. Decouple the domains explicitly.",
    judgeFacts: ["registration", "aid", "deposit"],
  },
  {
    id: "xd-014",
    category: "cross_domain",
    capability: "deadlines",
    persona: "aid_verification_outstanding",
    question: "Across everything — enrollment and aid — what are my next three deadlines?",
    tags: ["cross_domain"],
    expect: {
      requestTypes: ["deadlines"],
      requiredTools: ["getStudentDeadlines"],
    },
    checks: [
      { kind: "mentions", any: ["deposit"] },
      { kind: "mentions", any: ["worksheet", "verification"] },
    ],
    expectedBehavior:
      "Merged and ordered: deposit (~10d), identity/transcript (~15d), worksheet (~20d) — both domains in one timeline, correctly sequenced.",
    judgeFacts: ["checklist", "aid", "deposit"],
  },
];
