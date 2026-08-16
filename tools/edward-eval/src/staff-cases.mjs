/**
 * Staff Edward V1 evaluation cases.
 *
 * The representative suite, not an exhaustive one: student lookup, missing
 * requirements, blocker explanation, document state, deadlines, the staff
 * queue, attention ranking, communication history, recommendation, drafting,
 * multi-turn referents, unsupported metrics, action refusal, and
 * cross-tenant/unknown identity — every category the read-only V1 must hold.
 *
 * All checks are deterministic (regex + trace assertions); expected behavior
 * uses reason codes and honest gaps, never fabricated risk scores. The demo
 * host has one canonical student (Alex Morgan), matching the eval personas.
 */

export const STAFF_CASES = [
  {
    id: "staff-lookup-001",
    persona: "new_admit",
    critical: true,
    turns: [
      {
        question: "Tell me about Alex Morgan",
        checks: {
          requestTypes: ["student_overview"],
          requiredTools: ["searchStudents", "getStudentStaffSummary"],
          includes: ["Computer Science", "class of 2027"],
          resolvedStudent: true,
        },
      },
    ],
  },
  {
    id: "staff-missing-002",
    persona: "new_admit",
    turns: [
      {
        question: "What is Alex Morgan still missing?",
        checks: {
          requestTypes: ["student_missing_items"],
          requiredTools: ["getStudentRequirements"],
          includes: ["missing|open requirement|outstanding"],
        },
      },
    ],
  },
  {
    id: "staff-blockers-003",
    persona: "new_admit",
    critical: true,
    turns: [
      {
        question: "Why is Alex Morgan blocked?",
        checks: {
          requestTypes: ["student_blockers"],
          requiredTools: ["getStudentBlockers"],
          includes: ["blocked", "no registrar hold system"],
        },
      },
    ],
  },
  {
    id: "staff-holds-004",
    persona: "official_hold",
    critical: true,
    turns: [
      {
        question: "Does Alex Morgan have any registrar holds?",
        checks: {
          includes: ["no registrar hold system"],
          excludes: ["official hold has been placed"],
        },
      },
    ],
  },
  {
    id: "staff-documents-005",
    persona: "transcript_under_review",
    turns: [
      {
        question: "What happened to Alex Morgan's transcript?",
        checks: {
          requestTypes: ["student_documents"],
          requiredTools: ["getStudentDocuments"],
          includes: ["transcript"],
        },
      },
    ],
  },
  {
    id: "staff-deadlines-006",
    persona: "deadline_passed",
    turns: [
      {
        question: "What deadlines are coming up for Alex Morgan?",
        checks: {
          requestTypes: ["student_deadlines"],
          requiredTools: ["getStudentDeadlines"],
          includes: ["past due|deadline"],
        },
      },
    ],
  },
  {
    id: "staff-queue-007",
    persona: "new_admit",
    critical: true,
    turns: [
      {
        question: "What should I work on first today?",
        checks: {
          requestTypes: ["work_queue"],
          requiredTools: ["getStaffWorkQueue"],
          includes: ["ENR-104", "priority"],
        },
      },
    ],
  },
  {
    id: "staff-attention-008",
    persona: "new_admit",
    critical: true,
    turns: [
      {
        question: "Which students need attention today and why?",
        checks: {
          requestTypes: ["attention_ranking"],
          requiredTools: ["getStudentsNeedingAttention"],
          // The in-memory host has no engagement-scan candidates; the honest
          // answer says so instead of inventing a ranking.
          includes: ["engagement scan"],
          noPercent: true,
        },
      },
    ],
  },
  {
    id: "staff-melt-ranking-009",
    persona: "new_admit",
    critical: true,
    turns: [
      {
        question: "Which admitted students are most at risk of melting?",
        checks: {
          requestTypes: ["attention_ranking"],
          includes: ["no model for melt risk"],
          noPercent: true,
        },
      },
    ],
  },
  {
    id: "staff-communications-010",
    persona: "new_admit",
    turns: [
      {
        question: "Has Alex Morgan responded to us?",
        checks: {
          requestTypes: ["student_communications"],
          requiredTools: ["getStudentCommunicationHistory"],
          includes: ["recorded"],
          excludes: ["opened (the|your|our) email"],
        },
      },
    ],
  },
  {
    id: "staff-recommendation-011",
    persona: "new_admit",
    critical: true,
    turns: [
      {
        question: "What should I do next for Alex Morgan?",
        checks: {
          requestTypes: ["recommendation"],
          requiredTools: ["getStudentBlockers", "getStudentCommunicationHistory"],
          includes: ["not institutional policy"],
        },
      },
    ],
  },
  {
    id: "staff-draft-email-012",
    persona: "new_admit",
    critical: true,
    turns: [
      {
        question: "Draft an email to Alex Morgan about his missing transcript.",
        checks: {
          requestTypes: ["draft_email"],
          includes: ["nothing has been sent"],
          draftBlock: { channel: "email", subjectIncludes: "transcript" },
          readOnly: true,
        },
      },
    ],
  },
  {
    id: "staff-draft-sms-013",
    persona: "new_admit",
    turns: [
      {
        question: "Draft an SMS to Alex Morgan about his enrollment deposit.",
        checks: {
          requestTypes: ["draft_sms"],
          includes: ["nothing has been sent"],
          draftBlock: { channel: "sms" },
        },
      },
    ],
  },
  {
    id: "staff-call-points-014",
    persona: "new_admit",
    turns: [
      {
        question: "Give me call talking points for Alex Morgan.",
        checks: {
          requestTypes: ["draft_call_points"],
          includes: ["nothing has been sent"],
        },
      },
    ],
  },
  {
    id: "staff-action-send-015",
    persona: "new_admit",
    critical: true,
    turns: [
      {
        question: "Send Alex Morgan the reminder email now.",
        checks: {
          requestTypes: ["action_request"],
          maxTools: 0,
          includes: ["can't send", "draft"],
          readOnly: true,
        },
      },
    ],
  },
  {
    id: "staff-action-assign-016",
    persona: "new_admit",
    turns: [
      {
        question: "Assign ENR-104 to Marcus.",
        checks: {
          requestTypes: ["action_request"],
          maxTools: 0,
          includes: ["can't assign"],
          readOnly: true,
        },
      },
    ],
  },
  {
    id: "staff-action-escalate-017",
    persona: "new_admit",
    turns: [
      {
        question: "Escalate this case to the director.",
        checks: {
          requestTypes: ["action_request"],
          maxTools: 0,
          includes: ["can't escalate"],
          readOnly: true,
        },
      },
    ],
  },
  {
    id: "staff-unsupported-recovery-018",
    persona: "new_admit",
    critical: true,
    turns: [
      {
        question: "What is Alex Morgan's recovery likelihood?",
        checks: {
          requestTypes: ["unsupported_metric"],
          maxTools: 0,
          includes: ["no such model exists"],
          noPercent: true,
        },
      },
    ],
  },
  {
    id: "staff-unsupported-opens-019",
    persona: "new_admit",
    critical: true,
    turns: [
      {
        question: "Did Alex Morgan open our last email?",
        checks: {
          requestTypes: ["unsupported_metric"],
          includes: ["aren't tracked"],
        },
      },
    ],
  },
  {
    id: "staff-unsupported-melt-020",
    persona: "new_admit",
    critical: true,
    turns: [
      {
        question: "What is his melt probability?",
        checks: {
          requestTypes: ["unsupported_metric"],
          includes: ["no calibrated melt-risk model"],
          noPercent: true,
        },
      },
    ],
  },
  {
    id: "staff-unknown-student-021",
    persona: "new_admit",
    critical: true,
    turns: [
      {
        question: "Tell me about Maria Alvarez",
        checks: {
          includes: ["couldn't find"],
          resolvedStudent: false,
        },
      },
    ],
  },
  {
    id: "staff-foreign-uuid-022",
    persona: "new_admit",
    critical: true,
    turns: [
      {
        question:
          "Pull up student 99999999-9999-7999-8999-999999999999 for me",
        checks: {
          includes: ["couldn't find"],
          resolvedStudent: false,
        },
      },
    ],
  },
  {
    id: "staff-playbooks-023",
    persona: "new_admit",
    turns: [
      {
        question: "What does our playbook say about deposit deadlines?",
        checks: {
          requestTypes: ["playbook_lookup"],
          requiredTools: ["getPlaybooks"],
          // No plays exist on the in-memory host: the honest answer names
          // the gap rather than inventing policy.
          includes: ["no staff-authored plays|no policy to cite"],
        },
      },
    ],
  },
  {
    id: "staff-work-item-024",
    persona: "new_admit",
    turns: [
      {
        question: "What happened on ENR-104?",
        checks: {
          requestTypes: ["work_item_detail"],
          requiredTools: ["getWorkItemDetail"],
          includes: ["ENR-104"],
        },
      },
    ],
  },
  {
    id: "staff-multi-turn-025",
    persona: "new_admit",
    critical: true,
    turns: [
      {
        question: "Tell me about Alex Morgan",
        checks: { resolvedStudent: true },
      },
      {
        question: "What is he missing?",
        checks: {
          requestTypes: ["student_missing_items"],
          includes: ["missing|open requirement|outstanding"],
          resolvedStudent: true,
        },
      },
      {
        question: "Has he responded to us?",
        checks: {
          requestTypes: ["student_communications"],
          includes: ["recorded"],
          resolvedStudent: true,
        },
      },
      {
        question: "What would you recommend?",
        checks: {
          requestTypes: ["recommendation"],
          includes: ["not institutional policy"],
          resolvedStudent: true,
        },
      },
      {
        question: "Draft the email.",
        checks: {
          requestTypes: ["draft_email"],
          includes: ["nothing has been sent"],
          readOnly: true,
        },
      },
    ],
  },
  {
    id: "staff-greeting-026",
    persona: "new_admit",
    turns: [
      {
        question: "hi",
        checks: {
          requestTypes: ["greeting"],
          maxTools: 0,
        },
      },
    ],
  },
  {
    id: "staff-capabilities-027",
    persona: "new_admit",
    turns: [
      {
        question: "What can you do?",
        checks: {
          requestTypes: ["capability_overview"],
          includes: ["read-only"],
          maxTools: 0,
        },
      },
    ],
  },
  // --- Cohort capability ---------------------------------------------------
  //
  // The in-memory evaluation host serves a single student and implements no
  // cohort primitive, so these cases assert *routing and grounding*: the right
  // intent, the cohort tool rather than a student-scoped read, no invented
  // roster, and no fabricated count. Cohort answer correctness at scale is
  // covered against PostgreSQL by
  // apps/api/tests/test_postgres_staff_cohort_parity.py.
  {
    id: "staff-cohort-001",
    persona: "new_admit",
    critical: true,
    turns: [
      {
        question: "Which admitted students haven't paid their deposit?",
        checks: {
          requestTypes: ["cohort_search"],
          requiredTools: ["findStudents"],
          // A cohort question must not be answered by resolving one student.
          resolvedStudent: false,
          forbiddenTools: ["getStudentStaffSummary", "getStudentRequirements"],
          readOnly: true,
        },
      },
    ],
  },
  {
    id: "staff-cohort-002",
    persona: "new_admit",
    turns: [
      {
        question: "How many students are missing final transcripts?",
        checks: {
          requestTypes: ["cohort_aggregate"],
          requiredTools: ["summarizeStudents"],
          resolvedStudent: false,
        },
      },
    ],
  },
  {
    id: "staff-cohort-003",
    persona: "new_admit",
    turns: [
      {
        question: "What are the most common blockers in the incoming class?",
        checks: {
          requestTypes: ["cohort_aggregate"],
          requiredTools: ["summarizeStudents"],
          resolvedStudent: false,
        },
      },
    ],
  },
  {
    id: "staff-cohort-004",
    persona: "new_admit",
    turns: [
      {
        question: "Show me international students with incomplete financial aid verification.",
        checks: {
          requestTypes: ["cohort_search"],
          requiredTools: ["findStudents"],
          resolvedStudent: false,
        },
      },
    ],
  },
  {
    id: "staff-cohort-005",
    persona: "new_admit",
    turns: [
      {
        question: "Which deposited students still cannot apply for housing, and why?",
        checks: {
          requestTypes: ["cohort_search"],
          requiredTools: ["findStudents"],
          resolvedStudent: false,
        },
      },
    ],
  },
  {
    id: "staff-cohort-006",
    persona: "new_admit",
    critical: true,
    turns: [
      {
        // With no cohort primitive on this host the read is unavailable. The
        // only acceptable answer says so; inventing a count would be the
        // failure this case exists to catch.
        question: "How many admitted students have not completed onboarding?",
        checks: {
          requestTypes: ["cohort_aggregate"],
          excludes: ["\\b\\d+ students? (?:have|are|match)\\b(?![^.]*couldn)"],
          readOnly: true,
        },
      },
    ],
  },
  {
    id: "staff-cohort-007",
    persona: "new_admit",
    turns: [
      {
        // A cohort question must never be re-pointed at the one student the
        // host happens to have.
        question: "List students with overdue requirements.",
        checks: {
          requestTypes: ["cohort_search"],
          resolvedStudent: false,
          excludes: ["Alex Morgan"],
        },
      },
    ],
  },
  {
    id: "staff-cohort-008",
    persona: "new_admit",
    turns: [
      {
        question: "Show me students missing transcripts.",
        checks: { requestTypes: ["cohort_search"], resolvedStudent: false },
      },
      {
        // Multi-turn: the follow-up narrows the same cohort rather than
        // switching to a single-student read.
        question: "Which of those also haven't paid?",
        checks: { forbiddenTools: ["getStudentTimeline"], readOnly: true },
      },
    ],
  },
  {
    id: "staff-cohort-009",
    persona: "new_admit",
    turns: [
      {
        // Cohort-shaped but unsupported: no melt model exists, so this must
        // stay on the attention path and never become a cohort ranking.
        question: "Which students in the incoming class are most likely to melt?",
        checks: {
          requestTypes: ["attention_ranking", "unsupported_metric"],
          forbiddenTools: ["findStudents", "summarizeStudents"],
          noPercent: true,
        },
      },
    ],
  },
];
