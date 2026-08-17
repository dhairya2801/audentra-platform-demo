/**
 * Compound requests: questions that ask more than one thing.
 *
 * Promoted from the Session 4 routing-coverage experiment corpus when the
 * deterministic coverage gate became the production default. Before the gate,
 * `classify()` returned the first matching domain at full confidence and the
 * remaining asks were dropped silently — the answer was correct, confident,
 * and incomplete, which is worse than a miss because nothing signals it.
 *
 * Each case therefore pins the *second* ask with `requiredTools`: the read
 * that answers the part first-match routing used to lose. A regression here
 * means the gate stopped supplementing, not that prose changed.
 *
 * The context-mention cases at the end are the other half of the guarantee.
 * "I paid my deposit. Why can't I apply for housing?" names two domains and
 * asks about one; a gate that widened those would bloat every answer and
 * escalate turns that were already right. They pin that it does not.
 */

export const COMPOUND_REQUEST_CASES = [
  {
    id: "cmp-001",
    category: "multi_domain",
    capability: "housing_eligibility",
    persona: "new_admit",
    question:
      "I paid my deposit. Why can't I apply for housing and what documents am I still missing?",
    tags: ["cross_domain", "compound"],
    expect: {
      requestTypes: ["housing_eligibility"],
      // The documents ask is the one first-match routing dropped.
      requiredTools: ["getStudentHousingEligibility", "getDocumentStatuses"],
    },
    checks: [
      { kind: "mentions", any: ["deposit"] },
      { kind: "mentions", any: ["transcript", "identity", "immuniz", "immunis", "document"] },
      { kind: "no_false_causation" },
    ],
    expectedBehavior:
      "Answer both: the housing step is gated by the unposted deposit, and separately state where the outstanding documents stand. Neither ask may be dropped.",
    judgeFacts: ["housing", "documents", "deposit"],
  },
  {
    id: "cmp-002",
    category: "multi_domain",
    capability: "campus_life",
    persona: "new_admit",
    question: "What's my account balance and what clubs can I join?",
    tags: ["compound"],
    expect: {
      requestTypes: ["campus_life"],
      // Total loss at baseline: the balance was never read at all.
      requiredTools: ["getCampusLife", "getStudentAccountSummary"],
    },
    checks: [
      { kind: "mentions", any: ["balance", "owe", "$"] },
      { kind: "mentions", any: ["club"] },
    ],
    expectedBehavior:
      "State the real outstanding balance and list the clubs. Answering only the clubs is the failure this case exists to catch.",
    judgeFacts: ["account", "campus"],
  },
  {
    id: "cmp-003",
    category: "multi_domain",
    capability: "document_status",
    persona: "transcript_under_review",
    question: "How much do I owe and are my documents all in?",
    tags: ["compound"],
    expect: {
      requestTypes: ["document_status", "missing_documents"],
      requiredTools: ["getDocumentStatuses", "getStudentAccountSummary"],
    },
    checks: [
      { kind: "mentions", any: ["balance", "owe", "$"] },
      { kind: "mentions", any: ["transcript", "document", "review"] },
    ],
    expectedBehavior:
      "Both asks are answered: the amount owed from the account, and the document position including the transcript still under review.",
    judgeFacts: ["account", "documents"],
  },
  {
    id: "cmp-004",
    category: "multi_domain",
    capability: "holds_and_blockers",
    persona: "official_hold",
    question: "Do I have any holds and what clubs can I join?",
    tags: ["compound"],
    expect: {
      requestTypes: ["holds_and_blockers"],
      requiredTools: ["getEnrollmentHolds", "getCampusLife"],
    },
    checks: [{ kind: "mentions", any: ["club"] }],
    expectedBehavior:
      "Report the holds and still answer the unrelated clubs question. The two asks share no domain, so neither may absorb the other.",
    judgeFacts: ["holds", "campus"],
  },
  {
    id: "cmp-005",
    category: "multi_domain",
    capability: "missing_documents",
    persona: "new_admit",
    question: "What documents am I missing and when are they due?",
    tags: ["compound"],
    expect: {
      requestTypes: ["missing_documents", "document_status"],
      // "when are they due" matches no classifier deadline hint; the gate's
      // own vocabulary extension is what routes it.
      requiredTools: ["getDocumentStatuses", "getStudentDeadlines"],
    },
    checks: [
      { kind: "mentions", any: ["transcript", "identity", "immuniz", "immunis", "document"] },
      { kind: "mentions", any: ["due", "deadline", "by "] },
    ],
    expectedBehavior:
      "Name what is outstanding and give the dates. A list with no dates only answers half of what was asked.",
    judgeFacts: ["documents", "deadlines"],
  },

  // --- Context mentions: exactly one ask, and the gate must stay out ---------

  {
    id: "cmp-101",
    category: "causal",
    capability: "housing_eligibility",
    persona: "new_admit",
    question: "I paid my deposit. Why can't I apply for housing?",
    tags: ["compound"],
    expect: {
      requestTypes: ["housing_eligibility"],
      forbiddenTools: ["getCampusLife"],
    },
    checks: [{ kind: "no_false_causation" }],
    expectedBehavior:
      "One ask, not two: the deposit sentence is context for the housing question. Answer housing, and do not widen into an account report.",
    judgeFacts: ["housing", "deposit"],
  },
  {
    id: "cmp-102",
    category: "causal",
    capability: "registration_status",
    persona: "new_admit",
    question: "Why can't I register even though I paid?",
    tags: ["compound"],
    expect: {
      requestTypes: ["registration_status"],
      requiredTools: ["getRegistrationStatus"],
    },
    checks: [{ kind: "no_false_causation" }],
    expectedBehavior:
      "The concessive clause is a premise to check, not a second question. Answer registration; the dependency round verifies the payment claim.",
    judgeFacts: ["registration", "deposit"],
  },
];
