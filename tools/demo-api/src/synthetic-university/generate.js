/**
 * SYNTHETIC UNIVERSITY GENERATOR.
 *
 * Builds a whole fictional institution — students, applications, documents,
 * holds, financial aid, ledgers, housing, orientation, registration — from a
 * single integer seed. Nothing here describes a real institution or a real
 * person, and no rule expressed in this data should be read as real policy.
 *
 * Three properties matter more than realism:
 *
 *   1. Determinism. The same seed rebuilds byte-identical JSON, so an eval
 *      transcript recorded last week still refers to the same student today.
 *      That rules out `Math.random` and `Date.now`; "now" is a parameter.
 *   2. Internal consistency. The relationships that make questions answerable
 *      — a disbursement implies an accepted award, a room implies a posted
 *      deposit — are established here at construction time and re-checked by
 *      `invariants.js`. A generator that produces impossible states teaches an
 *      assistant to answer impossible questions.
 *   3. Guaranteed coverage. `state-matrix.js` forces every interesting state
 *      into the population rather than leaving it to the dice.
 *
 * Policy lives in this data (deadlines, gates, thresholds, which hold blocks
 * what), deliberately, so that changing a rule is a data change rather than a
 * prompt edit.
 */

import { SYNTHETIC_PERSONAS } from "./personas.js";
import { STATE_MATRIX, applyStateKey } from "./state-matrix.js";
import {
  addDays,
  addHours,
  chance,
  createRng,
  floatBetween,
  intBetween,
  pick,
  sample,
  shuffle,
  uuidFrom,
  uuidFromString,
  weighted,
} from "./random.js";

export const DEFAULT_SEED = 20260810;
export const DEFAULT_STUDENT_COUNT = 3000;
/** Fixed clock: the generator never reads the wall clock. */
export const DEFAULT_NOW = "2026-08-05T12:00:00.000Z";
export const MIN_STUDENT_COUNT = 2000;
export const MAX_STUDENT_COUNT = 4000;
export const AID_YEAR = "2026-2027";
export const ENROLLMENT_DEPOSIT_USD = 500;
/** A balance above this places a financial hold; quoted by registration gates. */
export const BALANCE_HOLD_THRESHOLD_USD = 250;
export const UNIVERSE_VERSION = "synthetic-university-v1";

/** How many students each matrix state gets before randomisation takes over. */
const MATRIX_REPEATS = 4;

const APPLICATION_WINDOW_OPENS = "2025-10-01T15:00:00.000Z";

/* ------------------------------------------------------------------ */
/* Catalogues                                                          */
/* ------------------------------------------------------------------ */

const TERM_SEEDS = [
  {
    code: "2026FA",
    name: "Fall 2026",
    startsAt: "2026-08-31T12:00:00.000Z",
    endsAt: "2026-12-18T22:00:00.000Z",
    registrationOpensAt: "2026-08-17T13:00:00.000Z",
    registrationClosesAt: "2026-09-11T23:59:00.000Z",
  },
  {
    code: "2027SP",
    name: "Spring 2027",
    startsAt: "2027-01-19T12:00:00.000Z",
    endsAt: "2027-05-07T22:00:00.000Z",
    registrationOpensAt: "2026-11-02T13:00:00.000Z",
    registrationClosesAt: "2027-01-29T23:59:00.000Z",
  },
  {
    code: "2027FA",
    name: "Fall 2027",
    startsAt: "2027-08-30T12:00:00.000Z",
    endsAt: "2027-12-17T22:00:00.000Z",
    registrationOpensAt: "2027-04-05T13:00:00.000Z",
    registrationClosesAt: "2027-09-10T23:59:00.000Z",
  },
];

const PROGRAM_SEEDS = [
  ["BS-CS", "Computer Science", "BS", "Computing", 120],
  ["BS-DS", "Data Science", "BS", "Computing", 120],
  ["BS-ME", "Mechanical Engineering", "BS", "Engineering", 128],
  ["BS-EE", "Electrical Engineering", "BS", "Engineering", 128],
  ["BS-CE", "Civil Engineering", "BS", "Engineering", 128],
  ["BBA", "Business Administration", "BBA", "Business", 120],
  ["BS-ACC", "Accounting", "BS", "Business", 122],
  ["BA-ECON", "Economics", "BA", "Social Sciences", 120],
  ["BA-PSY", "Psychology", "BA", "Social Sciences", 120],
  ["BA-ENG", "English Literature", "BA", "Humanities", 120],
  ["BS-BIO", "Biology", "BS", "Natural Sciences", 124],
  ["BS-CHEM", "Chemistry", "BS", "Natural Sciences", 124],
  ["BSN", "Nursing", "BSN", "Health Sciences", 130],
  ["BFA-DES", "Design", "BFA", "Arts", 126],
];

const SUBJECT_SEEDS = [
  ["CS", "Computing", ["Programming Fundamentals", "Data Structures", "Software Engineering"]],
  ["DATA", "Computing", ["Foundations of Data Science", "Statistical Computing", "Machine Learning Practicum"]],
  ["MATH", "Natural Sciences", ["Calculus I", "Calculus II", "Linear Algebra"]],
  ["PHYS", "Natural Sciences", ["University Physics I", "University Physics II", "Modern Physics"]],
  ["ME", "Engineering", ["Engineering Design", "Statics", "Thermodynamics"]],
  ["EE", "Engineering", ["Circuit Analysis", "Digital Logic", "Signals and Systems"]],
  ["CE", "Engineering", ["Surveying and Site Design", "Structural Analysis", "Environmental Systems"]],
  ["BUS", "Business", ["Foundations of Business", "Organisational Behaviour", "Operations Management"]],
  ["ACCT", "Business", ["Financial Accounting", "Managerial Accounting", "Auditing Principles"]],
  ["ECON", "Social Sciences", ["Microeconomics", "Macroeconomics", "Econometrics"]],
  ["PSY", "Social Sciences", ["Introduction to Psychology", "Research Methods", "Cognitive Psychology"]],
  ["ENGL", "Humanities", ["Academic Writing", "World Literature", "Rhetoric and Argument"]],
  ["BIO", "Natural Sciences", ["General Biology", "Genetics", "Microbiology"]],
  ["NURS", "Health Sciences", ["Foundations of Nursing", "Health Assessment", "Clinical Pharmacology"]],
];

const RESIDENCE_HALL_SEEDS = [
  ["ALD", "Alder Hall", "traditional", 320],
  ["BIR", "Birchwood Commons", "suite", 260],
  ["CED", "Cedarcroft House", "traditional", 180],
  ["DUN", "Dunmore Hall", "suite", 240],
  ["ELM", "Elmridge Commons", "apartment", 200],
  ["FER", "Fernhollow House", "apartment", 150],
];

const MEAL_PLAN_SEEDS = [
  ["MP-ANY", "Unlimited Access", 21, 2900],
  ["MP-14", "Fourteen Meals Weekly", 14, 2450],
  ["MP-10", "Ten Meals Weekly", 10, 2050],
  ["MP-COMMUTER", "Commuter Block of Sixty", 4, 850],
];

