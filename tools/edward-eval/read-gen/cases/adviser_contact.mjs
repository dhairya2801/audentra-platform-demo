import { S, ST, f, soft, forbid, one, FAKE_EMAIL, NO_UUID, NO_PHONE } from "./common.mjs";

const LUCIA = S("lucia");
const KWAME = S("kwame");
const NOOR = S("noor");
const HANA = S("hana");
const PETRA = S("petra");
const ADRIA = S("adria");
const BRUNO = S("bruno");

export const ADVISER_CONTACT_CASES = [
  one("rg-adv-001", "adviser_contact", "student", "lucia",
    "who's my adviser",
    "Names the primary academic adviser (Caleb Mossbank) from the advising read; no invented contact details.",
    {
      requestTypes: ["advising", "appointments", "student_advising", "adviser", "contact"],
      anyOfTools: [["getStudentAdvising", "getStudentAppointments"]],
      facts: [f("adviser name", `{{re:${LUCIA}.primaryAdviser.namePattern}}`)],
      forbidden: [FAKE_EMAIL, NO_UUID, NO_PHONE],
    }),
  one("rg-adv-002", "adviser_contact", "student", "kwame",
    "whats my financial aid counselors email",
    "Gives the financial-aid counselor's name and exact email (Emeka Crane) — not the academic adviser's.",
    {
      anyOfTools: [["getStudentAdvising", "getFinancialAidSupportOptions", "getSupportOptions"]],
      facts: [
        f("FA counselor email", `{{gt:${KWAME}.financialAidCounselor.email}}`),
        soft("FA counselor name", `{{re:${KWAME}.financialAidCounselor.namePattern}}`),
      ],
      forbidden: [FAKE_EMAIL, forbid("gives the academic adviser's email instead", `^(?:(?!{{gt:${KWAME}.financialAidCounselor.email}})[\\s\\S])*{{gt:${KWAME}.primaryAdviser.email}}(?:(?!{{gt:${KWAME}.financialAidCounselor.email}})[\\s\\S])*$`)],
    }),
  one("rg-adv-003", "adviser_contact", "student", "noor",
    "Which office is my academic adviser in? I want to drop by.",
    "Gives the office location and, because the adviser is on leave, says so rather than inviting a visit.",
    {
      anyOfTools: [["getStudentAdvising", "getStudentAppointments"]],
      facts: [
        f("office", `{{gt:${NOOR}.primaryAdviser.officeLocation}}`),
        f("on leave", "on leave|leave until|away until|out (?:of (?:the )?office|until)"),
        soft("adviser name", `{{re:${NOOR}.primaryAdviser.namePattern}}`),
      ],
      forbidden: [NO_PHONE, FAKE_EMAIL],
    }),
  one("rg-adv-004", "adviser_contact", "student", "kwame",
    "who do I talk to about housing stuff?",
    "Names the assigned housing coordinator (Farid Jokinen), not the academic adviser.",
    {
      anyOfTools: [["getStudentAdvising", "getStudentHousingStatus", "getSupportOptions", "getHousingOptions"]],
      facts: [f("housing coordinator", `{{re:${KWAME}.housingCoordinator.namePattern}}`)],
      forbidden: [FAKE_EMAIL],
    }),
  one("rg-adv-005", "adviser_contact", "student", "hana",
    "how do i reach my international adviser",
    "Names the international adviser (Matthias Gunnarsson) with the email on record.",
    {
      anyOfTools: [["getStudentAdvising", "getSupportOptions"]],
      facts: [
        f("international adviser", `{{re:${HANA}.internationalAdviser.namePattern}}`),
        f("email", `{{gt:${HANA}.internationalAdviser.email}}`),
      ],
      forbidden: [FAKE_EMAIL, NO_PHONE],
    }),
  one("rg-adv-006", "adviser_contact", "staff", "priya",
    "which adviser is assigned to Petra Oakenshaw?",
    "Resolves the (unique) student and names her primary adviser Noor Brightwater.",
    {
      resolvedStudentId: `gt:${PETRA}.id`,
      facts: [f("adviser", `{{re:${PETRA}.primaryAdviser.namePattern}}`)],
      forbidden: [NO_UUID],
    }),
  one("rg-adv-007", "adviser_contact", "staff", "zelda",
    "Who is Kwame Oakenshaw's admissions counselor?",
    "Names the admissions counselor Kwame Radcliffe (same first name as the student — must not conflate).",
    {
      resolvedStudentId: `gt:${KWAME}.id`,
      facts: [f("admissions counselor", `{{gt:${KWAME}.admissionsCounselor.name}}`)],
      forbidden: [forbid("returns the academic adviser as the admissions counselor", `admissions counselor (?:is|:) {{gt:${KWAME}.primaryAdviser.name}}`)],
    }),
  one("rg-adv-008", "adviser_contact", "staff", "leandro",
    "is noor zephyrine's adviser on leave right now",
    "Says yes: Junia Pemberwell is on leave, with the return date on record.",
    {
      resolvedStudentId: `gt:${NOOR}.id`,
      facts: [
        f("adviser", `{{re:${NOOR}.primaryAdviser.namePattern}}`),
        f("on leave", "on leave|\\byes\\b|is away|leave until"),
        soft("return date", `{{date:${NOOR}.primaryAdviser.leaveUntil}}|{{date:${NOOR}.primaryAdviser.currentAbsenceEnds}}`),
      ],
      forbidden: [forbid("denies the leave", "not on leave|is available|\\bno\\b,? (?:she|he|they) (?:is|are)n'?t")],
    }),
  one("rg-adv-009", "adviser_contact", "staff", "marcus",
    "adviser for SYN-001645?",
    "Looks the student up by external reference and names the adviser Ivo Whitlowe.",
    {
      resolvedStudentId: `gt:${BRUNO}.id`,
      facts: [f("adviser", `{{re:${BRUNO}.primaryAdviser.namePattern}}`), soft("student name", `{{gt:${BRUNO}.firstName}}`)],
    }),
  one("rg-adv-010", "adviser_contact", "staff", "greta",
    "Does Adria Kettleby actually still have an adviser? Something looked off.",
    "Names Quentin Zephyrine and says he has departed / is no longer with the university, so she effectively has no active adviser.",
    {
      resolvedStudentId: `gt:${ADRIA}.id`,
      facts: [
        f("adviser named", `{{re:${ADRIA}.primaryAdviser.namePattern}}`),
        f("departed", "departed|no longer|has left|left the university|not (?:currently )?active|inactive|former"),
      ],
      forbidden: [forbid("presents the departed adviser as active", "(?:is|remains) (?:her |the )?(?:active|current) adviser|still (?:her|the) adviser")],
    }),
];
