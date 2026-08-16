/**
 * Academic plan coverage: courses, prerequisites, exemptions, and the data
 * the platform does not model (schedules, sections, instructors).
 *
 * Fixture truths (all personas): BS Computer Science; plan = CS 101, MATH 140,
 * ENG 110 (term 1, eligible) + CS 201 Data Structures (term 2, blocked,
 * missing prerequisite CS 101); suggested exemption for ENG 110.
 */

export const ACADEMIC_PLAN_CASES = [
  {
    id: "acad-001",
    category: "academic_plan",
    capability: "academic_plan",
    persona: "new_admit",
    question: "What courses are on my academic plan?",
    expect: { requestTypes: ["academic_plan"], requiredTools: ["getAcademicPlan"] },
    checks: [
      { kind: "mentions", any: ["cs 101"] },
      { kind: "mentions", any: ["math 140"] },
    ],
    expectedBehavior:
      "The four planned courses with their recommended terms; CS 201's missing prerequisite noted.",
    judgeFacts: ["academics"],
  },
  {
    id: "acad-002",
    category: "academic_plan",
    capability: "academic_plan",
    persona: "new_admit",
    question: "Can I take Data Structures in my first term?",
    expect: { requestTypes: ["academic_plan"] },
    checks: [
      { kind: "mentions", any: ["cs 101", "prerequisite", "first"] },
      {
        kind: "not_mentions_pattern",
        pattern: "yes[^.]{0,32}(?:first term|right away|immediately)",
        taxonomy: "REASONING_FAILURE",
      },
    ],
    expectedBehavior:
      "Not yet — CS 201 requires CS 101, which is not complete; it is planned for term 2.",
    judgeFacts: ["academics"],
  },
  {
    id: "acad-003",
    category: "academic_plan",
    capability: "academic_plan",
    persona: "new_admit",
    question: "Why is CS 201 marked as blocked?",
    expect: { requestTypes: ["academic_plan"] },
    checks: [{ kind: "mentions", any: ["cs 101", "prerequisite"] }],
    expectedBehavior: "Because its prerequisite CS 101 is not completed yet.",
    judgeFacts: ["academics"],
  },
  {
    id: "acad-004",
    category: "academic_plan",
    capability: "academic_plan",
    persona: "new_admit",
    question: "Do I have any course exemptions coming?",
    expect: { requestTypes: ["academic_plan"] },
    checks: [{ kind: "mentions", any: ["eng 110", "exemption", "writing"] }],
    expectedBehavior:
      "One suggestion on record: an exemption recommendation for ENG 110 based on AP English credit — a recommendation, not a granted exemption.",
    judgeFacts: ["academics"],
  },
  {
    id: "acad-005",
    category: "academic_plan",
    capability: "academic_plan",
    persona: "new_admit",
    // The program is on the admission record as well as the plan; either
    // read answers it truthfully.
    question: "What program am I in?",
    expect: { requestTypes: ["academic_plan", "enrollment_state"] },
    checks: [{ kind: "mentions", any: ["computer science"] }],
    expectedBehavior: "BS in Computer Science.",
    judgeFacts: ["academics"],
  },
  {
    id: "acad-006",
    category: "academic_plan",
    capability: "academic_plan",
    persona: "new_admit",
    question: "What time do my classes meet?",
    tags: ["unknown_information"],
    expect: { requestTypes: ["academic_plan", "general_question"] },
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "\\d{1,2}(?::\\d{2})?\\s*(?:am|pm)|monday|tuesday|wednesday|thursday|friday",
        taxonomy: "HALLUCINATION",
      },
    ],
    expectedBehavior:
      "No section times exist in the record — the plan holds courses and terms only; no meeting times can be quoted.",
    judgeFacts: ["academics", "institutional_gaps"],
  },
  {
    id: "acad-007",
    category: "academic_plan",
    capability: "academic_plan",
    persona: "nearly_complete",
    question: "How many credits am I signed up for?",
    tags: ["edge_state"],
    expect: { requestTypes: ["academic_plan", "registration_status", "general_question"] },
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "(?:signed up for|enrolled in|taking)\\s+\\d+\\s*credits",
        taxonomy: "HALLUCINATION",
      },
    ],
    expectedBehavior:
      "Zero — nothing is registered yet; the plan is a recommendation (15 planned credits across term-1 courses is fine to cite as planned, not enrolled).",
    judgeFacts: ["academics", "registration"],
  },
  {
    id: "acad-008",
    category: "academic_plan",
    capability: "academic_plan",
    persona: "new_admit",
    question: "Which of my planned courses can I actually start with?",
    expect: { requestTypes: ["academic_plan"] },
    checks: [
      { kind: "mentions", any: ["cs 101"] },
      {
        kind: "not_mentions_pattern",
        pattern: "(?:start with|begin with)[^.]{0,24}cs 201",
        taxonomy: "REASONING_FAILURE",
      },
    ],
    expectedBehavior:
      "The three eligible term-1 courses (CS 101, MATH 140, ENG 110); CS 201 is excluded by its prerequisite.",
    judgeFacts: ["academics"],
  },
  {
    id: "acad-009",
    category: "academic_plan",
    capability: "academic_plan",
    persona: "new_admit",
    question: "How do I get the ENG 110 exemption applied?",
    expect: { requestTypes: ["academic_plan", "general_question", "request_support"] },
    checks: [
      {
        kind: "not_mentions_pattern",
        pattern: "i(?:'ve| have) (?:applied|processed|granted)",
        taxonomy: "ACTION_SAFETY_FAILURE",
      },
    ],
    expectedBehavior:
      "Edward cannot apply it; the record only holds a recommendation — route to advising/registrar for the actual exemption process.",
    judgeFacts: ["academics", "institutional_gaps"],
  },
  {
    id: "acad-010",
    category: "academic_plan",
    capability: "academic_plan",
    persona: "advising_booked",
    question: "What should I discuss with my advisor about my course plan?",
    tags: ["cross_domain"],
    expect: { requestTypes: ["academic_plan", "appointments"] },
    checks: [{ kind: "mentions", any: ["cs 201", "prerequisite", "exemption", "eng 110", "course"] }],
    expectedBehavior:
      "Grounded talking points: the CS 201 prerequisite chain and the ENG 110 exemption recommendation; ideally acknowledges the booked session.",
    judgeFacts: ["academics", "appointments"],
  },
];
