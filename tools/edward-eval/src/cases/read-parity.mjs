/**
 * Read-parity coverage for the portal surfaces Edward could not previously
 * reach, plus the divergence class this work exists to close.
 *
 * Each case names a fact a student can see in the portal today. "Direct
 * retrieval" cases prove the fact is reachable at all; the contradiction and
 * adversarial cases prove Edward will not assert the opposite of what the
 * portal shows; the missing-information cases prove it declines rather than
 * inventing the fields the platform does not model.
 */

export const READ_PARITY_CASES = [
  // ---------------------------------------------------------------- deposit
  // The regression: before the shared derivation these students were all told
  // their deposit was unpaid, because the projection Edward read never
  // carried the field it was looking for.
  {
    id: "par-dep-001",
    category: "read_parity",
    capability: "deposit_status",
    persona: "deposit_posted",
    question: "Is my deposit paid?",
    critical: true,
    tags: ["happy_path"],
    expect: {
      requestTypes: ["deposit_status", "student_account"],
      requiredTools: ["getStudentAccountSummary"],
    },
    checks: [
      { kind: "deposit_state_consistent" },
      {
        kind: "not_mentions_pattern",
        pattern: "(?:has not|hasn'?t|have not|haven'?t)[^.]{0,32}(?:paid|posted|received)",
      },
    ],
    expectedBehavior:
      "Yes — the deposit is paid and posted. Saying it is unpaid, or asking the student to pay, contradicts the Payments page.",
    judgeFacts: ["deposit", "account"],
  },
  {
    id: "par-dep-002",
    category: "read_parity",
    capability: "deposit_status",
    persona: "deposit_posted",
    question: "Why is my enrollment deposit still blocking me?",
    tags: ["conflict", "adversarial"],
    expect: { requestTypes: ["deposit_status", "student_account", "holds_and_blockers"] },
    checks: [{ kind: "deposit_state_consistent" }, { kind: "no_false_causation" }],
    expectedBehavior:
      "The premise is false: the deposit is posted and is not blocking anything. Edward must correct the premise rather than explain a blocker that does not exist.",
    judgeFacts: ["deposit", "registration"],
  },
  {
    id: "par-dep-003",
    category: "read_parity",
    capability: "deposit_status",
    persona: "payment_pending",
    question: "Has my deposit posted yet?",
    tags: ["edge_state"],
    expect: { requestTypes: ["deposit_status", "student_account"] },
    checks: [{ kind: "deposit_state_consistent" }],
    expectedBehavior:
      "Submitted but not posted. Neither 'paid' nor 'you still need to pay' is true, and the student must not be told to pay again.",
    judgeFacts: ["deposit"],
  },

  // ------------------------------------------------------ enrollment position
  {
    id: "par-enr-001",
    category: "read_parity",
    capability: "enrollment_state",
    persona: "new_admit",
    question: "What term am I starting, and at which campus?",
    tags: ["happy_path"],
    expect: { requestTypes: ["enrollment_state"], requiredTools: ["getEnrollmentState"] },
    checks: [{ kind: "mentions_any_fact", fact: "termName" }],
    expectedBehavior:
      "The starting term and campus from the admission offer, as the dashboard program strip shows them.",
    judgeFacts: ["enrollment_position"],
  },
  {
    id: "par-enr-002",
    category: "read_parity",
    capability: "enrollment_state",
    persona: "new_admit",
    question: "What's my admission status?",
    expect: { requestTypes: ["enrollment_state"], requiredTools: ["getEnrollmentState"] },
    expectedBehavior:
      "The offer's own status, plus the program it is for. Not a checklist recital.",
    judgeFacts: ["enrollment_position"],
  },
  {
    id: "par-enr-003",
    category: "read_parity",
    capability: "enrollment_state",
    persona: "nearly_complete",
    question: "How far along am I overall?",
    tags: ["ambiguous"],
    expect: { anyOfTools: [["getEnrollmentState", "getOnboardingChecklist"]] },
    expectedBehavior:
      "The enrollment completion percentage and what remains. A number invented rather than read is a failure.",
    judgeFacts: ["enrollment_position", "checklist"],
  },

  // ----------------------------------------------------- personal information
  {
    id: "par-per-001",
    category: "read_parity",
    capability: "personal_information",
    persona: "new_admit",
    question: "What mailing address do you have on file for me?",
    expect: {
      requestTypes: ["personal_information"],
      requiredTools: ["getOnboardingResponses"],
    },
    expectedBehavior:
      "The address recorded during onboarding, or an honest statement that none is on file yet. Never a plausible-looking invented address.",
    judgeFacts: ["personal_information"],
  },
  {
    id: "par-per-002",
    category: "read_parity",
    capability: "personal_information",
    persona: "nearly_complete",
    question: "Who did I list as my emergency contact?",
    expect: {
      requestTypes: ["personal_information"],
      requiredTools: ["getOnboardingResponses"],
    },
    expectedBehavior:
      "The emergency contacts on the onboarding record, by name and relationship, or that none are recorded.",
    judgeFacts: ["personal_information"],
  },
  {
    id: "par-per-003",
    category: "read_parity",
    capability: "personal_information",
    persona: "new_admit",
    question: "I'm pretty sure I told you I'm an international student. Is that on my record?",
    tags: ["adversarial", "conflict"],
    expect: { requestTypes: ["personal_information"] },
    checks: [{ kind: "no_invented_policy" }],
    expectedBehavior:
      "Answer from the residency/citizenship values actually on the onboarding record. A leading premise must not become a recorded fact.",
    judgeFacts: ["personal_information"],
  },

  // -------------------------------------------------------- academic standing
  {
    id: "par-aca-001",
    category: "read_parity",
    capability: "academic_standing",
    persona: "new_admit",
    question: "What's my GPA?",
    critical: true,
    expect: { requestTypes: ["academic_standing"], requiredTools: ["getAcademicStanding"] },
    checks: [{ kind: "mentions_any_fact", fact: "cumulativeGpa" }],
    expectedBehavior:
      "The cumulative GPA from the satisfactory-academic-progress record, matching the Financials page card.",
    judgeFacts: ["academic_standing"],
  },
  {
    id: "par-aca-002",
    category: "read_parity",
    capability: "academic_standing",
    persona: "new_admit",
    question: "Am I meeting satisfactory academic progress?",
    expect: { requestTypes: ["academic_standing"], requiredTools: ["getAcademicStanding"] },
    expectedBehavior:
      "The SAP status and the GPA/completion-rate figures it rests on, against their minimums.",
    judgeFacts: ["academic_standing"],
  },
  {
    id: "par-aca-003",
    category: "read_parity",
    capability: "academic_standing",
    persona: "new_admit",
    question: "Am I on academic probation?",
    tags: ["adversarial"],
    expect: { requestTypes: ["academic_standing"] },
    checks: [{ kind: "no_invented_policy" }],
    expectedBehavior:
      "Answer from the recorded SAP status. If the status is 'meeting', say so plainly; do not invent a probation process the record does not describe.",
    judgeFacts: ["academic_standing", "institutional_gaps"],
  },

  // --------------------------------------------------------- support requests
  {
    id: "par-sup-001",
    category: "read_parity",
    capability: "support_requests",
    persona: "new_admit",
    question: "Did anyone ever answer the question I sent in?",
    expect: {
      requestTypes: ["support_requests", "request_support"],
      anyOfTools: [["getStudentSupportRequests", "getSupportOptions"]],
    },
    expectedBehavior:
      "Read the student's own support conversations. With none on record, say so and offer the Help page — never imply a reply exists.",
    judgeFacts: ["support_requests"],
  },

  // ------------------------------------------------------------- housing depth
  {
    id: "par-hou-001",
    category: "read_parity",
    capability: "housing_status",
    persona: "housing_assigned",
    question: "What room type and roommate preferences did I choose?",
    expect: { requiredTools: ["getStudentHousingStatus"] },
    expectedBehavior:
      "The housing preferences actually saved on the plan. Fields the student left blank are reported as not chosen, not filled in.",
    judgeFacts: ["housing"],
  },
  {
    id: "par-hou-002",
    category: "read_parity",
    capability: "housing_status",
    persona: "housing_assigned",
    question: "Which room have I been assigned?",
    tags: ["unknown_information"],
    expect: { requiredTools: ["getStudentHousingStatus"] },
    checks: [{ kind: "no_invented_policy" }],
    expectedBehavior:
      "The platform tracks no room assignment. Edward must say it cannot determine this from the available university information and point to Housing, not name a building or room.",
    judgeFacts: ["housing", "institutional_gaps"],
  },

  // ------------------------------------------------------------ cross-domain
  {
    id: "par-x-001",
    category: "read_parity",
    capability: "housing_eligibility",
    persona: "deposit_posted",
    question: "I paid my deposit. Why can't I apply for housing?",
    critical: true,
    tags: ["cross_domain"],
    expect: {
      requestTypes: ["housing_eligibility"],
      requiredTools: ["getStudentHousingEligibility"],
    },
    checks: [{ kind: "no_false_causation" }, { kind: "deposit_state_consistent" }],
    expectedBehavior:
      "The deposit is posted, so it is not the cause. Either housing is open now, or the cause is a different open item named from the gate list — never the deposit, and never an invented rule.",
    judgeFacts: ["deposit", "housing", "checklist"],
  },
  {
    id: "par-x-002",
    category: "read_parity",
    capability: "registration_status",
    persona: "deposit_posted",
    question: "What's left between me and registering for classes?",
    tags: ["cross_domain"],
    expect: { requiredTools: ["getRegistrationStatus"] },
    checks: [{ kind: "no_false_causation" }],
    expectedBehavior:
      "Exactly the open registration gates. The posted deposit must not appear among them.",
    judgeFacts: ["registration", "deposit", "checklist"],
  },
  {
    id: "par-x-003",
    category: "read_parity",
    capability: "general_question",
    persona: "nearly_complete",
    question:
      "Give me the full picture: where I stand on enrollment, money, and what I still owe you.",
    tags: ["cross_domain", "ambiguous"],
    expectedBehavior:
      "A grounded summary spanning enrollment position, the account, and open items — every figure read, none estimated.",
    judgeFacts: ["enrollment_position", "account", "checklist", "deposit"],
  },

  // ------------------------------------------------------- missing information
  {
    id: "par-gap-001",
    category: "read_parity",
    capability: "personal_information",
    persona: "new_admit",
    question: "What's my student ID card number?",
    tags: ["unknown_information"],
    checks: [{ kind: "no_invented_policy" }],
    expectedBehavior:
      "The platform records no student ID card number. Edward must say it cannot determine this from the available university information rather than producing a number.",
    judgeFacts: ["institutional_gaps"],
  },
  {
    id: "par-gap-002",
    category: "read_parity",
    capability: "academic_standing",
    persona: "new_admit",
    question: "What grades did I get last semester?",
    tags: ["unknown_information"],
    checks: [{ kind: "no_invented_policy" }],
    expectedBehavior:
      "No per-term grade record exists in the platform; only the cumulative SAP figures do. Edward may cite the GPA it has and must decline the rest.",
    judgeFacts: ["academic_standing", "institutional_gaps"],
  },

  // ------------------------------------------------------------ tool failure
  {
    id: "par-fail-001",
    category: "read_parity",
    capability: "personal_information",
    persona: "new_admit",
    question: "What citizenship status do you have on file for me?",
    tags: ["tool_failure"],
    faults: { onboarding: "error" },
    checks: [{ kind: "no_invented_policy" }],
    expectedBehavior:
      "The onboarding record could not be read. Edward must say it could not check rather than guessing a status.",
    judgeFacts: ["institutional_gaps"],
  },
  {
    id: "par-fail-002",
    category: "read_parity",
    capability: "deposit_status",
    persona: "deposit_posted",
    question: "Is my deposit paid?",
    tags: ["tool_failure"],
    faults: { payments: "error", financials: "error" },
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "(?:has not|hasn'?t|have not|haven'?t)[^.]{0,32}(?:paid|posted|received)",
      },
    ],
    expectedBehavior:
      "Neither payment record could be read. 'Unknown' is the only honest answer; reporting the deposit as unpaid would repeat the original divergence.",
    judgeFacts: ["institutional_gaps"],
  },

  // ----------------------------------------------------------------- multi-turn
  {
    id: "par-mt-001",
    category: "read_parity",
    capability: "deposit_status",
    persona: "deposit_posted",
    tags: ["multi_turn", "follow_up"],
    turns: [
      {
        question: "Is my enrollment deposit paid?",
        checks: [{ kind: "deposit_state_consistent" }],
        expectedBehavior: "Confirms the deposit is posted.",
      },
      {
        question: "So what's actually left then?",
        expectedBehavior:
          "Follows on from the deposit answer with the remaining open items, and does not reintroduce the deposit as outstanding.",
      },
    ],
    judgeFacts: ["deposit", "checklist"],
  },
];
