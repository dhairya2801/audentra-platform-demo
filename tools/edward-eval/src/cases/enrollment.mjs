/**
 * Enrollment / checklist coverage: what remains, what's next, why incomplete,
 * priority ordering, multiple blockers — across states, not just new_admit.
 *
 * Persona truths these cases lean on (derived live, listed here for readers):
 *  - new_admit          deposit unpaid; identity, transcript, immunization open;
 *                       aid verification open (non-blocking); housing blocked
 *  - deposit_posted     deposit done; three documents still open
 *  - nearly_complete    only housing (ready) + aid verification remain; no blocking item
 *  - deadline_passed    deposit, identity, transcript overdue
 *  - document_needs_resubmission  transcript returned; student must act again
 */

export const ENROLLMENT_CASES = [
  {
    id: "enr-001",
    category: "enrollment",
    capability: "remaining_steps",
    persona: "new_admit",
    question: "What do I still have left to do on my checklist?",
    expect: {
      requestTypes: ["remaining_steps"],
      requiredTools: ["getOnboardingChecklist"],
    },
    checks: [
      { kind: "mentions", any: ["deposit"] },
      { kind: "mentions", any: ["transcript"] },
      { kind: "mentions", any: ["immuniz", "immunis"] },
    ],
    expectedBehavior:
      "List the open checklist items (deposit, identity document, transcript, immunization, aid verification, housing) without inventing completions.",
    judgeFacts: ["checklist"],
  },
  {
    id: "enr-002",
    category: "enrollment",
    capability: "remaining_steps",
    persona: "nearly_complete",
    question: "What's left on my checklist?",
    expect: { requestTypes: ["remaining_steps"], requiredTools: ["getOnboardingChecklist"] },
    checks: [
      { kind: "mentions", any: ["housing", "aid", "verification"] },
      {
        kind: "not_mentions_pattern",
        pattern: "(?:still need to|you must|remaining[^.]{0,30})(?:pay|submit)[^.]{0,30}(?:deposit|transcript|immuni)",
        taxonomy: "STATE_GROUNDING_FAILURE",
      },
    ],
    expectedBehavior:
      "Only the housing preference selection and financial-aid verification remain; the paid/completed items must not be re-demanded.",
    judgeFacts: ["checklist"],
  },
  {
    id: "enr-003",
    category: "enrollment",
    capability: "next_action",
    persona: "new_admit",
    question: "What should I do first?",
    expect: {
      requestTypes: ["next_action", "general_help"],
      requiredTools: [
        "getOnboardingChecklist",
        "getEnrollmentHolds",
        "getStudentDeadlines",
      ],
    },
    checks: [{ kind: "mentions", any: ["deposit"] }],
    expectedBehavior:
      "Lead with the enrollment deposit — it is the journey's next action and gates housing; one concrete action, not the whole checklist.",
    judgeFacts: ["checklist", "deposit"],
  },
  {
    id: "enr-004",
    category: "enrollment",
    capability: "next_action",
    persona: "deposit_posted",
    question: "What's my next step?",
    expect: { requestTypes: ["next_action"] },
    checks: [
      { kind: "mentions", any: ["identity", "transcript", "immuniz", "immunis", "document"] },
      {
        kind: "not_mentions_pattern",
        pattern: "(?:pay|need to pay)[^.]{0,24}deposit",
        taxonomy: "STATE_GROUNDING_FAILURE",
      },
    ],
    expectedBehavior:
      "The deposit is posted, so the next step is one of the open documents — never asking for the deposit again.",
    judgeFacts: ["checklist", "deposit"],
  },
  {
    id: "enr-005",
    category: "enrollment",
    capability: "onboarding_status",
    persona: "new_admit",
    question: "How far along am I with enrollment?",
    expect: { requestTypes: ["onboarding_status", "remaining_steps", "enrollment_state"] },
    checks: [{ kind: "mentions", any: ["deposit", "step", "checklist", "remain"] }],
    expectedBehavior:
      "An honest position: profile verification done, six items open, deposit first.",
    judgeFacts: ["checklist"],
  },
  {
    id: "enr-006",
    category: "enrollment",
    capability: "completed_steps",
    persona: "nearly_complete",
    question: "Which steps have I already finished?",
    expect: { requestTypes: ["completed_steps"], requiredTools: ["getOnboardingChecklist"] },
    checks: [
      { kind: "mentions", any: ["deposit"] },
      { kind: "mentions", any: ["transcript"] },
    ],
    expectedBehavior:
      "Name the completed items (profile, deposit, identity, transcript, immunization) and not claim housing or aid verification is done.",
    judgeFacts: ["checklist"],
  },
  {
    id: "enr-007",
    category: "enrollment",
    capability: "holds_and_blockers",
    persona: "new_admit",
    question: "Is anything blocking my enrollment right now?",
    expect: {
      requestTypes: ["holds_and_blockers"],
      requiredTools: ["getEnrollmentHolds"],
    },
    checks: [{ kind: "mentions", any: ["deposit"] }],
    expectedBehavior:
      "Yes: the unpaid deposit plus the open blocking documents. Blockers are named specifically, not counted.",
    judgeFacts: ["registration", "checklist", "deposit"],
  },
  {
    id: "enr-008",
    category: "enrollment",
    capability: "holds_and_blockers",
    persona: "nearly_complete",
    question: "Do I have any blockers left?",
    expect: { requestTypes: ["holds_and_blockers"], requiredTools: ["getEnrollmentHolds"] },
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "(?:blocked by|must (?:pay|submit))[^.]{0,32}(?:deposit|transcript|immuni)",
        taxonomy: "STATE_GROUNDING_FAILURE",
      },
    ],
    expectedBehavior:
      "No — nothing blocking remains. Optionally note the open non-blocking items (housing choice, aid verification) without dressing them up as blockers.",
    judgeFacts: ["registration", "checklist"],
  },
  {
    id: "enr-009",
    category: "enrollment",
    capability: "why_incomplete",
    persona: "deadline_passed",
    question: "Why is my enrollment still incomplete?",
    expect: { requestTypes: ["holds_and_blockers", "onboarding_status", "remaining_steps", "general_question"] },
    checks: [
      { kind: "mentions", any: ["deposit"] },
      { kind: "mentions", any: ["overdue", "passed", "missed", "past due", "was due"] },
    ],
    expectedBehavior:
      "The open items are the reason, and three of them (deposit, identity, transcript) are already overdue — the answer should say so plainly.",
    judgeFacts: ["checklist"],
  },
  {
    id: "enr-010",
    category: "enrollment",
    capability: "priority_ordering",
    persona: "deadline_passed",
    question: "I have limited time this week. What should I tackle in what order?",
    expect: { requestTypes: ["next_action", "general_help", "remaining_steps", "deadlines"] },
    checks: [{ kind: "mentions", any: ["deposit"] }],
    expectedBehavior:
      "Overdue items first — the deposit above all, since it also gates housing; a defensible order with reasons, not an unordered dump.",
    judgeFacts: ["checklist", "deposit"],
  },
  {
    id: "enr-011",
    category: "enrollment",
    capability: "priority_ordering",
    persona: "new_admit",
    question: "If I can only do one thing today, what should it be and why?",
    expect: { requestTypes: ["next_action", "general_help"] },
    checks: [{ kind: "mentions", any: ["deposit"] }],
    expectedBehavior:
      "The deposit, with the reason: it is due soonest and unlocks the housing step.",
    judgeFacts: ["checklist", "deposit"],
  },
  {
    id: "enr-012",
    category: "enrollment",
    capability: "deadlines",
    persona: "new_admit",
    question: "What deadlines am I up against?",
    expect: { requestTypes: ["deadlines"], requiredTools: ["getStudentDeadlines"] },
    checks: [{ kind: "mentions", any: ["deposit"] }],
    expectedBehavior:
      "The requirement due dates on record (deposit soonest), plus the aid document deadlines; no invented term dates.",
    judgeFacts: ["checklist", "aid", "institutional_gaps"],
  },
  {
    id: "enr-013",
    category: "enrollment",
    capability: "deadlines",
    persona: "deadline_passed",
    question: "Have I missed anything?",
    expect: { requestTypes: ["deadlines", "holds_and_blockers", "remaining_steps", "general_question", "onboarding_status"] },
    checks: [{ kind: "mentions", any: ["overdue", "passed", "missed", "past due", "was due"] }],
    expectedBehavior:
      "Yes — deposit, identity document, and transcript deadlines have passed; say which and what to do now, without inventing penalties.",
    judgeFacts: ["checklist", "institutional_gaps"],
  },
  {
    id: "enr-014",
    category: "enrollment",
    capability: "why_incomplete",
    persona: "new_admit",
    question: "Why does my dashboard say 10% complete?",
    expect: { requestTypes: ["onboarding_status", "general_question", "remaining_steps"] },
    checks: [{ kind: "mentions", any: ["deposit", "open", "remain", "checklist", "step"] }],
    expectedBehavior:
      "Because only profile verification is done; the open items are the missing 90%. No invented percentage mechanics.",
    judgeFacts: ["checklist"],
  },
  {
    id: "enr-015",
    category: "enrollment",
    capability: "remaining_steps",
    persona: "document_needs_resubmission",
    question: "What do I still need to do to finish enrolling?",
    expect: { requestTypes: ["remaining_steps", "next_action", "general_help", "housing_remaining_steps"] },
    checks: [
      { kind: "mentions", any: ["transcript"] },
      { kind: "mentions", any: ["resubmit", "again", "returned", "rejected", "new copy", "re-upload", "reupload", "attention"] },
    ],
    expectedBehavior:
      "The returned transcript is the student's move again — it must appear in the remaining work alongside deposit, identity, immunization.",
    judgeFacts: ["checklist", "documents"],
  },
  {
    id: "enr-016",
    category: "enrollment",
    capability: "holds_and_blockers",
    persona: "transcript_under_review",
    question: "What's blocking me right now, and whose move is each one?",
    expect: { requestTypes: ["holds_and_blockers"], requiredTools: ["getEnrollmentHolds"] },
    checks: [
      { kind: "mentions", any: ["review", "university", "no action", "waiting"] },
      { kind: "mentions", any: ["deposit"] },
    ],
    expectedBehavior:
      "Distinguish ownership: the submitted transcript is the university's move (under review), while the deposit, identity document, and immunization are the student's.",
    judgeFacts: ["registration", "documents", "deposit"],
  },
  {
    id: "enr-017",
    category: "enrollment",
    capability: "next_action",
    persona: "advising_booked",
    question: "I have my advising session booked. What should I be doing before then?",
    expect: { requestTypes: ["next_action", "appointments", "general_help", "remaining_steps"] },
    checks: [{ kind: "mentions", any: ["identity", "transcript", "immuniz", "immunis", "document"] }],
    expectedBehavior:
      "Acknowledge the booked session and point at the open documents; the deposit is already posted and must not be re-demanded.",
    judgeFacts: ["checklist", "appointments", "deposit"],
  },
  {
    id: "enr-018",
    category: "enrollment",
    capability: "remaining_steps",
    persona: "housing_assigned",
    question: "Now that housing is sorted, what's left for me?",
    expect: { requestTypes: ["remaining_steps", "next_action", "general_help"] },
    checks: [
      { kind: "mentions", any: ["identity", "transcript", "immuniz", "immunis", "document"] },
    ],
    expectedBehavior:
      "Housing preference is completed; the open documents and aid verification are what remain.",
    judgeFacts: ["checklist", "housing"],
  },
  {
    id: "enr-019",
    category: "enrollment",
    capability: "why_incomplete",
    persona: "nearly_complete",
    question: "I feel like I've done everything. Am I actually done?",
    expect: { requestTypes: ["onboarding_status", "remaining_steps", "general_question", "general_help"] },
    checks: [{ kind: "mentions", any: ["housing", "verification", "aid"] }],
    expectedBehavior:
      "Nearly: nothing blocking remains, but the housing selection and aid verification are still open. No false 'all done'.",
    judgeFacts: ["checklist"],
  },
  {
    id: "enr-020",
    category: "enrollment",
    capability: "deadlines",
    persona: "nearly_complete",
    question: "Anything due soon that I should worry about?",
    expect: { requestTypes: ["deadlines"] },
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "(?:deposit|transcript|immuni)[^.]{0,40}(?:is|are) due",
        taxonomy: "STATE_GROUNDING_FAILURE",
      },
    ],
    expectedBehavior:
      "Only the open items carry deadlines now (housing selection, aid verification/worksheet, award acceptance); completed items must not resurface as due.",
    judgeFacts: ["checklist", "aid"],
  },
  {
    id: "enr-021",
    category: "enrollment",
    capability: "remaining_steps",
    persona: "payment_pending",
    question: "What's outstanding on my checklist?",
    expect: { requestTypes: ["remaining_steps"] },
    checks: [{ kind: "deposit_state_consistent" }],
    expectedBehavior:
      "The deposit requirement is still open but a payment is pending — say it is processing rather than 'unpaid'; documents remain open.",
    judgeFacts: ["checklist", "deposit"],
  },
  {
    id: "enr-022",
    category: "enrollment",
    capability: "multiple_blockers",
    persona: "new_admit",
    question: "How many things are actually blocking me, and what are they?",
    expect: { requestTypes: ["holds_and_blockers"], requiredTools: ["getEnrollmentHolds"] },
    checks: [
      { kind: "mentions", any: ["deposit"] },
      { kind: "mentions", any: ["transcript"] },
      { kind: "mentions", any: ["immuniz", "immunis"] },
      { kind: "mentions", any: ["identity"] },
    ],
    expectedBehavior:
      "All four blocking items named: deposit, identity document, transcript, immunization. Naming some and stopping is an incomplete answer.",
    judgeFacts: ["registration", "checklist"],
  },
  {
    id: "enr-023",
    category: "enrollment",
    capability: "next_action",
    persona: "fafsa_missing",
    question: "What needs my attention most urgently?",
    expect: { requestTypes: ["next_action", "general_help", "holds_and_blockers", "deadlines"] },
    checks: [{ kind: "mentions", any: ["deposit", "fafsa"] }],
    expectedBehavior:
      "The deposit is the checklist's next action; a strong answer also flags that no FAFSA is on file. Neither invented urgency nor a full dump.",
    judgeFacts: ["checklist", "aid", "deposit"],
  },
  {
    id: "enr-024",
    category: "enrollment",
    capability: "completed_steps",
    persona: "new_admit",
    question: "Have I completed anything yet?",
    expect: { requestTypes: ["completed_steps", "onboarding_status"] },
    checks: [{ kind: "mentions", any: ["profile"] }],
    expectedBehavior:
      "Yes, exactly one: profile verification. Claiming more is a grounding failure.",
    judgeFacts: ["checklist"],
  },
  {
    id: "enr-025",
    category: "enrollment",
    capability: "deadlines",
    persona: "new_admit",
    question: "When is my enrollment deposit due?",
    expect: { requestTypes: ["deadlines", "student_account"] },
    checks: [{ kind: "mentions", any: ["deposit"] }],
    expectedBehavior:
      "The canonical due date on the record (about ten days out), stated once — no invented consequences.",
    judgeFacts: ["checklist", "deposit"],
  },
  {
    id: "enr-026",
    category: "enrollment",
    capability: "why_incomplete",
    persona: "deposit_posted",
    question: "I paid my deposit, so why isn't my checklist done?",
    expect: {
      requestTypes: [
        "remaining_steps",
        "onboarding_status",
        "holds_and_blockers",
        "general_question",
        "student_account",
        "deposit_status",
      ],
    },
    checks: [
      { kind: "mentions", any: ["identity", "transcript", "immuniz", "immunis", "document"] },
    ],
    expectedBehavior:
      "Confirm the deposit is posted, then name what the checklist still wants: the three documents plus aid verification and housing selection.",
    judgeFacts: ["checklist", "deposit"],
  },
  {
    id: "enr-027",
    category: "enrollment",
    capability: "remaining_steps",
    persona: "official_hold",
    question: "The portal feels stuck. Is there a hold on my account or am I missing something?",
    expect: { requestTypes: ["holds_and_blockers"] },
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "(?:there(?:'s| is) (?:a|an) |you (?:do )?have (?:a|an) |placed (?:a|an) )[^.]{0,16}hold",
        taxonomy: "HALLUCINATION",
      },
    ],
    expectedBehavior:
      "No hold system exists and no hold is on record — say so, then point at the genuinely open items (documents).",
    judgeFacts: ["registration", "checklist", "institutional_gaps"],
  },
  {
    id: "enr-028",
    category: "enrollment",
    capability: "onboarding_status",
    persona: "document_needs_resubmission",
    question: "Give me an honest status check on my enrollment.",
    expect: {
      requestTypes: [
        "onboarding_status",
        "remaining_steps",
        "general_help",
        "general_question",
        "enrollment_state",
      ],
    },
    checks: [
      { kind: "mentions", any: ["transcript"] },
      { kind: "mentions", any: ["resubmit", "returned", "rejected", "again", "attention", "re-upload", "reupload"] },
    ],
    expectedBehavior:
      "The returned transcript is the headline: it was submitted but came back and needs the student's action; the rest of the open list follows.",
    judgeFacts: ["checklist", "documents"],
  },
];
