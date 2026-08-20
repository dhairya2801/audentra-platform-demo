/**
 * Student Edward v3 — holdout suite (20 scenarios).
 *
 * Written alongside the development suite and **not executed** until the
 * implementation work was finished, so the score measures first-contact
 * generalization rather than fit. Same capabilities as the dev suite, but
 * different personas, different phrasings, and different intent combinations.
 */

export const HOLDOUT_CASES = [
  {
    id: "hold-stu-001",
    category: "overview",
    persona: "aid_ready_to_disburse",
    turns: [
      {
        question: "give me the short version of where i'm at",
        expect: {
          facts: [
            { pattern: "{{all:openRequirementTitles}}", desc: "names every open requirement", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "hold-stu-002",
    category: "overview",
    persona: "official_hold",
    turns: [
      {
        question: "Anything I'm forgetting?",
        expect: {
          facts: [
            { pattern: "{{any:openBlockingTitles}}", desc: "names a genuinely open blocking item", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "hold-stu-003",
    category: "requirements",
    persona: "advising_booked",
    turns: [
      {
        question: "Is my identity document sorted?",
        expect: {
          facts: [
            { pattern: "identity", desc: "answers about the identity document", critical: true },
            { pattern: "(?:not (?:yet )?(?:submitted|uploaded|accepted|complete)|still|missing|outstanding|haven'?t)", desc: "reports the real open state", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "hold-stu-004",
    category: "requirements",
    persona: "aid_finalized",
    turns: [
      {
        question: "what's left besides the deposit",
        expect: {
          facts: [
            { pattern: "{{any:missingDocumentTitles}}", desc: "names the remaining document work", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "hold-stu-005",
    category: "why_blocked",
    persona: "fafsa_missing",
    turns: [
      {
        question: "why is my aid page basically empty",
        expect: {
          facts: [
            { pattern: "FAFSA", desc: "names the FAFSA as the reason", critical: true },
          ],
          forbidden: [
            { pattern: "Pell Grant|Achievement Scholarship", desc: "invents awards" },
          ],
        },
      },
    ],
  },
  {
    id: "hold-stu-006",
    category: "why_blocked",
    persona: "payment_pending",
    turns: [
      {
        question: "why does my checklist still say i owe the deposit",
        expect: {
          facts: [
            { pattern: "(?:pending|processing|not (?:yet )?posted|hasn'?t posted|still clearing)", desc: "explains the payment has not posted yet", critical: true },
          ],
          forbidden: [
            { pattern: "pay (?:it|the deposit) again|make (?:another|a second) payment", desc: "tells the student to pay twice" },
          ],
        },
      },
    ],
  },
  {
    id: "hold-stu-007",
    category: "conflict",
    persona: "deposit_posted",
    turns: [
      {
        question: "I don't think my deposit ever went through.",
        expect: {
          facts: [
            { pattern: "(?:paid|posted|received|complete|cleared)", desc: "corrects the student with the posted record", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "hold-stu-008",
    category: "deadlines",
    persona: "aid_refund_due",
    turns: [
      {
        question: "anything i need to handle this month",
        expect: {
          facts: [
            { pattern: "(?:{{any:openRequirementTitles}}|nothing|no (?:deadlines|items)|none)", desc: "answers from the real deadline set", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "hold-stu-009",
    category: "aid",
    persona: "aid_finalized",
    turns: [
      {
        question: "do i still have to do the verification thing",
        expect: {
          facts: [
            { pattern: "(?:complete|done|verified|finished|no(?:thing)? (?:more|else|further))", desc: "reports verification as finished", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "hold-stu-010",
    category: "aid",
    persona: "no_aid",
    turns: [
      {
        question: "what scholarships did i get",
        expect: {
          facts: [
            { pattern: "(?:no|not|none|don'?t have)", desc: "states there are no awards", critical: true },
          ],
          forbidden: [
            { pattern: "Achievement Scholarship|Pell Grant", desc: "invents awards" },
          ],
        },
      },
    ],
  },
  {
    id: "hold-stu-011",
    category: "housing",
    persona: "aid_ready_to_disburse",
    turns: [
      {
        question: "is housing open to me yet",
        expect: {
          facts: [
            { pattern: "(?:yes|you can|available|open|ready|able)", desc: "confirms housing is reachable", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "hold-stu-012",
    category: "documents",
    persona: "document_needs_resubmission",
    turns: [
      {
        question: "do i need to send the transcript again",
        expect: {
          facts: [
            { pattern: "(?:yes|resubmi|again|new (?:copy|upload)|returned|rejected)", desc: "confirms a resubmission is needed", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "hold-stu-013",
    category: "prioritization",
    persona: "payment_pending",
    turns: [
      {
        question: "what's the single most useful thing i can do right now",
        expect: {
          facts: [
            { pattern: "{{any:missingDocumentTitles}}", desc: "recommends an action the student can actually take", critical: true },
          ],
          forbidden: [
            { pattern: "pay (?:your|the) (?:enrollment )?deposit(?![^.]*(?:pending|processing|posted|clear))", desc: "recommends re-paying a pending deposit" },
          ],
        },
      },
    ],
  },
  {
    id: "hold-stu-014",
    category: "navigation",
    persona: "deposit_posted",
    turns: [
      {
        question: "where in the portal do i see what i owe",
        expect: {
          facts: [
            { pattern: "/financials|/payments|Financials page|Payments page", desc: "names the real billing page", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "hold-stu-015",
    category: "navigation",
    persona: "advising_booked",
    turns: [
      {
        question: "how do i find my advising appointment",
        expect: {
          facts: [
            { pattern: "/appointments|Appointments page", desc: "names the Appointments page", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "hold-stu-016",
    category: "knowledge",
    persona: "housing_assigned",
    turns: [
      {
        question: "any outdoorsy clubs?",
        expect: {
          requiredTools: ["getCampusLife"],
          facts: [
            { pattern: "{{any:clubNames}}", desc: "names real clubs", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "hold-stu-017",
    category: "multi_intent",
    persona: "deadline_passed",
    turns: [
      {
        question: "how much is the deposit, when was it due, and can i still pay it",
        expect: {
          facts: [
            { pattern: "{{f:depositAmountUsd}}", desc: "answers the amount clause", critical: true },
            { pattern: "(?:overdue|past due|July|was due)", desc: "answers the when clause", critical: true },
            { pattern: "/payments|Payments page|you can (?:still )?pay", desc: "answers the can-I-still-pay clause", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "hold-stu-018",
    category: "context_carry",
    persona: "transcript_under_review",
    turns: [
      { question: "What's the status of my transcript?", expect: {} },
      {
        question: "how long does that usually take",
        expect: {
          facts: [
            { pattern: "(?:transcript|review)", desc: "stays on the transcript review", critical: true },
          ],
          forbidden: [
            { pattern: "(?:usually|typically) takes \\d+", desc: "invents a processing-time SLA" },
          ],
        },
      },
    ],
  },
  {
    id: "hold-stu-019",
    category: "context_reset",
    persona: "aid_verification_outstanding",
    turns: [
      { question: "What's blocking my enrollment?", expect: {} },
      {
        question: "Where's the help page?",
        expect: {
          facts: [
            { pattern: "/help|Help page", desc: "answers the navigation question", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "hold-stu-020",
    category: "unsupported",
    persona: "aid_ready_to_disburse",
    turns: [
      {
        question: "can you drop me from the immunization requirement",
        expect: {
          facts: [
            { pattern: "(?:can'?t|cannot|unable|don'?t)[^.]{0,60}(?:waive|remove|drop|change|do that)", desc: "declines the write action", critical: true },
          ],
        },
      },
    ],
  },
];

export default HOLDOUT_CASES;
