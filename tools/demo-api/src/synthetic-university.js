/**
 * SYNTHETIC DEMO UNIVERSITY DATA.
 *
 * Every record in this file is invented for the Aster demo tenant. None of it
 * describes a real institution, and no rule here should be treated as real
 * policy. It exists so Edward can answer realistic questions about registration,
 * billing, housing eligibility, term dates, appointments, and institutional
 * rules without those facts being buried in a prompt.
 *
 * A real deployment replaces this module with reads against the institution's
 * own systems: the shapes returned here are the contract
 * (`@vv/student-assistant-core`), not the values.
 *
 * Internal consistency rules maintained by hand in this file:
 *   - The enrollment deposit gates both housing application and registration.
 *   - Registration additionally requires immunisation clearance and advising.
 *   - Term dates order as: registration opens < orientation < move-in < classes.
 *   - Any amount quoted in a policy also appears in the charge list.
 *   - Aid disbursement follows the first day of classes, never precedes it.
 */

export const SYNTHETIC_TERM = Object.freeze({
  code: "2026FA",
  name: "Fall 2026",
});

/** Cost figures reused by both the account summary and the policy text. */
export const SYNTHETIC_AMOUNTS = Object.freeze({
  enrollmentDepositUsd: 500,
  tuitionUsd: 18400,
  housingUsd: 7250,
  mealPlanUsd: 2900,
  orientationFeeUsd: 175,
  healthInsuranceUsd: 1850,
});

export const SYNTHETIC_CALENDAR = Object.freeze([
  {
    code: "registration_opens_new_students",
    label: "Course registration opens for new students",
    startsAt: "2026-08-17T13:00:00.000Z",
    endsAt: null,
    category: "registration",
    audience: "new_students",
    description:
      "New students register after their advising meeting, in assigned time slots.",
  },
  {
    code: "registration_closes",
    label: "Add/drop deadline",
    startsAt: "2026-09-11T23:59:00.000Z",
    endsAt: null,
    category: "registration",
    audience: "all",
    description:
      "Last day to add or drop a course without a transcript notation or charge.",
  },
  {
    code: "orientation",
    label: "New student orientation",
    startsAt: "2026-08-24T12:00:00.000Z",
    endsAt: "2026-08-26T21:00:00.000Z",
    category: "orientation",
    audience: "new_students",
    description:
      "Three days of required sessions for all incoming students, on the main campus.",
  },
  {
    code: "international_check_in",
    label: "International student check-in",
    startsAt: "2026-08-21T13:00:00.000Z",
    endsAt: "2026-08-22T21:00:00.000Z",
    category: "orientation",
    audience: "international",
    description:
      "Immigration document check-in, required before international students may register.",
  },
  {
    code: "housing_move_in",
    label: "Residence hall move-in",
    startsAt: "2026-08-22T14:00:00.000Z",
    endsAt: "2026-08-23T22:00:00.000Z",
    category: "housing",
    audience: "residential",
    description:
      "Move-in runs in assigned arrival windows and opens before orientation begins.",
  },
  {
    code: "housing_application_opens",
    label: "Housing application opens",
    startsAt: "2026-07-15T13:00:00.000Z",
    endsAt: null,
    category: "housing",
    audience: "new_students",
    description:
      "Housing applications open to deposited students only.",
  },
  {
    code: "housing_application_closes",
    label: "Housing application deadline",
    startsAt: "2026-08-22T23:59:00.000Z",
    endsAt: null,
    category: "housing",
    audience: "new_students",
    description:
      "Applications after this date are placed on the waitlist rather than assigned.",
  },
  {
    code: "classes_begin",
    label: "First day of classes",
    startsAt: "2026-08-31T12:00:00.000Z",
    endsAt: null,
    category: "term",
    audience: "all",
    description: "Fall 2026 instruction begins.",
  },
  {
    code: "tuition_due",
    label: "Tuition payment deadline",
    startsAt: "2026-08-28T23:59:00.000Z",
    endsAt: null,
    category: "billing",
    audience: "all",
    description:
      "Full payment or an active payment plan is required by this date to avoid a billing hold.",
  },
  {
    code: "aid_disbursement_fall",
    label: "Fall financial aid disbursement",
    startsAt: "2026-09-04T12:00:00.000Z",
    endsAt: null,
    category: "billing",
    audience: "all",
    description:
      "Accepted aid is credited to student accounts on this date, once enrollment is confirmed and no aid requirement is outstanding. Aid never disburses before classes begin.",
  },
  {
    code: "aid_disbursement_spring",
    label: "Spring financial aid disbursement",
    startsAt: "2027-01-22T12:00:00.000Z",
    endsAt: null,
    category: "billing",
    audience: "all",
    description:
      "The second half of an annual award is credited on this date.",
  },
  {
    code: "advising_window",
    label: "New student advising window",
    startsAt: "2026-08-10T13:00:00.000Z",
    endsAt: "2026-08-21T21:00:00.000Z",
    category: "advising",
    audience: "new_students",
    description:
      "Every new student meets an academic adviser once before registering.",
  },
]);