const HOLD_TYPE_SEEDS = [
  ["financial", "Past-due account balance", true, true, "Office of Student Accounts"],
  ["academic", "Academic standing review", true, false, "Office of the Registrar"],
  ["health", "Missing immunisation clearance", true, false, "Student Health Services"],
  ["conduct", "Open conduct matter", false, true, "Office of Student Conduct"],
  ["advising", "Advising meeting not completed", true, false, "Academic Advising"],
];

const CHECKLIST_TASK_SEEDS = [
  ["accept_offer", "Accept your offer of admission", "Office of Admissions", "/enrollment"],
  ["pay_enrollment_deposit", "Pay your enrolment deposit", "Office of Student Accounts", "/financials"],
  ["submit_final_transcript", "Submit your final official transcript", "Office of the Registrar", "/documents"],
  ["submit_immunization_record", "Submit your immunisation record", "Student Health Services", "/documents"],
  ["complete_fafsa", "Complete the FAFSA", "Office of Financial Aid", "/financials"],
  ["apply_for_housing", "Apply for housing", "Housing & Residence Life", "/housing"],
  ["register_for_orientation", "Register for orientation", "New Student Programs", "/orientation"],
  ["meet_academic_advisor", "Meet your academic adviser", "Academic Advising", "/appointments"],
];

const DOCUMENT_CATEGORY_SEEDS = [
  ["transcript", "Final official transcript", "Office of the Registrar", "all"],
  ["immunization", "Immunisation record", "Student Health Services", "all"],
  ["photo_id", "Government photo identification", "Enrollment Documentation", "all"],
  ["residency_affidavit", "In-state residency affidavit", "Office of the Registrar", "in_state"],
  ["verification_worksheet", "Federal verification worksheet", "Office of Financial Aid", "verification"],
  ["tax_return_transcript", "Tax return transcript", "Office of Financial Aid", "verification"],
  ["i20_support", "I-20 financial support documentation", "International Student Services", "international"],
  ["english_proficiency", "English proficiency score report", "Office of Admissions", "international"],
];

const DOCUMENT_STATUSES = Object.freeze([
  "NOT_SUBMITTED",
  "UPLOADED",
  "UNDER_REVIEW",
  "ACCEPTED",
  "REJECTED",
  "NEEDS_RESUBMISSION",
  "WAIVED",
]);

/**
 * The only status paths a document may take. Building history from a path
 * rather than by sampling is what keeps the monotonic-timestamp and
 * last-entry-matches-status invariants true by construction.
 */
const DOCUMENT_STATUS_PATHS = Object.freeze({
  NOT_SUBMITTED: ["NOT_SUBMITTED"],
  UPLOADED: ["NOT_SUBMITTED", "UPLOADED"],
  UNDER_REVIEW: ["NOT_SUBMITTED", "UPLOADED", "UNDER_REVIEW"],
  ACCEPTED: ["NOT_SUBMITTED", "UPLOADED", "UNDER_REVIEW", "ACCEPTED"],
  REJECTED: ["NOT_SUBMITTED", "UPLOADED", "UNDER_REVIEW", "REJECTED"],
  NEEDS_RESUBMISSION: ["NOT_SUBMITTED", "UPLOADED", "UNDER_REVIEW", "NEEDS_RESUBMISSION"],
  WAIVED: ["NOT_SUBMITTED", "WAIVED"],
});

const VERIFICATION_REQUIREMENT_SEEDS = [
  ["v1_household_resources", "Household size and resources worksheet"],
  ["v1_income_documentation", "Income documentation for the base year"],
  ["v4_identity_statement", "Identity and statement of educational purpose"],
  ["v5_aggregate_review", "Aggregate verification review"],
];

const INTERNATIONAL_REQUIREMENT_SEEDS = [
  ["i20_issued", "Form I-20 issued and received", "International Student Services"],
  ["sevis_fee_paid", "SEVIS I-901 fee paid", "International Student Services"],
  ["visa_interview", "Visa interview completed", "International Student Services"],
  ["english_proficiency", "English proficiency requirement met", "Office of Admissions"],
];

const AWARD_FUND_SEEDS = [
  ["PELL", "Federal Pell Grant", "grant", "federal"],
  ["SEOG", "Federal Supplemental Educational Opportunity Grant", "grant", "federal"],
  ["STATE-GRANT", "State Access Grant", "grant", "state"],
  ["ASTER-MERIT", "Aster Merit Scholarship", "scholarship", "institutional"],
  ["ASTER-ACCESS", "Aster Access Scholarship", "scholarship", "institutional"],
  ["DL-SUB", "Federal Direct Subsidised Loan", "loan", "federal"],
  ["DL-UNSUB", "Federal Direct Unsubsidised Loan", "loan", "federal"],
  ["FWS", "Federal Work-Study", "work_study", "federal"],
];

const ORIENTATION_SESSION_SEEDS = [
  ["ORI-A", "Orientation Session A", "2026-08-24T12:00:00.000Z", "2026-08-24T21:00:00.000Z", 420, "new_students"],
  ["ORI-B", "Orientation Session B", "2026-08-25T12:00:00.000Z", "2026-08-25T21:00:00.000Z", 420, "new_students"],
  ["ORI-C", "Orientation Session C", "2026-08-26T12:00:00.000Z", "2026-08-26T21:00:00.000Z", 420, "new_students"],
  ["ORI-D", "Transfer Orientation", "2026-08-27T13:00:00.000Z", "2026-08-27T20:00:00.000Z", 260, "transfer"],
  ["ORI-E", "Remote Orientation", "2026-08-19T15:00:00.000Z", "2026-08-19T19:00:00.000Z", 300, "new_students"],
  ["INTL-CHECKIN", "International Check-In and Orientation", "2026-08-21T13:00:00.000Z", "2026-08-22T21:00:00.000Z", 240, "international"],
];

const TUITION_BY_RESIDENCY = Object.freeze({
  in_state: 18_400,
  out_of_state: 28_900,
  international: 31_200,
});

const FIRST_NAMES = [
  "Ada", "Bruno", "Camila", "Dmitri", "Elena", "Farid", "Greta", "Hana",
  "Ivo", "Jolene", "Kwame", "Lucia", "Mateo", "Nadia", "Omar", "Petra",
  "Quentin", "Rosa", "Sven", "Tessa", "Ugo", "Vera", "Wesley", "Ximena",
  "Yusuf", "Zara", "Anton", "Bianca", "Caleb", "Delphine", "Emre", "Fiona",
  "Gustav", "Helia", "Ismael", "Junia", "Kaito", "Liora", "Milo", "Noor",
];

/** Constructed surnames: none is a plausible real family being described. */
const LAST_NAMES = [
  "Ashgrove", "Brightwater", "Calderwood", "Dunmire", "Everlyn", "Fennwick",
  "Glimmerly", "Halloway", "Ironwood", "Jessamy", "Kettleby", "Larkspur",
  "Mossbank", "Netherby", "Oakenshaw", "Pemberwell", "Quillfeather",
  "Ravensworth", "Stonebrook", "Thistlebrook", "Underhollow", "Vellacourt",
  "Whitlowe", "Yarrowby", "Zephyrine",
];

