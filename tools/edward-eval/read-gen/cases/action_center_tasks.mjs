import { S, ST, f, soft, forbid, one, NONE_PATTERN, NO_UUID } from "./common.mjs";

const GRETA = ST("greta");
const MATTHIAS = ST("matthias");
const ZELDA = ST("zelda");
const MARCUS = ST("marcus");
const REGISTRAR = ST("registrar");
const ADA_A = ST("ada_a");
const JUNIA = ST("junia");
const KWAME = S("kwame");
const HANA = S("hana");

export const ACTION_CENTER_CASES = [
  one("rg-act-001", "action_center_tasks", "staff", "greta",
    "what's on my plate",
    "Her open work-item count (55) and a sense of what they are (verification items).",
    {
      resolvedStudentId: null,
      anyOfTools: [["getStaffWorkQueue", "summarizeWorkQueue", "getMorningBriefing", "getStaffProfile"]],
      facts: [f("open count", `{{num:${GRETA}.work.open}}`), soft("verification theme", "verification")],
      forbidden: [NO_UUID],
    }),
  one("rg-act-002", "action_center_tasks", "staff", "matthias",
    "which of my items are overdue",
    "All 276 open items are past due; the count must be stated (±2%).",
    {
      resolvedStudentId: null,
      anyOfTools: [["getStaffWorkQueue", "summarizeWorkQueue", "searchWorkQueue", "getStaffProfile"]],
      facts: [f("overdue count", `{{num~:${MATTHIAS}.work.overdue}}`)],
      forbidden: [forbid("says none overdue", "no overdue|nothing (?:is )?overdue")],
    }),
  one("rg-act-003", "action_center_tasks", "staff", "matthias",
    "show my urgent items",
    "The urgent-priority count (73) and at least one of the affected students.",
    {
      resolvedStudentId: null,
      anyOfTools: [["getStaffWorkQueue", "summarizeWorkQueue", "searchWorkQueue"]],
      facts: [
        f("urgent count", `{{num~:${MATTHIAS}.work.urgent}}`),
        f("an urgent student", `{{any:${MATTHIAS}.work.urgentStudents}}`),
      ],
    }),
  one("rg-act-004", "action_center_tasks", "staff", "zelda",
    "what's the oldest thing sitting in my queue",
    "The earliest-created open item (key and/or student), not merely the highest priority one.",
    {
      resolvedStudentId: null,
      anyOfTools: [["getStaffWorkQueue", "searchWorkQueue", "summarizeWorkQueue"]],
      factGroups: [
        [f("oldest key", `{{gt:${ZELDA}.work.oldestOpen.key}}`)],
        [f("oldest student", `{{gt:${ZELDA}.work.oldestOpen.student}}`)],
      ],
    }),
  one("rg-act-005", "action_center_tasks", "staff", "leandro",
    "what does Kwame Oakenshaw's action center contain",
    "Six open items across Financial Aid, International, Student Accounts and Housing, with owners; one urgent, one blocked.",
    {
      resolvedStudentId: `gt:${KWAME}.id`,
      anyOfTools: [["getStudentOwnership", "getStudentStaffSummary", "searchWorkQueue", "getStudentBlockers"]],
      facts: [
        f("count", `{{num:${KWAME}.work.openCount}}`),
        f("an owner", `{{any:${KWAME}.work.assignees}}`),
        soft("housing", "housing"),
      ],
      forbidden: [forbid("says none", "no open (?:work )?items|nothing in (?:the|his) action center")],
    }),
  one("rg-act-006", "action_center_tasks", "staff", "priya",
    "who owns hana mossbank's next action",
    "The head-of-queue item's assignee (Kwame Radcliffe on the deposit item) — an owner of one of her two open items is acceptable.",
    {
      resolvedStudentId: `gt:${HANA}.id`,
      anyOfTools: [["getStudentOwnership", "getStudentStaffSummary", "searchWorkQueue"]],
      facts: [f("an owner", `{{any:${HANA}.work.assignees}}`)],
      forbidden: [forbid("says unowned", "no one (?:owns|is assigned)|unassigned|nobody")],
    }),
  one("rg-act-007", "action_center_tasks", "staff", "marcus",
    "tasks on my board that are in progress",
    "The in-progress count (14), ideally with a couple of item titles.",
    {
      resolvedStudentId: null,
      anyOfTools: [["getStaffWorkQueue", "searchWorkQueue", "summarizeWorkQueue"]],
      facts: [f("in-progress count", `{{num:${MARCUS}.work.inProgress}}`), soft("an item", `{{any:${MARCUS}.work.inProgressItems|key}}|{{any:${MARCUS}.work.inProgressItems|student}}`)],
    }),
  one("rg-act-008", "action_center_tasks", "staff", "greta",
    "how many of my items are blocked",
    "Ten blocked items.",
    {
      resolvedStudentId: null,
      anyOfTools: [["getStaffWorkQueue", "searchWorkQueue", "summarizeWorkQueue"]],
      facts: [f("blocked count", `{{num:${GRETA}.work.blocked}}`)],
    }),
  one("rg-act-009", "action_center_tasks", "staff", "registrar",
    "cases about transcripts that I own",
    "22 of his 32 open items are transcript reviews.",
    {
      resolvedStudentId: null,
      anyOfTools: [["searchWorkQueue", "getStaffWorkQueue", "summarizeWorkQueue"]],
      facts: [f("transcript-topic count", `{{num:${REGISTRAR}.work.transcriptTopic}}`)],
      forbidden: [forbid("uses the full open count as the transcript count", `{{num:${REGISTRAR}.work.open}} (?:transcript|cases about transcript)`)],
    }),
  one("rg-act-010", "action_center_tasks", "staff", "priya",
    "what's on my plate",
    "Nothing is assigned to her; says so honestly instead of listing the department queue as hers.",
    {
      resolvedStudentId: null,
      anyOfTools: [["getStaffWorkQueue", "summarizeWorkQueue", "getMorningBriefing", "getStaffProfile"]],
      facts: [f("empty", NONE_PATTERN)],
      forbidden: [forbid("invents assigned items", "\\b[1-9]\\d* (?:open )?(?:work )?(?:items?|tasks?|cases?) (?:assigned|on your plate|in your queue)|you have \\b[1-9]\\d* (?:open|items|tasks)")],
    }),
  one("rg-act-011", "action_center_tasks", "staff", "ada_a",
    "what should I pick up first",
    "The top item by priority/due date (a high-priority overdue no-show follow-up); key or student name is accepted.",
    {
      resolvedStudentId: null,
      anyOfTools: [["getStaffWorkQueue", "getMorningBriefing", "summarizeWorkQueue", "searchWorkQueue"]],
      factGroups: [
        [f("head key", `{{gt:${ADA_A}.work.head.key}}`)],
        [f("head student", `{{gt:${ADA_A}.work.head.student}}`)],
        [f("oldest key", `{{gt:${ADA_A}.work.oldestOpen.key}}`)],
      ],
    }),
  one("rg-act-012", "action_center_tasks", "staff", "junia",
    "anything land on me while I've been out?",
    "Two open items assigned to her (the advising hold for Gustav Whitlowe and one more); count or student name.",
    {
      resolvedStudentId: null,
      anyOfTools: [["getStaffWorkQueue", "summarizeWorkQueue", "searchWorkQueue", "getStaffProfile"]],
      factGroups: [
        [f("count", `{{num:${JUNIA}.work.open}}`)],
        [f("a student", `{{any:${JUNIA}.work.openStudents}}`)],
      ],
      forbidden: [forbid("says nothing assigned", "nothing (?:is )?assigned|no (?:open )?items")],
    }),
];
