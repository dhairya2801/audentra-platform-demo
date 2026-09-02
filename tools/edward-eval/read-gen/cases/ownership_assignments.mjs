import { S, ST, f, soft, forbid, one, ASKS_WHICH, NO_UUID } from "./common.mjs";

const LUCIA = S("lucia");
const KWAME = S("kwame");
const GUSTAV = S("gustav");
const MATTHIAS = ST("matthias");
const ADA_A = ST("ada_a");

export const OWNERSHIP_CASES = [
  one("rg-own-001", "ownership_assignments", "staff", "leandro",
    "who owns Lucia Zephyrine's open items",
    "Three Lucia Zephyrines exist; must ask which one rather than pick.",
    {
      resolvedStudentId: null,
      facts: [ASKS_WHICH],
    }),
  one("rg-own-002", "ownership_assignments", "staff", "leandro",
    "who owns the open items for SYN-001278",
    "Resolves by ref: seven open items owned by six staff (Cormac Gunnarsson, Greta Radcliffe, Jasper Njoku, Matthias Gunnarsson, Rosalind Zaragoza, Yusuf Crane).",
    {
      resolvedStudentId: `gt:${LUCIA}.id`,
      anyOfTools: [["getStudentOwnership", "getStudentStaffSummary", "searchWorkQueue"]],
      facts: [
        f("an owner", `{{any:${LUCIA}.work.assignees}}`),
        soft("count", `{{num:${LUCIA}.work.openCount}}`),
        soft("a second owner", `(?:{{any:${LUCIA}.work.assignees}})[\\s\\S]*(?:{{any:${LUCIA}.work.assignees}})`),
      ],
      forbidden: [NO_UUID],
    }),
  one("rg-own-003", "ownership_assignments", "staff", "priya",
    "which advisers and counselors are assigned to Kwame Oakenshaw",
    "All five assignments: academic adviser, admissions counselor, financial-aid counselor, housing coordinator, international adviser — each by name.",
    {
      resolvedStudentId: `gt:${KWAME}.id`,
      anyOfTools: [["getStudentOwnership", "getStudentStaffSummary"]],
      facts: [
        f("primary adviser", `{{re:${KWAME}.primaryAdviser.namePattern}}`),
        f("international adviser", `{{re:${KWAME}.internationalAdviser.namePattern}}`),
        f("housing coordinator", `{{re:${KWAME}.housingCoordinator.namePattern}}`),
        soft("FA counselor", `{{re:${KWAME}.financialAidCounselor.namePattern}}`),
        soft("admissions counselor", `{{gt:${KWAME}.admissionsCounselor.name}}`),
      ],
    }),
  one("rg-own-004", "ownership_assignments", "staff", "registrar",
    "how many students is Matthias Gunnarsson assigned to, across all roles?",
    "319 (all as international adviser; zero primary advisees).",
    {
      resolvedStudentId: null,
      anyOfTools: [["getStaffCaseload", "getStaffProfile", "searchStaff"]],
      facts: [f("assignment count", `{{num:${MATTHIAS}.caseload.assignments}}`)],
      forbidden: [forbid("reports zero because he has no primary advisees", "(?:no|0|zero) students (?:are )?assigned")],
    }),
  one("rg-own-005", "ownership_assignments", "staff", "matthias",
    "how many advisees does Ada Ashgrove carry right now",
    "77 primary advisees.",
    {
      resolvedStudentId: null,
      anyOfTools: [["getStaffCaseload", "getStaffProfile", "searchStaff"]],
      facts: [f("advisee count", `{{num:${ADA_A}.caseload.primaryAdvisees}}`)],
    }),
  one("rg-own-006", "ownership_assignments", "staff", "greta",
    "Who's handling Gustav Fennwick's transcript review? The one in Economics.",
    "The Economics Gustav (SYN-002720) has one open item, the transcript review owned by Camila Okonkwo.",
    {
      resolvedStudentId: `gt:${GUSTAV}.id`,
      anyOfTools: [["getStudentOwnership", "getStudentStaffSummary", "searchWorkQueue", "searchStudents"]],
      facts: [f("owner", `{{re:${GUSTAV}.work.open.0.assigneePattern}}`)],
    }),
];