const ADVISOR_TITLES = [
  "Academic Adviser",
  "Senior Academic Adviser",
  "Faculty Adviser",
  "Transfer Success Adviser",
];

/* ------------------------------------------------------------------ */
/* Catalogue builders (fresh objects per call: no shared mutable state) */
/* ------------------------------------------------------------------ */

function buildTerms() {
  return TERM_SEEDS.map((term) => ({
    id: uuidFromString(`synthetic-university:term:${term.code}`),
    ...term,
    isSynthetic: true,
  }));
}

function buildPrograms() {
  return PROGRAM_SEEDS.map(([code, name, degreeType, department, requiredCredits]) => ({
    id: uuidFromString(`synthetic-university:program:${code}`),
    code,
    name,
    degreeType,
    department,
    requiredCredits,
    isSynthetic: true,
  }));
}

function buildAdvisors(rng, programs) {
  const advisors = [];
  for (let index = 0; index < 25; index += 1) {
    const firstName = FIRST_NAMES[(index * 7) % FIRST_NAMES.length];
    const lastName = LAST_NAMES[(index * 3) % LAST_NAMES.length];
    const department = programs[index % programs.length].department;
    advisors.push({
      id: uuidFromString(`synthetic-university:advisor:${index}`),
      externalRef: `SYN-ADV-${String(index).padStart(3, "0")}`,
      firstName,
      lastName,
      title: ADVISOR_TITLES[index % ADVISOR_TITLES.length],
      department,
      email: `${firstName}.${lastName}.adv${index}@synthetic.aster.example`.toLowerCase(),
      officeLocation: `Advising Centre, Room ${200 + index}`,
      caseloadCap: intBetween(rng, 120, 220),
      isSynthetic: true,
    });
  }
  return advisors;
}

function buildCourses() {
  const courses = [];
  for (const [subject, department, titles] of SUBJECT_SEEDS) {
    titles.forEach((title, offset) => {
      const number = 101 + offset * 100;
      const code = `${subject} ${number}`;
      courses.push({
        id: uuidFromString(`synthetic-university:course:${code}`),
        code,
        subject,
        title,
        department,
        level: 100 * (offset + 1),
        credits: subject === "NURS" || subject === "ME" ? 3 : 4,
        isSynthetic: true,
      });
    });
  }
  return courses;
}

function buildSections(rng, courses, advisors) {
  const sections = [];
  courses.forEach((course, courseIndex) => {
    // The first handful of courses are the high-demand ones and carry a third
    // section; that is what puts the catalogue at ninety.
    const sectionCount = courseIndex < 6 ? 3 : 2;
    for (let offset = 0; offset < sectionCount; offset += 1) {
      const sectionCode = String(offset + 1).padStart(3, "0");
      const capacity = intBetween(rng, 24, 120);
      sections.push({
        id: uuidFromString(`synthetic-university:section:${course.code}:${sectionCode}`),
        courseId: course.id,
        courseCode: course.code,
        sectionCode,
        termCode: offset === 2 ? "2027SP" : "2026FA",
        capacity,
        enrolled: intBetween(rng, 0, capacity),
        modality: weighted(rng, [["in_person", 70], ["hybrid", 20], ["online", 10]]),
        meetingPattern: pick(rng, ["MWF 09:00", "MWF 11:00", "TR 08:30", "TR 13:00", "W 18:00"]),
        instructorName: `${pick(rng, advisors).firstName} ${pick(rng, advisors).lastName}`,
        isSynthetic: true,
      });
    }
  });
  return sections;
}

function buildResidenceHalls() {
  return RESIDENCE_HALL_SEEDS.map(([code, name, style, capacity]) => ({
    id: uuidFromString(`synthetic-university:hall:${code}`),
    code,
    name,
    style,
    capacity,
    isSynthetic: true,
  }));
}

function buildMealPlans() {
  return MEAL_PLAN_SEEDS.map(([code, name, mealsPerWeek, termCostUsd]) => ({
    id: uuidFromString(`synthetic-university:meal-plan:${code}`),
    code,
    name,
    mealsPerWeek,
    termCostUsd,
    isSynthetic: true,
  }));
}

function buildHoldTypes() {
  return HOLD_TYPE_SEEDS.map(
    ([code, label, blocksRegistration, blocksTranscript, resolutionOffice]) => ({
      id: uuidFromString(`synthetic-university:hold-type:${code}`),
      code,
      label,
      blocksRegistration,
      blocksTranscript,
      resolutionOffice,
      isSynthetic: true,
    }),
  );
}

function buildChecklistTaskCatalogue() {
  return CHECKLIST_TASK_SEEDS.map(([code, label, office, route], order) => ({
    id: uuidFromString(`synthetic-university:checklist-task:${code}`),
    code,
    label,
    responsibleOffice: office,
    navigationRoute: route,
    displayOrder: order,
    isSynthetic: true,
  }));
}

function buildDocumentCategories() {
  return DOCUMENT_CATEGORY_SEEDS.map(([code, label, office, requiredFor]) => ({
    id: uuidFromString(`synthetic-university:document-category:${code}`),
    code,
    label,
    responsibleOffice: office,
    requiredFor,
    isSynthetic: true,
  }));
}

function buildAwardFunds() {
  return AWARD_FUND_SEEDS.map(([fundCode, name, awardType, source]) => ({
    id: uuidFromString(`synthetic-university:fund:${fundCode}`),
    fundCode,
    name,
    awardType,
    source,
    requiresPromissoryNote: awardType === "loan",
    requiresEntranceCounseling: awardType === "loan",
    isSynthetic: true,
  }));
}

function buildOrientationSessions() {
  return ORIENTATION_SESSION_SEEDS.map(
    ([code, name, startsAt, endsAt, capacity, audience]) => ({
      id: uuidFromString(`synthetic-university:orientation:${code}`),
      code,
      name,
      startsAt,
      endsAt,
      capacity,
      audience,
      format: code === "ORI-E" ? "remote" : "on_campus",
      isSynthetic: true,
    }),
  );
}

function buildCostOfAttendance(cohorts) {
  const rows = [];
  for (const cohort of cohorts) {
    for (const residency of ["in_state", "out_of_state", "international"]) {
      const tuitionUsd = TUITION_BY_RESIDENCY[residency];
      const feesUsd = 1_450;
      const housingUsd = 7_250;
      const mealPlanUsd = 2_900;
      const booksUsd = 1_200;
      const personalUsd = residency === "international" ? 3_400 : 2_100;
      rows.push({
        id: uuidFromString(`synthetic-university:coa:${cohort}:${residency}`),
        cohort,
        residency,
        aidYear: AID_YEAR,
        tuitionUsd,
        feesUsd,
        housingUsd,
        mealPlanUsd,
        booksUsd,
        personalUsd,
        // Kept as an explicit sum so `validateUniverse` can catch a hand edit
        // that changes a component without changing the total.
        totalUsd:
          tuitionUsd + feesUsd + housingUsd + mealPlanUsd + booksUsd + personalUsd,
        isSynthetic: true,
      });
    }
  }
  return rows;
}

