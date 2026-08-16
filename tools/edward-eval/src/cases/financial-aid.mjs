/**
 * Financial-aid coverage beyond the legacy fa1–fa25 set: canonical amounts,
 * award-level actions, disbursement honesty (no ledger exists), and the
 * empty/missing states.
 *
 * Persona truths:
 *  - aid_verification_outstanding  FAFSA received; worksheet action_required;
 *      award acceptance not started; Pell $7,395 + scholarship $8,000 accepted;
 *      loan $3,500 offered (requires action); work-study $2,500 pending
 *  - aid_finalized      everything received/accepted; total $21,395
 *  - fafsa_missing      no FAFSA; zero awards
 *  - no_aid             no awards AND no aid documents at all
 *  - aid_refund_due     accepted aid exceeds $12,000 cost; balance −$9,395
 */

export const FINANCIAL_AID_CASES = [
  {
    id: "aid-001",
    category: "financial_aid",
    capability: "aid_summary",
    persona: "aid_verification_outstanding",
    question: "How much is my Pell Grant?",
    expect: { requestTypes: ["aid_summary", "aid_status"] },
    checks: [{ kind: "mentions", any: ["7,395", "7395"] }],
    expectedBehavior: "The Federal Pell Grant is $7,395, accepted. One number, correct.",
    judgeFacts: ["aid"],
  },
  {
    id: "aid-002",
    category: "financial_aid",
    capability: "aid_summary",
    persona: "aid_finalized",
    question: "What's my total aid package worth now that everything is finalized?",
    expect: { requestTypes: ["aid_summary"] },
    checks: [{ kind: "mentions_amount", fact: "acceptedAidUsd" }],
    expectedBehavior:
      "$21,395 accepted across the four awards; the package is final, not an estimate.",
    judgeFacts: ["aid"],
  },
  {
    id: "aid-003",
    category: "financial_aid",
    capability: "aid_award_acceptance_status",
    persona: "aid_verification_outstanding",
    question: "Is there any award I still need to accept or decline?",
    expect: { requestTypes: ["aid_award_acceptance_status", "aid_status", "aid_remaining_steps", "aid_missing_documents"] },
    checks: [{ kind: "mentions", any: ["loan", "subsidized"] }],
    expectedBehavior:
      "Yes: the Direct Subsidized Loan ($3,500) is offered and awaiting a decision; work-study is pending review, not a decision.",
    judgeFacts: ["aid"],
  },
  {
    id: "aid-004",
    category: "financial_aid",
    capability: "aid_disbursement",
    persona: "aid_ready_to_disburse",
    question: "Has any of my aid actually been paid out yet?",
    expect: { requestTypes: ["aid_disbursement"], requiredTools: ["getAidDisbursements"] },
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "(?:disbursed|paid out) on (?:january|february|march|april|may|june|july|august|september|october|november|december)",
        taxonomy: "HALLUCINATION",
      },
    ],
    expectedBehavior:
      "No disbursement ledger exists — nothing has been recorded as paid out and no schedule is tracked; say so honestly, route to the aid office for timing.",
    judgeFacts: ["aid", "institutional_gaps"],
  },
  {
    id: "aid-005",
    category: "financial_aid",
    capability: "aid_disbursement",
    persona: "aid_verification_outstanding",
    question: "What's holding up my aid money?",
    expect: { requestTypes: ["aid_disbursement", "aid_incomplete_reason", "aid_status"] },
    checks: [{ kind: "mentions", any: ["verification", "worksheet", "accept"] }],
    expectedBehavior:
      "The verification worksheet and award acceptance are the open conditions holding the package; those are the reasons, not an invented schedule.",
    judgeFacts: ["aid"],
  },
  {
    id: "aid-006",
    category: "financial_aid",
    capability: "aid_status",
    persona: "fafsa_missing",
    question: "What does my financial aid look like right now?",
    expect: { requestTypes: ["aid_status", "aid_summary"] },
    checks: [
      { kind: "mentions", any: ["fafsa"] },
      { kind: "not_mentions", all: ["pell", "scholarship"] },
    ],
    expectedBehavior:
      "No FAFSA on file and no awards — the FAFSA is the missing first step. No invented package.",
    judgeFacts: ["aid"],
  },
  {
    id: "aid-007",
    category: "financial_aid",
    capability: "aid_missing_documents",
    persona: "aid_verification_outstanding",
    question: "Which financial aid documents are you still waiting on from me?",
    expect: { requestTypes: ["aid_missing_documents", "aid_status", "aid_remaining_steps"] },
    checks: [
      { kind: "mentions", any: ["worksheet", "verification"] },
      {
        kind: "not_mentions_pattern",
        pattern: "fafsa[^.]{0,40}(?:missing|still need|not (?:been )?(?:received|submitted))",
        taxonomy: "STATE_GROUNDING_FAILURE",
      },
    ],
    expectedBehavior:
      "The verification worksheet (action required) and award acceptance (not started); the FAFSA is already received and must not be re-demanded.",
    judgeFacts: ["aid"],
  },
  {
    id: "aid-008",
    category: "financial_aid",
    capability: "aid_summary",
    persona: "no_aid",
    question: "Show me a breakdown of my awards.",
    expect: { requestTypes: ["aid_summary", "aid_status"] },
    checks: [
      { kind: "mentions", any: ["no ", "not", "none", "empty", "aren't", "don't"] },
      { kind: "not_mentions", all: ["pell", "scholarship", "work-study"] },
    ],
    expectedBehavior:
      "There are no awards to break down — an empty state reported plainly, with where to start if aid is wanted.",
    judgeFacts: ["aid"],
  },
  {
    id: "aid-009",
    category: "financial_aid",
    capability: "aid_verification_status",
    persona: "aid_finalized",
    question: "Do I still owe you a verification worksheet?",
    expect: { requestTypes: ["aid_verification_status", "aid_missing_documents", "aid_status"] },
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "(?:yes|still)[^.]{0,32}(?:owe|need to (?:submit|send|complete))[^.]{0,32}(?:verification|worksheet)",
        taxonomy: "STATE_GROUNDING_FAILURE",
      },
    ],
    expectedBehavior: "No — it was received; verification is complete.",
    judgeFacts: ["aid"],
  },
  {
    id: "aid-010",
    category: "financial_aid",
    capability: "aid_coverage",
    persona: "aid_refund_due",
    question: "Will I get money back this term?",
    tags: ["edge_state"],
    expect: { requestTypes: ["aid_coverage", "aid_disbursement", "student_account", "aid_summary"] },
    checks: [
      { kind: "mentions", any: ["refund", "credit", "more than", "exceeds"] },
      {
        kind: "not_mentions_pattern",
        pattern: "refund[^.]{0,40}(?:within|in|on)\\s+\\d+\\s*(?:day|week|business)",
        taxonomy: "HALLUCINATION",
      },
    ],
    expectedBehavior:
      "Aid exceeds the recorded cost, so the account shows a credit of $9,395 — but no refund process or date exists in the record; route for the mechanics.",
    judgeFacts: ["aid", "account", "institutional_gaps"],
  },
  {
    id: "aid-011",
    category: "financial_aid",
    capability: "aid_status",
    persona: "aid_verification_outstanding",
    question: "Is my work-study confirmed?",
    expect: { requestTypes: ["aid_status", "aid_summary", "aid_award_acceptance_status"] },
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "(?:yes|confirmed)[^.]{0,24}work[- ]study",
        taxonomy: "STATE_GROUNDING_FAILURE",
      },
      { kind: "mentions", any: ["pending", "not", "under review", "awaiting"] },
    ],
    expectedBehavior:
      "No — Federal Work-Study ($2,500) is pending, not confirmed; nothing for the student to do on it right now.",
    judgeFacts: ["aid"],
  },
  {
    id: "aid-012",
    category: "financial_aid",
    capability: "aid_incomplete_reason",
    persona: "fafsa_missing",
    question: "Why does the portal say I have no aid package?",
    expect: { requestTypes: ["aid_incomplete_reason", "aid_status", "aid_application_status", "aid_summary"] },
    checks: [{ kind: "mentions", any: ["fafsa"] }],
    expectedBehavior:
      "Because no FAFSA has been received — that is the first gate; filing it is the next step.",
    judgeFacts: ["aid"],
  },
  {
    id: "aid-013",
    category: "financial_aid",
    capability: "aid_support",
    persona: "aid_verification_outstanding",
    question: "I don't understand the verification worksheet. Who can walk me through it?",
    expect: { requestTypes: ["aid_support", "request_support", "aid_verification_status"] },
    checks: [{ kind: "mentions", any: ["appointment", "financial aid", "support", "contact", "office", "help"] }],
    expectedBehavior:
      "Route to the approved aid support options (appointment, financials page) — grounded contacts only.",
    judgeFacts: ["aid"],
  },
  {
    id: "aid-014",
    category: "financial_aid",
    capability: "aid_summary",
    persona: "aid_verification_outstanding",
    question: "Which of my awards are grants versus loans?",
    expect: { requestTypes: ["aid_summary", "aid_status", "aid_award_acceptance_status"] },
    checks: [
      { kind: "mentions", any: ["pell"] },
      { kind: "mentions", any: ["loan", "subsidized"] },
    ],
    expectedBehavior:
      "Grants/scholarship: Pell and Aster Achievement. Loan: Direct Subsidized ($3,500, needs a decision). Work-study is its own category. Typed correctly.",
    judgeFacts: ["aid"],
  },
  {
    id: "aid-015",
    category: "financial_aid",
    capability: "aid_application_status",
    persona: "aid_finalized",
    question: "Was my FAFSA processed?",
    expect: { requestTypes: ["aid_application_status", "aid_verification_status", "aid_status"] },
    checks: [{ kind: "mentions", any: ["received", "yes", "processed", "complete"] }],
    expectedBehavior: "Yes — received, and every aid document is satisfied.",
    judgeFacts: ["aid"],
  },
  {
    id: "aid-016",
    category: "financial_aid",
    capability: "aid_disbursement",
    persona: "no_aid",
    question: "When will my aid hit my account?",
    tags: ["edge_state"],
    expect: { requestTypes: ["aid_disbursement", "aid_status", "aid_summary"] },
    checks: [
      { kind: "mentions", any: ["no ", "not", "don't", "aren't", "none"] },
      {
        kind: "not_mentions_pattern",
        pattern: "(?:will (?:be )?disbursed?|arrives?) (?:on|in|by)",
        taxonomy: "HALLUCINATION",
      },
    ],
    expectedBehavior:
      "There is no aid on file to disburse — correct the premise before anything else.",
    judgeFacts: ["aid"],
  },
  {
    id: "aid-017",
    category: "financial_aid",
    capability: "aid_next_action",
    persona: "aid_verification_outstanding",
    question: "What's the single most useful thing I can do for my aid right now?",
    expect: { requestTypes: ["aid_next_action", "aid_remaining_steps", "aid_status"] },
    checks: [{ kind: "mentions", any: ["worksheet", "verification"] }],
    expectedBehavior:
      "Complete the verification worksheet (due ~20 days) — it is the earliest-due open aid item; award acceptance follows.",
    judgeFacts: ["aid"],
  },
  {
    id: "aid-018",
    category: "financial_aid",
    capability: "aid_summary",
    persona: "aid_refund_due",
    question: "My aid is more than my costs. Is that a mistake?",
    tags: ["edge_state"],
    expect: { requestTypes: ["aid_summary", "aid_coverage", "student_account", "general_question"] },
    checks: [{ kind: "mentions", any: ["exceed", "more than", "credit", "refund", "higher"] }],
    expectedBehavior:
      "Not a mistake per the record: accepted aid $21,395 against a $12,000 cost of attendance leaves a credit; verify with the aid office if it looks wrong.",
    judgeFacts: ["aid", "account"],
  },
];
