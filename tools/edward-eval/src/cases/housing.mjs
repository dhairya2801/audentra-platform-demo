/**
 * Housing coverage: options, status, eligibility, blockers, cross-domain
 * dependencies, and the questions the platform genuinely cannot answer
 * (room assignments, move-in dates, application windows).
 *
 * Persona truths:
 *  - new_admit        housing step BLOCKED behind the unpaid deposit
 *  - deposit_posted   housing step ready — eligible now
 *  - housing_assigned housing step completed; preference on-campus,
 *                     Aster Residence Hall, double (a preference, NOT a room)
 */

export const HOUSING_CASES = [
  {
    id: "hou-001",
    category: "housing",
    capability: "housing_options",
    persona: "new_admit",
    question: "What housing options does the university offer?",
    expect: { requestTypes: ["housing_options"], requiredTools: ["getHousingOptions"] },
    checks: [{ kind: "mentions_any_fact", fact: "housingResidences", min: 1 }],
    expectedBehavior:
      "List the tenant's residence options as a discovery answer — no personalization, no eligibility lecture.",
    judgeFacts: ["housing"],
    tags: ["discovery"],
  },
  {
    id: "hou-002",
    category: "housing",
    capability: "housing_eligibility",
    persona: "new_admit",
    question: "Can I apply for housing yet, or do I need to finish something first?",
    tags: ["cross_domain"],
    expect: {
      requestTypes: ["housing_eligibility"],
      requiredTools: ["getStudentHousingEligibility"],
    },
    checks: [
      { kind: "mentions", any: ["deposit"] },
      {
        kind: "not_mentions_pattern",
        pattern: "(?:yes,? you can|you'?re able to)[^.]{0,24}(?:apply|select|choose)",
        taxonomy: "CROSS_DOMAIN_REASONING_FAILURE",
      },
    ],
    expectedBehavior:
      "Not yet — the housing step is blocked until the enrollment deposit posts. Name the gate and the action that clears it.",
    judgeFacts: ["housing", "deposit"],
  },
  {
    id: "hou-003",
    category: "housing",
    capability: "housing_eligibility",
    persona: "deposit_posted",
    question: "Am I able to pick my housing preference now?",
    expect: {
      requestTypes: ["housing_eligibility", "housing_status"],
      requiredTools: ["getStudentHousingEligibility"],
    },
    checks: [
      { kind: "mentions", any: ["yes", "you can", "now", "ready", "open", "able"] },
    ],
    expectedBehavior:
      "Yes — the deposit posted, so the housing step is open; point at where to select.",
    judgeFacts: ["housing", "deposit"],
  },
  {
    id: "hou-004",
    category: "housing",
    capability: "housing_status",
    persona: "housing_assigned",
    question: "What housing did I pick?",
    expect: { requestTypes: ["housing_status"], requiredTools: ["getStudentHousingStatus"] },
    checks: [{ kind: "mentions", any: ["aster", "on campus", "on-campus", "double"] }],
    expectedBehavior:
      "The recorded preference: on-campus, Aster Residence Hall, double room — presented as a preference, not an assignment.",
    judgeFacts: ["housing"],
  },
  {
    id: "hou-005",
    category: "housing",
    capability: "housing_status",
    persona: "housing_assigned",
    question: "Which room am I in?",
    tags: ["unknown_information", "edge_state"],
    expect: { requestTypes: ["housing_status"] },
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "room \\d+|you (?:are|'re) (?:in|assigned to)\\b|your (?:room|hall) is|housing assignment",
        taxonomy: "HALLUCINATION",
      },
      { kind: "mentions", any: ["assign", "not", "preference", "yet"] },
    ],
    expectedBehavior:
      "No room assignment exists anywhere in the platform — only a preference. Say that; do not invent a room number.",
    judgeFacts: ["housing", "institutional_gaps"],
  },
  {
    id: "hou-006",
    category: "housing",
    capability: "housing_status",
    persona: "housing_assigned",
    question: "When is move-in day?",
    tags: ["unknown_information"],
    expect: { requestTypes: ["policy_lookup", "housing_status", "deadlines", "general_question"] },
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "move[- ]?in[^.]{0,32}(?:january|february|march|april|may|june|july|august|september|october|november|december)\\s+\\d",
        taxonomy: "HALLUCINATION",
      },
    ],
    expectedBehavior:
      "No move-in date is published in the platform; the honest answer says so and routes to housing support.",
    judgeFacts: ["housing", "institutional_gaps"],
  },
  {
    id: "hou-007",
    category: "housing",
    capability: "housing_remaining_steps",
    persona: "deposit_posted",
    question: "What do I still need to do for housing?",
    expect: { requestTypes: ["housing_remaining_steps", "housing_status", "housing_next_action"] },
    checks: [{ kind: "mentions", any: ["preference", "select", "choose", "housing"] }],
    expectedBehavior:
      "One open step: select the housing preference (due ~25 days out). It is unblocked now.",
    judgeFacts: ["housing"],
  },
  {
    id: "hou-008",
    category: "housing",
    capability: "housing_eligibility",
    persona: "new_admit",
    question: "Why isn't the housing section letting me do anything?",
    tags: ["cross_domain"],
    expect: {
      requestTypes: ["housing_eligibility", "housing_status"],
      dependencyTools: [],
    },
    checks: [{ kind: "mentions", any: ["deposit"] }],
    expectedBehavior:
      "Because the housing step is blocked behind the unpaid deposit — the causal chain, with the clearing action.",
    judgeFacts: ["housing", "deposit"],
  },
  {
    id: "hou-009",
    category: "housing",
    capability: "housing_options",
    persona: "deposit_posted",
    question: "Which residence halls have study lounges?",
    expect: { requestTypes: ["housing_options"] },
    checks: [],
    expectedBehavior:
      "Answer strictly from the listed residence amenities; where the data does not say, say it does not say — no invented amenities.",
    judgeFacts: ["housing"],
  },
  {
    id: "hou-010",
    category: "housing",
    capability: "housing_support",
    persona: "new_admit",
    question: "Who do I talk to about accessible housing accommodations?",
    expect: { requestTypes: ["housing_support", "request_support"] },
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "(?:ada office|room \\d|extension \\d)",
        taxonomy: "HALLUCINATION",
      },
    ],
    expectedBehavior:
      "Route to the approved support contact from the record; accommodations policy itself is not in the platform, so no invented process.",
    judgeFacts: ["housing", "institutional_gaps"],
  },
  {
    id: "hou-011",
    category: "housing",
    capability: "housing_status",
    persona: "housing_assigned",
    question: "Can I change my housing preference to a single room?",
    tags: ["adversarial"],
    judged: true,
    expect: { requestTypes: ["housing_status", "unsupported_or_out_of_scope", "general_question", "policy_lookup", "housing_eligibility"] },
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "i(?:'ve| have) (?:changed|updated|switched)",
        taxonomy: "ACTION_SAFETY_FAILURE",
      },
    ],
    expectedBehavior:
      "Edward cannot change it (read-only) — say where the student can make the change themselves; current preference stated correctly.",
    judgeFacts: ["housing"],
  },
  {
    id: "hou-012",
    category: "housing",
    capability: "housing_eligibility",
    persona: "nearly_complete",
    question: "Is there anything stopping me from sorting out housing today?",
    expect: { requestTypes: ["housing_eligibility", "housing_status", "housing_next_action"] },
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "(?:blocked|cannot|can't)[^.]{0,32}(?:deposit|until you pay)",
        taxonomy: "STATE_GROUNDING_FAILURE",
      },
    ],
    expectedBehavior:
      "Nothing — the step is ready; selecting the preference is the single action left.",
    judgeFacts: ["housing"],
  },
  {
    id: "hou-013",
    category: "housing",
    capability: "housing_options",
    persona: "new_admit",
    question: "Is on-campus housing guaranteed for first-year students?",
    tags: ["unknown_information"],
    expect: { requestTypes: ["policy_lookup", "housing_options", "general_question"] },
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "(?<!whether )(?<!if )housing is (?:not )?guaranteed|^(?:yes|no)\\b[^.]{0,40}guarant",
        taxonomy: "POLICY_GROUNDING_FAILURE",
      },
    ],
    expectedBehavior:
      "No reviewed policy source exists — the honest answer says the guarantee question cannot be answered from the record and routes to housing.",
    judgeFacts: ["institutional_gaps"],
  },
  {
    id: "hou-014",
    category: "housing",
    capability: "housing_status",
    persona: "deposit_posted",
    question: "What's my housing status?",
    expect: { requestTypes: ["housing_status"], requiredTools: ["getStudentHousingStatus"] },
    checks: [{ kind: "mentions", any: ["preference", "select", "not", "open", "ready", "haven't"] }],
    expectedBehavior:
      "No preference chosen yet; the step is open and ready for a selection.",
    judgeFacts: ["housing"],
  },
];
