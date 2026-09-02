import { S, ST, f, soft, forbid, one, NO_UUID, FAKE_EMAIL } from "./common.mjs";

const PETRA = S("petra");
const LUCIA = S("lucia");
const KWAME = S("kwame");
const HANA = S("hana");
const NOOR = S("noor");
const ADA_A = ST("ada_a");

export const CROSS_ENTITY_CASES = [
  one("rg-xe-001", "cross_entity", "student", "petra",
    "is my adviser available before my deposit deadline?",
    "Two reads: the deposit deadline has already passed (overdue), and adviser Noor Brightwater is bookable — the honest answer says the deadline is behind us, and names the adviser / an upcoming appointment with her.",
    {
      anyOfTools: [["getStudentAdvising", "getStudentAppointments"]],
      facts: [
        f("adviser", `{{re:${PETRA}.primaryAdviser.namePattern}}`),
        f("deadline passed", "passed|overdue|past due|already due|was due|behind"),
      ],
      forbidden: [FAKE_EMAIL],
    }),
  one("rg-xe-002", "cross_entity", "student", "lucia",
    "which of my overdue items belong to financial aid, and who's my FA counselor?",
    "Financial-aid verification is the Financial Aid overdue item; the counselor is Greta Radcliffe.",
    {
      anyOfTools: [["getStudentDeadlines", "getOnboardingChecklist"]],
      facts: [
        f("verification", "verification"),
        f("FA counselor", `{{re:${LUCIA}.financialAidCounselor.namePattern}}`),
      ],
      forbidden: [FAKE_EMAIL, forbid("puts the transcript under financial aid", "financial aid[^.]{0,40}transcript")],
    }),
  one("rg-xe-003", "cross_entity", "student", "kwame",
    "do I get to see my adviser before my immunization thing is due?",
    "Immunization records were due Aug 19 (already overdue); the next advising appointment with Bianca Netherby is on the recorded date — so no, the deadline has passed; the appointment is still on.",
    {
      anyOfTools: [["getStudentAppointments", "getStudentDeadlines", "getOnboardingChecklist", "getStudentAdvising"]],
      facts: [
        f("adviser or appointment date", `{{re:${KWAME}.appointments.next.staffNamePattern}}|{{date:${KWAME}.appointments.next.date}}`),
        f("deadline already passed", "passed|overdue|past due|already|was due|behind"),
      ],
    }),
  one("rg-xe-004", "cross_entity", "student", "hana",
    "is my next appointment with my actual adviser or someone else",
    "Yes — the next appointment is academic advising with Omar Glimmerly, who is her primary adviser.",
    {
      anyOfTools: [["getStudentAppointments", "getStudentAdvising"]],
      facts: [
        f("adviser", `{{re:${HANA}.primaryAdviser.namePattern}}`),
        f("affirmative", "\\byes\\b|your (?:academic )?adviser|is your adviser|with your adviser"),
      ],
      forbidden: [forbid("says it is someone else", "not your adviser|someone else|different adviser")],
    }),
  one("rg-xe-005", "cross_entity", "staff", "leandro",
    "does petra oakenshaw have an appointment with her adviser before her earliest overdue requirement is due?",
    "Earliest overdue requirement was the deposit (July 22, already past); the next appointment with Noor Brightwater is in September — so no; the honest answer notes the deadline has passed.",
    {
      resolvedStudentId: `gt:${PETRA}.id`,
      anyOfTools: [["getStudentAppointments", "getStudentRequirements", "getStudentDeadlines", "getStudentBlockers", "getStudentStaffSummary"]],
      facts: [
        f("appointment with adviser", `{{re:${PETRA}.appointments.next.staffNamePattern}}|{{date:${PETRA}.appointments.next.date}}`),
        f("deadline already passed", "\\bno\\b|passed|overdue|past due|already|was due"),
      ],
      forbidden: [NO_UUID],
    }),
  one("rg-xe-006", "cross_entity", "staff", "leandro",
    "which of Ada Ashgrove's advisees have overdue work?",
    "31 advisees carry at least one overdue open work item; the count (±2%) and at least one name.",
    {
      resolvedStudentId: null,
      anyOfTools: [["getStaffCaseload", "findStudents", "summarizeStudents", "searchWorkQueue"]],
      factGroups: [
        [f("count", `{{num~:${ADA_A}.advisees.with_overdue_work}}`)],
        [f("a name", `{{any:${ADA_A}.advisees.overdueWorkNames}}`)],
      ],
    }),
  one("rg-xe-007", "cross_entity", "staff", "priya",
    "who is Noor Zephyrine's adviser and can that person actually be booked?",
    "Junia Pemberwell, who is on leave and therefore not bookable.",
    {
      resolvedStudentId: `gt:${NOOR}.id`,
      facts: [
        f("adviser", `{{re:${NOOR}.primaryAdviser.namePattern}}`),
        f("not bookable / on leave", `{{re:${NOOR}.primaryAdviser.bookablePattern}}`),
      ],
      forbidden: [forbid("says bookable", "\\bis bookable\\b|can be booked|yes,? (?:she|they) (?:can|is)")],
    }),
  one("rg-xe-008", "cross_entity", "staff", "matthias",
    "of Hana Mossbank's open items, which one is mine?",
    "The supporting-document review (AST-02482) is assigned to him; the deposit item belongs to Kwame Radcliffe.",
    {
      resolvedStudentId: `gt:${HANA}.id`,
      anyOfTools: [["getStudentOwnership", "searchWorkQueue", "getStaffWorkQueue", "getStudentStaffSummary"]],
      facts: [f("his item", `{{gt:${HANA}.work.open.1.key}}|supporting document`)],
      forbidden: [forbid("claims the deposit item", `{{gt:${HANA}.work.open.0.key}}[^.]{0,40}(?:yours|assigned to you)|deposit[^.]{0,60}(?:is yours|assigned to you)`)],
    }),
];
