/**
 * Tool/data-failure coverage. Each case boots its host with an injected fault
 * (`--fault primitive=mode`): `timeout` (read timed out), `error` (read
 * failed), `empty` (read succeeded but the records are gone). Edward must
 * acknowledge what it could not read and must not invent — nor silently
 * answer from a half-picture as if it were whole.
 *
 * Ground truth for these cases comes from the SAME persona's fault-free
 * snapshot, so "what the answer should have said if healthy" stays derivable.
 */

export const TOOL_FAILURE_CASES = [
  {
    id: "tf-001",
    category: "tool_failure",
    capability: "document_status",
    persona: "transcript_under_review",
    faults: { documents: "timeout" },
    question: "Have I uploaded my transcript?",
    // Fixed in the v2 round (failed reads are first-class evidence + guard);
    // now a CI-critical invariant.
    critical: true,
    expect: { requestTypes: ["document_status"] },
    checks: [
      { kind: "acknowledges_unavailable" },
      {
        kind: "not_mentions_pattern",
        pattern: "(?:yes|no)[^.]{0,10}(?:,|—|-)?[^.]{0,30}(?:uploaded|submitted|received)(?![^.]{0,60}(?:couldn'?t|could not|unable|right now|unavailable))",
        taxonomy: "TOOL_ERROR_HANDLING",
      },
    ],
    expectedBehavior:
      "The documents read timed out — the honest answer says the document status could not be checked right now, offers a retry/route, and asserts nothing about upload state.",
    judgeFacts: ["documents"],
  },
  {
    id: "tf-002",
    category: "tool_failure",
    capability: "aid_status",
    persona: "aid_verification_outstanding",
    faults: { financials: "error" },
    question: "What's my financial aid status?",
    critical: true,
    expect: { requestTypes: ["aid_status", "aid_summary"] },
    checks: [
      { kind: "acknowledges_unavailable" },
      {
        kind: "not_mentions_pattern",
        pattern: "\\$\\s?\\d",
        taxonomy: "HALLUCINATION",
      },
      { kind: "not_mentions", all: ["pell", "scholarship"] },
    ],
    expectedBehavior:
      "The aid read failed — no award names or amounts may appear; say the aid record couldn't be read and route.",
    judgeFacts: ["aid"],
  },
  {
    id: "tf-003",
    category: "tool_failure",
    capability: "remaining_steps",
    persona: "new_admit",
    faults: { requirements: "error" },
    question: "What's left on my checklist?",
    expect: { requestTypes: ["remaining_steps"] },
    checks: [
      { kind: "acknowledges_unavailable" },
      {
        kind: "not_mentions_pattern",
        pattern: "(?:you (?:still )?need to|remaining[^.]{0,20})(?:pay|submit|upload)",
        taxonomy: "TOOL_ERROR_HANDLING",
      },
    ],
    expectedBehavior:
      "The checklist read failed — no items may be enumerated from memory or guessed; acknowledge and route.",
    judgeFacts: ["checklist"],
  },
  {
    id: "tf-004",
    category: "tool_failure",
    capability: "student_account",
    persona: "deposit_posted",
    faults: { payments: "timeout", financials: "timeout" },
    question: "Did my deposit payment go through?",
    expect: { requestTypes: ["deposit_status", "student_account"] },
    checks: [
      { kind: "acknowledges_unavailable" },
    ],
    expectedBehavior:
      "The payment/financials reads timed out. The dashboard read may still show the deposit as paid — an honest answer either reports only what it could verify (with the caveat) or says it cannot confirm right now. Confidently asserting full payment detail is the failure.",
    judgeFacts: ["deposit"],
  },
  {
    id: "tf-005",
    category: "tool_failure",
    capability: "campus_life",
    persona: "new_admit",
    faults: { campus_life: "empty" },
    question: "What clubs can I join?",
    expect: { requestTypes: ["campus_life"] },
    checks: [
      {
        kind: "not_mentions_fact",
        fact: "clubNames",
        taxonomy: "HALLUCINATION",
      },
    ],
    expectedBehavior:
      "The campus-life read succeeded but lists nothing — say no clubs are published right now (an empty state, not an error), and where they will appear.",
    judgeFacts: [],
  },
  {
    id: "tf-006",
    category: "tool_failure",
    capability: "housing_status",
    persona: "housing_assigned",
    faults: { housing_plan: "error" },
    question: "What housing preference do I have on file?",
    expect: { requestTypes: ["housing_status"] },
    checks: [
      { kind: "acknowledges_unavailable" },
      {
        kind: "not_mentions_pattern",
        pattern: "(?:aster|double|on[- ]campus)",
        taxonomy: "HALLUCINATION",
      },
    ],
    expectedBehavior:
      "The housing read failed — the recorded preference must NOT be recited (it can't have been read); acknowledge and route.",
    judgeFacts: ["housing"],
  },
  {
    id: "tf-007",
    category: "tool_failure",
    capability: "aid_summary",
    persona: "aid_verification_outstanding",
    faults: { financials: "empty" },
    question: "How much aid do I have?",
    tags: ["edge_state"],
    expect: { requestTypes: ["aid_summary", "aid_status"] },
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "(?:7,?395|8,?000|pell|aster achievement)",
        taxonomy: "HALLUCINATION",
      },
    ],
    expectedBehavior:
      "The read 'succeeded' but shows no awards — the truthful reply reports an empty package (or flags the inconsistency), never recites the awards that exist in a healthy record.",
    judgeFacts: [],
  },
  {
    id: "tf-008",
    category: "tool_failure",
    capability: "missing_documents",
    persona: "new_admit",
    faults: { documents: "error" },
    question: "Which documents am I missing?",
    expect: { requestTypes: ["missing_documents"] },
    checks: [
      { kind: "acknowledges_unavailable" },
    ],
    expectedBehavior:
      "The checklist read still works (three document requirements are open) but upload verification failed — a good answer gives the checklist view WITH the caveat that current upload status couldn't be verified.",
    judgeFacts: ["documents"],
  },
  {
    id: "tf-009",
    category: "tool_failure",
    capability: "academic_plan",
    persona: "new_admit",
    faults: { academics: "timeout" },
    question: "What courses are on my plan?",
    expect: { requestTypes: ["academic_plan"] },
    checks: [
      { kind: "acknowledges_unavailable" },
      {
        kind: "not_mentions_pattern",
        pattern: "cs ?10?1|math ?140|eng ?110",
        taxonomy: "HALLUCINATION",
      },
    ],
    expectedBehavior:
      "The academics read timed out — no course codes may be quoted; acknowledge and suggest retrying or the Academics page.",
    judgeFacts: [],
  },
  {
    id: "tf-010",
    category: "tool_failure",
    capability: "registration_status",
    persona: "new_admit",
    faults: { requirements: "timeout", dashboard: "timeout" },
    question: "Am I eligible to register?",
    critical: true,
    expect: { requestTypes: ["registration_status"] },
    checks: [
      { kind: "acknowledges_unavailable" },
      {
        kind: "not_mentions_pattern",
        pattern: "(?:yes|you (?:are|'re) (?:eligible|cleared|all set))",
        taxonomy: "TOOL_ERROR_HANDLING",
      },
    ],
    expectedBehavior:
      "The gate derivation lost its inputs — 'eligible' must NOT be claimed from an empty gate list built on failed reads; say eligibility couldn't be determined right now.",
    judgeFacts: ["registration"],
  },
];