/* ------------------------------------------------------------------ */
/* Per-student planning                                                */
/* ------------------------------------------------------------------ */

/**
 * The internal trait bag. It is attached to the student draft while dependent
 * records are generated and removed before the student is published, so it
 * never leaks into the JSON.
 */
function emptyPlan() {
  return {
    decision: null,
    documentProfile: null,
    holdCount: null,
    depositState: null,
    fafsaState: null,
    aidState: null,
    housingState: null,
    housingAssigned: null,
    coverage: null,
    balanceState: null,
    advisingComplete: null,
    orientationStatus: null,
    deadlinePassed: false,
  };
}

const AID_ELIGIBLE_FAFSA_STATES = new Set([
  "received",
  "selected_for_verification",
  "verification_complete",
]);

/**
 * Fills the traits the state matrix left alone, then reconciles the
 * combinations that cannot coexist. Reconciliation runs after the draws so
 * that a matrix entry which sets only one trait still produces a coherent
 * student.
 */
function normalisePlan(student, rng) {
  const plan = student.plan;
  plan.decision ??= weighted(rng, [["admitted", 86], ["waitlisted", 9], ["denied", 5]]);

  if (plan.decision === "denied") {
    plan.documentProfile = "missing";
    plan.holdCount = 0;
    plan.depositState = "none";
    plan.fafsaState = "none";
    plan.aidState = "none";
    plan.housingState = "none";
    plan.housingAssigned = false;
    plan.coverage = "none";
    plan.balanceState = "settled";
    plan.advisingComplete = false;
    plan.orientationStatus = "none";
    return;
  }

  plan.documentProfile ??= weighted(rng, [
    ["complete", 52],
    ["missing", 18],
    ["under_review", 14],
    ["rejected", 6],
    ["needs_resubmission", 10],
  ]);
  plan.holdCount ??= weighted(rng, [[0, 68], [1, 22], [2, 7], [3, 3]]);
  plan.depositState ??= weighted(rng, [["posted", 74], ["pending", 14], ["none", 12]]);
  plan.fafsaState ??= weighted(rng, [
    ["none", 12],
    ["not_received", 8],
    ["received", 20],
    ["selected_for_verification", 18],
    ["verification_complete", 40],
    ["rejected", 2],
  ]);
  plan.aidState ??= weighted(rng, [
    ["none", 8],
    ["estimated", 30],
    ["finalized", 40],
    ["disbursed", 22],
  ]);
  plan.housingState ??= weighted(rng, [["residential", 62], ["commuter", 26], ["none", 12]]);
  plan.housingAssigned ??= chance(rng, 0.88);
  plan.coverage ??= weighted(rng, [["partial", 45], ["full", 42], ["over", 13]]);
  plan.advisingComplete ??= chance(rng, 0.82);
  plan.orientationStatus ??= weighted(rng, [
    ["registered", 70],
    ["attended", 20],
    ["cancelled", 7],
    ["no_show", 3],
  ]);

  // A passed deposit deadline is only interesting when the deposit is in fact
  // unpaid, and an unpaid deposit closes housing.
  if (plan.deadlinePassed) {
    plan.depositState = "none";
    plan.housingState = "none";
  }

  // Waitlisted students have not been offered a package and cannot deposit.
  if (plan.decision !== "admitted") {
    plan.depositState = "none";
    plan.aidState = "none";
    plan.housingState = "none";
  }

  // Aid requires a usable FAFSA, and an unverified FAFSA can only support an
  // estimate — never a finalised or disbursed package.
  if (!AID_ELIGIBLE_FAFSA_STATES.has(plan.fafsaState)) {
    plan.aidState = "none";
  } else if (plan.fafsaState !== "verification_complete" && plan.aidState !== "none") {
    plan.aidState = "estimated";
  }

  // Money does not move to a student who has not enrolled, so a package can be
  // final without a deposit but can never have disbursed without one.
  if (plan.aidState === "disbursed" && plan.depositState !== "posted") {
    plan.aidState = "finalized";
  }

  if (plan.aidState === "none") plan.coverage = "none";
  if (plan.aidState !== "none" && plan.coverage === "none") plan.coverage = "partial";
  // Over-coverage only produces a credit balance once money has actually moved.
  if (plan.coverage === "over" && plan.aidState !== "disbursed") plan.coverage = "full";

  if (plan.depositState !== "posted") {
    plan.housingState = "none";
    plan.housingAssigned = false;
  }

  plan.balanceState ??=
    plan.coverage === "over"
      ? weighted(rng, [["refund", 60], ["settled", 40]])
      : weighted(rng, [["settled", 55], ["owed", 45]]);
  // "refund" means an unissued credit balance, which requires over-coverage.
  if (plan.balanceState === "refund" && plan.coverage !== "over") {
    plan.balanceState = "owed";
  }
}

function enrollmentStatusFor(plan) {
  if (plan.decision === "denied") return "denied";
  if (plan.decision === "waitlisted") return "waitlisted";
  if (plan.depositState === "posted") return "enrolled";
  if (plan.depositState === "pending") return "deposit_pending";
  return "admitted";
}

/* ------------------------------------------------------------------ */
/* Record builders                                                     */
/* ------------------------------------------------------------------ */

function documentStatusFor(category, plan, rng) {
  // "complete" means complete: no waivers, no stragglers, nothing in review.
  // Personas whose scenario says "everything is accepted" depend on it.
  if (plan.documentProfile === "complete") return "ACCEPTED";
  if (category === "residency_affidavit") {
    return weighted(rng, [["ACCEPTED", 70], ["WAIVED", 20], ["UPLOADED", 10]]);
  }
  if (category === "verification_worksheet" || category === "tax_return_transcript") {
    return weighted(rng, [["NOT_SUBMITTED", 40], ["UPLOADED", 35], ["UNDER_REVIEW", 25]]);
  }
  if (category === "i20_support" || category === "english_proficiency") {
    return weighted(rng, [["ACCEPTED", 55], ["UNDER_REVIEW", 30], ["NOT_SUBMITTED", 15]]);
  }
  const focus = {
    missing: ["transcript", "immunization"],
    under_review: ["transcript"],
    rejected: ["transcript"],
    needs_resubmission: ["immunization"],
  }[plan.documentProfile];
  const focusStatus = {
    missing: "NOT_SUBMITTED",
    under_review: "UNDER_REVIEW",
    rejected: "REJECTED",
    needs_resubmission: "NEEDS_RESUBMISSION",
  }[plan.documentProfile];
  return focus.includes(category) ? focusStatus : "ACCEPTED";
}

/**
 * Walks the status path for the final status, spacing entries by a
 * non-negative number of hours. Equal timestamps are allowed (same-day bulk
 * imports do that in real systems) but time never runs backwards.
 */
function buildStatusHistory(status, startIso, office, rng) {
  const path = DOCUMENT_STATUS_PATHS[status];
  let at = startIso;
  return path.map((entry, index) => {
    if (index > 0) at = addHours(at, intBetween(rng, 0, 96));
    const actor =
      entry === "NOT_SUBMITTED"
        ? "system"
        : entry === "UPLOADED"
          ? "student"
          : office;
    return { status: entry, at, actor };
  });
}

