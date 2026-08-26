/**
 * Staff Edward university benchmark — case bank.
 *
 * Every expected fact is a `{{num:…}}`, `{{date:…}}` or `{{gt:…}}` template
 * over ground-truth.json (SQL over the product's own database), so the suite
 * never encodes an author's belief about the answer. Wording is not graded;
 * facts, entity resolution, forbidden claims and (for diagnosis) the routing
 * family are.
 *
 * `actor` is the persona signed in for the case (a staff external ref).
 * `family` / `toolFamily` are diagnostic: they tell the failure classifier
 * which routing family the question belongs to. `productGap` marks cases
 * whose best answer is an honest "not modelled".
 */

const DIRECTOR = "SYN-STF-ADV-DIR"; // Leandro Hartigan, Director of Academic Advising
const ELENA = "SYN-ADV-012"; // overloaded adviser
const VERA = "SYN-ADV-003"; // falling behind
const PRIYA = "SYN-STF-ADM-AD"; // Admissions associate director
const REGISTRAR = "SYN-STF-REG-DIR";
const EVALUATOR = "SYN-STF-REG-EV2"; // Camila Okonkwo, on vacation
const DSO1 = "SYN-STF-ISS-DSO1"; // Beatrix Zaragoza
const ISS_DIR = "SYN-STF-ISS-DIR";
const HANA = "SYN-ADV-001";
const VP = "SYN-STF-VP";

const S = (ref) => `students.${ref}`;
const ST = (ref) => `staff.byRef.${ref}`;

const f = (desc, pattern, critical = true) => ({ desc, pattern, critical });
const soft = (desc, pattern) => ({ desc, pattern, critical: false });
const NOT_ASK_STUDENT = f(
  "does not ask which student",
  "^(?!.*tell me which student you mean)",
);
const NOT_STUDENT_FOUND = { desc: "not a roster hit", pattern: "students? matching" };
const NO_READ_FAIL = f("no failed read", "^(?!.*couldn'?t (?:read|check|run))");

function one(id, category, actor, question, expect, extra = {}) {
  return { id, category, actor, turns: [{ question, expect }], ...extra };
}

