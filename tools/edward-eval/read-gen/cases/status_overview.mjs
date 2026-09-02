import { S, f, soft, forbid, one, NONE_PATTERN, NO_UUID, FAKE_EMAIL } from "./common.mjs";

const BRUNO = S("bruno");
const GUSTAV = S("gustav");
const OMAR = S("omar");
const ADA = S("ada");
const NOOR = S("noor");
const GRETA_E = S("greta_e");
const LUCIA = S("lucia");

export const STATUS_OVERVIEW_CASES = [
  one("rg-sts-001", "status_overview", "student", "bruno",
    "am i done?",
    "Yes — the enrollment journey is complete (8 of 8 requirements), nothing pending.",
    {
      anyOfTools: [["getEnrollmentState", "getOnboardingChecklist"]],
      facts: [f("complete", "\\byes\\b|complete|all (?:set|done)|finished|nothing (?:left|pending|outstanding)")],
      forbidden: [forbid("invents pending work", "still (?:need|have) to|outstanding|overdue|not (?:yet )?(?:done|complete)")],
    }),
  one("rg-sts-002", "status_overview", "student", "gustav",
    "where do i stand, honestly",
    "In progress: 3 of 8 requirements done, 5 open and overdue, deposit unpaid, no adviser assigned yet.",
    {
      anyOfTools: [["getEnrollmentState", "getOnboardingChecklist"]],
      facts: [
        f("open or completed count", `{{num:${GUSTAV}.requirements.openCount}}|{{num:${GUSTAV}.requirements.completedCount}}`),
        f("deposit unpaid", "deposit"),
        soft("overdue", "overdue|past due|behind"),
      ],
      forbidden: [forbid("claims completion", "you'?re (?:all )?(?:done|set|complete)|journey is complete")],
    }),
  one("rg-sts-003", "status_overview", "student", "omar",
    "whats my enrollment status",
    "In progress with 3 requirements remaining (housing plans, deposit, orientation); no primary adviser assigned.",
    {
      anyOfTools: [["getEnrollmentState", "getOnboardingChecklist"]],
      facts: [
        f("in progress", "in progress|underway|not (?:yet )?complete|remaining|left|open"),
        f("remaining count", `{{num:${OMAR}.requirements.openCount}}|{{num:${OMAR}.requirements.completedCount}}`),
      ],
      forbidden: [forbid("says overdue", "\\boverdue\\b"), forbid("claims complete", "enrollment is complete|you'?re all set")],
    }),
  one("rg-sts-004", "status_overview", "student", "ada",
    "gimme the rundown",
    "Two open requirements (financial-aid verification under review; orientation registration), deposit paid, an upcoming advising appointment.",
    {
      anyOfTools: [["getEnrollmentState", "getOnboardingChecklist", "getStudentDeadlines"]],
      facts: [
        f("orientation", "orientation"),
        f("financial-aid verification", "financial[- ]aid|verification"),
        soft("deposit paid", "deposit[^.]{0,40}(?:paid|received|posted|complete)"),
      ],
      forbidden: [forbid("says deposit unpaid", "deposit[^.]{0,40}(?:unpaid|outstanding|still due|not (?:yet )?paid)")],
    }),
  one("rg-sts-005", "status_overview", "staff", "leandro",
    "give me the picture on noor zephyrine",
    "Resolves Noor Zephyrine (unique): 2 overdue requirements (transcript, immunization), adviser Junia Pemberwell on leave, one open housing work item, no appointments.",
    {
      resolvedStudentId: `gt:${NOOR}.id`,
      facts: [
        f("adviser on leave", "on leave|leave"),
        f("transcript", "transcript"),
        soft("housing work item", "housing"),
        soft("immunization", "immuni[sz]ation"),
      ],
      forbidden: [NO_UUID],
    }),
  one("rg-sts-006", "status_overview", "staff", "priya",
    "latest enrollment status for Greta Everlyn?",
    "In progress: 6 of 8 done, 2 open (financial-aid verification, housing plans), nothing overdue, no open work items.",
    {
      resolvedStudentId: `gt:${GRETA_E}.id`,
      facts: [
        f("in progress / not complete", "in progress|not (?:yet )?complete|remaining|open|left"),
        f("count", `{{num:${GRETA_E}.requirements.openCount}}|{{num:${GRETA_E}.requirements.completedCount}}`),
      ],
      forbidden: [forbid("says overdue", "\\boverdue\\b"), forbid("says complete", "journey (?:is )?complete|has completed (?:her )?enrollment")],
    }),
  one("rg-sts-007", "status_overview", "staff", "matthias",
    "quick summary on SYN-001278",
    "Resolves Lucia Zephyrine by ref (no ambiguity via ref): in progress, 2 overdue requirements, 7 open work items.",
    {
      resolvedStudentId: `gt:${LUCIA}.id`,
      facts: [
        f("name", `{{gt:${LUCIA}.firstName}}`),
        f("open work items", `{{num:${LUCIA}.work.openCount}}|transcript|verification`),
      ],
      forbidden: [forbid("asks which Lucia despite the ref", "which (?:one|Lucia|student)")],
    }),
  one("rg-sts-008", "status_overview", "staff", "greta",
    "Bruno Stonebrook — where's he at?",
    "Completed journey; all documents accepted; nothing open. Honest empty rather than a fabricated to-do.",
    {
      resolvedStudentId: `gt:${BRUNO}.id`,
      facts: [f("complete / nothing open", "complete|all (?:set|done)|nothing (?:open|outstanding|pending)|no open")],
      forbidden: [forbid("invents blockers", "\\boverdue\\b|blocked by|is waiting on")],
    }),
];