/**
 * Splits `total` into `parts` whole-dollar amounts that sum to exactly
 * `total`; the remainder lands on the last part so the sum is never off by a
 * rounding cent.
 */
function splitAmount(rng, total, parts) {
  if (parts <= 1) return [total];
  const weights = [];
  let weightTotal = 0;
  for (let index = 0; index < parts; index += 1) {
    const weight = intBetween(rng, 15, 60);
    weights.push(weight);
    weightTotal += weight;
  }
  const amounts = [];
  let assigned = 0;
  for (let index = 0; index < parts - 1; index += 1) {
    const amount = Math.round((total * weights[index]) / weightTotal);
    amounts.push(amount);
    assigned += amount;
  }
  amounts.push(total - assigned);
  return amounts;
}

/* ------------------------------------------------------------------ */
/* Generation                                                          */
/* ------------------------------------------------------------------ */

/**
 * @param {object} [options]
 * @param {number} [options.seed]
 * @param {number} [options.studentCount] between 2000 and 4000
 * @param {string} [options.now] ISO instant treated as the current time
 * @returns {object} the whole synthetic institution
 */
export function generateUniverse({
  seed = DEFAULT_SEED,
  studentCount = DEFAULT_STUDENT_COUNT,
  now = DEFAULT_NOW,
} = {}) {
  if (!Number.isInteger(studentCount) || studentCount < MIN_STUDENT_COUNT || studentCount > MAX_STUDENT_COUNT) {
    throw new RangeError(
      `studentCount must be an integer between ${MIN_STUDENT_COUNT} and ${MAX_STUDENT_COUNT}; received ${studentCount}`,
    );
  }
  if (Number.isNaN(Date.parse(now))) {
    throw new TypeError(`now must be an ISO instant; received ${now}`);
  }

  const rng = createRng(seed);
  const cohorts = ["2026FA", "2027SP"];

  const terms = buildTerms();
  const programs = buildPrograms();
  const advisors = buildAdvisors(rng, programs);
  const courses = buildCourses();
  const sections = buildSections(rng, courses, advisors);
  const residenceHalls = buildResidenceHalls();
  const mealPlans = buildMealPlans();
  const holdTypes = buildHoldTypes();
  const checklistTaskCatalogue = buildChecklistTaskCatalogue();
  const documentCategories = buildDocumentCategories();
  const awardFunds = buildAwardFunds();
  const orientationSessions = buildOrientationSessions();
  const costOfAttendance = buildCostOfAttendance(cohorts);

  const universe = {
    meta: {
      version: UNIVERSE_VERSION,
      seed,
      studentCount,
      generatedFor: now,
      tenant: "Aster University",
      note: "Entirely synthetic. No record here describes a real institution or person.",
      isSynthetic: true,
    },
    terms,
    programs,
    advisors,
    courses,
    sections,
    residenceHalls,
    mealPlans,
    holdTypes,
    checklistTaskCatalogue,
    documentCategories,
    awardFunds,
    orientationSessions,
    costOfAttendance,
    students: [],
    applications: [],
    checklistTasks: [],
    documents: [],
    holds: [],
    fafsaRecords: [],
    verificationRequirements: [],
    aidAwards: [],
    disbursements: [],
    sapStatus: [],
    accountLedger: [],
    housingApplications: [],
    housingAssignments: [],
    orientationRegistrations: [],
    registrationEligibility: [],
    internationalRequirements: [],
    personas: structuredClone(SYNTHETIC_PERSONAS),
  };

  const termByCode = new Map(terms.map((term) => [term.code, term]));
  const coaByKey = new Map(
    costOfAttendance.map((row) => [`${row.cohort}:${row.residency}`, row]),
  );

  const matrixSlots = STATE_MATRIX.length * MATRIX_REPEATS;
  const personaCount = SYNTHETIC_PERSONAS.length;

  for (let index = 0; index < studentCount; index += 1) {
    const persona = index < personaCount ? SYNTHETIC_PERSONAS[index] : null;
    const firstName = persona ? persona.firstName : pick(rng, FIRST_NAMES);
    const lastName = persona ? persona.lastName : pick(rng, LAST_NAMES);
    const cohort = weighted(rng, [["2026FA", 78], ["2027SP", 22]]);
    const student = {
      id: persona ? persona.studentId : uuidFrom(rng),
      externalRef: persona ? persona.externalRef : `SYN-${String(index).padStart(6, "0")}`,
      firstName,
      lastName,
      preferredName: persona ? persona.preferredName : firstName,
      email: `${firstName}.${lastName}.${index}@synthetic.aster.example`.toLowerCase(),
      cohort,
      admitType: weighted(rng, [["first_year", 68], ["transfer", 20], ["international", 12]]),
      residency: weighted(rng, [["in_state", 52], ["out_of_state", 34], ["international", 14]]),
      programCode: pick(rng, programs).code,
      enrollmentStatus: "admitted",
      advisorId: pick(rng, advisors).id,
      personaKey: persona ? persona.key : null,
      stateKeys: [],
      isSynthetic: true,
      plan: emptyPlan(),
    };

    if (persona) {
      // Personas are curated, so their demographics are curated too: a
      // persona is domestic and first-year unless a state key says otherwise.
      // Leaving these to the dice makes the scenario text drift from the data.
      student.admitType = "first_year";
      student.residency = "in_state";
      for (const key of persona.stateKeys) {
        applyStateKey(student, key, universe, rng);
        student.stateKeys.push(key);
      }
    } else if (index < personaCount + matrixSlots) {
      const entry = STATE_MATRIX[(index - personaCount) % STATE_MATRIX.length];
      entry.assign(student, universe, rng);
      student.stateKeys.push(entry.key);
    }

    // An international admit is international for residency purposes too, and
    // vice versa: the two fields are not independent in the source systems.
    if (student.admitType === "international") student.residency = "international";
    if (student.residency === "international") student.admitType = "international";

    normalisePlan(student, rng);
    student.enrollmentStatus = enrollmentStatusFor(student.plan);

    populateStudent(universe, student, {
      rng,
      now,
      termByCode,
      coaByKey,
      holdTypes,
      documentCategories,
      awardFunds,
      residenceHalls,
      mealPlans,
      orientationSessions,
      checklistTaskCatalogue,
    });

    delete student.plan;
    universe.students.push(student);
  }

  return universe;
}

/**
 * Builds every record that hangs off one student, in dependency order:
 * documents and holds first, then aid, then the ledger that aid feeds, then
 * housing and registration which read the ledger.
 */
