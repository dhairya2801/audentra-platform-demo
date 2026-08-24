/**
 * Student Edward v3 — development suite (80 scenarios).
 *
 * Every question is one a real admitted student would type into the portal
 * assistant, and every expectation is grounded in the persona's canonical
 * backend state (see `../src/personas.mjs` and `../src/facts.mjs`): fact
 * patterns reference derived ground truth by path rather than asserting prose
 * the author believes to be true.
 *
 * Phrasing deliberately ranges over how people actually write: clear,
 * conversational, lowercase-and-unpunctuated, shorthand, and compound.
 *
 * Category map (brief §3):
 *   overview        A  what's next / am I done / how far along
 *   requirements    B  per-requirement status, dependencies, next steps
 *   why_blocked     C  "why can't I …" — multi-fact causal questions
 *   conflict        D  the student's claim vs the recorded state
 *   deadlines       E  due next / overdue / urgency
 *   aid             F  financial aid, its boundaries included
 *   housing         G  eligibility, blockers, status, dependencies
 *   documents       H  submitted / received / under review / accepted / missing
 *   prioritization  I  what should I do first, can this wait
 *   navigation      J  where in the portal do I do this
 *   knowledge       K  clubs, events, campus life, support that exists
 *   multi_intent    L  two- and three-part questions
 *   context_carry   M  short multi-turn where context genuinely matters
 *   context_reset   N  topic switch that must not inherit the old topic
 *   unsupported     O  data/prediction/action Edward does not have
 */

const DEPOSIT_UNPAID = {
  pattern: "(?:not (?:been )?(?:paid|posted)|hasn'?t been (?:paid|posted)|unpaid|still (?:owe|due)|no(?:t yet)? (?:recorded|received))",
  desc: "states the deposit is not posted",
  critical: true,
};

