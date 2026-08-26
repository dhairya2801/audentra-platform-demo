/**
 * Staff Edward university benchmark — holdout suite.
 *
 * Written after the development suite but run only once the fixes were
 * finished: different phrasings, different personas, and people/departments
 * the development questions never named. Same grading, same ground truth.
 * A gap between dev and holdout pass rates is the measure of how much the
 * fixes fit the questions rather than the problem.
 */

const DIRECTOR = "SYN-STF-ADV-DIR";
const VP = "SYN-STF-VP";
const FA_DIR = "SYN-STF-FA-DIR"; // Keziah Abernathy
const ADM_DIR = "SYN-STF-ADM-DIR"; // Hollis Zaragoza
const HOUSING = "SYN-STF-HRL-ASG"; // Farid Jokinen
const ADA = "SYN-ADV-000";
const XIMENA = "SYN-ADV-009";
const TCE = "SYN-STF-REG-TCE"; // Juniper Jokinen, sole transfer-credit evaluator
const HEALTH = "SYN-STF-SHS-REC1"; // Kirsten Abernathy

const S = (ref) => `students.${ref}`;
const ST = (ref) => `staff.byRef.${ref}`;
const f = (desc, pattern, critical = true) => ({ desc, pattern, critical });
const soft = (desc, pattern) => ({ desc, pattern, critical: false });
const NOT_ASK_STUDENT = f("does not ask which student", "^(?!.*tell me which student you mean)");
const NO_READ_FAIL = f("no failed read", "^(?!.*couldn'?t (?:read|check|run))");
const NOT_STUDENT_FOUND = { desc: "not a roster hit", pattern: "students? matching" };

function one(id, category, actor, question, expect, extra = {}) {
  return { id, category, actor, turns: [{ question, expect }], ...extra };
}