function populateStudent(universe, student, context) {
  const {
    rng,
    now,
    termByCode,
    coaByKey,
    holdTypes,
    documentCategories,
    awardFunds,
    residenceHalls,
    mealPlans,
    orientationSessions,
    checklistTaskCatalogue,
  } = context;
  const plan = student.plan;
  const term = termByCode.get(student.cohort);

  /* Application ---------------------------------------------------- */
  const submittedAt = addDays(APPLICATION_WINDOW_OPENS, intBetween(rng, 0, 90));
  const decidedAt = addDays(submittedAt, intBetween(rng, 14, 60));
  const application = {
    id: uuidFrom(rng),
    studentId: student.id,
    termCode: student.cohort,
    submittedAt,
    decision: plan.decision,
    decidedAt,
    // A passed deadline is expressed in the data, not in prose, so that
    // "is it too late" is answerable by comparison rather than by belief.
    depositDeadline: plan.deadlinePassed
      ? "2026-06-01T23:59:00.000Z"
      : "2026-09-01T23:59:00.000Z",
    isSynthetic: true,
  };
  universe.applications.push(application);

  // A denied applicant has no downstream life at this institution.
  if (plan.decision === "denied") return;

  const admitted = plan.decision === "admitted";

  /* Documents ------------------------------------------------------ */
  const requiredCategories = documentCategories.filter((category) => {
    if (category.requiredFor === "all") return true;
    if (category.requiredFor === "in_state") return student.residency === "in_state";
    if (category.requiredFor === "international") return student.residency === "international";
    if (category.requiredFor === "verification") {
      return plan.fafsaState === "selected_for_verification";
    }
    return false;
  });

  const documentStatusByCategory = new Map();
  for (const category of requiredCategories) {
    const status = documentStatusFor(category.code, plan, rng);
    const statusHistory = buildStatusHistory(
      status,
      addDays(decidedAt, intBetween(rng, 1, 40)),
      category.responsibleOffice,
      rng,
    );
    const uploadedEntry = statusHistory.find((entry) => entry.status === "UPLOADED");
    const reviewedEntry = [...statusHistory]
      .reverse()
      .find((entry) => ["UNDER_REVIEW", "ACCEPTED", "REJECTED", "NEEDS_RESUBMISSION"].includes(entry.status));
    documentStatusByCategory.set(category.code, status);
    universe.documents.push({
      id: uuidFrom(rng),
      studentId: student.id,
      category: category.code,
      label: category.label,
      responsibleOffice: category.responsibleOffice,
      status,
      submittedAt: uploadedEntry ? uploadedEntry.at : null,
      reviewedAt: reviewedEntry ? reviewedEntry.at : null,
      statusHistory,
      isSynthetic: true,
    });
  }

  /* Holds ---------------------------------------------------------- */
  const holdCount = plan.holdCount ?? 0;
  const chosenHoldTypes = sample(rng, holdTypes, holdCount);
  // When the student owes money, make the financial hold one of the ones they
  // actually have: a balance with no matching hold reads as a data bug.
  if (holdCount > 0 && plan.balanceState === "owed") {
    const financial = holdTypes.find((type) => type.code === "financial");
    if (!chosenHoldTypes.some((type) => type.code === "financial")) {
      chosenHoldTypes[0] = financial;
    }
  }
  const activeHolds = [];
  for (const holdType of chosenHoldTypes) {
    const hold = {
      id: uuidFrom(rng),
      studentId: student.id,
      holdType: holdType.code,
      label: holdType.label,
      reason: `${holdType.label} recorded by ${holdType.resolutionOffice}.`,
      placedAt: addDays(now, -intBetween(rng, 3, 90)),
      releasedAt: null,
      blocksRegistration: holdType.blocksRegistration,
      blocksTranscript: holdType.blocksTranscript,
      resolutionOffice: holdType.resolutionOffice,
      isSynthetic: true,
    };
    universe.holds.push(hold);
    activeHolds.push(hold);
  }

  /* FAFSA and verification ----------------------------------------- */
  let fafsa = null;
  if (plan.fafsaState !== "none") {
    fafsa = {
      id: uuidFrom(rng),
      studentId: student.id,
      aidYear: AID_YEAR,
      status: plan.fafsaState,
      receivedAt:
        plan.fafsaState === "not_received"
          ? null
          : addDays(decidedAt, intBetween(rng, 2, 45)),
      // Student Aid Index replaced the Expected Family Contribution; the
      // negative floor is deliberate, it is a real part of the scale.
      studentAidIndex: intBetween(rng, -1_500, 42_000),
      isirTransactionNumber: `0${intBetween(rng, 1, 9)}`,
      isSynthetic: true,
    };
    universe.fafsaRecords.push(fafsa);
  }

  if (fafsa && fafsa.status === "selected_for_verification") {
    for (const [code, label] of sample(rng, VERIFICATION_REQUIREMENT_SEEDS, intBetween(rng, 2, 3))) {
      universe.verificationRequirements.push({
        id: uuidFrom(rng),
        studentId: student.id,
        aidYear: AID_YEAR,
        code,
        label,
        status: weighted(rng, [["outstanding", 55], ["submitted", 30], ["satisfied", 15]]),
        dueAt: plan.deadlinePassed
          ? addDays(now, -intBetween(rng, 5, 30))
          : addDays(now, intBetween(rng, 5, 45)),
        isSynthetic: true,
      });
    }
  }

  /* Charges -------------------------------------------------------- */
  const coa = coaByKey.get(`${student.cohort}:${student.residency}`);
  const residential = plan.housingState === "residential";
  const mealPlan = residential
    ? pick(rng, mealPlans.filter((entry) => entry.code !== "MP-COMMUTER"))
    : mealPlans.find((entry) => entry.code === "MP-COMMUTER");
  const chargeLines = [
    ["tuition", "Tuition", coa.tuitionUsd],
    ["mandatory_fees", "Mandatory fees", coa.feesUsd],
  ];
  if (residential) {
    chargeLines.push(["housing", "Residence hall charge", coa.housingUsd]);
    chargeLines.push(["meal_plan", `Meal plan: ${mealPlan.name}`, mealPlan.termCostUsd]);
  }
  const chargeTotal = chargeLines.reduce((sum, [, , amount]) => sum + amount, 0);

  /* Awards --------------------------------------------------------- */
  const coverageFraction = {
    none: 0,
    partial: floatBetween(rng, 0.3, 0.65, 4),
    full: floatBetween(rng, 0.92, 1, 4),
    over: floatBetween(rng, 1.06, 1.25, 4),
  }[plan.coverage];
  const aidTarget = plan.aidState === "none" ? 0 : Math.round(chargeTotal * coverageFraction);
  const acceptedAwards = [];

  if (plan.aidState !== "none" && aidTarget > 0) {
    const isEstimated = plan.aidState === "estimated";
    const funds = sample(rng, awardFunds, intBetween(rng, 2, 4));
    const amounts = splitAmount(rng, aidTarget, funds.length);
    funds.forEach((fund, offset) => {
      const offeredAmountUsd = amounts[offset];
      const status = isEstimated
        ? weighted(rng, [["pending", 55], ["offered", 45]])
        : "accepted";
      const award = {
        id: uuidFrom(rng),
        studentId: student.id,
        aidYear: AID_YEAR,
        awardType: fund.awardType,
        fundCode: fund.fundCode,
        name: fund.name,
        source: fund.source,
        offeredAmountUsd,
        acceptedAmountUsd: status === "accepted" ? offeredAmountUsd : null,
        status,
        isEstimated,
        requiresPromissoryNote: fund.requiresPromissoryNote,
        requiresEntranceCounseling: fund.requiresEntranceCounseling,
        offeredAt: addDays(decidedAt, intBetween(rng, 5, 60)),
        isSynthetic: true,
      };
      universe.aidAwards.push(award);
      if (status === "accepted") acceptedAwards.push(award);
    });

    // A declined loan on top of the accepted package: it must not change the
    // coverage arithmetic, which is why it is added after the split.
    if (!isEstimated && chance(rng, 0.25)) {
      const loan = awardFunds.find((fund) => fund.fundCode === "DL-UNSUB");
      universe.aidAwards.push({
        id: uuidFrom(rng),
        studentId: student.id,
        aidYear: AID_YEAR,
        awardType: loan.awardType,
        fundCode: loan.fundCode,
        name: loan.name,
        source: loan.source,
        offeredAmountUsd: intBetween(rng, 1_000, 6_000),
        acceptedAmountUsd: 0,
        status: "declined",
        isEstimated: false,
        requiresPromissoryNote: loan.requiresPromissoryNote,
        requiresEntranceCounseling: loan.requiresEntranceCounseling,
        offeredAt: addDays(decidedAt, intBetween(rng, 5, 60)),
        isSynthetic: true,
      });
    }
  }

  /* Disbursements -------------------------------------------------- */
  // Collected locally rather than re-scanned out of the universe: the ledger
  // below only needs this student's rows, and a global filter per student
  // would make generation quadratic in the population size.
  const hasFinancialHold = activeHolds.some((hold) => hold.holdType === "financial");
  const studentDisbursements = [];
  for (const award of acceptedAwards) {
    const scheduledFor = addDays(term.startsAt, -intBetween(rng, 1, 10));
    const disbursed = plan.aidState === "disbursed";
    const status = disbursed ? "disbursed" : hasFinancialHold ? "held" : "scheduled";
    const disbursement = {
      id: uuidFrom(rng),
      studentId: student.id,
      awardId: award.id,
      term: student.cohort,
      amountUsd: award.acceptedAmountUsd,
      scheduledFor,
      disbursedAt: disbursed ? addDays(scheduledFor, intBetween(rng, 0, 3)) : null,
      status,
      holdReason: status === "held" ? "past_due_account_balance" : null,
      isSynthetic: true,
    };
    studentDisbursements.push(disbursement);
    universe.disbursements.push(disbursement);
  }

  /* Ledger --------------------------------------------------------- */
  const studentLedger = [];
  const postLedger = (entry) => {
    studentLedger.push(entry);
    universe.accountLedger.push(entry);
  };
  if (admitted) {
    const postedAt = addDays(term.startsAt, -intBetween(rng, 20, 45));
    for (const [code, label, amountUsd] of chargeLines) {
      postLedger({
        id: uuidFrom(rng),
        studentId: student.id,
        postedAt,
        type: "charge",
        code,
        label,
        amountUsd,
        term: student.cohort,
        isSynthetic: true,
      });
    }
    if (plan.depositState === "posted") {
      postLedger({
        id: uuidFrom(rng),
        studentId: student.id,
        postedAt: addDays(decidedAt, intBetween(rng, 3, 60)),
        type: "payment",
        code: "enrollment_deposit",
        label: "Enrolment deposit",
        amountUsd: -ENROLLMENT_DEPOSIT_USD,
        term: student.cohort,
        isSynthetic: true,
      });
    }
    for (const disbursement of studentDisbursements) {
      if (disbursement.status !== "disbursed") continue;
      postLedger({
        id: uuidFrom(rng),
        studentId: student.id,
        postedAt: disbursement.disbursedAt,
        type: "aid_credit",
        code: "aid_disbursement",
        label: "Financial aid disbursement",
        amountUsd: -disbursement.amountUsd,
        term: disbursement.term,
        isSynthetic: true,
      });
    }

    const balance = studentLedger.reduce((sum, entry) => sum + entry.amountUsd, 0);

    if (plan.balanceState === "settled" && balance > 0) {
      postLedger({
        id: uuidFrom(rng),
        studentId: student.id,
        postedAt: addDays(now, -intBetween(rng, 1, 20)),
        type: "payment",
        code: "student_payment",
        label: "Payment received",
        amountUsd: -balance,
        term: student.cohort,
        isSynthetic: true,
      });
    } else if (plan.balanceState === "settled" && balance < 0) {
      // The credit was already refunded, which is what "settled" means for an
      // over-covered student.
      postLedger({
        id: uuidFrom(rng),
        studentId: student.id,
        postedAt: addDays(now, -intBetween(rng, 1, 14)),
        type: "refund",
        code: "credit_balance_refund",
        label: "Credit balance refund issued",
        amountUsd: -balance,
        term: student.cohort,
        isSynthetic: true,
      });
    }
  }

  const finalBalance = studentLedger.reduce((sum, entry) => sum + entry.amountUsd, 0);

  /* Housing -------------------------------------------------------- */
  if (admitted && plan.housingState === "commuter") {
    universe.housingApplications.push({
      id: uuidFrom(rng),
      studentId: student.id,
      termCode: student.cohort,
      preference: "commuter",
      roomTypePreference: null,
      submittedAt: addDays(now, -intBetween(rng, 10, 60)),
      status: "exempted",
      isSynthetic: true,
    });
  } else if (admitted && plan.housingState === "residential") {
    const waitlisted = !plan.housingAssigned;
    const housingApplication = {
      id: uuidFrom(rng),
      studentId: student.id,
      termCode: student.cohort,
      preference: "residential",
      roomTypePreference: pick(rng, ["single", "double", "suite", "no_preference"]),
      submittedAt: addDays(now, -intBetween(rng, 10, 70)),
      status: waitlisted ? "waitlisted" : "assigned",
      isSynthetic: true,
    };
    universe.housingApplications.push(housingApplication);
    if (!waitlisted) {
      const hall = pick(rng, residenceHalls);
      universe.housingAssignments.push({
        id: uuidFrom(rng),
        studentId: student.id,
        applicationId: housingApplication.id,
        hallId: hall.id,
        hallCode: hall.code,
        roomLabel: `${hall.code}-${intBetween(rng, 1, 5)}${String(intBetween(rng, 1, 40)).padStart(2, "0")}${pick(rng, ["A", "B"])}`,
        mealPlanCode: mealPlan.code,
        moveInAt: addDays(term.startsAt, -intBetween(rng, 6, 9)),
        status: "confirmed",
        isSynthetic: true,
      });
    }
  }

  /* Orientation ---------------------------------------------------- */
  let orientationRegistered = false;
  if (admitted && plan.depositState === "posted") {
    const eligibleSessions = orientationSessions.filter((session) => {
      if (student.residency === "international") return session.audience === "international";
      if (student.admitType === "transfer") return session.audience !== "international";
      return session.audience === "new_students";
    });
    const session = pick(rng, eligibleSessions);
    const status = plan.orientationStatus;
    orientationRegistered = status !== "cancelled";
    universe.orientationRegistrations.push({
      id: uuidFrom(rng),
      studentId: student.id,
      sessionId: session.id,
      status,
      registeredAt: addDays(now, -intBetween(rng, 2, 40)),
      isSynthetic: true,
    });
  }

  /* Registration eligibility --------------------------------------- */
  if (admitted) {
    const blockingHold = activeHolds.find((hold) => hold.blocksRegistration) ?? null;
    const gates = [
      {
        code: "deposit_posted",
        label: "Enrolment deposit posted",
        satisfied: plan.depositState === "posted",
        blockingHoldId: null,
      },
      {
        code: "immunization_cleared",
        label: "Immunisation record accepted",
        satisfied: documentStatusByCategory.get("immunization") === "ACCEPTED",
        blockingHoldId:
          activeHolds.find((hold) => hold.holdType === "health")?.id ?? null,
      },
      {
        code: "transcript_received",
        label: "Final official transcript accepted",
        satisfied: documentStatusByCategory.get("transcript") === "ACCEPTED",
        blockingHoldId: null,
      },
      {
        code: "advising_complete",
        label: "Advising meeting completed",
        satisfied:
          plan.advisingComplete && !activeHolds.some((hold) => hold.holdType === "advising"),
        blockingHoldId:
          activeHolds.find((hold) => hold.holdType === "advising")?.id ?? null,
      },
      {
        code: "balance_under_threshold",
        label: `Account balance at or below $${BALANCE_HOLD_THRESHOLD_USD}`,
        satisfied: finalBalance <= BALANCE_HOLD_THRESHOLD_USD,
        blockingHoldId:
          activeHolds.find((hold) => hold.holdType === "financial")?.id ?? null,
      },
    ];
    if (student.residency === "international") {
      gates.push({
        code: "international_check_in",
        label: "International immigration check-in completed",
        satisfied: chance(rng, 0.45),
        blockingHoldId: null,
      });
    }
    universe.registrationEligibility.push({
      id: uuidFrom(rng),
      studentId: student.id,
      termCode: student.cohort,
      // Registration runs in assigned slots; the offset is what makes the
      // "when does my window open" question have a per-student answer.
      windowOpensAt: addHours(term.registrationOpensAt, intBetween(rng, 0, 96)),
      eligible: gates.every((gate) => gate.satisfied) && !blockingHold,
      blockingHoldId: blockingHold ? blockingHold.id : null,
      gates,
      isSynthetic: true,
    });
  }

  /* International requirements ------------------------------------- */
  if (student.residency === "international") {
    for (const [code, label, office] of INTERNATIONAL_REQUIREMENT_SEEDS) {
      const status = weighted(rng, [["complete", 45], ["in_progress", 30], ["not_started", 25]]);
      universe.internationalRequirements.push({
        id: uuidFrom(rng),
        studentId: student.id,
        code,
        label,
        responsibleOffice: office,
        status,
        dueAt: addDays(term.startsAt, -intBetween(rng, 7, 30)),
        completedAt: status === "complete" ? addDays(now, -intBetween(rng, 1, 60)) : null,
        isSynthetic: true,
      });
    }
  }

  /* Satisfactory academic progress --------------------------------- */
  const sapState = weighted(rng, [
    ["meeting", 80],
    ["warning", 10],
    ["probation", 7],
    ["suspension", 3],
  ]);
  const sapRanges = {
    meeting: [[2, 4], [0.67, 1]],
    warning: [[1.7, 2.1], [0.6, 0.75]],
    probation: [[1.3, 1.9], [0.5, 0.66]],
    suspension: [[0.5, 1.4], [0.2, 0.5]],
  }[sapState];
  universe.sapStatus.push({
    id: uuidFrom(rng),
    studentId: student.id,
    aidYear: AID_YEAR,
    status: sapState,
    gpa: floatBetween(rng, sapRanges[0][0], sapRanges[0][1], 2),
    completionRate: floatBetween(rng, sapRanges[1][0], sapRanges[1][1], 2),
    evaluatedAt: addDays(now, -intBetween(rng, 5, 120)),
    isSynthetic: true,
  });

  /* Checklist ------------------------------------------------------ */
  const documentTaskStatus = (category) => {
    const status = documentStatusByCategory.get(category);
    if (status === "ACCEPTED" || status === "WAIVED") return "complete";
    if (status === "NOT_SUBMITTED" || status === undefined) return "not_started";
    return "in_progress";
  };
  const taskStatuses = {
    accept_offer: admitted ? "complete" : "not_started",
    pay_enrollment_deposit:
      plan.depositState === "posted"
        ? "complete"
        : plan.depositState === "pending"
          ? "in_progress"
          : "not_started",
    submit_final_transcript: documentTaskStatus("transcript"),
    submit_immunization_record: documentTaskStatus("immunization"),
    complete_fafsa:
      plan.fafsaState === "none"
        ? "not_started"
        : plan.fafsaState === "not_received"
          ? "in_progress"
          : "complete",
    apply_for_housing:
      plan.housingState === "residential"
        ? "complete"
        : plan.housingState === "commuter"
          ? "waived"
          : "not_started",
    register_for_orientation: orientationRegistered ? "complete" : "not_started",
    meet_academic_advisor:
      plan.advisingComplete && !activeHolds.some((hold) => hold.holdType === "advising")
        ? "complete"
        : "not_started",
  };
  for (const task of checklistTaskCatalogue) {
    const status = taskStatuses[task.code];
    universe.checklistTasks.push({
      id: uuidFrom(rng),
      studentId: student.id,
      taskCode: task.code,
      label: task.label,
      status,
      dueAt: plan.deadlinePassed
        ? addDays(now, -intBetween(rng, 3, 40))
        : addDays(term.startsAt, -intBetween(rng, 1, 45)),
      completedAt: status === "complete" ? addDays(now, -intBetween(rng, 1, 60)) : null,
      isSynthetic: true,
    });
  }
}

