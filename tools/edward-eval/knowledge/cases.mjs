/**
 * Institutional-knowledge suite — development bank.
 *
 * Every case combines a persona's own record with the approved corpus:
 * consequences of missing a deadline, whether a rule applies to this student,
 * who owns a step, what a date or amount is. Facts are regexes over the
 * answer; `{{gt:…}}` templates resolve against ground-truth.json (persona
 * facts and the corpus constants read from the packaged files), so a
 * regenerated snapshot or a re-versioned policy cannot silently drift from
 * the bank. Wording is never graded; facts, forbidden claims and the reads
 * that must have happened are.
 */

import { STUDENTS, STAFF } from "./personas.mjs";

const S = (key) => `students.${STUDENTS[key].ref}`;
const ST = (key) => `staff.byRef.${STAFF[key].ref}`;
const C = "corpus";

export const f = (desc, pattern, critical = true) => ({ desc, pattern, critical });
export const soft = (desc, pattern) => ({ desc, pattern, critical: false });
export const forbid = (desc, pattern) => ({ desc, pattern });

export function one(id, category, actorKind, actor, question, expectedBehavior, expect, extra = {}) {
  return { id, category, actorKind, actor, expectedBehavior, turns: [{ question, expect }], ...extra };
}
export function conversation(id, category, actorKind, actor, expectedBehavior, turns, extra = {}) {
  return { id, category, actorKind, actor, expectedBehavior, conversation: true, turns, ...extra };
}

const KNOWLEDGE_STUDENT = "getInstitutionalPolicies";
const KNOWLEDGE_STAFF = "searchInstitutionalKnowledge";

// Hallucination traps shared across categories.
const NO_UUID = forbid("leaks a UUID", "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}");
const NO_PHONE = forbid("invents a phone number", "\\(?\\b\\d{3}\\)?[-. ]\\d{3}[-. ]\\d{4}\\b");
const NO_FAKE_EMAIL = forbid(
  "invented email domain",
  "[a-z0-9._-]+@(?!synthetic\\.aster\\.example|aster\\.example\\.edu)[a-z0-9.-]+\\.[a-z]{2,}",
);
const NO_REFUSAL = forbid(
  "refuses institutional questions",
  "don'?t have access to (?:approved|institutional|policy)|can only answer from approved sources, which I don'?t have",
);
const NO_DONE_CLAIM = forbid(
  "claims to have done it",
  "\\bI(?:'ve| have| just) (?:booked|scheduled|sent|marked|updated|reassigned|closed|accepted|approved|created|emailed|extended|waived)\\b|\\b(?:has|have) been (?:sent|emailed|booked|scheduled|reassigned) (?:to|for|on)\\b",
);
const STANDARD = [NO_UUID, NO_PHONE, NO_FAKE_EMAIL, NO_REFUSAL, NO_DONE_CLAIM];

const NONE = "\\bno\\b|none|nothing|not (?:eligible|apply|applicable|required|subject)|does ?n[o']t apply|exempt";

