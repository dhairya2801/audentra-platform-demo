/**
 * Registration coverage: eligible, blocked by one/many gates, cross-domain
 * explanations, and non-gates (aid) asserted as gates.
 *
 * Persona truths (gates = unpaid deposit + open blocking requirements):
 *  - new_admit         4 gates: deposit, identity, transcript, immunization
 *  - deposit_posted    3 gates: identity, transcript, immunization
 *  - nearly_complete   0 gates — eligible
 *  - transcript_under_review  4 gates, but the transcript one is the
 *                      university's move (submitted/under review)
 *  - deadline_passed   4 gates, three of them overdue
 */

export const REGISTRATION_CASES = [
  {
    id: "reg-001",
    category: "registration",
    capability: "registration_status",
    persona: "nearly_complete",
    question: "Am I cleared to register for classes?",
    expect: {
      requestTypes: ["registration_status"],
      requiredTools: ["getRegistrationStatus"],
    },
    checks: [
      { kind: "mentions", any: ["yes", "no gate", "nothing", "eligible", "cleared", "no blocker"] },
      {
        kind: "not_mentions_pattern",
        pattern: "(?:blocked|cannot register|can't register)[^.]{0,40}(?:deposit|transcript|immuni|identity)",
        taxonomy: "STATE_GROUNDING_FAILURE",
      },
    ],
    expectedBehavior:
      "Yes — no gate blocks registration; note honestly that no registration window/date is published by the platform.",
    judgeFacts: ["registration", "institutional_gaps"],
  },
  {
    id: "reg-002",
    category: "registration",
    capability: "registration_status",
    persona: "new_admit",
    question: "What exactly is stopping me from registering?",
    expect: {
      requestTypes: ["registration_status", "holds_and_blockers"],
      requiredTools: ["getRegistrationStatus"],
    },
    checks: [
      { kind: "mentions", any: ["deposit"] },
      { kind: "mentions", any: ["transcript"] },
      { kind: "mentions", any: ["immuniz", "immunis"] },
      { kind: "mentions", any: ["identity"] },
    ],
    expectedBehavior:
      "All four gates named — deposit, identity document, transcript, immunization — each with its clearing action. A partial list is an incomplete answer.",
    judgeFacts: ["registration"],
  },
  {
    id: "reg-003",
    category: "registration",
    capability: "registration_status",
    persona: "deposit_posted",
    question: "I paid my deposit. Can I register now?",
    tags: ["cross_domain"],
    expect: { requestTypes: ["registration_status"], requiredTools: ["getRegistrationStatus"] },
    checks: [
      { kind: "mentions", any: ["identity", "transcript", "immuniz", "immunis", "document"] },
      {
        kind: "not_mentions_pattern",
        pattern: "(?:pay|paying)[^.]{0,24}deposit(?![^.]{0,30}(?:posted|already|done|complete))",
        taxonomy: "STATE_GROUNDING_FAILURE",
      },
    ],
    expectedBehavior:
      "Not yet — the deposit is settled, but the three document gates remain; name them rather than re-demanding the deposit.",
    judgeFacts: ["registration", "deposit"],
  },
  {
    id: "reg-004",
    category: "registration",
    capability: "registration_status",
    persona: "transcript_under_review",
    question: "Once my transcript clears review, will I be able to register?",
    tags: ["cross_domain"],
    expect: { requestTypes: ["registration_status"] },
    checks: [
      { kind: "mentions", any: ["deposit", "identity", "immuniz", "immunis"] },
    ],
    expectedBehavior:
      "Not by itself — the deposit, identity document, and immunization gates would still be open; an accurate conditional answer lists them.",
    judgeFacts: ["registration", "documents", "deposit"],
  },
  {
    id: "reg-005",
    category: "registration",
    capability: "registration_status",
    persona: "new_admit",
    question: "When does class registration open?",
    tags: ["unknown_information"],
    expect: { requestTypes: ["registration_status", "policy_lookup", "deadlines", "general_question"] },
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "registration (?:opens?|begins?|starts?) (?:on|in) (?:january|february|march|april|may|june|july|august|september|october|november|december)",
        taxonomy: "HALLUCINATION",
      },
    ],
    expectedBehavior:
      "No registration window is published in the platform — say so, and note what the student can clear meanwhile.",
    judgeFacts: ["registration", "institutional_gaps"],
  },
  {
    id: "reg-006",
    category: "registration",
    capability: "registration_status",
    persona: "deadline_passed",
    question: "Can I still register even though I missed some deadlines?",
    tags: ["edge_state"],
    expect: { requestTypes: ["registration_status", "holds_and_blockers", "deadlines"] },
    checks: [
      { kind: "mentions", any: ["deposit", "transcript", "identity", "immuniz", "immunis"] },
      {
        kind: "not_mentions_pattern",
        pattern: "(?:no longer|cannot ever|permanently|forfeited)",
        taxonomy: "HALLUCINATION",
      },
    ],
    expectedBehavior:
      "The gates are still the open items (now overdue) — clearing them is still the path; no invented lockout policy.",
    judgeFacts: ["registration", "checklist", "institutional_gaps"],
  },
  {
    id: "reg-007",
    category: "registration",
    capability: "registration_status",
    persona: "aid_verification_outstanding",
    question: "Someone said unfinished financial aid blocks class registration. Is that true for me?",
    tags: ["cross_domain"],
    expect: {
      requestTypes: ["registration_status"],
      requiredTools: ["getRegistrationStatus"],
    },
    checks: [{ kind: "no_false_causation" }],
    expectedBehavior:
      "No — aid completeness is not a registration gate here; the actual gates (deposit, documents) are what matter. Correct the folklore with the gate list.",
    judgeFacts: ["registration", "aid"],
  },
  {
    id: "reg-008",
    category: "registration",
    capability: "registration_status",
    persona: "nearly_complete",
    question: "What courses should I register for first?",
    tags: ["cross_domain"],
    expect: { requestTypes: ["academic_plan", "registration_status"] },
    checks: [{ kind: "mentions", any: ["cs 101", "math 140", "eng 110", "course"] }],
    expectedBehavior:
      "Draw on the academic plan: term-1 eligible courses (CS 101, MATH 140, ENG 110); CS 201 is blocked on CS 101. Registration itself has no gates for this student.",
    judgeFacts: ["academics", "registration"],
  },
  {
    id: "reg-009",
    category: "registration",
    capability: "registration_status",
    persona: "payment_pending",
    question: "Is my pending deposit payment enough to unlock registration?",
    tags: ["cross_domain", "edge_state"],
    expect: { requestTypes: ["registration_status", "student_account"] },
    checks: [
      { kind: "deposit_state_consistent" },
    ],
    expectedBehavior:
      "Not until it posts — the gate is 'deposit posted', and the payment is still pending; the document gates remain besides.",
    judgeFacts: ["registration", "deposit"],
  },
  {
    id: "reg-010",
    category: "registration",
    capability: "registration_status",
    persona: "official_hold",
    question: "Is there a registration hold on my record?",
    expect: { requestTypes: ["registration_status", "holds_and_blockers"] },
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "(?:there(?:'s| is) (?:currently )?(?:a|an) |you (?:do )?have (?:a|an) )[^.]{0,16}hold",
        taxonomy: "HALLUCINATION",
      },
    ],
    expectedBehavior:
      "No hold system exists and no hold is on record; the open document gates are the real story.",
    judgeFacts: ["registration", "institutional_gaps"],
  },
];