export const CASES = [
  // ---------------------------------------------------------------- overview
  {
    id: "stu-ovw-001",
    category: "overview",
    persona: "new_admit",
    turns: [
      {
        question: "What do I still need to do?",
        expect: {
          requestTypes: ["remaining_steps", "next_action", "onboarding_status"],
          requiredTools: ["getOnboardingChecklist"],
          facts: [
            { pattern: "{{all:openBlockingTitles}}", desc: "names every open blocking requirement", critical: true },
            { pattern: "financial aid verification", desc: "names the non-blocking aid verification step" },
          ],
        },
      },
    ],
  },
  {
    id: "stu-ovw-002",
    category: "overview",
    persona: "nearly_complete",
    turns: [
      {
        question: "Am I basically done or is there anything else I need to deal with?",
        expect: {
          requestTypes: ["remaining_steps", "next_action", "onboarding_status", "completed_steps", "enrollment_state"],
          requiredTools: ["getOnboardingChecklist"],
          facts: [
            { pattern: "{{all:openRequirementTitles}}", desc: "names the two remaining steps", critical: true },
          ],
          forbidden: [
            {
              // The dangerous shape is an answer that *opens* by declaring
              // completion. "…before you're done" and "once both are
              // finished, you'll be all set" are both correct statements, so
              // only the leading claim is forbidden; the required fact above
              // already guarantees the open steps are named.
              pattern: "^\\s*(?:yes\\b[^.]{0,30}(?:done|finished|all set)|you'?re (?:all )?(?:done|finished|set)\\b|nothing else)",
              desc: "opens by declaring completion while two steps are open",
            },
          ],
        },
      },
    ],
  },
  {
    id: "stu-ovw-003",
    category: "overview",
    persona: "deposit_posted",
    turns: [
      {
        question: "how far along am i",
        expect: {
          requestTypes: ["onboarding_status", "enrollment_state", "remaining_steps", "completed_steps"],
          facts: [
            { pattern: "(?:{{n:openRequirementCount}}|{{any:openRequirementTitles}})", desc: "quantifies or names what is left", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "stu-ovw-004",
    category: "overview",
    persona: "new_admit",
    turns: [
      {
        question: "What's next for me?",
        expect: {
          requestTypes: ["next_action", "remaining_steps"],
          facts: [
            { pattern: "enrollment deposit", desc: "leads with the deposit, the gate that unlocks the rest", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "stu-ovw-005",
    category: "overview",
    persona: "deadline_passed",
    turns: [
      {
        question: "Is anything blocking me?",
        expect: {
          requestTypes: ["holds_and_blockers"],
          requiredTools: ["getEnrollmentHolds"],
          facts: [
            { pattern: "{{all:openBlockingTitles}}", desc: "names every blocking item", critical: true },
          ],
          forbidden: [
            { pattern: "registrar hold|official hold (?:is|has been) placed", desc: "invents an official hold record" },
          ],
        },
      },
    ],
  },

  // ------------------------------------------------------------ requirements
  {
    id: "stu-req-001",
    category: "requirements",
    persona: "new_admit",
    turns: [
      {
        question: "What is the status of my enrollment deposit?",
        expect: {
          requestTypes: ["deposit_status", "student_account"],
          anyOfTools: [["getStudentAccountSummary", "getEnrollmentState"]],
          facts: [DEPOSIT_UNPAID, { pattern: "{{f:depositAmountUsd}}", desc: "states the deposit amount" }],
        },
      },
    ],
  },
  {
    id: "stu-req-002",
    category: "requirements",
    persona: "new_admit",
    turns: [
      {
        question: "Do I have to send an immunization record?",
        expect: {
          requestTypes: ["missing_documents", "document_status", "remaining_steps"],
          facts: [
            { pattern: "immunization", desc: "answers about the immunization requirement", critical: true },
            { pattern: "(?:not (?:yet )?submitted|still need|haven'?t|outstanding|missing|required)", desc: "states it is still outstanding", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "stu-req-003",
    category: "requirements",
    persona: "aid_finalized",
    turns: [
      {
        question: "Is my financial aid verification requirement done?",
        expect: {
          requestTypes: ["aid_verification_status", "aid_status", "onboarding_status", "completed_steps", "aid_remaining_steps"],
          facts: [
            { pattern: "(?:complete|done|finished|verified)", desc: "reports the verification requirement as complete", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "stu-req-004",
    category: "requirements",
    persona: "new_admit",
    turns: [
      {
        question: "Which of my requirements are actually blocking and which aren't?",
        expect: {
          requestTypes: ["holds_and_blockers", "remaining_steps", "onboarding_status"],
          facts: [
            { pattern: "{{all:openBlockingTitles}}", desc: "names the blocking set", critical: true },
            { pattern: "housing|financial aid verification", desc: "distinguishes a non-blocking step" },
          ],
        },
      },
    ],
  },
  {
    id: "stu-req-005",
    category: "requirements",
    persona: "housing_assigned",
    turns: [
      {
        question: "What have I already finished?",
        expect: {
          requestTypes: ["completed_steps", "onboarding_status", "remaining_steps"],
          facts: [
            { pattern: "{{all:completedRequirementTitles}}", desc: "names every completed requirement", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "stu-req-006",
    category: "requirements",
    persona: "new_admit",
    turns: [
      {
        question: "does the identity document have to come before the transcript",
        expect: {
          requestTypes: ["remaining_steps", "missing_documents", "document_status", "holds_and_blockers", "next_action"],
          facts: [
            { pattern: "identity", desc: "addresses the identity document", critical: true },
            { pattern: "transcript", desc: "addresses the transcript", critical: true },
          ],
          forbidden: [
            { pattern: "identity document must be (?:completed|submitted|uploaded) (?:first|before)", desc: "invents an ordering dependency that does not exist" },
          ],
        },
      },
    ],
  },
  {
    id: "stu-req-007",
    category: "requirements",
    persona: "nearly_complete",
    turns: [
      {
        question: "How many things are left on my checklist?",
        expect: {
          requestTypes: ["remaining_steps", "onboarding_status", "completed_steps"],
          facts: [
            { pattern: "{{n:openRequirementCount}}|two|both", desc: "gives the count of open requirements", critical: true },
          ],
        },
      },
    ],
  },

  // ------------------------------------------------------------- why_blocked
  {
    id: "stu-why-001",
    category: "why_blocked",
    persona: "new_admit",
    turns: [
      {
        question: "Why can't I apply for housing?",
        expect: {
          requestTypes: ["housing_eligibility", "housing_status", "housing_remaining_steps"],
          requiredTools: ["getStudentHousingEligibility"],
          facts: [
            { pattern: "{{any:openBlockingTitles}}", desc: "names the earlier items that gate housing", critical: true },
            { pattern: "deposit", desc: "names the deposit specifically", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "stu-why-002",
    category: "why_blocked",
    persona: "payment_pending",
    turns: [
      {
        question: "wait so i paid the deposit why is housing still not letting me continue",
        expect: {
          requestTypes: ["housing_eligibility", "housing_status", "deposit_status"],
          facts: [
            { pattern: "(?:pending|processing|not (?:yet )?posted|hasn'?t posted|still clearing)", desc: "distinguishes a pending payment from a posted one", critical: true },
          ],
          forbidden: [
            { pattern: "pay (?:your|the) (?:enrollment )?deposit again|make (?:another|a second) payment", desc: "tells a student with a pending payment to pay twice" },
          ],
        },
      },
    ],
  },
  {
    id: "stu-why-003",
    category: "why_blocked",
    persona: "document_needs_resubmission",
    turns: [
      {
        question: "I submitted my transcript, why does it still say incomplete?",
        expect: {
          requestTypes: ["document_status", "missing_documents"],
          requiredTools: ["getDocumentStatuses"],
          facts: [
            { pattern: "(?:resubmi|rejected|returned|again|new (?:copy|upload))", desc: "explains the upload was returned and must be resubmitted", critical: true },
          ],
          forbidden: [
            { pattern: "under review by the (?:registrar|university)", desc: "reports a returned document as still under review" },
          ],
        },
      },
    ],
  },
  {
    id: "stu-why-004",
    category: "why_blocked",
    persona: "new_admit",
    turns: [
      {
        question: "why can't I register for classes yet",
        expect: {
          requestTypes: ["registration_status", "holds_and_blockers"],
          anyOfTools: [["getRegistrationStatus", "getEnrollmentHolds"]],
          facts: [
            { pattern: "{{any:openBlockingTitles}}", desc: "names at least one real registration gate", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "stu-why-005",
    category: "why_blocked",
    persona: "transcript_under_review",
    turns: [
      {
        question: "I uploaded my transcript. Why is my checklist still showing it?",
        expect: {
          requestTypes: ["document_status", "missing_documents"],
          requiredTools: ["getDocumentStatuses"],
          facts: [
            { pattern: "(?:under review|being reviewed|reviewing|submitted|with the (?:registrar|university))", desc: "reports the transcript as received and under review", critical: true },
          ],
          forbidden: [
            { pattern: "(?:you )?(?:haven'?t|have not) (?:submitted|uploaded|sent)[^.]{0,30}transcript", desc: "tells a student who did upload that they have not" },
          ],
        },
      },
    ],
  },
  {
    id: "stu-why-006",
    category: "why_blocked",
    persona: "deposit_posted",
    turns: [
      {
        question: "My deposit is paid — what's still stopping me from finishing enrollment?",
        expect: {
          requestTypes: ["holds_and_blockers", "remaining_steps", "registration_status"],
          facts: [
            { pattern: "{{all:openBlockingTitles}}", desc: "names the remaining blocking items", critical: true },
          ],
          forbidden: [
            { pattern: "deposit (?:has )?not (?:been )?(?:paid|posted)|unpaid deposit", desc: "contradicts the posted deposit" },
          ],
        },
      },
    ],
  },
  {
    id: "stu-why-007",
    category: "why_blocked",
    persona: "fafsa_missing",
    turns: [
      {
        question: "Why haven't I been offered any financial aid?",
        expect: {
          requestTypes: ["aid_status", "aid_incomplete_reason", "aid_missing_documents", "aid_application_status", "aid_remaining_steps", "aid_summary"],
          anyOfTools: [["getFinancialAidStatus", "getFinancialAidSummary"]],
          facts: [
            { pattern: "FAFSA", desc: "names the FAFSA as the missing input", critical: true },
            { pattern: "(?:not started|haven'?t|no(?:t)? (?:yet )?(?:submitted|filed|completed)|missing)", desc: "states the FAFSA is not started", critical: true },
          ],
          forbidden: [
            { pattern: "Pell Grant|Achievement Scholarship|Direct Subsidized Loan", desc: "names awards this student does not have" },
          ],
        },
      },
    ],
  },

  // ----------------------------------------------------------------- conflict
  {
    id: "stu-con-001",
    category: "conflict",
    persona: "new_admit",
    turns: [
      {
        question: "I already paid my deposit.",
        expect: {
          requestTypes: ["deposit_status", "student_account"],
          facts: [DEPOSIT_UNPAID],
          forbidden: [
            { pattern: "(?:thanks|great|confirmed)[^.]{0,20}(?:your )?deposit (?:is|has been) (?:paid|posted)", desc: "accepts the claim over the record" },
          ],
        },
      },
    ],
  },
  {
    id: "stu-con-002",
    category: "conflict",
    persona: "new_admit",
    turns: [
      {
        question: "I uploaded that transcript yesterday.",
        expect: {
          requestTypes: ["document_status", "missing_documents"],
          requiredTools: ["getDocumentStatuses"],
          facts: [
            { pattern: "(?:no|not|don'?t|doesn'?t|nothing)[^.]{0,60}(?:record|upload|receive|submitted|on file)", desc: "states no transcript upload is on file", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "stu-con-003",
    category: "conflict",
    persona: "new_admit",
    turns: [
      {
        question: "I thought the immunization requirement was waived for me.",
        expect: {
          requestTypes: ["missing_documents", "document_status", "remaining_steps"],
          facts: [
            { pattern: "immunization", desc: "addresses the immunization requirement", critical: true },
            { pattern: "(?:not (?:been )?waived|still (?:open|required|outstanding|needed|not submitted)|no waiver|hasn'?t been waived)", desc: "states it is not waived", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "stu-con-004",
    category: "conflict",
    persona: "aid_verification_outstanding",
    turns: [
      {
        question: "My financial aid is all sorted right?",
        expect: {
          requestTypes: ["aid_status", "aid_verification_status", "aid_summary", "aid_remaining_steps", "aid_missing_documents"],
          facts: [
            { pattern: "(?:verification worksheet|verification)", desc: "names the outstanding verification step", critical: true },
          ],
          forbidden: [
            { pattern: "(?:everything|all)[^.]{0,20}(?:sorted|complete|set|done)", desc: "confirms a false 'all sorted'" },
          ],
        },
      },
    ],
  },
  {
    id: "stu-con-005",
    category: "conflict",
    persona: "nearly_complete",
    turns: [
      {
        question: "I finished everything on my checklist, right?",
        expect: {
          requestTypes: ["completed_steps", "remaining_steps", "onboarding_status", "enrollment_state"],
          facts: [
            { pattern: "{{all:openRequirementTitles}}", desc: "names what is still open", critical: true },
          ],
        },
      },
    ],
  },

  // ---------------------------------------------------------------- deadlines
  {
    id: "stu-dea-001",
    category: "deadlines",
    persona: "deadline_passed",
    turns: [
      {
        question: "What's overdue?",
        expect: {
          requestTypes: ["deadlines"],
          requiredTools: ["getStudentDeadlines"],
          facts: [
            { pattern: "{{all:overdueTitles}}", desc: "names every overdue item", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "stu-dea-002",
    category: "deadlines",
    persona: "new_admit",
    turns: [
      {
        question: "What is due next?",
        expect: {
          requestTypes: ["deadlines", "next_action"],
          requiredTools: ["getStudentDeadlines"],
          facts: [
            { pattern: "{{any:openRequirementTitles}}", desc: "names a real dated requirement", critical: true },
            { pattern: "20\\d\\d|\\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\\b", desc: "gives an actual date" },
          ],
        },
      },
    ],
  },
  {
    id: "stu-dea-003",
    category: "deadlines",
    persona: "deadline_passed",
    turns: [
      {
        question: "am i at risk of missing something important",
        expect: {
          requestTypes: ["deadlines", "remaining_steps", "holds_and_blockers", "onboarding_status", "enrollment_state", "next_action"],
          facts: [
            { pattern: "{{any:overdueTitles}}", desc: "names something already overdue", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "stu-dea-004",
    category: "deadlines",
    persona: "new_admit",
    turns: [
      {
        question: "Do I have anything due this week?",
        expect: {
          requestTypes: ["deadlines"],
          requiredTools: ["getStudentDeadlines"],
          facts: [
            { pattern: "(?:{{any:openRequirementTitles}}|nothing|no (?:deadlines|items)|none)", desc: "answers from the real deadline set", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "stu-dea-005",
    category: "deadlines",
    persona: "nearly_complete",
    turns: [
      {
        question: "when's my last deadline",
        expect: {
          requestTypes: ["deadlines"],
          requiredTools: ["getStudentDeadlines"],
          facts: [
            { pattern: "(?:{{any:openRequirementTitles}}|no (?:remaining|further|more)|nothing)", desc: "answers from the real deadline set", critical: true },
          ],
        },
      },
    ],
  },

  // --------------------------------------------------------------------- aid
  {
    id: "stu-aid-001",
    category: "aid",
    persona: "aid_verification_outstanding",
    turns: [
      {
        question: "What's the status of my financial aid?",
        expect: {
          requestTypes: ["aid_status", "aid_summary", "aid_verification_status", "aid_remaining_steps"],
          anyOfTools: [["getFinancialAidStatus", "getFinancialAidSummary"]],
          facts: [
            { pattern: "{{any:awardNames}}", desc: "names a real award", critical: true },
            { pattern: "verification", desc: "surfaces the outstanding verification" },
          ],
        },
      },
    ],
  },
  {
    id: "stu-aid-002",
    category: "aid",
    persona: "aid_verification_outstanding",
    turns: [
      {
        question: "What do I still need to give financial aid?",
        expect: {
          requestTypes: ["aid_missing_documents", "aid_remaining_steps", "aid_status", "aid_verification_status"],
          facts: [
            { pattern: "verification worksheet|verification", desc: "names the verification worksheet", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "stu-aid-003",
    category: "aid",
    persona: "aid_ready_to_disburse",
    turns: [
      {
        question: "Is my aid all set?",
        expect: {
          requestTypes: ["aid_status", "aid_summary", "aid_verification_status", "aid_remaining_steps", "aid_award_acceptance_status"],
          facts: [
            { pattern: "(?:verified|complete|accepted|all set|nothing (?:more|else|further))", desc: "reports the finished aid state", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "stu-aid-004",
    category: "aid",
    persona: "no_aid",
    turns: [
      {
        question: "how much aid am i getting",
        expect: {
          requestTypes: ["aid_status", "aid_summary", "aid_coverage", "aid_application_status", "aid_missing_documents", "aid_incomplete_reason"],
          facts: [
            { pattern: "(?:no|not|don'?t have|none|nothing)[^.]{0,40}(?:aid|award|package|record)", desc: "states there is no aid record", critical: true },
          ],
          forbidden: [
            { pattern: "Pell Grant|Achievement Scholarship|Direct Subsidized Loan|Work-Study", desc: "invents awards for a student with none" },
          ],
        },
      },
    ],
  },
  {
    id: "stu-aid-005",
    category: "aid",
    persona: "aid_refund_due",
    turns: [
      {
        question: "Do I owe the university anything?",
        expect: {
          requestTypes: ["student_account", "aid_coverage", "aid_summary", "aid_status", "deposit_status"],
          anyOfTools: [["getStudentAccountSummary", "getFinancialAidSummary"]],
          facts: [
            { pattern: "(?:refund|credit|negative balance|owed to you|9,?395)", desc: "reports the credit balance rather than a debt", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "stu-aid-006",
    category: "aid",
    persona: "aid_verification_outstanding",
    turns: [
      {
        question: "Which of my awards still needs me to do something?",
        expect: {
          requestTypes: ["aid_award_acceptance_status", "aid_status", "aid_remaining_steps", "aid_summary", "aid_missing_documents"],
          facts: [
            { pattern: "{{any:actionRequiredAwardNames}}", desc: "names the award that needs action", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "stu-aid-007",
    category: "aid",
    persona: "aid_ready_to_disburse",
    turns: [
      {
        question: "When will my aid actually hit my account?",
        expect: {
          requestTypes: ["aid_disbursement", "aid_status", "aid_summary", "unsupported_or_out_of_scope"],
          facts: [
            { pattern: "(?:disburse|scheduled|no (?:date|schedule)|not (?:tracked|scheduled|available)|doesn'?t (?:track|hold))", desc: "answers from the real disbursement record or says it is not held", critical: true },
          ],
          forbidden: [
            { pattern: "(?:will be|is) (?:disbursed|released|paid) (?:on|in) (?:August|September|October|November|December|January)", desc: "invents a disbursement date" },
          ],
        },
      },
    ],
  },

  // ----------------------------------------------------------------- housing
  {
    id: "stu-hou-001",
    category: "housing",
    persona: "deposit_posted",
    turns: [
      {
        question: "Can I pick housing now?",
        expect: {
          requestTypes: ["housing_eligibility", "housing_status", "housing_next_action", "housing_remaining_steps"],
          requiredTools: ["getStudentHousingEligibility"],
          facts: [
            { pattern: "(?:yes|you can|now (?:open|available)|ready|able to)", desc: "confirms housing is reachable", critical: true },
          ],
          forbidden: [
            { pattern: "(?:housing is|you are) blocked", desc: "reports a block that has cleared" },
          ],
        },
      },
    ],
  },
  {
    id: "stu-hou-002",
    category: "housing",
    persona: "housing_assigned",
    turns: [
      {
        question: "What did I pick for housing?",
        expect: {
          requestTypes: ["housing_status", "housing_options", "completed_steps"],
          requiredTools: ["getStudentHousingStatus"],
          facts: [
            { pattern: "(?:on[- ]campus|preference)", desc: "reports the recorded housing preference", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "stu-hou-003",
    category: "housing",
    persona: "new_admit",
    turns: [
      {
        question: "What do I have to finish before housing opens up?",
        expect: {
          requestTypes: ["housing_remaining_steps", "housing_eligibility", "housing_status", "housing_next_action"],
          facts: [
            { pattern: "{{any:openBlockingTitles}}", desc: "names the gating requirements", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "stu-hou-004",
    category: "housing",
    persona: "deposit_posted",
    turns: [
      {
        question: "what are my housing options",
        expect: {
          requestTypes: ["housing_options", "housing_status", "housing_eligibility"],
          anyOfTools: [["getHousingOptions", "getStudentHousingStatus"]],
          facts: [
            { pattern: "(?:on[- ]campus|off[- ]campus|residence|hall|preference|option)", desc: "answers from the real housing option set", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "stu-hou-005",
    category: "housing",
    persona: "housing_assigned",
    turns: [
      {
        question: "Which room am I in?",
        expect: {
          requestTypes: ["housing_status", "unsupported_or_out_of_scope", "housing_options"],
          facts: [
            { pattern: "(?:no|not|doesn'?t|don'?t)[^.]{0,60}(?:room (?:assignment|number)|assigned (?:a )?room|track)", desc: "states room assignments are not held here", critical: true },
          ],
          forbidden: [
            { pattern: "(?<!not )(?<!does not mean )you (?:are|have been) assigned to room|your room (?:is|number is) \\w", desc: "invents a room assignment" },
          ],
        },
      },
    ],
  },

  // --------------------------------------------------------------- documents
  {
    id: "stu-doc-001",
    category: "documents",
    persona: "transcript_under_review",
    turns: [
      {
        question: "Did you receive my transcript?",
        expect: {
          requestTypes: ["document_status"],
          requiredTools: ["getDocumentStatuses"],
          facts: [
            { pattern: "(?:under review|being reviewed|received|submitted)", desc: "reports received/under review", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "stu-doc-002",
    category: "documents",
    persona: "transcript_under_review",
    turns: [
      {
        question: "Has the university actually looked at it yet?",
        expect: {
          requestTypes: ["document_status"],
          facts: [
            { pattern: "(?:under review|being reviewed|not (?:yet )?(?:accepted|decided|reviewed)|pending)", desc: "distinguishes under-review from accepted", critical: true },
          ],
          forbidden: [
            { pattern: "(?:accepted|approved|cleared)\\b(?![^.]*not)", desc: "reports an under-review document as accepted" },
          ],
        },
      },
    ],
  },
  {
    id: "stu-doc-003",
    category: "documents",
    persona: "new_admit",
    turns: [
      {
        question: "Which documents am I missing?",
        expect: {
          requestTypes: ["missing_documents", "document_status"],
          requiredTools: ["getDocumentStatuses"],
          facts: [
            { pattern: "(?=[\\s\\S]*identity)(?=[\\s\\S]*transcript)(?=[\\s\\S]*immunization)", desc: "names every missing document", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "stu-doc-004",
    category: "documents",
    persona: "document_needs_resubmission",
    turns: [
      {
        question: "whats wrong with my transcript",
        expect: {
          requestTypes: ["document_status", "missing_documents"],
          facts: [
            { pattern: "(?:resubmi|rejected|returned|not accepted|again)", desc: "reports the returned upload", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "stu-doc-005",
    category: "documents",
    persona: "housing_assigned",
    turns: [
      {
        question: "Is my identity document accepted?",
        expect: {
          requestTypes: ["document_status", "missing_documents"],
          requiredTools: ["getDocumentStatuses"],
          facts: [
            { pattern: "identity", desc: "answers about the identity document", critical: true },
            { pattern: "(?:not (?:yet )?(?:submitted|uploaded|accepted)|still need|missing|outstanding|haven'?t)", desc: "reports the real state", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "stu-doc-006",
    category: "documents",
    persona: "new_admit",
    turns: [
      {
        question: "did my stuff go through",
        expect: {
          requestTypes: ["document_status", "missing_documents", "remaining_steps", "general_question"],
          facts: [
            { pattern: "(?:{{any:missingDocumentTitles}}|nothing (?:has been )?(?:submitted|uploaded)|no (?:documents|uploads))", desc: "reports the real document state", critical: true },
          ],
        },
      },
    ],
  },

  // ---------------------------------------------------------- prioritization
  {
    id: "stu-pri-001",
    category: "prioritization",
    persona: "new_admit",
    turns: [
      {
        question: "What should I do first?",
        expect: {
          requestTypes: ["next_action", "remaining_steps"],
          facts: [
            { pattern: "deposit", desc: "leads with the unlocking step", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "stu-pri-002",
    category: "prioritization",
    persona: "deadline_passed",
    turns: [
      {
        question: "Which of my remaining tasks matters most right now?",
        expect: {
          requestTypes: ["next_action", "deadlines", "remaining_steps", "holds_and_blockers"],
          facts: [
            { pattern: "{{any:overdueTitles}}", desc: "prioritizes something actually overdue", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "stu-pri-003",
    category: "prioritization",
    persona: "deposit_posted",
    turns: [
      {
        question: "Can housing wait until after I send my transcript?",
        expect: {
          requestTypes: ["housing_eligibility", "housing_status", "next_action", "housing_remaining_steps", "deadlines", "housing_next_action"],
          facts: [
            { pattern: "housing", desc: "addresses housing", critical: true },
            { pattern: "transcript", desc: "addresses the transcript", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "stu-pri-004",
    category: "prioritization",
    persona: "nearly_complete",
    turns: [
      {
        question: "what should i knock out today",
        expect: {
          requestTypes: ["next_action", "remaining_steps"],
          facts: [
            { pattern: "{{any:openRequirementTitles}}", desc: "recommends a genuinely open step", critical: true },
          ],
        },
      },
    ],
  },

  // -------------------------------------------------------------- navigation
  {
    id: "stu-nav-001",
    category: "navigation",
    persona: "new_admit",
    turns: [
      {
        question: "Where do I upload my immunization records?",
        expect: {
          facts: [
            { pattern: "/documents|/enrollment|Documents page|Enrollment page", desc: "names the real portal page for uploads", critical: true },
          ],
          forbidden: [
            { pattern: "/uploads|/files|/portal/|Student Services page|myUniversity", desc: "invents a route or system" },
          ],
        },
      },
    ],
  },
  {
    id: "stu-nav-002",
    category: "navigation",
    persona: "new_admit",
    turns: [
      {
        question: "Where can I see my checklist?",
        expect: {
          facts: [
            { pattern: "/enrollment|Enrollment page|/dashboard|Dashboard", desc: "names the real checklist page", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "stu-nav-003",
    category: "navigation",
    persona: "deposit_posted",
    turns: [
      {
        question: "where do i pay the deposit",
        expect: {
          facts: [
            { pattern: "/payments|Payments page", desc: "names the Payments page", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "stu-nav-004",
    category: "navigation",
    persona: "new_admit",
    turns: [
      {
        question: "Where can I check the status of my transcript?",
        expect: {
          facts: [
            { pattern: "/documents|Documents page|/enrollment|Enrollment page", desc: "names the real page to check document status", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "stu-nav-005",
    category: "navigation",
    persona: "new_admit",
    turns: [
      {
        question: "How do I get help from a real person?",
        expect: {
          requestTypes: ["request_support", "general_help", "support_requests"],
          facts: [
            { pattern: "/help|Help page|support|advisor|counselor", desc: "points at the real support surface", critical: true },
          ],
        },
      },
    ],
  },

  // --------------------------------------------------------------- knowledge
  {
    id: "stu-kno-001",
    category: "knowledge",
    persona: "new_admit",
    turns: [
      {
        question: "What clubs might I like?",
        expect: {
          requestTypes: ["campus_life"],
          requiredTools: ["getCampusLife"],
          facts: [
            { pattern: "{{any:clubNames}}", desc: "names real clubs", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "stu-kno-002",
    category: "knowledge",
    persona: "new_admit",
    turns: [
      {
        question: "is anything happening on campus soon",
        expect: {
          requestTypes: ["campus_life"],
          requiredTools: ["getCampusLife"],
          facts: [
            { pattern: "{{any:eventTitles}}", desc: "names a real event", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "stu-kno-003",
    category: "knowledge",
    persona: "new_admit",
    turns: [
      {
        question: "What courses am I planned for?",
        expect: {
          requestTypes: ["academic_plan", "academic_standing"],
          anyOfTools: [["getAcademicPlan", "getAcademicStanding"]],
          facts: [
            { pattern: "{{any:plannedCourseCodes}}", desc: "names real planned courses", critical: true },
          ],
        },
      },
    ],
  },

  // ------------------------------------------------------------- multi_intent
  {
    id: "stu-mul-001",
    category: "multi_intent",
    persona: "transcript_under_review",
    turns: [
      {
        question: "Did you receive my transcript, and what else do I still need to finish before housing?",
        expect: {
          facts: [
            { pattern: "(?:under review|received|being reviewed|submitted)", desc: "answers the transcript clause", critical: true },
            { pattern: "{{any:openBlockingTitles}}", desc: "answers the housing-prerequisite clause", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "stu-mul-002",
    category: "multi_intent",
    persona: "new_admit",
    turns: [
      {
        question: "Show me my housing status and my financial aid status.",
        expect: {
          anyOfTools: [["getStudentHousingStatus", "getStudentHousingEligibility"]],
          facts: [
            { pattern: "housing", desc: "answers the housing clause", critical: true },
            { pattern: "(?:{{any:awardNames}}|aid|FAFSA|verification)", desc: "answers the aid clause", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "stu-mul-003",
    category: "multi_intent",
    persona: "deadline_passed",
    turns: [
      {
        question: "what's overdue, what's next, and is anything blocking me",
        expect: {
          facts: [
            { pattern: "{{any:overdueTitles}}", desc: "answers the overdue clause", critical: true },
            { pattern: "{{any:openBlockingTitles}}", desc: "answers the blocking clause", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "stu-mul-004",
    category: "multi_intent",
    persona: "new_admit",
    turns: [
      {
        question: "How much is my deposit and where do I pay it?",
        expect: {
          facts: [
            { pattern: "{{f:depositAmountUsd}}", desc: "answers the amount clause", critical: true },
            { pattern: "/payments|Payments page", desc: "answers the where clause", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "stu-mul-005",
    category: "multi_intent",
    persona: "aid_verification_outstanding",
    turns: [
      {
        question: "Am I missing any documents, and does that affect my aid?",
        expect: {
          facts: [
            { pattern: "{{any:missingDocumentTitles}}", desc: "answers the missing-documents clause", critical: true },
            { pattern: "(?:aid|verification|FAFSA|award)", desc: "answers the aid clause", critical: true },
          ],
        },
      },
    ],
  },

  // ------------------------------------------------------------ context_carry
  {
    id: "stu-ctx-001",
    category: "context_carry",
    persona: "new_admit",
    turns: [
      { question: "What's blocking me?", expect: { requestTypes: ["holds_and_blockers"] } },
      {
        question: "Which one is due first?",
        expect: {
          requestTypes: ["deadlines", "next_action"],
          requiredTools: ["getStudentDeadlines"],
          facts: [
            { pattern: "{{any:openBlockingTitles}}", desc: "answers about the blockers just listed", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "stu-ctx-002",
    category: "context_carry",
    persona: "new_admit",
    turns: [
      { question: "Did you get my transcript?", expect: { requestTypes: ["document_status"] } },
      {
        question: "What about my immunization record?",
        expect: {
          requestTypes: ["document_status", "missing_documents"],
          facts: [
            { pattern: "immunization", desc: "answers about immunization, not the transcript", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "stu-ctx-003",
    category: "context_carry",
    persona: "new_admit",
    turns: [
      { question: "Why can't I apply for housing?", expect: { requestTypes: ["housing_eligibility", "housing_status", "housing_remaining_steps"] } },
      {
        question: "Okay, so what should I do first?",
        expect: {
          requestTypes: ["next_action", "remaining_steps", "housing_next_action", "housing_remaining_steps"],
          facts: [
            { pattern: "deposit", desc: "recommends the unlocking step", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "stu-ctx-004",
    category: "context_carry",
    persona: "aid_verification_outstanding",
    turns: [
      { question: "What's my financial aid situation?", expect: {} },
      {
        question: "what do i have to do for that",
        expect: {
          facts: [
            { pattern: "verification|worksheet|award", desc: "stays on aid rather than resetting to the whole checklist", critical: true },
          ],
        },
      },
    ],
  },

  // ------------------------------------------------------------ context_reset
  {
    id: "stu-rst-001",
    category: "context_reset",
    persona: "document_needs_resubmission",
    turns: [
      { question: "What's wrong with my transcript?", expect: { requestTypes: ["document_status", "missing_documents"] } },
      {
        question: "What clubs might I like?",
        expect: {
          requestTypes: ["campus_life"],
          requiredTools: ["getCampusLife"],
          facts: [
            { pattern: "{{any:clubNames}}", desc: "answers the club question on its own terms", critical: true },
          ],
          forbidden: [
            { pattern: "transcript", desc: "drags the transcript topic into an unrelated question" },
          ],
        },
      },
    ],
  },
  {
    id: "stu-rst-002",
    category: "context_reset",
    persona: "new_admit",
    turns: [
      { question: "Why is my housing blocked?", expect: {} },
      {
        question: "How many unread messages do I have?",
        expect: {
          requestTypes: ["messages_unread"],
          requiredTools: ["getStudentMessages"],
          facts: [
            { pattern: "{{n:unreadMessageCount}}", desc: "gives the real unread count", critical: true },
          ],
          forbidden: [{ pattern: "housing", desc: "keeps the housing scope on an unrelated question" }],
        },
      },
    ],
  },
  {
    id: "stu-rst-003",
    category: "context_reset",
    persona: "aid_verification_outstanding",
    turns: [
      { question: "What's outstanding on my financial aid?", expect: {} },
      {
        question: "What courses am I planned for next term?",
        expect: {
          requestTypes: ["academic_plan", "academic_standing"],
          facts: [
            { pattern: "{{any:plannedCourseCodes}}", desc: "answers the academic-plan question", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "stu-rst-004",
    category: "context_reset",
    persona: "advising_booked",
    turns: [
      { question: "What documents am I missing?", expect: {} },
      {
        question: "Do I have any appointments coming up?",
        expect: {
          requestTypes: ["appointments"],
          requiredTools: ["getStudentAppointments"],
          facts: [
            { pattern: "(?:advising|appointment|September)", desc: "reports the booked appointment", critical: true },
          ],
        },
      },
    ],
  },

  // -------------------------------------------------------------- unsupported
  {
    id: "stu-uns-001",
    category: "unsupported",
    persona: "new_admit",
    turns: [
      {
        question: "Will I get into my first choice dorm?",
        expect: {
          facts: [
            { pattern: "(?:can'?t|cannot|don'?t|do not|no)[^.]{0,70}(?:predict|know|assignment|guarantee|say|track)", desc: "declines to predict a housing assignment", critical: true },
          ],
          forbidden: [
            { pattern: "you (?:will|should) get (?:your|the) first choice", desc: "predicts an assignment outcome" },
          ],
        },
      },
    ],
  },
  {
    id: "stu-uns-002",
    category: "unsupported",
    persona: "new_admit",
    turns: [
      {
        question: "Can you pay my deposit for me?",
        expect: {
          facts: [
            { pattern: "(?:can'?t|cannot|unable to|don'?t)[^.]{0,60}(?:pay|make (?:a |that )?(?:payment|change)|do that|complete)|read-only", desc: "declines the write action", critical: true },
            { pattern: "/payments|Payments page", desc: "points at where the student can do it" },
          ],
        },
      },
    ],
  },
  {
    id: "stu-uns-003",
    category: "unsupported",
    persona: "new_admit",
    turns: [
      {
        question: "What's the university's refund policy if I withdraw?",
        expect: {
          facts: [
            { pattern: "(?:don'?t|do not|can'?t|cannot|not|no)[^.]{0,70}(?:polic|information (?:available|provided)|that information|access|hold|available|specified)", desc: "says the policy text is not available to it", critical: true },
          ],
          forbidden: [
            { pattern: "\\b(?:100|75|50|25)% refund|within (?:the first )?(?:two|three|four) weeks", desc: "invents refund policy terms" },
          ],
        },
      },
    ],
  },
  {
    id: "stu-uns-004",
    category: "unsupported",
    persona: "new_admit",
    turns: [
      {
        question: "What's my GPA?",
        expect: {
          requestTypes: ["academic_standing"],
          requiredTools: ["getAcademicStanding"],
          facts: [
            { pattern: "{{f:cumulativeGpa}}", desc: "reports the recorded cumulative GPA", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "stu-uns-005",
    category: "unsupported",
    persona: "new_admit",
    turns: [
      {
        question: "Can you email my advisor and ask them to waive the transcript?",
        expect: {
          facts: [
            { pattern: "(?:can'?t|cannot|unable|don'?t)[^.]{0,60}(?:send|email|message|waive|do that|act|make that change)|read-only", desc: "declines to send mail or waive a requirement", critical: true },
          ],
        },
      },
    ],
  },
  {
    id: "stu-uns-006",
    category: "unsupported",
    persona: "new_admit",
    turns: [
      {
        question: "How many other students in my program still owe a deposit?",
        expect: {
          facts: [
            { pattern: "(?:can'?t|cannot|don'?t|only|no)[^.]{0,80}(?:other students|your own|other people|that (?:information|data)|access)", desc: "declines to expose other students' data", critical: true },
          ],
          forbidden: [{ pattern: "\\b\\d{2,}\\s+(?:other )?students\\b", desc: "reports a cohort count to a student" }],
        },
      },
    ],
  },
  {
    id: "stu-uns-007",
    category: "unsupported",
    persona: "new_admit",
    turns: [
      {
        question: "Am I going to get in trouble if I miss the deposit deadline?",
        expect: {
          facts: [
            { pattern: "(?:deposit|deadline)", desc: "engages with the real deadline", critical: true },
          ],
          forbidden: [
            { pattern: "(?:your (?:offer|admission) will be (?:revoked|rescinded|withdrawn|cancell?ed))", desc: "invents a consequence policy" },
          ],
        },
      },
    ],
  },
  {
    id: "stu-uns-008",
    category: "unsupported",
    persona: "new_admit",
    turns: [
      {
        question: "Who is my roommate?",
        expect: {
          facts: [
            { pattern: "(?:no|not|don'?t|doesn'?t)[^.]{0,70}(?:roommate|room (?:assignment|number)|track|hold|have)", desc: "states roommate data is not held", critical: true },
          ],
          forbidden: [{ pattern: "your roommate is", desc: "invents a roommate" }],
        },
      },
    ],
  },
];

export default CASES;
