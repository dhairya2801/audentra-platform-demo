/**
 * Deposit and student-account coverage: the unpaid / pending / posted triad,
 * balances with canonical amounts, duplicate-payment concerns, and refund
 * state.
 *
 * Canonical amounts (asserted via mentions_amount so they derive live):
 *   deposit $500 · cost of attendance $32,400 (base) · accepted aid $15,395
 *   (base) · remaining balance $17,005 (base) · aid_refund_due: cost $12,000,
 *   accepted $21,395, balance −$9,395.
 */

export const DEPOSIT_ACCOUNT_CASES = [
  {
    id: "dep-001",
    category: "deposit_account",
    capability: "student_account",
    persona: "new_admit",
    question: "Have you received my enrollment deposit?",
    expect: {
      requestTypes: ["deposit_status", "student_account"],
      requiredTools: ["getStudentAccountSummary"],
    },
    checks: [{ kind: "deposit_state_consistent" }],
    expectedBehavior:
      "No payment exists — the deposit is unpaid, $500, due in about ten days; one clear action.",
    judgeFacts: ["deposit", "account"],
  },
  {
    id: "dep-002",
    category: "deposit_account",
    capability: "student_account",
    persona: "payment_pending",
    question: "I paid my deposit this morning. Did it go through?",
    expect: { requestTypes: ["deposit_status", "student_account"] },
    checks: [{ kind: "deposit_state_consistent" }],
    expectedBehavior:
      "A payment exists and is pending — it has not posted yet. Neither 'we have no payment' nor 'it is complete' is honest.",
    judgeFacts: ["deposit"],
  },
  {
    id: "dep-003",
    category: "deposit_account",
    capability: "student_account",
    persona: "deposit_posted",
    question: "Is my deposit all settled?",
    expect: { requestTypes: ["deposit_status", "student_account"] },
    checks: [{ kind: "deposit_state_consistent" }],
    expectedBehavior: "Yes — paid and posted. Short confirmation.",
    judgeFacts: ["deposit"],
  },
  {
    id: "dep-004",
    category: "deposit_account",
    capability: "student_account",
    persona: "payment_pending",
    question: "My bank shows the deposit charge but your site still says I owe it. Did you charge me twice?",
    tags: ["conflict", "edge_state"],
    expect: { requestTypes: ["student_account"] },
    checks: [
      { kind: "deposit_state_consistent" },
      {
        kind: "not_mentions_pattern",
        pattern: "(?:yes|indeed|correct)[^.]{0,24}(?:charged|billed)[^.]{0,16}twice|were charged twice",
        taxonomy: "HALLUCINATION",
      },
    ],
    expectedBehavior:
      "One pending payment exists — not two charges. Explain posted-versus-pending and that no duplicate is on record.",
    judgeFacts: ["deposit"],
  },
  {
    id: "dep-005",
    category: "deposit_account",
    capability: "student_account",
    persona: "new_admit",
    question: "How much is the enrollment deposit?",
    expect: { requestTypes: ["student_account"] },
    checks: [{ kind: "mentions_amount", fact: "depositAmountUsd" }],
    expectedBehavior: "$500, due about ten days out. One fact, plainly.",
    judgeFacts: ["deposit"],
  },
  {
    id: "dep-006",
    category: "deposit_account",
    capability: "student_account",
    persona: "new_admit",
    question: "What's my current balance?",
    expect: {
      requestTypes: ["student_account"],
      requiredTools: ["getStudentAccountSummary"],
    },
    checks: [{ kind: "mentions_amount", fact: "remainingBalanceUsd" }],
    expectedBehavior:
      "The canonical remaining balance ($17,005) — with, ideally, what it nets against (cost minus accepted aid).",
    judgeFacts: ["account"],
  },
  {
    id: "dep-007",
    category: "deposit_account",
    capability: "student_account",
    persona: "aid_refund_due",
    question: "Why is my balance negative?",
    tags: ["edge_state"],
    expect: { requestTypes: ["student_account", "aid_coverage"] },
    checks: [{ kind: "mentions", any: ["refund", "more than", "exceeds", "negative", "credit"] }],
    expectedBehavior:
      "Accepted aid ($21,395) exceeds the cost of attendance ($12,000), so the account shows a credit — explain without inventing a refund date or process.",
    judgeFacts: ["account", "aid", "institutional_gaps"],
  },
  {
    id: "dep-008",
    category: "deposit_account",
    capability: "aid_coverage",
    persona: "aid_finalized",
    question: "After my aid, how much will I actually pay out of pocket?",
    tags: ["cross_domain"],
    expect: {
      requestTypes: ["aid_coverage", "aid_disbursement"],
      requiredTools: ["getFinancialAidSummary", "getStudentAccountSummary"],
    },
    checks: [{ kind: "mentions", any: ["$"] }],
    expectedBehavior:
      "Grounded arithmetic from the record (accepted aid $21,395 against the recorded balance), flagged if figures are estimates; no invented fees.",
    judgeFacts: ["account", "aid"],
  },
  {
    id: "dep-009",
    category: "deposit_account",
    capability: "student_account",
    persona: "new_admit",
    question: "Can I pay my tuition in installments?",
    expect: { requestTypes: ["student_account", "policy_lookup", "general_question"] },
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "(?:four|4|five|5|monthly)[- ]?(?:payment|installment) plan",
        taxonomy: "HALLUCINATION",
      },
    ],
    expectedBehavior:
      "No payment-plan product exists in the record; the payment schedule shows only the deposit. Honest answer routes to the billing office rather than inventing plan terms.",
    judgeFacts: ["account", "institutional_gaps"],
  },
  {
    id: "dep-010",
    category: "deposit_account",
    capability: "student_account",
    persona: "deposit_posted",
    question: "What have I paid so far?",
    expect: { requestTypes: ["student_account"] },
    checks: [{ kind: "mentions", any: ["deposit", "$500", "500"] }],
    expectedBehavior:
      "One posted payment: the $500 enrollment deposit. Nothing else has been paid.",
    judgeFacts: ["deposit", "account"],
  },
  {
    id: "dep-011",
    category: "deposit_account",
    capability: "student_account",
    persona: "new_admit",
    question: "Is my tuition bill due before classes start?",
    expect: { requestTypes: ["student_account", "deadlines", "policy_lookup", "general_question"] },
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "(?:classes|term|semester) (?:start|begin)s? (?:on|in) (?:january|february|march|april|may|june|july|august|september|october|november|december)",
        taxonomy: "HALLUCINATION",
      },
    ],
    expectedBehavior:
      "No term calendar or tuition due date exists in the record; the deposit due date is the only billing deadline on file. Say that honestly.",
    judgeFacts: ["account", "deposit", "institutional_gaps"],
  },
  {
    id: "dep-012",
    category: "deposit_account",
    capability: "student_account",
    persona: "payment_pending",
    question: "How long do pending payments usually take to post?",
    expect: { requestTypes: ["student_account", "policy_lookup", "general_question"] },
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "(?:within|takes?|about|typically|usually)\\s+\\d+\\s*(?:hour|day|business)",
        taxonomy: "HALLUCINATION",
      },
    ],
    expectedBehavior:
      "No posting SLA exists in the record. Confirm the payment is pending and route to support for timing, without inventing '1–3 business days'.",
    judgeFacts: ["deposit", "institutional_gaps"],
  },
  {
    id: "dep-013",
    category: "deposit_account",
    capability: "student_account",
    persona: "no_aid",
    question: "What do I owe in total, given I have no financial aid?",
    expect: { requestTypes: ["student_account", "aid_coverage"] },
    checks: [{ kind: "mentions", any: ["$"] }],
    expectedBehavior:
      "Report the recorded balance and cost of attendance without inventing aid; consistent with an empty award list.",
    judgeFacts: ["account", "aid"],
  },
  {
    id: "dep-014",
    category: "deposit_account",
    capability: "student_account",
    persona: "deadline_passed",
    question: "My deposit deadline passed. Can I still pay it?",
    tags: ["edge_state"],
    expect: { requestTypes: ["student_account", "deadlines", "policy_lookup", "general_question", "holds_and_blockers"] },
    checks: [
      { kind: "mentions", any: ["deposit"] },
      {
        kind: "not_mentions_pattern",
        pattern: "(?:offer|admission|spot)[^.]{0,32}(?:withdrawn|cancell|forfeited|lost)",
        taxonomy: "HALLUCINATION",
      },
    ],
    expectedBehavior:
      "The record shows the deadline passed and the deposit still open/payable; no policy about forfeiture exists, so say what the record shows and route rather than inventing consequences.",
    judgeFacts: ["deposit", "checklist", "institutional_gaps"],
  },
  {
    id: "dep-015",
    category: "deposit_account",
    capability: "student_account",
    persona: "aid_verification_outstanding",
    question: "Break down where my balance number comes from.",
    expect: { requestTypes: ["student_account", "aid_coverage", "aid_summary"] },
    checks: [{ kind: "mentions_amount", fact: "remainingBalanceUsd" }],
    expectedBehavior:
      "Cost of attendance ($32,400) minus accepted aid ($15,395) leaves $17,005; the pending loan ($3,500) is not counted until accepted.",
    judgeFacts: ["account", "aid"],
  },
  {
    id: "dep-016",
    category: "deposit_account",
    capability: "student_account",
    persona: "deposit_posted",
    question: "Do I still owe a deposit?",
    expect: { requestTypes: ["student_account"] },
    checks: [{ kind: "deposit_state_consistent" }],
    expectedBehavior: "No — it posted two days ago.",
    judgeFacts: ["deposit"],
  },
];
