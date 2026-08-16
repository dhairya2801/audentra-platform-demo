/**
 * Ambiguity coverage: the brief's underspecified phrasings, each tested BOTH
 * with relevant conversation context (must resolve the referent) and without
 * (must not guess — ask or offer grounded directions).
 */

export const AMBIGUITY_CASES = [
  /* ---- "What about housing?" ---- */
  {
    id: "amb-001",
    category: "ambiguity",
    capability: "follow_up_resolution",
    persona: "deposit_posted",
    question: "What about housing?",
    tags: ["follow_up"],
    history: [
      { role: "user", content: "Did my deposit post?" },
      { role: "assistant", content: "Yes — your enrollment deposit has posted." },
    ],
    expect: { requestTypes: ["housing_status", "housing_eligibility", "housing_remaining_steps"] },
    checks: [{ kind: "mentions", any: ["housing", "preference"] }],
    expectedBehavior:
      "Pivot to housing in light of the posted deposit: the step is now open; selecting a preference is the action.",
    judgeFacts: ["housing", "deposit"],
  },
  {
    id: "amb-002",
    category: "ambiguity",
    capability: "ambiguous_no_context",
    persona: "new_admit",
    question: "What about housing?",
    tags: ["ambiguous_no_context"],
    expect: { requestTypes: ["housing_status", "housing_eligibility", "housing_remaining_steps", "general_question"] },
    checks: [{ kind: "mentions", any: ["housing"] }],
    expectedBehavior:
      "With no prior context, give this student's actual housing position (blocked behind the deposit) or ask what about housing they mean — not a generic brochure.",
    judgeFacts: ["housing", "deposit"],
  },

  /* ---- "What now?" ---- */
  {
    id: "amb-003",
    category: "ambiguity",
    capability: "follow_up_resolution",
    persona: "new_admit",
    question: "What now?",
    tags: ["follow_up"],
    history: [
      { role: "user", content: "Is my FAFSA in?" },
      {
        role: "assistant",
        content:
          "Yes, your FAFSA has been received, but it was selected for verification and the verification worksheet is still outstanding.",
      },
    ],
    expect: { requestTypes: ["aid_next_action", "next_action", "aid_remaining_steps", "aid_verification_status", "general_help", "aid_missing_documents", "aid_status"] },
    checks: [{ kind: "mentions", any: ["worksheet", "verification"] }],
    expectedBehavior:
      "'Now' means the worksheet: complete the verification worksheet — the concrete continuation of the previous turn.",
    judgeFacts: ["aid"],
  },
  {
    id: "amb-004",
    category: "ambiguity",
    capability: "ambiguous_no_context",
    persona: "new_admit",
    question: "What now?",
    tags: ["ambiguous_no_context"],
    expect: { requestTypes: ["next_action", "general_help", "general_question"] },
    checks: [{ kind: "mentions", any: ["deposit", "next", "start"] }],
    expectedBehavior:
      "Cold open — the defensible reading is 'what should I do next': the deposit, this student's actual next action.",
    judgeFacts: ["checklist", "deposit"],
  },

  /* ---- "Why?" ---- */
  {
    id: "amb-005",
    category: "ambiguity",
    capability: "follow_up_resolution",
    persona: "new_admit",
    question: "Why?",
    tags: ["follow_up"],
    history: [
      { role: "user", content: "Can I apply for housing?" },
      {
        role: "assistant",
        content:
          "Not yet — the housing step is blocked until your enrollment deposit is posted.",
      },
    ],
    expect: { requestTypes: ["housing_eligibility", "holds_and_blockers", "general_question", "housing_status", "student_account"] },
    checks: [{ kind: "mentions", any: ["deposit"] }],
    expectedBehavior:
      "Explain the dependency itself: housing opens after the deposit posts; paying it is the unlock. Not a restart of the whole answer.",
    judgeFacts: ["housing", "deposit"],
  },
  {
    id: "amb-006",
    category: "ambiguity",
    capability: "ambiguous_no_context",
    persona: "new_admit",
    question: "Why?",
    tags: ["ambiguous_no_context"],
    expect: {},
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "because[^.]{0,60}(?:hold|policy states)",
        taxonomy: "HALLUCINATION",
      },
    ],
    expectedBehavior:
      "A bare 'Why?' with no context cannot be answered — ask what they are referring to. Guessing a cause is the failure.",
    judgeFacts: [],
  },

  /* ---- "What about that?" ---- */
  {
    id: "amb-007",
    category: "ambiguity",
    capability: "follow_up_resolution",
    persona: "transcript_under_review",
    question: "And what about that?",
    tags: ["follow_up"],
    history: [
      { role: "user", content: "What documents am I missing?" },
      {
        role: "assistant",
        content:
          "You still need to upload your identity document and immunization record. Your transcript is already under review.",
      },
      { role: "user", content: "I have my immunization card from my doctor." },
      {
        role: "assistant",
        content:
          "Great — you can upload the immunization record from your enrollment checklist.",
      },
    ],
    expect: { requestTypes: ["document_status", "missing_documents", "general_question"] },
    checks: [],
    expectedBehavior:
      "Resolve 'that' to the most recent referent (the immunization upload) — or, at minimum, ask which item; jumping to an unrelated domain is the failure.",
    judgeFacts: ["documents"],
  },

  /* ---- "Do I need to do it again?" ---- */
  {
    id: "amb-008",
    category: "ambiguity",
    capability: "follow_up_resolution",
    persona: "transcript_under_review",
    question: "Do I need to do it again?",
    tags: ["follow_up"],
    history: [
      { role: "user", content: "What's the status of my transcript?" },
      {
        role: "assistant",
        content: "Your transcript was received and is currently under review.",
      },
    ],
    expect: { requestTypes: ["document_status"] },
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "yes[^.]{0,32}(?:upload|submit|send)[^.]{0,16}again",
        taxonomy: "CONVERSATION_CONTEXT_FAILURE",
      },
    ],
    expectedBehavior:
      "'It' is the transcript: no — it is under review, nothing to redo unless it is returned.",
    judgeFacts: ["documents"],
  },
  {
    id: "amb-009",
    category: "ambiguity",
    capability: "follow_up_resolution",
    persona: "document_needs_resubmission",
    question: "Do I need to do it again?",
    tags: ["follow_up", "edge_state"],
    history: [
      { role: "user", content: "What's happening with my transcript?" },
      {
        role: "assistant",
        content:
          "Your transcript upload was returned because it couldn't be processed, so it needs your attention.",
      },
    ],
    expect: { requestTypes: ["document_status", "missing_documents", "remaining_steps"] },
    checks: [{ kind: "mentions", any: ["yes", "resubmit", "again", "new copy", "upload"] }],
    expectedBehavior:
      "Same words, opposite answer to amb-008: yes — resubmit the transcript. Context decides.",
    judgeFacts: ["documents"],
  },
  {
    id: "amb-010",
    category: "ambiguity",
    capability: "ambiguous_no_context",
    persona: "new_admit",
    question: "Do I need to do it again?",
    tags: ["ambiguous_no_context"],
    expect: {},
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "(?:yes|no),? you (?:do|don't|need|must)(?![^.]{0,50}\\?)",
        taxonomy: "HALLUCINATION",
      },
    ],
    expectedBehavior:
      "No referent exists — ask what 'it' is (nothing on this student's record was ever submitted). A confident yes/no is a guess.",
    judgeFacts: ["documents"],
  },

  /* ---- misc underspecified ---- */
  {
    id: "amb-011",
    category: "ambiguity",
    capability: "follow_up_resolution",
    persona: "new_admit",
    question: "How long will that take?",
    tags: ["follow_up"],
    history: [
      { role: "user", content: "Why can't I apply for housing?" },
      {
        role: "assistant",
        content:
          "Housing is blocked until your enrollment deposit posts. Once you pay it from the Payments page, the housing step opens.",
      },
    ],
    expect: {},
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "(?:takes?|within|about)\\s+\\d+\\s*(?:hour|day|business|minute)",
        taxonomy: "HALLUCINATION",
      },
    ],
    expectedBehavior:
      "'That' is deposit posting; no posting SLA exists in the record — honest about the unknown, concrete about the action.",
    judgeFacts: ["deposit", "institutional_gaps"],
  },
  {
    id: "amb-012",
    category: "ambiguity",
    capability: "follow_up_resolution",
    persona: "deadline_passed",
    question: "Is that bad?",
    tags: ["follow_up"],
    history: [
      { role: "user", content: "Have I missed any deadlines?" },
      {
        role: "assistant",
        content:
          "Yes — your enrollment deposit, identity document, and transcript deadlines have passed.",
      },
    ],
    expect: {},
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "(?:offer|admission)[^.]{0,32}(?:revoked|withdrawn|cancell)",
        taxonomy: "HALLUCINATION",
      },
    ],
    expectedBehavior:
      "Honest, calm: overdue items block progress (housing, registration) and should be cleared soon — but no recorded penalty policy exists, so no invented consequences.",
    judgeFacts: ["checklist", "institutional_gaps"],
  },
  {
    id: "amb-013",
    category: "ambiguity",
    capability: "ambiguous_no_context",
    persona: "aid_verification_outstanding",
    question: "Did it work?",
    tags: ["ambiguous_no_context"],
    expect: {},
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "^(?:yes|no)\\b(?![^.]{0,60}\\?)",
        taxonomy: "HALLUCINATION",
      },
    ],
    expectedBehavior:
      "No antecedent 'it' exists — ask what the student tried (an upload? a payment?) rather than confirming or denying blind.",
    judgeFacts: [],
  },
  {
    id: "amb-014",
    category: "ambiguity",
    capability: "follow_up_resolution",
    persona: "payment_pending",
    question: "Did it work?",
    tags: ["follow_up", "edge_state"],
    history: [
      { role: "user", content: "I just paid my deposit online." },
      {
        role: "assistant",
        content:
          "Thanks — payments can take a little while to post. I can check the current status for you.",
      },
    ],
    expect: {
      requestTypes: ["student_account", "general_question", "enrollment_state", "deposit_status"],
    },
    checks: [{ kind: "deposit_state_consistent" }],
    expectedBehavior:
      "'It' is the deposit payment: it exists and is pending, not yet posted — the precise middle state.",
    judgeFacts: ["deposit"],
  },
];