/**
 * @param {object} universe
 * @param {string} key a STATE_MATRIX key
 * @returns {object[]} the students the matrix deliberately placed in that state
 */
export function studentsByStateKey(universe, key) {
  return universe.students.filter((student) => student.stateKeys.includes(key));
}

/**
 * @param {object} universe
 * @param {string} studentId
 * @returns {object} every record belonging to one student, for demo reads
 */
export function studentRecord(universe, studentId) {
  const byStudent = (collection) =>
    universe[collection].filter((row) => row.studentId === studentId);
  return {
    student: universe.students.find((student) => student.id === studentId) ?? null,
    application: byStudent("applications")[0] ?? null,
    checklistTasks: byStudent("checklistTasks"),
    documents: byStudent("documents"),
    holds: byStudent("holds"),
    fafsa: byStudent("fafsaRecords")[0] ?? null,
    verificationRequirements: byStudent("verificationRequirements"),
    aidAwards: byStudent("aidAwards"),
    disbursements: byStudent("disbursements"),
    sapStatus: byStudent("sapStatus")[0] ?? null,
    accountLedger: byStudent("accountLedger"),
    housingApplication: byStudent("housingApplications")[0] ?? null,
    housingAssignment: byStudent("housingAssignments")[0] ?? null,
    orientationRegistration: byStudent("orientationRegistrations")[0] ?? null,
    registrationEligibility: byStudent("registrationEligibility")[0] ?? null,
    internationalRequirements: byStudent("internationalRequirements"),
  };
}

export { DOCUMENT_STATUSES };