/**
 * Approved institutional policy, in the shape a policy service would return.
 * `keywords` exist only so the demo search can match a student's own words.
 */
export const SYNTHETIC_POLICIES = Object.freeze([
  {
    policyId: "pol-housing-first-year",
    code: "housing_first_year_residency",
    topic: "housing",
    title: "First-year residency requirement",
    appliesTo: "first-year undergraduate students",
    sourceOwner: "Housing & Residence Life",
    version: "2026.1",
    effectiveFrom: "2026-05-01",
    effectiveUntil: null,
    studentVisibleText:
      "First-year undergraduate students live in university housing for both semesters of their first year. Exemptions are granted only for students living with an immediate family member within 30 miles of campus, students aged 21 or older at the start of term, and students with an approved accommodation. Exemption requests go to Housing & Residence Life and must be filed before the housing application deadline.",
    keywords: [
      "off campus",
      "off-campus",
      "live off",
      "first year",
      "first-year",
      "residency",
      "exemption",
      "commute",
      "live at home",
    ],
  },
  {
    policyId: "pol-housing-eligibility",
    code: "housing_application_eligibility",
    topic: "housing",
    title: "Housing application eligibility",
    appliesTo: "admitted students who have accepted an offer",
    sourceOwner: "Housing & Residence Life",
    version: "2026.1",
    effectiveFrom: "2026-05-01",
    effectiveUntil: null,
    studentVisibleText:
      "The housing application opens to a student once their enrollment deposit has posted to the student account. A pending or in-transit payment does not open the application; the deposit must have cleared. Applications submitted after the housing application deadline are waitlisted rather than assigned.",
    keywords: [
      "housing application",
      "apply for housing",
      "housing eligibility",
      "housing open",
      "deposit housing",
    ],
  },
  {
    policyId: "pol-deposit-refund",
    code: "enrollment_deposit_refund",
    topic: "deposits",
    title: "Enrollment deposit refund and forfeiture",
    appliesTo: "admitted undergraduate students",
    sourceOwner: "Office of Admissions",
    version: "2026.2",
    effectiveFrom: "2026-04-01",
    effectiveUntil: null,
    studentVisibleText:
      "The $500 enrollment deposit is refundable if a written withdrawal request reaches the Office of Admissions on or before 2026-06-01. After that date the deposit is non-refundable and is forfeited if the student does not enrol. The deposit is credited against first-semester tuition for students who do enrol.",
    keywords: [
      "deposit refund",
      "lose my deposit",
      "lose the deposit",
      "forfeit",
      "get my deposit back",
      "withdraw",
      "non-refundable",
    ],
  },
  {
    policyId: "pol-registration-holds",
    code: "registration_hold_policy",
    topic: "registration",
    title: "Holds that prevent course registration",
    appliesTo: "all enrolled and incoming students",
    sourceOwner: "Office of the Registrar",
    version: "2026.1",
    effectiveFrom: "2026-05-01",
    effectiveUntil: null,
    studentVisibleText:
      "A student may not register while any of the following is open: an unpaid balance over $250, a missing immunisation record, an incomplete advising requirement, or an outstanding official transcript. Financial-aid verification being incomplete does not by itself prevent registration, though it may delay disbursement.",
    keywords: [
      "cannot register",
      "can't register",
      "registration hold",
      "blocked from registering",
      "why can't i register",
      "register hold",
    ],
  },
  {
    policyId: "pol-immunisation",
    code: "immunization_requirement",
    topic: "health",
    title: "Immunisation requirement",
    appliesTo: "all incoming students",
    sourceOwner: "Student Health Services",
    version: "2026.1",
    effectiveFrom: "2026-05-01",
    effectiveUntil: null,
    studentVisibleText:
      "Incoming students submit proof of measles, mumps, rubella, and meningococcal immunisation before registering for classes. Student Health Services reviews submissions within three business days. A submitted but unreviewed record does not clear the registration gate.",
    keywords: [
      "immunisation",
      "immunization",
      "vaccine",
      "vaccination",
      "meningitis",
      "mmr",
      "health form",
      "shots",
    ],
  },
  {
    policyId: "pol-aid-verification",
    code: "financial_aid_verification_policy",
    topic: "financial_aid",
    title: "Financial-aid verification",
    appliesTo: "students selected for federal verification",
    sourceOwner: "Office of Financial Aid",
    version: "2026.1",
    effectiveFrom: "2026-05-01",
    effectiveUntil: null,
    studentVisibleText:
      "Students selected for verification submit a verification worksheet and any requested supporting documents. Aid is not disbursed until verification completes, but a selected student may still register for classes and may still enrol in a payment plan. Verification review takes up to ten business days after all documents arrive.",
    keywords: [
      "verification",
      "fafsa",
      "aid incomplete",
      "financial aid incomplete",
      "why is my aid",
      "disbursement",
      "worksheet",
    ],
  },
  {
    policyId: "pol-payment-plan",
    code: "payment_plan_policy",
    topic: "billing",
    title: "Payment plans and billing holds",
    appliesTo: "all students with a balance",
    sourceOwner: "Office of Student Accounts",
    version: "2026.1",
    effectiveFrom: "2026-05-01",
    effectiveUntil: null,
    studentVisibleText:
      "A student account balance over $250 that is past due places a billing hold on the account, which prevents course registration. Enrolling in the four-instalment payment plan before the tuition deadline prevents the hold from being placed. The payment plan carries a $45 enrolment fee and no interest.",
    keywords: [
      "payment plan",
      "billing hold",
      "balance",
      "instalment",
      "installment",
      "pay tuition",
      "owe",
    ],
  },
  {
    policyId: "pol-orientation",
    code: "orientation_requirement",
    topic: "orientation",
    title: "Orientation attendance",
    appliesTo: "all new undergraduate students",
    sourceOwner: "Office of New Student Programs",
    version: "2026.1",
    effectiveFrom: "2026-05-01",
    effectiveUntil: null,
    studentVisibleText:
      "Orientation attendance is required for all new undergraduate students. Students who live on campus may move into their residence hall during the published move-in window, which opens before orientation begins; students do not need to wait until orientation to move in. Orientation registration closes one week before the first session.",
    keywords: [
      "orientation",
      "move in",
      "move-in",
      "arrive early",
      "when can i move",
      "new student orientation",
    ],
  },
  {
    policyId: "pol-international",
    code: "international_student_requirements",
    topic: "international",
    title: "International student check-in",
    appliesTo: "students on an F-1 or J-1 visa",
    sourceOwner: "Office of International Student Services",
    version: "2026.1",
    effectiveFrom: "2026-05-01",
    effectiveUntil: null,
    studentVisibleText:
      "Students on an F-1 or J-1 visa complete immigration check-in with the Office of International Student Services before registering for classes, presenting a passport, visa, I-20 or DS-2019, and I-94. Check-in is held during the published international check-in window. SEVIS records are activated after check-in completes.",
    keywords: [
      "international",
      "visa",
      "i-20",
      "i20",
      "sevis",
      "f-1",
      "f1",
      "j-1",
      "passport",
      "immigration",
    ],
  },
  {
    policyId: "pol-transcript",
    code: "final_transcript_policy",
    topic: "admissions",
    title: "Final official transcript",
    appliesTo: "admitted students",
    sourceOwner: "Office of Admissions",
    version: "2026.1",
    effectiveFrom: "2026-05-01",
    effectiveUntil: null,
    studentVisibleText:
      "Admission is conditional until the Office of Admissions receives a final official transcript sent directly by the issuing school. A transcript uploaded by the student is accepted for review but does not satisfy the requirement on its own. An outstanding final transcript places a registration hold after the add/drop deadline.",
    keywords: [
      "transcript",
      "final transcript",
      "official transcript",
      "sent my transcript",
      "high school records",
    ],
  },
]);