export const CASES = [
  // ── individual students ───────────────────────────────────────────────
  one("u-st-001", "student", DIRECTOR, "What is blocking Ingrid Thistlebrook?", {
    family: ["student"],
    resolvedStudentId: `gt:${S("SYN-000004")}.id`,
    facts: [f("deposit blocker", "deposit"), f("transcript", "transcript")],
    forbidden: [{ desc: "invents holds", pattern: "three holds|3 holds" }],
  }),
  one("u-st-002", "student", DIRECTOR, "Who is Tobias Quillfeather's adviser?", {
    family: ["student"],
    resolvedStudentId: `gt:${S("SYN-000001")}.id`,
    facts: [f("adviser name", `{{gt:${S("SYN-000001")}.adviserName}}`)],
  }),
  one("u-st-003", "student", DIRECTOR, "Who is the adviser for student SYN-000034, and are they still here?", {
    family: ["student"],
    resolvedStudentId: `gt:${S("SYN-000034")}.id`,
    facts: [
      f("adviser name", `{{gt:${S("SYN-000034")}.adviserName}}`),
      f("departed", "departed|no longer|left the university|has left|inactive"),
    ],
  }),
  one("u-st-004", "student", DIRECTOR, "When is Tobias Quillfeather's next appointment and who is it with?", {
    family: ["student"],
    resolvedStudentId: `gt:${S("SYN-000001")}.id`,
    facts: [
      f("date", `{{date:${S("SYN-000001")}.nextAppointmentDate}}`),
      f("with", `{{gt:${S("SYN-000001")}.nextAppointmentWith}}`),
    ],
    forbidden: [{ desc: "reports a completed one as next", pattern: "already been completed" }],
  }),
  one("u-st-005", "student", DIRECTOR, "Does Georgina Underhollow have any open work items?", {
    family: ["student"],
    resolvedStudentId: `gt:${S("SYN-000009")}.id`,
    facts: [f("none open", "\\bno\\b|none|0 open|not in the action center|no open")],
    forbidden: [{ desc: "invents an item", pattern: "\\byes\\b.{0,40}(?:open|item)" }],
  }),
  one("u-st-006", "student", DIRECTOR, "Pull up SYN-000007.", {
    family: ["student"],
    resolvedStudentId: `gt:${S("SYN-000007")}.id`,
    facts: [f("name", "Ines Calderwood|Ines"), f("program", `{{gt:${S("SYN-000007")}.program}}`)],
  }),
  one("u-st-007", "student", DIRECTOR, "Has Ines Calderwood written to us, and what did she ask about?", {
    family: ["student"],
    resolvedStudentId: `gt:${S("SYN-000007")}.id`,
    facts: [f("inquiry subject", "I-20|verification")],
    forbidden: [{ desc: "denies the inquiry", pattern: "no (?:recorded )?(?:inquir|support|message|request)" }],
  }),
  one("u-st-008", "student", DIRECTOR, "Which counselors are assigned to Wren Halloway?", {
    family: ["student"],
    resolvedStudentId: `gt:${S("SYN-000000")}.id`,
    facts: [
      f("admissions counselor", `{{gt:${S("SYN-000000")}.counselors.0.name}}`),
      f("primary adviser", `{{gt:${S("SYN-000000")}.adviserName}}`),
    ],
  }),
  one("u-st-009", "student", DIRECTOR, "Who owns Ingrid Thistlebrook's open work and what is blocking her?", {
    family: ["student"],
    resolvedStudentId: `gt:${S("SYN-000004")}.id`,
    facts: [
      f("no open staff work", "no open (?:staff )?work|not in the action center|no (?:staff )?work items|0 open"),
      f("deposit blocker", "deposit"),
    ],
  }),

  // ── cohorts ───────────────────────────────────────────────────────────
  one("u-co-001", "cohort", DIRECTOR, "How many students have unpaid deposits?", {
    family: ["cohort"],
    resolvedStudentId: null,
    facts: [f("count", "{{num:cohorts.unpaid_deposit}}")],
    forbidden: [{ desc: "roster total as answer", pattern: "^(?:(?!{{num:cohorts.unpaid_deposit}}).)*{{num:cohorts.students}} students (?:have|with)" }],
  }),
  one("u-co-002", "cohort", DIRECTOR, "How many deposited students have no academic adviser?", {
    family: ["cohort"],
    resolvedStudentId: null,
    facts: [f("count", "{{num:cohorts.deposited_no_primary_adviser}}")],
    forbidden: [{ desc: "claims no adviser model", pattern: "no (?:formal )?(?:advisor|adviser|caseload) model" }],
  }),
  one("u-co-003", "cohort", DIRECTOR, "How many students are assigned to an adviser who has left the university?", {
    family: ["cohort"],
    resolvedStudentId: null,
    facts: [f("count", "{{num:cohorts.adviser_departed}}"), soft("names the adviser", "Quentin Zephyrine")],
  }),
  one("u-co-004", "cohort", DIRECTOR, "Which students have deposited but still have an overdue requirement?", {
    family: ["cohort"],
    resolvedStudentId: null,
    facts: [f("count", "{{num:cohorts.deposited_with_overdue_requirement}}")],
  }),
  one("u-co-005", "cohort", DIRECTOR, "How many admitted students still have no primary adviser?", {
    family: ["cohort"],
    resolvedStudentId: null,
    facts: [f("count", "{{num:cohorts.accepted_no_primary_adviser}}")],
  }),
  one("u-co-006", "cohort", DIRECTOR, "How many students have an adviser who is currently on leave?", {
    family: ["cohort"],
    resolvedStudentId: null,
    facts: [f("count", "{{num:cohorts.adviser_on_leave}}"), soft("names the adviser", "Junia Pemberwell")],
  }),
  one("u-co-007", "cohort", DIRECTOR, "Break down the students with unpaid deposits by program.", {
    family: ["cohort"],
    resolvedStudentId: null,
    facts: [f("total", "{{num:cohorts.unpaid_deposit}}"), f("program grouping", "program")],
  }),
  one("u-co-008", "cohort", DIRECTOR, "How many students currently have open Action Center work?", {
    family: ["cohort", "queue"],
    resolvedStudentId: null,
    facts: [f("count", "{{num:cohorts.with_open_work}}")],
  }),

  // ── the signed-in staff member's own work ─────────────────────────────
  one("u-mw-001", "my_work", VERA, "What's on my plate today?", {
    family: ["my_work", "queue"],
    resolvedStudentId: null,
    facts: [f("open items", `{{num:${ST(VERA)}.openItems}}`), NO_READ_FAIL],
  }),
  one("u-mw-002", "my_work", ELENA, "How many students do I advise, and am I over my cap?", {
    family: ["my_work", "staff"],
    resolvedStudentId: null,
    facts: [
      f("advisees", `{{num:${ST(ELENA)}.primaryAdvisees}}`),
      f("cap", `{{num:${ST(ELENA)}.caseloadCap}}`),
      f("over cap", "over|above|exceed"),
    ],
  }),
  one("u-mw-003", "my_work", DIRECTOR, "Show me the overdue items assigned to me.", {
    family: ["my_work", "queue"],
    resolvedStudentId: null,
    facts: [f("overdue count", `{{num~:${ST(DIRECTOR)}.overdueItems}}`), NOT_ASK_STUDENT, NO_READ_FAIL],
  }),
  one("u-mw-004", "my_work", VERA, "Which of my appointments still need an outcome recorded?", {
    family: ["my_work", "staff"],
    resolvedStudentId: null,
    facts: [f("awaiting outcome", `{{num:${ST(VERA)}.awaitingOutcome}}`)],
  }),
  one("u-mw-005", "my_work", EVALUATOR, "How many of my open items are overdue?", {
    family: ["my_work", "queue"],
    resolvedStudentId: null,
    facts: [
      f("overdue", `{{num~:${ST(EVALUATOR)}.overdueItems}}`),
      soft("open", `{{num:${ST(EVALUATOR)}.openItems}}`),
    ],
  }),
  one("u-mw-006", "my_work", ELENA, "When is my next open slot?", {
    family: ["my_work", "staff"],
    resolvedStudentId: null,
    facts: [f("next slot date", `{{date:${ST(ELENA)}.nextOpenSlotAt}}`)],
  }),
  one("u-mw-007", "my_work", PRIYA, "Who do I report to, and how many people report to me?", {
    family: ["my_work", "staff", "team"],
    resolvedStudentId: null,
    facts: [
      f("manager", `{{gt:${ST(PRIYA)}.managerName}}`),
      f("direct reports", `{{num:${ST(PRIYA)}.directReports}}`),
    ],
  }),
  one("u-mw-008", "my_work", DSO1, "What should I work on first?", {
    family: ["my_work", "queue", "ranking"],
    resolvedStudentId: null,
    facts: [f("her own queue", `{{num:${ST(DSO1)}.openItems}}|{{num~:${ST(DSO1)}.overdueItems}}`), NO_READ_FAIL],
  }),
  one("u-mw-009", "my_work", VERA, "Do I have any appointments today?", {
    family: ["my_work", "staff"],
    resolvedStudentId: null,
    facts: [f("today count", `{{num~:${ST(VERA)}.scheduledToday}}`)],
  }),
  one("u-mw-010", "my_work", VP, "What's my role here and who reports to me?", {
    family: ["my_work", "staff", "team"],
    resolvedStudentId: null,
    facts: [f("title", "Vice President"), f("reports", `{{num:${ST(VP)}.directReports}}`)],
  }),

  // ── another staff member ──────────────────────────────────────────────
  one("u-os-001", "other_staff", DIRECTOR, "How many open work items does Vera Jessamy have?", {
    family: ["staff"],
    resolvedStudentId: null,
    facts: [f("open items", `{{num:${ST(VERA)}.openItems}}`)],
    forbidden: [NOT_STUDENT_FOUND],
  }),
  one("u-os-002", "other_staff", REGISTRAR, "Is Camila Okonkwo out this week?", {
    family: ["staff"],
    resolvedStudentId: null,
    facts: [f("vacation", "vacation|away|out|absent|time off"), f("until", `{{date:${ST(EVALUATOR)}.currentAbsenceEnds}}`)],
    forbidden: [NOT_STUDENT_FOUND],
  }),
  one("u-os-003", "other_staff", DIRECTOR, "Who is Elena Larkspur's manager?", {
    family: ["staff"],
    resolvedStudentId: null,
    facts: [f("manager", `{{gt:${ST(ELENA)}.managerName}}`)],
    forbidden: [NOT_STUDENT_FOUND],
  }),
  one("u-os-004", "other_staff", DIRECTOR, "Is Junia Pemberwell available this week?", {
    family: ["staff"],
    resolvedStudentId: null,
    facts: [f("on leave", "leave"), f("until", `{{date:${ST("SYN-ADV-005")}.leaveUntil}}`)],
    forbidden: [NOT_STUDENT_FOUND],
  }),
  one("u-os-005", "other_staff", DIRECTOR, "Tell me about Thaddeus Crane.", {
    family: ["staff"],
    resolvedStudentId: null,
    facts: [
      f("title", `{{gt:${ST("SYN-STF-ADV-025")}.title}}`),
      f("advisees", `{{num:${ST("SYN-STF-ADV-025")}.primaryAdvisees}}`),
      soft("start date", `{{date:${ST("SYN-STF-ADV-025")}.startedAt}}`),
    ],
    forbidden: [NOT_STUDENT_FOUND],
  }),
  one("u-os-006", "other_staff", DIRECTOR, "Which of Vera Jessamy's work items have been in progress for more than a week?", {
    family: ["staff", "queue"],
    resolvedStudentId: null,
    facts: [f("count", `{{num:${ST(VERA)}.inProgressOverWeek}}`), f("in progress", "in progress")],
  }),
  one("u-os-007", "other_staff", DIRECTOR, "Does Quentin Zephyrine still have students assigned to him?", {
    family: ["staff"],
    resolvedStudentId: null,
    facts: [f("count", `{{num:${ST("SYN-ADV-008")}.primaryAdvisees}}`), f("departed", "departed|left|no longer")],
  }),
  one("u-os-008", "other_staff", DIRECTOR, "What is Hana Dunmire's caseload?", {
    family: ["staff"],
    resolvedStudentId: null,
    facts: [f("advisees", `{{num:${ST(HANA)}.primaryAdvisees}}`), soft("cap", `{{num:${ST(HANA)}.caseloadCap}}`)],
  }),
  one("u-os-009", "other_staff", DIRECTOR, "What does Beatrix Zaragoza do and how much is on her queue?", {
    family: ["staff"],
    resolvedStudentId: null,
    facts: [f("title", "DSO|International Student Adviser"), f("open items", `{{num:${ST(DSO1)}.openItems}}`)],
  }),

  // ── adviser caseload / capacity ───────────────────────────────────────
  one("u-cc-001", "caseload_capacity", DIRECTOR, "How many students does Elena Larkspur advise?", {
    family: ["staff"],
    resolvedStudentId: null,
    facts: [f("advisees", `{{num:${ST(ELENA)}.primaryAdvisees}}`), soft("cap", `{{num:${ST(ELENA)}.caseloadCap}}`)],
    forbidden: [NOT_STUDENT_FOUND, { desc: "claims no adviser model", pattern: "no (?:formal )?(?:advisor|adviser|caseload) model" }],
  }),
  one("u-cc-002", "caseload_capacity", DIRECTOR, "Which advisers are over their caseload cap?", {
    family: ["team", "staff", "department"],
    resolvedStudentId: null,
    facts: [f("over-cap adviser", "{{gt:staff.overCapAdvisers.0}}")],
  }),
  one("u-cc-003", "caseload_capacity", DIRECTOR, "Who on the advising team has spare capacity to take on students?", {
    family: ["team", "staff", "department"],
    resolvedStudentId: null,
    facts: [f("spare adviser", "{{gt:staff.spareCapacityAdvisers.0}}")],
  }),
  one("u-cc-004", "caseload_capacity", DIRECTOR, "Which advisers have no open appointment slots in the next two weeks?", {
    family: ["team", "staff", "department"],
    resolvedStudentId: null,
    facts: [f("Elena", "Elena Larkspur"), f("Lucia", "Lucia Oakenshaw", false)],
  }),
  one("u-cc-005", "caseload_capacity", DIRECTOR, "How is the advising team's capacity looking?", {
    family: ["team", "department"],
    resolvedStudentId: null,
    facts: [f("over cap", "Elena Larkspur"), f("on leave", "Junia Pemberwell"), f("departed", "Quentin Zephyrine")],
  }),
  one("u-cc-006", "caseload_capacity", DIRECTOR, "How many students does Junia Pemberwell have while she's on leave?", {
    family: ["staff"],
    resolvedStudentId: null,
    facts: [f("advisees", `{{num:${ST("SYN-ADV-005")}.primaryAdvisees}}`)],
  }),
  one("u-cc-007", "caseload_capacity", DIRECTOR, "Which active adviser has the lightest caseload relative to their cap?", {
    family: ["team", "staff", "department"],
    resolvedStudentId: null,
    facts: [f("lowest", "{{gt:staff.lowestLoadAdviser.name}}")],
  }),
  one("u-cc-008", "caseload_capacity", DIRECTOR, "How many advisees does Vera Jessamy have, and how many of them have overdue work?", {
    family: ["staff"],
    resolvedStudentId: null,
    facts: [
      f("advisees", `{{num:${ST(VERA)}.primaryAdvisees}}`),
      f("with overdue work", `{{num~:${ST(VERA)}.caseload.with_overdue_work}}`),
    ],
  }),
  one("u-cc-009", "caseload_capacity", DIRECTOR, "Is Lucia Oakenshaw close to her cap?", {
    family: ["staff"],
    resolvedStudentId: null,
    facts: [
      f("advisees", `{{num:${ST("SYN-ADV-013")}.primaryAdvisees}}`),
      f("cap", `{{num:${ST("SYN-ADV-013")}.caseloadCap}}`),
    ],
  }),

  // ── department / team operations ──────────────────────────────────────
  one("u-do-001", "department_ops", REGISTRAR, "How many document reviews are overdue in the Registrar's office?", {
    family: ["department", "queue"],
    resolvedStudentId: null,
    facts: [f("count", "{{num~:queue.byComponent.Registrar.overdue_document_reviews}}")],
  }),
  one("u-do-002", "department_ops", VP, "Which department has the most unassigned open work?", {
    family: ["department", "queue"],
    resolvedStudentId: null,
    facts: [f("component", "{{gt:queue.mostUnassignedComponent}}")],
  }),
  one("u-do-003", "department_ops", REGISTRAR, "Who in the Registrar's office is out this week?", {
    family: ["department", "team", "staff"],
    resolvedStudentId: null,
    facts: [f("Camila", "Camila Okonkwo")],
  }),
  one("u-do-004", "department_ops", VP, "How is Financial Aid doing on its queue?", {
    family: ["department", "queue"],
    resolvedStudentId: null,
    facts: [
      f("open", "{{num:queue.byComponent.Financial Aid.open}}"),
      soft("overdue", "{{num~:queue.byComponent.Financial Aid.overdue}}"),
    ],
  }),
  one("u-do-005", "department_ops", ISS_DIR, "How many open items does International Student Services have, and how many are overdue?", {
    family: ["department", "queue"],
    resolvedStudentId: null,
    facts: [
      f("open", "{{num:queue.byComponent.International Student Services.open}}"),
      f("overdue", "{{num~:queue.byComponent.International Student Services.overdue}}"),
    ],
  }),
  one("u-do-006", "department_ops", VP, "Which staff members have the most overdue work?", {
    family: ["queue", "department", "staff"],
    resolvedStudentId: null,
    facts: [f("top assignee", "{{gt:queue.mostOverdueAssignee.name}}"), soft("count", "{{num~:queue.mostOverdueAssignee.overdue}}")],
  }),
  one("u-do-007", "department_ops", DIRECTOR, "Is anyone in Academic Advising on leave or departed?", {
    family: ["department", "team"],
    resolvedStudentId: null,
    facts: [f("on leave", "Junia Pemberwell"), f("departed", "Quentin Zephyrine")],
  }),
  one("u-do-008", "department_ops", VP, "How many staff work in Housing, and how many open items does that team have?", {
    family: ["department"],
    resolvedStudentId: null,
    facts: [f("staff", "{{num:departments.Housing.staff}}"), f("open", "{{num:departments.Housing.open_items}}")],
  }),

  // ── Action Center ─────────────────────────────────────────────────────
  one("u-ac-001", "action_center", DIRECTOR, "How many open work items are unassigned right now?", {
    family: ["queue"],
    resolvedStudentId: null,
    facts: [f("count", "{{num:queue.unassigned}}"), NO_READ_FAIL],
  }),
  one("u-ac-002", "action_center", DIRECTOR, "How many items in the Action Center are urgent?", {
    family: ["queue"],
    resolvedStudentId: null,
    facts: [f("count", "{{num:queue.urgent}}"), NO_READ_FAIL],
  }),
  one("u-ac-003", "action_center", DIRECTOR, "What's at the top of the Action Center right now?", {
    family: ["queue"],
    resolvedStudentId: null,
    facts: [f("head key", "{{gt:queue.head.key}}")],
  }),
  one("u-ac-004", "action_center", DIRECTOR, "How many items are sitting in progress with no update for more than 10 days?", {
    family: ["queue"],
    resolvedStudentId: null,
    facts: [f("stale count", "{{num~:queue.stale_in_progress}}")],
  }),
  one("u-ac-005", "action_center", DIRECTOR, "How many transcript items are open in the Action Center?", {
    family: ["queue"],
    resolvedStudentId: null,
    facts: [f("count", "{{num:queue.transcriptTopicOpen}}")],
  }),
  one("u-ac-006", "action_center", DIRECTOR, "Show me the unassigned items in Housing.", {
    family: ["queue"],
    resolvedStudentId: null,
    facts: [f("count", "{{num:queue.byComponent.Housing.unassigned}}")],
  }),
  one("u-ac-007", "action_center", DIRECTOR, "What happened on {{gt:queue.head.key}}?", {
    family: ["queue"],
    facts: [f("student", "{{gt:queue.head.student}}"), NO_READ_FAIL],
  }),
  one("u-ac-008", "action_center", DIRECTOR, "How many open items are there in total, and how many are escalated?", {
    family: ["queue"],
    resolvedStudentId: null,
    facts: [f("open", "{{num:queue.open}}"), f("escalated", "{{num:queue.escalated}}")],
  }),

  // ── inquiries ─────────────────────────────────────────────────────────
  one("u-in-001", "inquiries", DIRECTOR, "How many student requests are still awaiting a first reply?", {
    family: ["inquiries"],
    resolvedStudentId: null,
    facts: [f("count", "{{num:inquiries.awaiting_first_reply}}")],
    forbidden: [{ desc: "roster size as the answer", pattern: "{{num:cohorts.students}} (?:student )?requests" }],
  }),
  one("u-in-002", "inquiries", DIRECTOR, "Which is the oldest unanswered inquiry?", {
    family: ["inquiries"],
    resolvedStudentId: null,
    facts: [f("oldest", "{{gt:inquiries.oldestAwaiting.subject}}|{{gt:inquiries.oldestAwaiting.student}}")],
  }),
  one("u-in-003", "inquiries", DIRECTOR, "How many inquiries are waiting on the student?", {
    family: ["inquiries"],
    resolvedStudentId: null,
    facts: [f("count", "{{num:inquiries.waiting_on_student}}")],
  }),
  one("u-in-004", "inquiries", DIRECTOR, "List open inquiries that have not been answered yet.", {
    family: ["inquiries"],
    resolvedStudentId: null,
    facts: [f("count", "{{num:inquiries.awaiting_first_reply}}"), f("inquiries", "inquir|request")],
  }),
  one("u-in-005", "inquiries", DIRECTOR, "Are any unanswered inquiries more than 24 hours old?", {
    family: ["inquiries"],
    resolvedStudentId: null,
    facts: [f("count", "{{num~:inquiries.awaiting_over_24h}}")],
  }),
  one("u-in-006", "inquiries", DIRECTOR, "How many open inquiries have nobody assigned?", {
    family: ["inquiries"],
    resolvedStudentId: null,
    facts: [f("count", "{{num:inquiries.unassigned_open}}")],
  }),
  one("u-in-007", "inquiries", DIRECTOR, "What are the unanswered inquiries mostly about?", {
    family: ["inquiries"],
    resolvedStudentId: null,
    facts: [f("top topic", "support"), soft("count", "{{num:inquiries.awaitingByTopic.support}}")],
  }),

  // ── deadlines ─────────────────────────────────────────────────────────
  one("u-dl-001", "deadlines", DIRECTOR, "How many open items are due today?", {
    family: ["queue"],
    resolvedStudentId: null,
    facts: [f("count", "{{num~:queue.due_today}}")],
  }),
  one("u-dl-002", "deadlines", DIRECTOR, "How much Action Center work is due in the next seven days?", {
    family: ["queue"],
    resolvedStudentId: null,
    facts: [f("count", "{{num~:queue.due_next_7_days}}")],
  }),
  one("u-dl-003", "deadlines", DIRECTOR, "What deadlines does Ines Calderwood have coming up?", {
    family: ["student"],
    resolvedStudentId: `gt:${S("SYN-000007")}.id`,
    facts: [f("deposit deadline", "deposit"), f("due language", "due|deadline")],
  }),
  one("u-dl-004", "deadlines", ELENA, "What deadlines should my team care about today?", {
    family: ["queue", "my_work", "team"],
    resolvedStudentId: null,
    facts: [f("due", "due|deadline|overdue"), NOT_ASK_STUDENT, NO_READ_FAIL],
  }),
  one("u-dl-005", "deadlines", DIRECTOR, "How many of the overdue items are in Academic Advising?", {
    family: ["queue", "department"],
    resolvedStudentId: null,
    facts: [f("count", "{{num~:queue.byComponent.Academic Advising.overdue}}")],
  }),

  // ── onboarding blockers ───────────────────────────────────────────────
  one("u-ob-001", "onboarding_blockers", DIRECTOR, "What is Tobias Quillfeather waiting on from us?", {
    family: ["student"],
    resolvedStudentId: `gt:${S("SYN-000001")}.id`,
    facts: [f("transcript", "transcript"), f("immunization", "immuni")],
    forbidden: [{ desc: "claims transcript received", pattern: "transcript (?:has been|was|is) (?:received|accepted)" }],
  }),
  one("u-ob-002", "onboarding_blockers", DIRECTOR, "Marisol Fennwick says she already paid her deposit. What's actually blocking her?", {
    family: ["student"],
    resolvedStudentId: `gt:${S("SYN-000002")}.id`,
    facts: [f("deposit not posted", "deposit"), f("transcript", "transcript")],
  }),
  one("u-ob-003", "onboarding_blockers", DIRECTOR, "How many of Ximena Calderwood's advisees still haven't completed their advising meeting?", {
    family: ["staff"],
    resolvedStudentId: null,
    facts: [f("count", `{{num:${ST("SYN-ADV-009")}.caseload.advising_not_completed}}`)],
  }),
  one("u-ob-004", "onboarding_blockers", DIRECTOR, "Is Wren Halloway all set for the fall?", {
    family: ["student"],
    resolvedStudentId: `gt:${S("SYN-000000")}.id`,
    facts: [f("nothing outstanding", "nothing (?:is )?(?:outstanding|blocking|left)|all set|no open|complete|0 open|no blockers|on track")],
  }),
  one("u-ob-005", "onboarding_blockers", DIRECTOR, "What are the most common blockers across the class right now?", {
    family: ["cohort"],
    resolvedStudentId: null,
    facts: [f("requirement names", "transcript|immuni|deposit|housing|orientation|verification")],
  }),

  // ── appointments ──────────────────────────────────────────────────────
  one("u-ap-001", "appointments", VERA, "What's on my calendar this week?", {
    family: ["my_work", "staff"],
    resolvedStudentId: null,
    facts: [f("scheduled count", `{{num~:${ST(VERA)}.scheduledNext7}}`)],
  }),
  one("u-ap-002", "appointments", DIRECTOR, "How many appointments does Elena Larkspur have today?", {
    family: ["staff"],
    resolvedStudentId: null,
    facts: [f("today", `{{num~:${ST(ELENA)}.scheduledToday}}`)],
    forbidden: [NOT_STUDENT_FOUND],
  }),
  one("u-ap-003", "appointments", DIRECTOR, "Who is Tobias Quillfeather meeting on {{gt:students.SYN-000001.nextAppointmentDate}}?", {
    family: ["student"],
    resolvedStudentId: `gt:${S("SYN-000001")}.id`,
    facts: [f("with", `{{gt:${S("SYN-000001")}.nextAppointmentWith}}`)],
  }),
  one("u-ap-004", "appointments", DIRECTOR, "Which advisers have past appointments they never closed out?", {
    family: ["team", "staff", "department"],
    resolvedStudentId: null,
    facts: [f("Vera", "{{gt:staff.unclosedAppointments.0.name}}"), soft("count", "{{num:staff.unclosedAppointments.0.awaitingOutcome}}")],
  }),
  one("u-ap-005", "appointments", DIRECTOR, "Does Georgina Underhollow have an upcoming appointment?", {
    family: ["student"],
    resolvedStudentId: `gt:${S("SYN-000009")}.id`,
    facts: [f("no upcoming", "\\bno\\b|none|nothing (?:scheduled|upcoming|booked)|not have")],
    forbidden: [{ desc: "completed reported as upcoming", pattern: "already been completed" }],
  }),
  one("u-ap-006", "appointments", HANA, "How many appointments do I have in the next two weeks?", {
    family: ["my_work", "staff"],
    resolvedStudentId: null,
    facts: [f("count", `{{num~:${ST(HANA)}.scheduledNext14}}`)],
  }),

  // ── scheduling / availability ─────────────────────────────────────────
  one("u-sc-001", "scheduling", DIRECTOR, "When is Elena Larkspur's next open advising slot?", {
    family: ["staff"],
    resolvedStudentId: null,
    facts: [f("date", `{{date:${ST(ELENA)}.nextOpenSlotAt}}`)],
    forbidden: [NOT_STUDENT_FOUND, { desc: "claims no availability model", pattern: "no (?:availability|calendar) (?:model|data)|don'?t have (?:availability|calendar)" }],
  }),
  one("u-sc-002", "scheduling", DIRECTOR, "When is Ximena Calderwood's next open slot?", {
    family: ["staff"],
    resolvedStudentId: null,
    facts: [f("date", `{{date:${ST("SYN-ADV-009")}.nextOpenSlotAt}}`)],
  }),
  one("u-sc-003", "scheduling", DIRECTOR, "Can a student book Junia Pemberwell right now?", {
    family: ["staff"],
    resolvedStudentId: null,
    facts: [f("no", "\\bno\\b|cannot|can'?t|not bookable|unavailable"), f("leave until", `{{date:${ST("SYN-ADV-005")}.leaveUntil}}`)],
  }),
  one("u-sc-004", "scheduling", DIRECTOR, "Which days does Vera Jessamy hold appointment hours?", {
    family: ["staff"],
    resolvedStudentId: null,
    facts: [f("Mon", "Mon"), f("Wed", "Wed"), f("Fri", "Fri")],
    forbidden: [{ desc: "invents Tuesday", pattern: "Tue" }],
  }),
  one("u-sc-005", "scheduling", DIRECTOR, "How many open slots does Ximena Calderwood have in the next two weeks?", {
    family: ["staff"],
    resolvedStudentId: null,
    facts: [f("count", `{{num~:${ST("SYN-ADV-009")}.openSlots14d}}`)],
  }),
  one("u-sc-006", "scheduling", DIRECTOR, "Is there anyone on the advising team a student could see tomorrow?", {
    family: ["team", "staff", "department"],
    resolvedStudentId: null,
    facts: [f("names an adviser with slots", "Ximena Calderwood|Hana Dunmire|Ada Ashgrove|Thaddeus Crane|Vera Jessamy|Omar Glimmerly|Caleb Mossbank|Camila Stonebrook|Emre Fennwick|Sven Ravensworth|Gustav Yarrowby|Noor Brightwater|Greta Everlyn|Ugo Kettleby|Bianca Netherby|Ivo Whitlowe|Freya Jokinen|Jolene Vellacourt|Liora Ironwood|Nadia Halloway|Bruno Thistlebrook")],
  }),

  // ── comparing groups or staff ─────────────────────────────────────────
  one("u-cp-001", "comparison", DIRECTOR, "Compare Vera Jessamy and Ada Ashgrove.", {
    family: ["staff"],
    resolvedStudentId: null,
    facts: [
      f("Vera open", `{{num:${ST(VERA)}.openItems}}`),
      f("Ada open", `{{num:${ST("SYN-ADV-000")}.openItems}}`),
    ],
  }),
  one("u-cp-002", "comparison", DIRECTOR, "Who has more advisees, Elena Larkspur or Lucia Oakenshaw?", {
    family: ["staff"],
    resolvedStudentId: null,
    facts: [
      f("Elena count", `{{num:${ST(ELENA)}.primaryAdvisees}}`),
      f("Lucia count", `{{num:${ST("SYN-ADV-013")}.primaryAdvisees}}`),
    ],
  }),
  one("u-cp-003", "comparison", VP, "Which has more overdue items, the Registrar or Financial Aid?", {
    family: ["department", "queue"],
    resolvedStudentId: null,
    facts: [
      f("Registrar overdue", "{{num~:queue.byComponent.Registrar.overdue}}"),
      f("FA overdue", "{{num~:queue.byComponent.Financial Aid.overdue}}"),
    ],
  }),
  one("u-cp-004", "comparison", ISS_DIR, "Compare the workloads of the two DSOs.", {
    family: ["staff", "team", "department"],
    resolvedStudentId: null,
    facts: [
      f("Beatrix", `{{num:${ST(DSO1)}.openItems}}`),
      f("Matthias", `{{num:${ST("SYN-STF-ISS-DSO2")}.openItems}}`),
    ],
  }),
  one("u-cp-005", "comparison", DIRECTOR, "Who has the bigger caseload, Hana Dunmire or Vera Jessamy?", {
    family: ["staff"],
    resolvedStudentId: null,
    facts: [f("Hana", `{{num:${ST(HANA)}.primaryAdvisees}}`), f("Vera", `{{num:${ST(VERA)}.primaryAdvisees}}`)],
  }),

  // ── students meeting multiple criteria ────────────────────────────────
  one("u-mc-001", "multi_criteria", DIRECTOR, "List Elena Larkspur's advisees who haven't completed advising and have no upcoming appointment.", {
    family: ["staff"],
    resolvedStudentId: null,
    facts: [f("count", `{{num:${ST(ELENA)}.caseload.advising_incomplete_no_booking}}`)],
  }),
  one("u-mc-002", "multi_criteria", DIRECTOR, "How many of Quentin Zephyrine's students have paid their deposit?", {
    family: ["staff"],
    resolvedStudentId: null,
    facts: [f("count", `{{num:${ST("SYN-ADV-008")}.caseload.deposited}}`)],
  }),
  one("u-mc-003", "multi_criteria", DIRECTOR, "Which students have deposited but have no adviser at all?", {
    family: ["cohort"],
    resolvedStudentId: null,
    facts: [f("count", "{{num:cohorts.deposited_no_primary_adviser}}")],
  }),
  one("u-mc-004", "multi_criteria", DIRECTOR, "Which of Vera Jessamy's advisees have overdue work items?", {
    family: ["staff"],
    resolvedStudentId: null,
    facts: [f("count", `{{num~:${ST(VERA)}.caseload.with_overdue_work}}`)],
  }),
  one("u-mc-005", "multi_criteria", PRIYA, "Show me unassigned urgent items in Admissions.", {
    family: ["queue"],
    resolvedStudentId: null,
    facts: [f("count", "{{num:queue.byComponent.Admissions.unassigned_urgent}}")],
  }),
  one("u-mc-006", "multi_criteria", DIRECTOR, "Which of Junia Pemberwell's advisees have no advising appointment booked?", {
    family: ["staff"],
    resolvedStudentId: null,
    facts: [f("count", `{{num:${ST("SYN-ADV-005")}.caseload.advising_incomplete_no_booking}}`)],
  }),

  // ── summaries ─────────────────────────────────────────────────────────
  one("u-su-001", "summary", VP, "Summarize the International Student Services situation.", {
    family: ["department"],
    resolvedStudentId: null,
    facts: [
      f("open", "{{num:departments.International Student Services.open_items}}"),
      soft("overdue", "{{num~:departments.International Student Services.overdue_items}}"),
    ],
  }),
  one("u-su-002", "summary", DIRECTOR, "Give me a summary of Vera Jessamy's workload.", {
    family: ["staff"],
    resolvedStudentId: null,
    facts: [
      f("open", `{{num:${ST(VERA)}.openItems}}`),
      f("overdue", `{{num~:${ST(VERA)}.overdueItems}}`),
      soft("unclosed appointments", `{{num:${ST(VERA)}.awaitingOutcome}}`),
    ],
  }),
  one("u-su-003", "summary", DIRECTOR, "Catch me up on the Action Center.", {
    family: ["queue"],
    resolvedStudentId: null,
    facts: [f("open", "{{num:queue.open}}"), soft("unassigned", "{{num:queue.unassigned}}"), NO_READ_FAIL],
  }),
  one("u-su-004", "summary", DIRECTOR, "Summarize my team.", {
    family: ["team", "my_work"],
    resolvedStudentId: null,
    facts: [f("Junia", "Junia Pemberwell"), f("Quentin", "Quentin Zephyrine"), f("Elena", "Elena Larkspur")],
  }),
  one("u-su-005", "summary", DIRECTOR, "Summarize Ingrid Thistlebrook's situation in two sentences.", {
    family: ["student"],
    resolvedStudentId: `gt:${S("SYN-000004")}.id`,
    facts: [f("deposit", "deposit"), f("program", `{{gt:${S("SYN-000004")}.program}}`)],
  }),

  // ── operational prioritization ────────────────────────────────────────
  one("u-pr-001", "prioritization", DSO1, "What should I start with this morning?", {
    family: ["my_work", "queue", "ranking"],
    resolvedStudentId: null,
    facts: [f("her queue", `{{num:${ST(DSO1)}.openItems}}|{{num~:${ST(DSO1)}.overdueItems}}|urgent|overdue`), NOT_ASK_STUDENT, NO_READ_FAIL],
  }),
  one("u-pr-002", "prioritization", DIRECTOR, "Which of my direct reports need my attention most?", {
    family: ["team", "my_work"],
    resolvedStudentId: null,
    facts: [f("flagged people", "(?:Vera Jessamy|Elena Larkspur|Junia Pemberwell|Quentin Zephyrine).*(?:Vera Jessamy|Elena Larkspur|Junia Pemberwell|Quentin Zephyrine)")],
  }),
  one("u-pr-003", "prioritization", VP, "Which department should leadership look at first?", {
    family: ["department", "queue"],
    resolvedStudentId: null,
    facts: [f("most overdue component", "{{gt:queue.mostOverdueComponent}}")],
  }),
  one("u-pr-004", "prioritization", REGISTRAR, "What's the biggest risk in my department right now?", {
    family: ["department", "team", "my_work"],
    resolvedStudentId: null,
    facts: [f("evaluator away or overdue", "Camila Okonkwo|{{num~:queue.byComponent.Registrar.overdue}}|overdue")],
  }),
  one("u-pr-005", "prioritization", DIRECTOR, "Which students need attention most urgently right now?", {
    family: ["ranking"],
    resolvedStudentId: null,
    facts: [f("attention", "attention|deadline|blocking|inactive")],
  }),

  // ── follow-ups / shallow multi-turn context ───────────────────────────
  {
    id: "u-fu-001",
    category: "follow_up",
    actor: DIRECTOR,
    conversation: true,
    turns: [
      {
        question: "How many students does Elena Larkspur advise?",
        expect: { family: ["staff"], resolvedStudentId: null, facts: [f("advisees", `{{num:${ST(ELENA)}.primaryAdvisees}}`)] },
      },
      {
        question: "When is her next open slot?",
        expect: { family: ["staff"], resolvedStudentId: null, facts: [f("date", `{{date:${ST(ELENA)}.nextOpenSlotAt}}`)] },
      },
    ],
  },
  {
    id: "u-fu-002",
    category: "follow_up",
    actor: DIRECTOR,
    conversation: true,
    turns: [
      {
        question: "Pull up Tobias Quillfeather.",
        expect: { family: ["student"], resolvedStudentId: `gt:${S("SYN-000001")}.id`, facts: [f("program", `{{gt:${S("SYN-000001")}.program}}`)] },
      },
      {
        question: "Who is his adviser?",
        expect: { family: ["student"], resolvedStudentId: `gt:${S("SYN-000001")}.id`, facts: [f("adviser", `{{gt:${S("SYN-000001")}.adviserName}}`)] },
      },
      {
        question: "Draft a short message to him about his transcript.",
        expect: { family: ["student"], resolvedStudentId: `gt:${S("SYN-000001")}.id`, facts: [f("transcript", "transcript"), NOT_ASK_STUDENT] },
      },
    ],
  },
  {
    id: "u-fu-003",
    category: "follow_up",
    actor: DIRECTOR,
    conversation: true,
    turns: [
      {
        question: "How many open work items are unassigned?",
        expect: { family: ["queue"], resolvedStudentId: null, facts: [f("count", "{{num:queue.unassigned}}")] },
      },
      {
        question: "Which department has the most of those?",
        expect: { family: ["queue", "department"], resolvedStudentId: null, facts: [f("component", "{{gt:queue.mostUnassignedComponent}}")] },
      },
    ],
  },
  {
    id: "u-fu-004",
    category: "follow_up",
    actor: DIRECTOR,
    conversation: true,
    turns: [
      {
        question: "Is Junia Pemberwell available this week?",
        expect: { family: ["staff"], resolvedStudentId: null, facts: [f("leave", "leave")] },
      },
      {
        question: "How many students does she have?",
        expect: { family: ["staff"], resolvedStudentId: null, facts: [f("advisees", `{{num:${ST("SYN-ADV-005")}.primaryAdvisees}}`)] },
      },
    ],
  },
  {
    id: "u-fu-005",
    category: "follow_up",
    actor: DIRECTOR,
    conversation: true,
    turns: [
      {
        question: "What is blocking Ingrid Thistlebrook?",
        expect: { family: ["student"], resolvedStudentId: `gt:${S("SYN-000004")}.id`, facts: [f("deposit", "deposit")] },
      },
      {
        question: "How many students have unpaid deposits?",
        expect: { family: ["cohort"], resolvedStudentId: null, facts: [f("count", "{{num:cohorts.unpaid_deposit}}")] },
      },
    ],
  },
  {
    id: "u-fu-006",
    category: "follow_up",
    actor: DIRECTOR,
    conversation: true,
    turns: [
      {
        question: "How many overdue items does Vera Jessamy have?",
        expect: { family: ["staff"], resolvedStudentId: null, facts: [f("overdue", `{{num~:${ST(VERA)}.overdueItems}}`)] },
      },
      {
        question: "And Ada Ashgrove?",
        expect: { family: ["staff"], resolvedStudentId: null, facts: [f("Ada overdue", `{{num~:${ST("SYN-ADV-000")}.overdueItems}}`)] },
      },
    ],
  },
  {
    id: "u-fu-007",
    category: "follow_up",
    actor: VERA,
    conversation: true,
    turns: [
      {
        question: "How many open items do I have?",
        expect: { family: ["my_work", "queue"], resolvedStudentId: null, facts: [f("open", `{{num:${ST(VERA)}.openItems}}`)] },
      },
      {
        question: "How many of those are overdue?",
        expect: { family: ["my_work", "queue"], resolvedStudentId: null, facts: [f("overdue", `{{num~:${ST(VERA)}.overdueItems}}`)] },
      },
    ],
  },

  // ── ambiguous names ───────────────────────────────────────────────────
  one("u-am-001", "ambiguous_names", DIRECTOR, "Tell me about Elena Larkspur.", {
    family: ["staff", "refusal", "student"],
    resolvedStudentId: null,
    facts: [f("acknowledges the staff member", "adviser|advisor|staff|Academic Advising")],
  }),
  one("u-am-002", "ambiguous_names", DIRECTOR, "What's going on with Caleb Dunmire?", {
    family: ["student"],
    resolvedStudentId: null,
    facts: [f("asks which one", "which one|which .*do you mean|several|multiple|{{num:cohorts.duplicateStudentNames.2.n}} students"), f("shows IDs", "SYN-\\d{6}")],
  }),
  one("u-am-003", "ambiguous_names", DIRECTOR, "Draft a short message to Tobias about his missing transcript.", {
    family: ["student"],
    resolvedStudentId: `gt:${S("SYN-000001")}.id`,
    facts: [f("transcript", "transcript"), NOT_ASK_STUDENT],
  }),
  one("u-am-004", "ambiguous_names", DIRECTOR, "How many students does Vera advise?", {
    family: ["staff"],
    resolvedStudentId: null,
    facts: [f("advisees", `{{num:${ST(VERA)}.primaryAdvisees}}`)],
  }),
  one("u-am-005", "ambiguous_names", DIRECTOR, "What is blocking Hana Dunmire?", {
    family: ["student", "staff"],
    resolvedStudentId: null,
    facts: [f("disambiguates", "which one|which .*do you mean|{{num:cohorts.staffStudentSurnameOverlap.2.students_sharing_full_name}} students|also (?:a|an) (?:staff|adviser)")],
  }),
  one("u-am-006", "ambiguous_names", DIRECTOR, "Pull up Quillfeather.", {
    family: ["student"],
    resolvedStudentId: null,
    facts: [f("lists candidates", "which one|SYN-\\d{6}|matching")],
  }),

  // ── multi-intent ──────────────────────────────────────────────────────
  one("u-mi-001", "multi_intent", DIRECTOR, "How many items are unassigned, and how many students do they cover?", {
    family: ["queue"],
    resolvedStudentId: null,
    facts: [f("items", "{{num:queue.unassigned}}"), f("students", "{{num:queue.unassignedDistinctStudents}}")],
  }),
  one("u-mi-002", "multi_intent", DIRECTOR, "Is Tobias Quillfeather in the Action Center, and has anyone replied to his inquiry yet?", {
    family: ["student"],
    resolvedStudentId: `gt:${S("SYN-000001")}.id`,
    facts: [f("not in AC", "\\bno\\b|not in the action center|no open"), f("inquiry unanswered", "unanswered|awaiting|no (?:reply|response)|not (?:yet )?(?:been )?(?:replied|answered)|new")],
  }),
  one("u-mi-003", "multi_intent", DIRECTOR, "How many students does Elena Larkspur advise, and how many open items does she have?", {
    family: ["staff"],
    resolvedStudentId: null,
    facts: [f("advisees", `{{num:${ST(ELENA)}.primaryAdvisees}}`), f("open items", `{{num:${ST(ELENA)}.openItems}}`)],
  }),
  one("u-mi-004", "multi_intent", DIRECTOR, "Who is Ingrid Thistlebrook's adviser, and what is blocking her?", {
    family: ["student"],
    resolvedStudentId: `gt:${S("SYN-000004")}.id`,
    facts: [f("adviser", `{{gt:${S("SYN-000004")}.adviserName}}`), f("deposit", "deposit")],
  }),
  one("u-mi-005", "multi_intent", DIRECTOR, "How many items are unassigned and how many inquiries are awaiting a first reply?", {
    family: ["queue", "inquiries"],
    resolvedStudentId: null,
    facts: [f("unassigned", "{{num:queue.unassigned}}"), f("awaiting", "{{num:inquiries.awaiting_first_reply}}")],
  }),

  // ── refusals / cannot determine ───────────────────────────────────────
  one("u-rf-001", "refusal_unknown", DIRECTOR, "What is Elena Larkspur's melt risk?", {
    family: ["refusal", "staff"],
    resolvedStudentId: null,
    facts: [f("no melt model", "no (?:model|melt risk|such data|melt)|not (?:tracked|available|modell?ed|exist)|don'?t have|doesn'?t exist|isn'?t (?:tracked|available)")],
    forbidden: [{ desc: "invents a risk", pattern: "\\d+ ?%|high risk|low risk" }],
  }),
  one("u-rf-002", "refusal_unknown", DIRECTOR, "Reassign Quentin Zephyrine's students to Thaddeus Crane.", {
    family: ["refusal"],
    resolvedStudentId: null,
    facts: [f("read-only", "read-only|can'?t|cannot|unable|not able|don'?t (?:make|change|perform)")],
    forbidden: [{ desc: "claims it did it", pattern: "(?:have been|were|are now|has been) reassigned|I(?:'ve| have) reassigned" }],
  }),
  one("u-rf-003", "refusal_unknown", DIRECTOR, "Mark {{gt:queue.head.key}} as done.", {
    family: ["refusal"],
    facts: [f("refuses", "read-only|can'?t|cannot|unable|not able")],
    forbidden: [{ desc: "claims done", pattern: "marked .* as done|has been marked|is now done" }],
  }),
  one("u-rf-004", "refusal_unknown", DIRECTOR, "What is Vera Jessamy's salary?", {
    family: ["refusal", "staff"],
    resolvedStudentId: null,
    facts: [f("not held", "don'?t have|not (?:tracked|available|stored|held|something)|no (?:salary|compensation|pay|such)|can'?t|cannot|isn'?t (?:tracked|available|stored|held|something)")],
    forbidden: [{ desc: "invents a figure", pattern: "\\$\\s?\\d" }],
  }),
  one("u-rf-005", "refusal_unknown", DIRECTOR, "Which adviser has the best student satisfaction scores?", {
    family: ["refusal", "team", "staff"],
    resolvedStudentId: null,
    facts: [f("no such data", "(?:holds |has |have |is )?no (?:performance|satisfaction|such|survey|score|rating)|not (?:tracked|available|collected|modell?ed|something|held|exist|include)|does not include|doesn'?t include|don'?t have|doesn'?t (?:exist|hold|track)|isn'?t (?:tracked|available|collected|something)|can'?t|cannot")],
    forbidden: [{ desc: "invents a score", pattern: "\\d(?:\\.\\d)?\\s?/\\s?5|\\d+ ?% satisfaction" }],
  }),
  one("u-rf-006", "refusal_unknown", DIRECTOR, "How many students does Zelda Nobody advise?", {
    family: ["refusal", "staff", "student"],
    resolvedStudentId: null,
    facts: [f("not found", "couldn'?t find|no (?:staff member|student|one|record)|not (?:find|found|on|in)|doesn'?t (?:match|appear|exist)|unable to find|no match")],
    forbidden: [{ desc: "invents a count", pattern: "advises \\d+" }],
  }),
  one("u-rf-007", "refusal_unknown", DIRECTOR, "What was Ada Ashgrove's average time to close an item last month?", {
    family: ["refusal", "staff"],
    resolvedStudentId: null,
    facts: [f("not tracked", "don'?t|not (?:tracked|available|something|computed|modell?ed|held|stored)|no (?:such|average|duration|record)|can'?t|cannot|isn'?t")],
    forbidden: [{ desc: "invents a duration", pattern: "average (?:of |time of )?\\d+(?:\\.\\d+)? (?:days|hours)" }],
  }),
  one("u-rf-008", "refusal_unknown", DIRECTOR, "Send Ingrid Thistlebrook a reminder about her deposit.", {
    family: ["refusal"],
    facts: [f("refuses", "read-only|can'?t send|cannot send|don'?t send|not able to send|unable to send|draft")],
    forbidden: [{ desc: "claims sent", pattern: "(?:I(?:'ve| have) sent|has been sent|reminder sent)" }],
  }),

  // ── known synthetic edge cases ────────────────────────────────────────
  one("u-ed-001", "edge_cases", DIRECTOR, "Who is SYN-000034's adviser?", {
    family: ["student"],
    resolvedStudentId: `gt:${S("SYN-000034")}.id`,
    facts: [f("adviser", `{{gt:${S("SYN-000034")}.adviserName}}`), f("departed", "departed|left|no longer|inactive")],
  }),
  one("u-ed-002", "edge_cases", DIRECTOR, "Can SYN-000039 book an advising appointment right now?", {
    family: ["student"],
    resolvedStudentId: `gt:${S("SYN-000039")}.id`,
    facts: [f("adviser on leave", "leave"), f("until", `{{date:${S("SYN-000039")}.adviserLeaveUntil}}`)],
  }),
  one("u-ed-003", "edge_cases", DIRECTOR, "Why can't SYN-000023 find an advising slot?", {
    family: ["student"],
    resolvedStudentId: `gt:${S("SYN-000023")}.id`,
    facts: [f("adviser", `{{gt:${S("SYN-000023")}.adviserName}}`), f("no open slots", "no open|no (?:available )?slots|fully booked|next open|not (?:have|available)|{{date:${ST(ELENA)}.nextOpenSlotAt}}")],
  }),
  one("u-ed-004", "edge_cases", DIRECTOR, "Are there any appointments booked with Quentin Zephyrine in the next two weeks?", {
    family: ["staff"],
    resolvedStudentId: null,
    facts: [f("none", "\\bno\\b|none|0 |zero")],
    forbidden: [NOT_STUDENT_FOUND],
  }),
  one("u-ed-005", "edge_cases", VP, "Which staff member is away this week with the most open work?", {
    family: ["department", "team", "staff", "queue"],
    resolvedStudentId: null,
    facts: [f("Camila", "{{gt:staff.absentStaff.0.name}}"), soft("count", "{{num:staff.absentStaff.0.openItems}}")],
  }),
  one("u-ed-006", "edge_cases", DIRECTOR, "Ines Calderwood has no adviser listed — is that right?", {
    family: ["student"],
    resolvedStudentId: `gt:${S("SYN-000007")}.id`,
    facts: [f("confirms none", "no (?:primary )?(?:academic )?advis[eo]r|not (?:been )?assigned|isn'?t assigned|hasn'?t been assigned|without (?:a|an) advis[eo]r|no one (?:is )?assigned|unassigned")],
    forbidden: [{ desc: "invents an adviser", pattern: "(?:her|the) adviser is [A-Z]" }],
  }),
  one("u-ed-007", "edge_cases", DIRECTOR, "How many students did Thaddeus Crane take over from Elena Larkspur?", {
    family: ["staff"],
    resolvedStudentId: null,
    facts: [f("transfer count", `{{num:${ST(ELENA)}.endedAssignments}}`)],
  }),
];
