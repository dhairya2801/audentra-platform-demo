import { S, ST, f, soft, forbid, one, NONE_PATTERN, FAKE_EMAIL, NO_UUID } from "./common.mjs";

const OMAR = S("omar");
const KWAME = S("kwame");
const NOOR = S("noor");
const ADA = S("ada");
const ADRIA = S("adria");
const LUCIA = S("lucia");
const GRETA = ST("greta");
const MATTHIAS = ST("matthias");
const PRIYA = ST("priya");

export const MULTI_INTENT_CASES = [
  one("rg-mi-001", "multi_intent", "student", "omar",
    "whats my status and what's my advisers email",
    "Both halves: 3 requirements remaining; no academic adviser is assigned yet (so no email) — must not invent one.",
    {
      facts: [
        f("remaining count", `{{num:${OMAR}.requirements.openCount}}|{{num:${OMAR}.requirements.completedCount}}`),
        f("no adviser assigned", "no (?:academic )?adviser|not (?:yet )?(?:been )?assigned|hasn'?t been assigned|isn'?t (?:an? )?(?:adviser|assigned)|don'?t have an? (?:academic )?adviser"),
      ],
      forbidden: [FAKE_EMAIL, forbid("invents an adviser email", "@synthetic\\.aster\\.example")],
    }),
  one("rg-mi-002", "multi_intent", "student", "kwame",
    "am i done with documents, and when's my next appointment?",
    "Not done: the immunization record was rejected and two documents are still pending; next appointment with Bianca Netherby on the recorded date.",
    {
      facts: [
        f("immunization rejected", "immuni[sz]ation"),
        f("next appointment", `{{date:${KWAME}.appointments.next.date}}|{{re:${KWAME}.appointments.next.staffNamePattern}}`),
      ],
      forbidden: [forbid("says all documents done", "all (?:your )?documents (?:are|have been) (?:accepted|approved|done)")],
    }),
  one("rg-mi-003", "multi_intent", "student", "noor",
    "when was my transcript due and can I get in to see my adviser about it?",
    "Transcript was due on the recorded date (overdue); adviser Junia Pemberwell is on leave, so not right now.",
    {
      facts: [
        f("due date", `{{date:${NOOR}.requirements.earliestOverdue.dueAt}}`),
        f("on leave", `{{re:${NOOR}.primaryAdviser.bookablePattern}}`),
      ],
    }),
  one("rg-mi-004", "multi_intent", "student", "ada",
    "how many unread messages do I have, and did my deposit go through?",
    "Six unread; the deposit is paid.",
    {
      facts: [
        f("unread count", `{{num:${ADA}.messages.unreadCount}}`),
        f("deposit paid", "deposit[^.]{0,60}(?:paid|received|posted|went through|complete|✓)|(?:paid|received|posted)[^.]{0,30}deposit"),
      ],
      forbidden: [forbid("says deposit unpaid", "deposit[^.]{0,40}(?:unpaid|outstanding|not (?:yet )?(?:paid|received))")],
    }),
  one("rg-mi-005", "multi_intent", "staff", "greta",
    "my overdue count and today's appointments please",
    "55 overdue items; 3 appointments today.",
    {
      resolvedStudentId: null,
      facts: [
        f("overdue count", `{{num~:${GRETA}.work.overdue}}`),
        f("appointments today", `{{num:${GRETA}.appointments.today}}`),
      ],
    }),
  one("rg-mi-006", "multi_intent", "staff", "matthias",
    "how many urgent items do I have, and who is the international adviser for SYN-001278?",
    "73 urgent items; Lucia Zephyrine's international adviser is Matthias Gunnarsson himself.",
    {
      resolvedStudentId: `gt:${LUCIA}.id`,
      facts: [
        f("urgent count", `{{num~:${MATTHIAS}.work.urgent}}`),
        f("international adviser", `{{re:${LUCIA}.internationalAdviser.namePattern}}|\\byou\\b|yourself`),
      ],
    }),
  one("rg-mi-007", "multi_intent", "staff", "zelda",
    "give me Adria Kettleby's adviser plus her open item count",
    "Adviser Quentin Zephyrine (departed); 4 open work items.",
    {
      resolvedStudentId: `gt:${ADRIA}.id`,
      facts: [
        f("adviser", `{{re:${ADRIA}.primaryAdviser.namePattern}}`),
        f("open item count", `{{num:${ADRIA}.work.openCount}}`),
        soft("departed", "departed|no longer|left"),
      ],
      forbidden: [NO_UUID],
    }),
  one("rg-mi-008", "multi_intent", "staff", "priya",
    "how many people report to me, and is anything assigned to me right now?",
    "12 direct reports; nothing assigned.",
    {
      resolvedStudentId: null,
      facts: [
        f("direct reports", `{{num:${PRIYA}.directReports}}`),
        f("nothing assigned", NONE_PATTERN),
      ],
      forbidden: [forbid("invents assigned items", "you have \\b[1-9]\\d* (?:open |assigned )?(?:items|tasks)")],
    }),
];