/**
 * Match approved policy by the student's own words. Deliberately simple keyword
 * scoring: a real deployment would use the institution's policy search.
 */
export function searchSyntheticPolicies(topic, limit = 4) {
  const text = String(topic ?? "").toLowerCase();
  if (!text.trim()) return [];
  const scored = SYNTHETIC_POLICIES.map((policy) => {
    let score = 0;
    for (const keyword of policy.keywords) {
      if (text.includes(keyword)) score += keyword.split(" ").length + 1;
    }
    if (text.includes(policy.topic)) score += 1;
    return { policy, score };
  })
    .filter((entry) => entry.score > 0)
    .sort((left, right) => right.score - left.score)
    .slice(0, limit);
  return scored.map(({ policy }) => ({
    policyId: policy.policyId,
    code: policy.code,
    topic: policy.topic,
    title: policy.title,
    studentVisibleText: policy.studentVisibleText,
    appliesTo: policy.appliesTo,
    sourceOwner: policy.sourceOwner,
    version: policy.version,
    effectiveFrom: policy.effectiveFrom,
    effectiveUntil: policy.effectiveUntil,
    synthetic: true,
  }));
}

export function syntheticCalendar() {
  return {
    termCode: SYNTHETIC_TERM.code,
    termName: SYNTHETIC_TERM.name,
    events: SYNTHETIC_CALENDAR.map((event) => ({ ...event })),
    synthetic: true,
  };
}
