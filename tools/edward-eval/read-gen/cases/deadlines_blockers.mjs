import { S, f, soft, forbid, one, NONE_PATTERN, NO_UUID } from "./common.mjs";

const LUCIA = S("lucia");
const PETRA = S("petra");
const GUSTAV = S("gustav");
const BRUNO = S("bruno");
const OMAR = S("omar");
const CAMILA = S("camila");
const ADA = S("ada");
const HANA = S("hana");
const KWAME = S("kwame");

export const DEADLINES_BLOCKERS_CASES = [
  one("rg-ddl-001", "deadlines_blockers", "student", "lucia",
    "do I need to worry about anything before friday?",
    "Surfaces the two overdue requirements (official transcript under review, financial-aid verification) rather than saying all clear.",
    {
      anyOfTools: [["getStudentDeadlines", "getOnboardingChecklist", "getEnrollmentHolds"]],
      facts: [
        f("transcript", "transcript"),
        f("financial-aid verification", "financial[- ]aid verification|verification"),
      ],
      forbidden: [forbid("says nothing is pending", "nothing (?:to worry|due|pending|outstanding)|you'?re all (?:set|caught up)|no (?:upcoming )?deadlines")],
    }),
  one("rg-ddl-002", "deadlines_blockers", "student", "petra",
    "whats stopping me from registering",
    "Names the unpaid enrollment deposit as a blocker plus the other overdue blocking items (transcript, immunization); the deposit due date has passed.",
    {
      anyOfTools: [["getEnrollmentHolds", "getRegistrationStatus", "getOnboardingChecklist", "getStudentDeadlines"]],
      facts: [
        f("deposit", "deposit"),
        f("transcript", "transcript"),
        soft("immunization", "immuni[sz]ation"),
      ],
      forbidden: [forbid("says the deposit is paid", "deposit (?:is|has been|was) (?:paid|received|posted)"), forbid("claims nothing blocks", "nothing (?:is )?(?:blocking|stopping)|no (?:holds|blockers)\\b(?![^.]*but)")],
    }),
  one("rg-ddl-003", "deadlines_blockers", "student", "gustav",
    "ok whats overdue for me rn",
    "Lists the overdue requirements: deposit, transcript (under review), financial-aid verification, housing plans, orientation — five in total.",
    {
      anyOfTools: [["getStudentDeadlines", "getOnboardingChecklist"]],
      facts: [
        f("deposit", "deposit"),
        f("transcript", "transcript"),
        f("orientation", "orientation"),
        soft("housing", "housing"),
        soft("count", `{{num:${GUSTAV}.requirements.overdueCount}}`),
      ],
      forbidden: [forbid("claims nothing overdue", "nothing (?:is )?overdue|no overdue")],
    }),
  one("rg-ddl-004", "deadlines_blockers", "student", "bruno",
    "anything due?",
    "Journey completed, every requirement done: says nothing is due, without inventing a deadline.",
    {
      anyOfTools: [["getStudentDeadlines", "getOnboardingChecklist"]],
      facts: [f("nothing due", NONE_PATTERN + "|all (?:set|done|complete)|caught up")],
      forbidden: [forbid("invents a due item", "\\bdue (?:on|by) (?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|\\d)"), forbid("overdue", "overdue")],
    }),
  one("rg-ddl-005", "deadlines_blockers", "student", "omar",
    "when is my deposit due",
    "Gives the deposit requirement's due date from the checklist and says it is not yet paid.",
    {
      anyOfTools: [["getStudentDeadlines", "getOnboardingChecklist", "getStudentAccountSummary", "getEnrollmentHolds"]],
      facts: [f("due date", `{{date:${OMAR}.deposit.requirementDueAt}}`)],
      forbidden: [forbid("says it is paid", "(?:already|has been|was) paid|deposit (?:is|was) (?:paid|received|posted)")],
    }),
  one("rg-ddl-006", "deadlines_blockers", "student", "camila",
    "Is anything blocking my enrollment right now?",
    "Deposit is paid; the immunization record was rejected and needs resubmission — that is the blocker to name. Nothing is overdue yet.",
    {
      anyOfTools: [["getEnrollmentHolds", "getOnboardingChecklist", "getDocumentStatuses", "getStudentDeadlines"]],
      facts: [f("immunization", "immuni[sz]ation")],
      forbidden: [
        forbid("claims the deposit is unpaid", "deposit[^.]{0,60}(?:unpaid|outstanding|not (?:yet )?(?:been )?(?:paid|received)|still due)"),
        forbid("claims something is overdue", "\\boverdue\\b"),
      ],
    }),
  one("rg-ddl-007", "deadlines_blockers", "student", "ada",
    "what's next on my list",
    "Points at the open items: financial-aid verification (under review) and orientation registration; the rest is complete.",
    {
      anyOfTools: [["getOnboardingChecklist", "getStudentDeadlines"]],
      facts: [f("orientation", "orientation"), f("financial-aid verification", "financial[- ]aid|verification")],
      forbidden: [forbid("says all done", "nothing left|all (?:done|complete|set)\\b(?![^.]*(?:except|but|apart))")],
    }),
  one("rg-ddl-008", "deadlines_blockers", "staff", "matthias",
    "what is Hana Mossbank waiting on",
    "Resolves Hana Mossbank (unique) and names the open requirements: transcript, financial-aid verification, immunization, housing, deposit, orientation — six open, all overdue.",
    {
      resolvedStudentId: `gt:${HANA}.id`,
      facts: [
        f("deposit", "deposit"),
        f("transcript", "transcript"),
        f("immunization", "immuni[sz]ation"),
        soft("count", `{{num:${HANA}.requirements.openCount}}`),
      ],
      forbidden: [forbid("says nothing outstanding", "nothing (?:outstanding|pending|open)|no open (?:items|requirements)")],
    }),
  one("rg-ddl-009", "deadlines_blockers", "staff", "registrar",
    "which of petra oakenshaw's items are overdue",
    "Lists Petra's six overdue requirements (deposit, transcript, financial-aid verification, immunization, housing plans, orientation).",
    {
      resolvedStudentId: `gt:${PETRA}.id`,
      facts: [
        f("deposit", "deposit"),
        f("transcript", "transcript"),
        f("orientation", "orientation"),
        soft("count", `{{num:${PETRA}.requirements.overdueCount}}`),
      ],
      forbidden: [NO_UUID],
    }),
  one("rg-ddl-010", "deadlines_blockers", "staff", "greta",
    "What's blocking Gustav Fennwick's enrollment?",
    "Two students share the name; asks which — or, if it resolves by context, names the unpaid deposit and transcript for the right one. Either way must not present a single student's blockers as certain without acknowledging the duplicate.",
    {
      factGroups: [
        [f("asks which Gustav", "which (?:one|Gustav|student)|two (?:students|matches)|\\b2 (?:students|matches)|more than one|multiple")],
        [f("deposit", "deposit"), f("transcript", "transcript"), f("acknowledges duplicates", "two|\\b2\\b|both|other Gustav|another Gustav")],
      ],
    }),
  one("rg-ddl-011", "deadlines_blockers", "staff", "zelda",
    "does bruno stonebrook have anything outstanding?",
    "No: journey complete, every requirement done, no open work items.",
    {
      resolvedStudentId: `gt:${BRUNO}.id`,
      facts: [f("nothing outstanding", NONE_PATTERN + "|complete|all (?:set|done)")],
      forbidden: [forbid("invents outstanding items", "\\boverdue\\b|still (?:needs|owes|has to)|outstanding (?:item|requirement)s? (?:are|is|include)")],
    }),
  one("rg-ddl-012", "deadlines_blockers", "staff", "priya",
    "What's the earliest overdue requirement for Kwame Oakenshaw, and when was it due?",
    "Immunization records, due 2026-08-19 (the earliest of his two overdue requirements).",
    {
      resolvedStudentId: `gt:${KWAME}.id`,
      facts: [
        f("immunization", "immuni[sz]ation"),
        f("due date", `{{date:${KWAME}.requirements.earliestOverdue.dueAt}}`),
      ],
    }),
];