export const HOLDOUT_CASES = [
  one("h-001", "other_staff", FA_DIR, "What's on Rosalind Zaragoza's plate right now?", {
    family: ["staff"],
    resolvedStudentId: null,
    facts: [f("open", `{{num:${ST("SYN-STF-FA-VER1")}.openItems}}`)],
    forbidden: [NOT_STUDENT_FOUND],
  }),
  one("h-002", "caseload_capacity", DIRECTOR, "Is Ada Ashgrove carrying more advisees than her cap allows?", {
    family: ["staff"],
    resolvedStudentId: null,
    facts: [f("advisees", `{{num:${ST(ADA)}.primaryAdvisees}}`), f("cap", `{{num:${ST(ADA)}.caseloadCap}}`)],
  }),
  one("h-003", "scheduling", DIRECTOR, "Earliest time a student could get in with Ximena Calderwood?", {
    family: ["staff"],
    resolvedStudentId: null,
    facts: [f("next slot", `{{date:${ST(XIMENA)}.nextOpenSlotAt}}`)],
  }),
  one("h-004", "my_work", ADA, "Am I behind on anything?", {
    family: ["my_work", "queue"],
    resolvedStudentId: null,
    facts: [f("overdue", `{{num~:${ST(ADA)}.overdueItems}}`), NO_READ_FAIL, NOT_ASK_STUDENT],
  }),
  one("h-005", "my_work", XIMENA, "How full is my caseload?", {
    family: ["my_work", "staff"],
    resolvedStudentId: null,
    facts: [f("advisees", `{{num:${ST(XIMENA)}.primaryAdvisees}}`), f("cap", `{{num:${ST(XIMENA)}.caseloadCap}}`)],
  }),
  one("h-006", "department_ops", VP, "Give me the picture for Student Health.", {
    family: ["department"],
    resolvedStudentId: null,
    facts: [f("open", "{{num:departments.Student Health.open_items}}")],
  }),
  one("h-007", "department_ops", ADM_DIR, "How much of Admissions' open work has no owner?", {
    family: ["department", "queue"],
    resolvedStudentId: null,
    facts: [f("unassigned", "{{num:queue.byComponent.Admissions.unassigned}}")],
  }),
  one("h-008", "action_center", DIRECTOR, "Count the escalated items on the board.", {
    family: ["queue"],
    resolvedStudentId: null,
    facts: [f("escalated", "{{num:queue.escalated}}")],
  }),
  one("h-009", "action_center", HOUSING, "Anything urgent sitting unowned in Housing?", {
    family: ["queue", "department"],
    resolvedStudentId: null,
    facts: [f("count or none", "{{num:queue.byComponent.Housing.unassigned_urgent}}|\\bno\\b|none|nothing")],
  }),
  one("h-010", "inquiries", DIRECTOR, "Which student has been waiting longest for someone to answer them?", {
    family: ["inquiries"],
    resolvedStudentId: null,
    facts: [f("oldest student", "{{gt:inquiries.oldestAwaiting.student}}")],
  }),
  one("h-011", "inquiries", DIRECTOR, "Do we have any urgent requests nobody has replied to?", {
    family: ["inquiries"],
    resolvedStudentId: null,
    facts: [f("count", "{{num:inquiries.awaiting_urgent}}|\\bno\\b|none")],
  }),
  one("h-012", "student", DIRECTOR, "Who looks after Rufus Tanglewood on the advising side?", {
    family: ["student"],
    resolvedStudentId: `gt:${S("SYN-000008")}.id`,
    facts: [f("adviser", `{{gt:${S("SYN-000008")}.adviserName}}`)],
  }),
  one("h-013", "student", DIRECTOR, "Is Cassius Pemberwell booked in to see anyone soon?", {
    family: ["student"],
    resolvedStudentId: `gt:${S("SYN-000005")}.id`,
    facts: [f("date", `{{date:${S("SYN-000005")}.nextAppointmentDate}}`), soft("with", `{{gt:${S("SYN-000005")}.nextAppointmentWith}}`)],
  }),
  one("h-014", "cohort", DIRECTOR, "Count the admitted students who don't have anyone advising them yet.", {
    family: ["cohort"],
    resolvedStudentId: null,
    facts: [f("count", "{{num:cohorts.accepted_no_primary_adviser}}")],
  }),
  one("h-015", "cohort", DIRECTOR, "How many students still owe their enrollment deposit?", {
    family: ["cohort"],
    resolvedStudentId: null,
    facts: [f("count", "{{num:cohorts.unpaid_deposit}}")],
  }),
  one("h-016", "comparison", DIRECTOR, "Ximena Calderwood versus Ada Ashgrove — who has more room to take on students?", {
    family: ["staff"],
    resolvedStudentId: null,
    facts: [f("Ximena advisees", `{{num:${ST(XIMENA)}.primaryAdvisees}}`), f("Ada advisees", `{{num:${ST(ADA)}.primaryAdvisees}}`)],
  }),
  one("h-017", "multi_criteria", DIRECTOR, "Show me Ximena Calderwood's advisees who have overdue work.", {
    family: ["staff"],
    resolvedStudentId: null,
    facts: [f("count", `{{num~:${ST(XIMENA)}.caseload.with_overdue_work}}`)],
  }),
  one("h-018", "other_staff", DIRECTOR, "Juniper Jokinen — what does she do and how loaded is she?", {
    family: ["staff"],
    resolvedStudentId: null,
    facts: [f("title", "Transfer Credit Evaluator"), f("open", `{{num:${ST(TCE)}.openItems}}`)],
    forbidden: [NOT_STUDENT_FOUND],
  }),
  one("h-019", "prioritization", HEALTH, "Where should I start this morning?", {
    family: ["my_work", "queue", "ranking"],
    resolvedStudentId: null,
    facts: [f("her queue", `{{num:${ST(HEALTH)}.openItems}}|{{num~:${ST(HEALTH)}.overdueItems}}|overdue|urgent`), NOT_ASK_STUDENT, NO_READ_FAIL],
  }),
  one("h-020", "refusal_unknown", DIRECTOR, "Rate Ada Ashgrove's performance out of ten.", {
    family: ["refusal", "staff"],
    resolvedStudentId: null,
    facts: [f("declines a score", "don'?t|can'?t|cannot|no (?:rating|score|performance)|not (?:something|tracked|available|rate)|isn'?t (?:something|tracked)|won'?t")],
    forbidden: [{ desc: "invents a rating", pattern: "\\b\\d+(?:\\.\\d)?\\s?(?:/|out of)\\s?10\\b" }],
  }),
  one("h-021", "refusal_unknown", DIRECTOR, "Move Bianca Kettleby to Thaddeus Crane's caseload.", {
    family: ["refusal"],
    facts: [f("read-only", "read-only|can'?t|cannot|unable|not able|don'?t")],
    forbidden: [{ desc: "claims moved", pattern: "has been moved|is now (?:on|assigned)|I(?:'ve| have) (?:moved|reassigned)" }],
  }),
  {
    id: "h-022",
    category: "follow_up",
    actor: DIRECTOR,
    conversation: true,
    turns: [
      { question: "What's Lucia Oakenshaw's caseload like?", expect: { family: ["staff"], resolvedStudentId: null, facts: [f("advisees", `{{num:${ST("SYN-ADV-013")}.primaryAdvisees}}`)] } },
      { question: "Does she have anything open in the next two weeks?", expect: { family: ["staff"], resolvedStudentId: null, facts: [f("no slots", `{{num~:${ST("SYN-ADV-013")}.openSlots14d}}|no open|not have any open|none|nothing`)] } },
    ],
  },
  {
    id: "h-023",
    category: "follow_up",
    actor: VP,
    conversation: true,
    turns: [
      { question: "How many items are overdue across the whole board?", expect: { family: ["queue"], resolvedStudentId: null, facts: [f("overdue", "{{num~:queue.overdue}}")] } },
      { question: "Which team owns most of them?", expect: { family: ["queue", "department"], resolvedStudentId: null, facts: [f("component", "{{gt:queue.mostOverdueComponent}}")] } },
    ],
  },
  one("h-024", "ambiguous_names", DIRECTOR, "Bring up Ada Ashgrove.", {
    family: ["staff", "student", "refusal"],
    resolvedStudentId: null,
    facts: [f("mentions the adviser or asks", "adviser|advisor|staff|which (?:one|do you mean)")],
  }),
  one("h-025", "edge_cases", DIRECTOR, "Which advisers still have students even though they've left or are away?", {
    family: ["team", "department", "staff"],
    resolvedStudentId: null,
    facts: [f("Quentin", "Quentin Zephyrine"), f("Junia", "Junia Pemberwell")],
  }),
];