export const CASES = [
  // ── Deposit: consequence + record ───────────────────────────────────────
  one("kn-dep-001", "deadline_consequence", "student", "petra",
    "what happens if I miss the deposit deadline?",
    "Says the offer is kept but housing/adviser/registration wait on the deposit; names the $500 deposit; notes it is overdue on her record; cites the deposit policy.",
    {
      requiredTools: [KNOWLEDGE_STUDENT],
      facts: [
        f("keeps the offer", "(?:keep|retain|still have|remains? (?:intact|valid|in place|open)|intact|does ?n[o']t (?:cancel|lose|forfeit)|not (?:cancel|lose|forfeit|withdrawn|rescinded))"),
        f("housing consequence", "housing"),
        soft("deposit amount", `{{gt:${C}.amounts.deposit}}`),
        soft("overdue on record", "overdue|past due|already passed"),
      ],
      forbidden: [...STANDARD, forbid("invents a forfeiture of the offer", "offer (?:is|will be|has been) (?:cancelled|canceled|rescinded|withdrawn|forfeited)")],
    }),
  one("kn-dep-002", "deadline_consequence", "student", "petra",
    "is the deposit refundable if I change my mind?",
    "Fall 2026 admit: refundable only on written withdrawal to Admissions by 1 June 2026, which has passed; names Admissions.",
    {
      requiredTools: [KNOWLEDGE_STUDENT],
      facts: [
        f("refund deadline", `{{date:${C}.calendar.fall2026-deposit-refund-deadline}}`),
        f("admissions owns it", "admissions"),
        soft("non-refundable now", "non-?refundable|no longer refundable|has passed|already passed"),
      ],
      forbidden: [...STANDARD],
    }),
  one("kn-dep-003", "applies_to_me", "student", "omar",
    "when do I have to pay my deposit and what happens if I'm late?",
    "Spring 2027 admit: deposit due date from his checklist (not overdue), late means housing/adviser/orientation wait; refund deadline 1 December 2026.",
    {
      requiredTools: [KNOWLEDGE_STUDENT],
      anyOfTools: [["getStudentDeadlines", "getOnboardingChecklist", "getEnrollmentState", "getStudentAccountSummary"]],
      facts: [
        f("his due date", `{{re:${S("omar")}.deposit.dueDatePattern}}`),
        f("consequence", "housing|adviser|orientation|registration"),
        soft("spring refund date", `{{date:${C}.calendar.spring2027-deposit-refund-deadline}}`),
      ],
      forbidden: [...STANDARD, forbid("says it is overdue", "overdue|past due|already passed")],
    }),
  one("kn-dep-004", "deadline_consequence", "student", "hana",
    "my deposit payment is still processing, do I need to pay again to avoid missing the deadline?",
    "No: a pending payment means she must not pay again; the checklist item shows processing until it posts; bank transfers post within five business days.",
    {
      requiredTools: [KNOWLEDGE_STUDENT],
      facts: [
        f("do not pay again", "(?:do not|don'?t|no need to|shouldn'?t|should not) (?:need to )?pay (?:again|twice|a second)"),
        soft("posting time", "five business days|1 business day|one business day|posts"),
      ],
      forbidden: [...STANDARD, forbid("tells her to pay again", "(?<!not need to )(?<!not have to )(?<!not )(?<!no need to )(?<!never )pay (?:it )?again|make another payment|submit a new payment")],
    }),

  // ── Housing residency requirement: applies / does not apply ────────────
  one("kn-hou-001", "applies_to_me", "student", "ada",
    "can freshmen live off campus? does that apply to me?",
    "Rule: first-year students live on campus both semesters unless exempt (family within 30 miles, 21+, accommodation); Ada is a first-year with an off-campus plan, so it applies and she needs an exemption filed with Housing.",
    {
      requiredTools: [KNOWLEDGE_STUDENT],
      facts: [
        f("rule stated", "first[- ]year|freshm[ae]n"),
        f("applies to her", "appl(?:y|ies) to you|you (?:are|'re) (?:a )?first[- ]year|required to live|must live|have to live"),
        soft("an exemption exists", "exempt"),
        soft("30 miles", "30 miles"),
      ],
      forbidden: [...STANDARD, forbid("says it does not apply", "does ?n[o']t apply to you|not (?:apply|applicable) to you|you (?:are )?exempt")],
    }),
  one("kn-hou-002", "applies_to_me", "student", "ivo",
    "do I have to live on campus?",
    "Ivo is a transfer student: the first-year residency requirement does not apply to him.",
    {
      requiredTools: [KNOWLEDGE_STUDENT],
      facts: [
        f("does not apply", "(?:does ?n[o']t|do not|doesn't) apply|not (?:required|subject)|\\bno\\b|transfer"),
        soft("names the transfer reason", "transfer"),
      ],
      forbidden: [...STANDARD, forbid("says he must", "(?:yes|you (?:have|need) to|you must|required to) live on campus")],
    }),
  one("kn-hou-003", "applies_to_me", "student", "ximena",
    "I'm starting in January — do I still have to live on campus, and for how long?",
    "Spring 2027 first-year: the requirement follows the student — Spring 2027 and Fall 2027.",
    {
      requiredTools: [KNOWLEDGE_STUDENT],
      facts: [
        f("applies", "(?:yes|you (?:do|must|have to|are required)|appl(?:y|ies) to you)"),
        f("two semesters incl. next fall", "Fall 2027|both semesters|two semesters|next fall|following fall|first (?:academic )?year"),
      ],
      forbidden: [...STANDARD, forbid("exempts her", "does ?n[o']t apply to you|you (?:are )?exempt")],
    }),
  one("kn-hou-004", "deadline_consequence", "student", "petra",
    "if I don't pay my deposit will I lose my housing spot?",
    "Her housing step is blocked until the deposit posts; applications after 22 August go to the waitlist; first-year students are still guaranteed a bed by the first day of classes.",
    {
      requiredTools: [KNOWLEDGE_STUDENT],
      facts: [
        f("deposit gates housing", "deposit"),
        soft("waitlist or guaranteed", "waitlist|guarantee"),
        soft("final deadline", `{{date:${C}.calendar.fall2026-housing-application-deadline}}`),
      ],
      forbidden: [...STANDARD],
    }),
  one("kn-hou-005", "office_routing", "student", "lucia",
    "when is move-in and can I get there before orientation?",
    "Move-in 22–23 August (past), before Orientation Sessions A–C; international students may arrive 20 August for check-in; Housing owns it.",
    {
      requiredTools: [KNOWLEDGE_STUDENT],
      facts: [
        f("move-in date", `{{date:${C}.calendar.fall2026-move-in}}`),
        f("before orientation", "before orientation|precedes|ahead of orientation|prior to orientation|do not need to wait"),
      ],
      forbidden: [...STANDARD],
    }),

  // ── Immunization / documents ────────────────────────────────────────────
  one("kn-imm-001", "office_routing", "student", "kwame",
    "my immunization record was rejected. what happens now and who handles it?",
    "Resubmit on the checklist item; Student Health reviews within seven business days; the due date does not move; registration stays gated until accepted; health hold on 1 October.",
    {
      requiredTools: [KNOWLEDGE_STUDENT],
      anyOfTools: [["getDocumentStatuses", "getOnboardingChecklist"]],
      facts: [
        f("resubmit", "resubmit|upload (?:a |the )?(?:new|again|another|complete|clear|corrected|replacement)|re-upload|submit (?:a new|again)|need to upload"),
        f("student health owns it", "student health"),
        soft("review time", "seven business days|7 business days"),
        soft("registration gate or hold", "regist|hold"),
      ],
      forbidden: [...STANDARD, forbid("says it was accepted", "(?:has been|was|is) accepted")],
    }),
  one("kn-imm-002", "deadline_consequence", "student", "camila",
    "what if I never get my immunization record accepted?",
    "Spring admit: cannot register until accepted or exempt; health hold placed on 1 March 2027 for Spring entrants; exemptions exist (medical/religious).",
    {
      requiredTools: [KNOWLEDGE_STUDENT],
      facts: [
        f("registration gated", "regist"),
        soft("hold", "hold"),
        soft("exemption route", "exempt"),
      ],
      forbidden: [...STANDARD],
    }),
  one("kn-doc-001", "deadline_consequence", "student", "lucia",
    "my transcript is still under review — what happens if the official one isn't there by the add/drop deadline?",
    "Academic-records hold after 11 September 2026 blocking next-term registration and Aster transcripts; the uploaded copy is for review only; Registrar owns it.",
    {
      requiredTools: [KNOWLEDGE_STUDENT],
      facts: [
        f("hold consequence", "hold"),
        f("add/drop date", `{{date:${C}.calendar.fall2026-add-drop-deadline}}`),
        soft("registrar", "registrar"),
        soft("official copy from the school", "directly|official|sealed|issuing school"),
      ],
      forbidden: [...STANDARD, forbid("says the upload satisfies it", "upload(?:ed)? (?:copy |transcript )?(?:is|counts as|satisfies) (?:official|enough|sufficient)")],
    }),
  one("kn-doc-002", "policy", "student", "ada",
    "what file types can I upload and how long does review take?",
    "PDF, JPEG or PNG up to 10 MB; Registrar/Financial Aid 5 business days, Student Health 7, Enrollment Services 1.",
    {
      requiredTools: [KNOWLEDGE_STUDENT],
      facts: [
        f("formats", "PDF"),
        soft("size", "10 ?MB"),
        f("review days", "business days"),
      ],
      forbidden: [...STANDARD],
    }),

  // ── Financial aid ───────────────────────────────────────────────────────
  one("kn-aid-001", "applies_to_me", "student", "kwame",
    "I'm international - am I eligible for a Pell grant?",
    "No: F-1/J-1 students cannot file the FAFSA and are not eligible for Pell/SEOG/Direct Loans/Work-Study; names the Aster Global Scholarship or Merit as alternatives.",
    {
      requiredTools: [KNOWLEDGE_STUDENT],
      facts: [
        f("not eligible", "not eligible|aren'?t eligible|cannot|can'?t|ineligible|\\bno\\b"),
        f("alternative fund", "Global Scholarship|Merit|campus employment|International Student Loan"),
      ],
      forbidden: [...STANDARD, forbid("says he is eligible", "(?:you are|you'?re|yes,? you (?:are|may be)) eligible for (?:a |the )?Pell")],
    }),
  one("kn-aid-002", "record_plus_policy", "student", "lucia",
    "what scholarships do I have and are any of them federal?",
    "Lists her awards from the record (institutional/private only) and says none is federal — international students are not eligible for federal aid.",
    {
      anyOfTools: [["getFinancialAidSummary", "getFinancialAidStatus"]],
      facts: [
        f("names an award", `{{gt:${S("lucia")}.awardNames.0}}`),
        f("none federal", "none (?:of them |of these )?(?:is|are) federal|neither[^.]{0,40}federal|not federal|no federal|aren'?t federal|isn'?t federal"),
      ],
      forbidden: [...STANDARD, forbid("names a federal fund as hers", "you have (?:a |the )?(?:Federal )?Pell|Federal Direct|Federal Work-Study")],
    }),
  one("kn-aid-003", "deadline_consequence", "student", "ada",
    "will I lose my aid if my GPA drops below 2.0?",
    "SAP: 2.0 cumulative GPA, 67 % pace, 150 % timeframe; first term below is a warning (aid continues), second is not meeting (suspended) unless an appeal is approved.",
    {
      requiredTools: [KNOWLEDGE_STUDENT],
      facts: [
        f("warning first", "warning"),
        soft("appeal route", "appeal"),
        soft("2.0 line", "2\\.0"),
        soft("completion rate", "67"),
      ],
      forbidden: [...STANDARD, forbid("immediate loss", "(?:immediately|automatically|right away) lose (?:all )?(?:your )?aid")],
    }),
  one("kn-aid-004", "record_plus_policy", "student", "hana",
    "my SAP status says probation — what does that mean for my aid this term?",
    "Probation = an approved appeal: aid continues for the term under an academic plan whose conditions must be met.",
    {
      requiredTools: [KNOWLEDGE_STUDENT],
      anyOfTools: [["getAcademicStanding", "getFinancialAidStatus", "getFinancialAidSummary"]],
      facts: [
        f("aid continues", "continue|keeps? (?:your )?aid|remains? eligible|still (?:receive|get)"),
        f("academic plan", "academic plan|plan"),
      ],
      forbidden: [...STANDARD, forbid("says aid is suspended", "aid (?:is|has been|will be) suspended|lose your aid this term")],
    }),
  one("kn-aid-005", "calendar", "student", "petra",
    "when does financial aid actually pay out for fall?",
    "Fall aid disburses 4 September 2026 (after classes begin) once every condition clears; credit balances refunded within 14 days.",
    {
      requiredTools: [KNOWLEDGE_STUDENT],
      facts: [
        f("disbursement date", `{{date:${C}.calendar.fall2026-aid-disbursement}}`),
        soft("conditions", "condition|verification|accept"),
      ],
      forbidden: [...STANDARD],
    }),
  one("kn-aid-006", "deadline_consequence", "student", "petra",
    "if my verification isn't done by the tuition due date, do I get a hold?",
    "Tuition is due 28 August; verification incomplete does not block registration but no aid disburses; a deferment memo or the payment plan prevents the billing hold ($250 past due).",
    {
      requiredTools: [KNOWLEDGE_STUDENT],
      facts: [
        f("payment plan or deferment", "payment plan|deferment"),
        f("hold threshold or hold", "\\$250|billing hold|hold"),
        soft("tuition due", `{{date:${C}.calendar.fall2026-tuition-due}}`),
      ],
      forbidden: [...STANDARD],
    }),

  // ── Billing / tuition ───────────────────────────────────────────────────
  one("kn-bil-001", "applies_to_me", "student", "lucia",
    "how much is tuition for me?",
    "International: $31,200 a year (not the $47,400 cost of attendance) plus $1,450 fees.",
    {
      requiredTools: [KNOWLEDGE_STUDENT],
      facts: [f("international tuition", `{{gt:${C}.amounts.tuitionInternational}}`)],
      forbidden: [
        ...STANDARD,
        forbid("quotes in-state tuition as hers", `{{gt:${C}.amounts.tuitionInState}}`),
        forbid("quotes COA as tuition", `tuition[^.]{0,40}{{gt:${C}.amounts.coaInternational}}`),
      ],
    }),
  one("kn-bil-002", "policy", "student", "ada",
    "what's the late fee and when does a hold get put on my account?",
    "$75 late fee at 30 days past due; a past-due balance over $250 places a billing hold blocking registration and transcripts.",
    {
      requiredTools: [KNOWLEDGE_STUDENT],
      facts: [
        f("late fee", `{{gt:${C}.amounts.lateFee}}`),
        f("hold threshold", `{{gt:${C}.amounts.holdThreshold}}`),
        soft("30 days", "30 days"),
      ],
      forbidden: [...STANDARD],
    }),
  one("kn-bil-003", "policy", "student", "omar",
    "can I pay tuition in installments?",
    "Four-instalment plan, $45 fee, no interest, enrol by the due date (8 January 2027 for Spring).",
    {
      requiredTools: [KNOWLEDGE_STUDENT],
      facts: [
        f("four instalments", "four|4[- ]instal"),
        f("fee", `{{gt:${C}.amounts.paymentPlanFee}}`),
        soft("spring due date", `{{date:${C}.calendar.spring2027-tuition-due}}`),
      ],
      forbidden: [...STANDARD, forbid("charges interest", "\\d+ ?% interest|interest (?:is|will be) charged")],
    }),
  one("kn-bil-004", "applies_to_me", "student", "bruno",
    "can I waive the student health insurance?",
    "No: F-1/J-1 students cannot waive the $1,850 plan (only embassy-sponsored coverage confirmed by ISS).",
    {
      requiredTools: [KNOWLEDGE_STUDENT],
      facts: [
        f("cannot waive", "cannot|can'?t|not (?:able|eligible|permitted)|\\bno\\b"),
        soft("amount", `{{gt:${C}.amounts.insurance}}`),
      ],
      forbidden: [...STANDARD, forbid("gives him the domestic waiver", "(?:yes|you can) waive|submit (?:the|a) waiver")],
    }),

  // ── Registration / calendar ─────────────────────────────────────────────
  one("kn-reg-001", "calendar", "student", "petra",
    "when is the add/drop deadline",
    "11 September 2026; after it a drop is a withdrawal with a W.",
    {
      requiredTools: [KNOWLEDGE_STUDENT],
      facts: [f("date", `{{date:${C}.calendar.fall2026-add-drop-deadline}}`)],
      forbidden: [...STANDARD],
    }),
  one("kn-reg-002", "record_plus_policy", "student", "petra",
    "why can't I register yet and what are the rules for what blocks registration?",
    "Her record's gates (deposit unpaid, immunization/transcript outstanding, advising meeting) plus the policy's five gates; aid verification does not block registration.",
    {
      anyOfTools: [["getRegistrationStatus", "getEnrollmentHolds"]],
      facts: [
        f("deposit", "deposit"),
        soft("advising meeting", "advis"),
        soft("immunization", "immuni[sz]ation"),
      ],
      forbidden: [...STANDARD, forbid("nothing blocking", "nothing (?:is )?blocking|no (?:gates|blockers)\\b(?![^.]*but)")],
    }),
  one("kn-reg-003", "applies_to_me", "student", "lucia",
    "can I drop down to 9 credits this fall?",
    "F-1 students must stay at 12 credits; below that needs a DSO-authorised reduced course load in advance; names ISS/DSO.",
    {
      requiredTools: [KNOWLEDGE_STUDENT],
      facts: [
        f("12-credit rule", "12 credits|twelve credits|full[- ]time"),
        f("DSO / ISS", "DSO|International Student Services|international adviser"),
        soft("reduced course load", "reduced course load|RCL|authoris|authoriz"),
      ],
      forbidden: [...STANDARD, forbid("says yes freely", "^(?:yes|sure)\\b(?![^.]*(?:but|only|if|unless))")],
    }),
  one("kn-reg-004", "calendar", "student", "omar",
    "when does registration open for me and when do classes start?",
    "Spring 2027 admits register from 4 January 2027; classes begin 19 January 2027.",
    {
      requiredTools: [KNOWLEDGE_STUDENT],
      facts: [
        f("registration opens", `{{date:${C}.calendar.spring2027-registration-opens-new}}`),
        f("classes begin", `{{date:${C}.calendar.spring2027-classes-begin}}`),
      ],
      forbidden: [...STANDARD, forbid("gives the fall dates as his", "17 August|August 17|31 August|August 31")],
    }),
  one("kn-reg-005", "policy", "student", "ada",
    "what's the refund if I withdraw from the university in the third week of classes?",
    "Refund schedule: 100 % through 11 Sep, 75 % through 18 Sep, 50 % through 25 Sep, 25 % through 2 Oct, none after.",
    {
      requiredTools: [KNOWLEDGE_STUDENT],
      facts: [
        f("percentage schedule", "50 ?%|75 ?%|25 ?%"),
        soft("withdrawal form to registrar", "registrar"),
      ],
      forbidden: [...STANDARD],
    }),

  // ── Advising / appointments ─────────────────────────────────────────────
  one("kn-adv-001", "record_plus_policy", "student", "noor",
    "my adviser is on leave — who covers for her and how do I book?",
    "Junia Pemberwell is on leave until 21 Sep 2026; policy: the Assistant Director, First-Year Advising covers first-year students; book the covering adviser on the Appointments page.",
    {
      requiredTools: [KNOWLEDGE_STUDENT],
      anyOfTools: [["getStudentAdvising", "getStudentAppointments"]],
      facts: [
        f("names the adviser", `{{gt:${S("noor")}.adviser.name}}`),
        f("covering role", "Assistant Director|covering adviser|covers"),
      ],
      forbidden: [...STANDARD, forbid("invents a colleague's phone", "\\d{3}-\\d{4}")],
    }),
  one("kn-adv-002", "record_plus_policy", "student", "adria",
    "my adviser left the university. what happens to me now?",
    "Departed adviser: the Advising Center reassigns within ten business days; meanwhile the assistant directors cover and she can book the covering adviser.",
    {
      requiredTools: [KNOWLEDGE_STUDENT],
      anyOfTools: [["getStudentAdvising", "getStudentAppointments"]],
      facts: [
        f("reassignment", "reassign"),
        soft("ten business days", "ten business days|10 business days"),
      ],
      forbidden: [...STANDARD],
    }),
  one("kn-adv-003", "record_plus_policy", "student", "gustav",
    "I don't have an adviser. is that normal and who do I ask?",
    "Advisers are assigned within five business days of the deposit posting; his deposit is unpaid/overdue so none is assigned; ask Enrollment Services once the deposit posts. (The write plane may answer this as a support-request offer; the trace then records no reads.)",
    {
      facts: [
        soft("deposit link", "deposit"),
        f("names an office to ask", "enrollment services|advising|adviser"),
        soft("five business days", "five business days|5 business days"),
        soft("enrollment services", "enrollment services|advising center"),
      ],
      forbidden: [...STANDARD, forbid("invents an adviser", "your adviser(?:,| is) [A-Z][a-z]+ [A-Z]")],
    }),
  one("kn-adv-004", "policy", "student", "milo",
    "what happens if I no-show my advising appointment twice?",
    "After two no-shows or late cancellations in a term, booking is only through the office's front desk; no fee; the first-meeting gate stays open.",
    {
      requiredTools: [KNOWLEDGE_STUDENT],
      facts: [
        f("front desk consequence", "front desk|through the office|contact(?:ing)? (?:the )?(?:advising|office)"),
        soft("no fee", "no fee|never carry a fee|not charged"),
      ],
      forbidden: [...STANDARD, forbid("invents a fee", "\\$\\d+ (?:fee|charge)")],
    }),

  // ── Programs / academics ────────────────────────────────────────────────
  one("kn-prg-001", "record_plus_policy", "student", "ada",
    "what should I take my first semester and how many credits is my degree?",
    "BS-DS first-year plan (DATA 110, CS 101, MATH 151, WRIT 101, AST 100); 120 credits.",
    {
      anyOfTools: [[KNOWLEDGE_STUDENT, "getAcademicPlan"]],
      facts: [
        f("a first-term course", "DATA 110|CS 101|MATH 151|WRIT 101"),
        f("credit total", "120"),
      ],
      forbidden: [...STANDARD, forbid("gives the CS degree instead", "Computer Science degree|BS-CS")],
    }),
  one("kn-prg-002", "applies_to_me", "student", "milo",
    "are there extra requirements for nursing students I should know about?",
    "BSN: 3.0 GPA and B- in NURS/BIO/CHEM, hepatitis B required, annual TB screening, CPR, background check before the first clinical placement.",
    {
      requiredTools: [KNOWLEDGE_STUDENT],
      facts: [
        f("hepatitis B or background check", "hepatitis B|background check|CPR|TB screening|tuberculosis"),
        soft("3.0 GPA", "3\\.0"),
      ],
      forbidden: [...STANDARD],
    }),
  one("kn-prg-003", "policy", "student", "ivo",
    "how many of my credits can transfer and what's the minimum grade?",
    "C or better; up to 60 from two-year colleges, 90 total; last 30 credits at Aster.",
    {
      requiredTools: [KNOWLEDGE_STUDENT],
      facts: [
        f("minimum grade", "\\bC\\b"),
        f("limit", "90|60"),
      ],
      forbidden: [...STANDARD],
    }),
  one("kn-prg-004", "policy", "student", "petra",
    "can I switch from electrical engineering to computer science and when would it take effect?",
    "Change-of-major form via the adviser; CS has no entry requirement; takes effect the next term; adviser reassigned if the department changes.",
    {
      requiredTools: [KNOWLEDGE_STUDENT],
      facts: [
        f("next term", "next term|following term|start of the next"),
        soft("adviser", "advis"),
      ],
      forbidden: [...STANDARD],
    }),

  // ── Offices / who handles ───────────────────────────────────────────────
  one("kn-off-001", "office_routing", "student", "ada",
    "which office do I contact about a hold on my account, and where is it?",
    "Billing hold → Student Accounts, Larkin Hall 120, studentaccounts@synthetic.aster.example; other holds have their own offices.",
    {
      requiredTools: [KNOWLEDGE_STUDENT],
      facts: [
        f("student accounts", "student accounts"),
        soft("location", "Larkin Hall 120"),
      ],
      forbidden: [...STANDARD],
    }),
  one("kn-off-002", "office_routing", "student", "lucia",
    "who do I talk to about my I-20 and where are they?",
    "International Student Services, Global Center 2nd floor, iss@synthetic.aster.example. (May be answered by the write plane's support-request offer; the trace then records no reads.)",
    {
      facts: [
        f("ISS", "International Student Services"),
        soft("location", "Global Center"),
      ],
      forbidden: [...STANDARD],
    }),
  one("kn-off-003", "office_routing", "student", "kwame",
    "I need accommodations for ADHD — who do I go to and is there a deadline?",
    "Accessibility Services, Wellness Center suite 110; documentation within three years; housing accommodations by 22 July; no staff records in the platform for that office.",
    {
      requiredTools: [KNOWLEDGE_STUDENT],
      facts: [
        f("accessibility services", "Accessibility Services"),
        soft("wellness center", "Wellness Center"),
      ],
      forbidden: [...STANDARD, forbid("invents a named contact person", "contact [A-Z][a-z]+ (?!Services|Office|Center|Centre|Hall|Union)[A-Z][a-z]+,? (?:the|who is|at)")],
    }),
  one("kn-off-004", "office_routing", "student", "ada",
    "how do I give my mom access to see my bill?",
    "FERPA release on the Enrollment → FERPA page, page by page; a parent has no automatic access; Registrar owns FERPA.",
    {
      requiredTools: [KNOWLEDGE_STUDENT],
      facts: [
        f("FERPA release", "FERPA|release"),
        soft("choose pages", "page|financials"),
      ],
      forbidden: [...STANDARD, forbid("says parents get access automatically", "automatic(?:ally)? (?:has|have|get)")],
    }),

  // ── Honesty: not in corpus ──────────────────────────────────────────────
  one("kn-hon-001", "honesty", "student", "ada",
    "what's the parking permit price for graduate students?",
    "Corpus has undergraduate permit price ($180) only; must not invent a graduate rate.",
    {
      facts: [f("either the undergrad figure or an honest limit", "\\$180|not (?:cover|list|have|state)|no (?:information|graduate)|only|can'?t (?:find|confirm)|isn'?t (?:covered|listed)")],
      forbidden: [...STANDARD, forbid("invents a graduate price", "graduate[^.]{0,40}\\$(?!180)\\d")],
    }),
  one("kn-hon-002", "honesty", "student", "petra",
    "what's the phone number for the registrar?",
    "No telephone numbers are on record; gives email/location instead.",
    {
      facts: [f("no phone / gives email", "no (?:phone|telephone)|not (?:published|listed|on record|available)|(?:could ?n[o']t|can'?t|cannot|unable to) (?:verify|find|confirm|provide)|registrar@synthetic\\.aster\\.example|Larkin Hall 150")],
      forbidden: [...STANDARD],
    }),

  // ── Staff: procedures, SLAs, applies-to-student ─────────────────────────
  one("kn-stf-001", "staff_procedure", "staff", "registrar",
    "what is the procedure when a transcript is rejected twice?",
    "Document review procedure: second rejection escalates to the office director, who schedules a call/virtual meeting within three business days.",
    {
      requiredTools: [KNOWLEDGE_STAFF],
      facts: [
        f("escalate to director", "director"),
        soft("three business days", "three business days|3 business days"),
      ],
      forbidden: [...STANDARD],
    }),
  one("kn-stf-002", "staff_procedure", "staff", "greta",
    "what's our service level for verification document review, and for the full review once the file is complete?",
    "Financial Aid: 5 business days per document; 10 business days for the full review after the file is complete.",
    {
      requiredTools: [KNOWLEDGE_STAFF],
      facts: [
        f("5 business days", "5 business days|five business days"),
        f("10 business days", "10 business days|ten business days"),
      ],
      forbidden: [...STANDARD, forbid("refuses as unsupported metric", "No SLA is defined")],
    }),
  one("kn-stf-003", "staff_applies_to_student", "staff", "priya",
    "can we extend Petra Oakenshaw's deposit deadline?",
    "Yes: a counselor may grant one extension of up to 30 days for hardship or a pending external decision; recorded on the requirement; the refund deadline never moves.",
    {
      requiredTools: [KNOWLEDGE_STAFF],
      resolvedStudentId: `gt:${S("petra")}.id`,
      facts: [
        f("yes with conditions", "\\byes\\b|can be extended|may (?:be )?(?:grant|extend)|one extension|up to 30 days"),
        soft("30 days", "30 days"),
      ],
      forbidden: [...STANDARD, forbid("says it cannot", "cannot be extended|can'?t be extended|not (?:possible|allowed) to extend")],
    }),
  one("kn-stf-004", "staff_applies_to_student", "staff", "hana_d",
    "does the first-year residency requirement apply to Ivo Ravensworth?",
    "Two students share the name — must ask which one rather than guess (name landmine).",
    {
      facts: [f("asks which", "which (?:one|student|Ivo)|more than one|two students|2 students|do you mean|\\?")],
      forbidden: [...STANDARD],
    }),
  one("kn-stf-005", "staff_applies_to_student", "staff", "hana_d",
    "does the first-year residency requirement apply to Petra Oakenshaw?",
    "Petra is a first-year Fall 2026 admit: it applies; she has not yet confirmed housing (deposit unpaid).",
    {
      requiredTools: [KNOWLEDGE_STAFF],
      resolvedStudentId: `gt:${S("petra")}.id`,
      facts: [f("applies", "\\byes\\b|appl(?:y|ies) to (?:her|Petra)|she is (?:a )?first[- ]year|required")],
      forbidden: [...STANDARD, forbid("says no", "does ?n[o']t apply to (?:her|Petra)|not (?:apply|applicable)|\\bno\\b,? (?:it|the requirement) does")],
    }),
  one("kn-stf-006", "staff_applies_to_student", "staff", "leandro",
    "Ivo Ravensworth SYN-001478 has no adviser — what does our policy say should have happened and who should handle it?",
    "Policy: assigned within five business days of deposit posting (his deposit is paid); Advising Center / the Director assigns from the Morning Brew list; Enrollment Services raises it.",
    {
      requiredTools: [KNOWLEDGE_STAFF],
      resolvedStudentId: `gt:${S("ivo")}.id`,
      facts: [
        f("five business days", "five business days|5 business days"),
        f("advising owns it", "Advising|Director of Academic Advising|Enrollment Services"),
      ],
      forbidden: [...STANDARD],
    }),
  one("kn-stf-007", "staff_procedure", "staff", "leandro",
    "who covers advisees while an adviser is on leave, and how fast do we reassign after someone leaves?",
    "Assistant Director, First-Year Advising (first-years) / Transfer & Spring Admits (transfers) cover; reassignment within ten business days of departure.",
    {
      requiredTools: [KNOWLEDGE_STAFF],
      facts: [
        f("assistant director", "Assistant Director"),
        f("ten business days", "ten business days|10 business days"),
      ],
      forbidden: [...STANDARD],
    }),
  one("kn-stf-008", "staff_procedure", "staff", "zubin",
    "what rejection code do we use for a record with only one MMR dose, and what do we tell the student?",
    "`incomplete`, with a note naming the missing dose; never `wrong_document`.",
    {
      requiredTools: [KNOWLEDGE_STAFF],
      facts: [
        f("incomplete", "incomplete"),
        soft("names the missing dose", "missing dose|name(?:s|ing)? (?:the|what is) missing|second dose"),
      ],
      forbidden: [...STANDARD, forbid("recommends wrong_document", "use (?:the )?['`\"]?wrong[_ ]document")],
    }),
  one("kn-stf-009", "staff_procedure", "staff", "housing",
    "what are the grounds for a residency exemption, and is financial hardship one of them?",
    "Family within 30 miles, 21+, Accessibility accommodation, married/dependent child/veteran; financial hardship is NOT a ground (refer to Financial Aid).",
    {
      requiredTools: [KNOWLEDGE_STAFF],
      facts: [
        f("30 miles", "30 miles"),
        f("hardship is not a ground", "not (?:a |an )?(?:ground|reason|exemption|itself)|hardship is not|does ?n[o']t (?:qualify|count)"),
      ],
      forbidden: [...STANDARD, forbid("accepts hardship", "hardship (?:is|qualifies as|counts as) (?:a |one )?(?:valid |accepted )?(?:ground|reason|exemption)")],
    }),
  one("kn-stf-010", "staff_procedure", "staff", "matthias",
    "a student can't arrive by August 31 — how long can we defer the I-20 and what do they need to do?",
    "A DSO can defer once by up to 30 days (to 30 September 2026) if coursework can still be completed; the student must write before the start date; otherwise reissue for Spring.",
    {
      requiredTools: [KNOWLEDGE_STAFF],
      facts: [
        f("30 days", "30 days|30 September|September 30"),
        soft("write before start date", "before|in writing|write"),
      ],
      forbidden: [...STANDARD],
    }),
  one("kn-stf-011", "staff_procedure", "staff", "uma",
    "a parent is on the phone asking about their student's bill — what am I allowed to say?",
    "Verify a current FERPA release naming the person and covering financials; otherwise directory information only and invite the student to add them.",
    {
      requiredTools: [KNOWLEDGE_STAFF],
      facts: [
        f("FERPA release check", "FERPA|release"),
        f("directory info only otherwise", "directory information|only (?:directory|name)"),
      ],
      forbidden: [...STANDARD],
    }),
  one("kn-stf-012", "staff_applies_to_student", "staff", "greta",
    "Kwame Oakenshaw asked about a Pell grant — is he eligible, and what can we offer instead?",
    "International: not eligible for federal aid; Aster Global Scholarship, Merit, International Campus Employment, partner loan.",
    {
      requiredTools: [KNOWLEDGE_STAFF],
      resolvedStudentId: `gt:${S("kwame")}.id`,
      facts: [
        f("not eligible", "not eligible|ineligible|cannot|can'?t|\\bno\\b"),
        f("alternative", "Global Scholarship|Merit|Campus Employment|International Student Loan"),
      ],
      forbidden: [...STANDARD, forbid("eligible", "(?:he is|he'?s|yes,? he is) eligible for (?:a |the )?Pell")],
    }),
  one("kn-stf-013", "calendar", "staff", "priya",
    "when do health holds get placed this fall and who runs the sweep?",
    "1 October 2026; Student Health runs it; blocks Spring registration.",
    {
      requiredTools: [KNOWLEDGE_STAFF],
      facts: [
        f("date", `{{date:${C}.calendar.fall2026-health-hold-date}}`),
        f("student health", "Student Health"),
      ],
      forbidden: [...STANDARD],
    }),
  one("kn-stf-014", "staff_procedure", "staff", "registrar",
    "what's the escalation path for a work item that's been in progress for two weeks with no update?",
    "Stale at 10 days → the owning office's director, who reassigns or closes; log the escalation on the item.",
    {
      requiredTools: [KNOWLEDGE_STAFF],
      facts: [
        f("director", "director"),
        soft("10 days", "10 days|ten days"),
      ],
      forbidden: [...STANDARD],
    }),
  one("kn-stf-015", "honesty", "staff", "hana_d",
    "are we meeting our SLA on transcript reviews this week?",
    "SLA compliance is not measured by the platform: honest refusal (and optionally the published service level).",
    {
      facts: [f("not measured", "no SLA|not (?:measured|tracked|defined|something)|doesn'?t (?:measure|track)|can'?t (?:measure|tell)|no service-level configuration")],
      forbidden: [...STANDARD, forbid("invents a compliance figure", "\\d{1,3} ?% (?:of|within|on time|compliance)")],
    }),

  // ── Multi-turn ──────────────────────────────────────────────────────────
  conversation("kn-mt-001", "multi_turn", "student", "petra",
    "First the record, then the rule about it, then the office — each turn builds on the last.",
    [
      {
        question: "is my deposit overdue?",
        expect: {
          anyOfTools: [["getStudentDeadlines", "getOnboardingChecklist", "getEnrollmentHolds", "getStudentAccountSummary"]],
          facts: [f("overdue", "overdue|past due|already passed|was due")],
          forbidden: [...STANDARD, forbid("says paid", "deposit (?:is|has been|was) (?:paid|posted|received)")],
        },
      },
      {
        question: "what happens now that I missed it?",
        expect: {
          requiredTools: [KNOWLEDGE_STUDENT],
          facts: [f("keeps offer / consequences", "keep|housing|adviser|registration|extension")],
          forbidden: [...STANDARD, forbid("offer cancelled", "offer (?:is|has been|will be) (?:cancelled|canceled|rescinded|withdrawn)")],
        },
      },
      {
        question: "who do I ask for an extension?",
        expect: {
          facts: [f("admissions", "admissions")],
          forbidden: [...STANDARD],
        },
      },
    ]),
  conversation("kn-mt-002", "multi_turn", "staff", "priya",
    "A staff member moves from a student's state to the policy on it.",
    [
      {
        question: "what's blocking Petra Oakenshaw?",
        expect: {
          resolvedStudentId: `gt:${S("petra")}.id`,
          facts: [f("deposit", "deposit")],
          forbidden: [...STANDARD],
        },
      },
      {
        question: "given his deposit is overdue, what does the policy let us do for him?",
        expect: {
          requiredTools: [KNOWLEDGE_STAFF],
          facts: [f("extension or waiver", "extension|extend|waiv")],
          forbidden: [...STANDARD],
        },
      },
    ]),
];
