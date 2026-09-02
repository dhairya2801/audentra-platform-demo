import { S, ST, f, soft, forbid, one, ASKS_WHICH, NONE_PATTERN, FAKE_EMAIL, NO_UUID } from "./common.mjs";

const OMAR = S("omar");
const NOOR = S("noor");
const MATTHIAS = ST("matthias");

export const AMBIGUOUS_CASES = [
  one("rg-amb-001", "ambiguous_incomplete", "staff", "priya",
    "what's Lucia Zephyrine's status",
    "Three matches: asks which one (ideally listing distinguishing details) instead of picking.",
    {
      resolvedStudentId: null,
      facts: [ASKS_WHICH, soft("says three", "three|\\b3\\b")],
    }),
  one("rg-amb-002", "ambiguous_incomplete", "staff", "marcus",
    "pull up caleb dunmire",
    "Eight students share this name; must not resolve arbitrarily.",
    {
      resolvedStudentId: null,
      facts: [ASKS_WHICH, soft("says eight", "eight|\\b8\\b")],
    }),
  one("rg-amb-003", "ambiguous_incomplete", "staff", "greta",
    "who is the academic adviser for SYN-001726?",
    "Omar Vellacourt (by ref) has no primary adviser: must say none is assigned, not name one.",
    {
      resolvedStudentId: `gt:${OMAR}.id`,
      facts: [f("none assigned", "no (?:academic |primary )?adviser|not (?:yet )?(?:been )?assigned|hasn'?t been assigned|isn'?t (?:one|an? adviser|assigned)|without an? (?:academic )?adviser|unassigned")],
      forbidden: [forbid("names an adviser", "(?:adviser|advisor) is [A-Z][a-z]+ [A-Z]|@synthetic\\.aster\\.example")],
    }),
  one("rg-amb-004", "ambiguous_incomplete", "student", "omar",
    "who is my adviser",
    "No academic adviser assigned yet — says so; may point to the admissions counselor as the current contact, but must not invent an adviser.",
    {
      anyOfTools: [["getStudentAdvising", "getStudentAppointments"]],
      facts: [f("none assigned", "no (?:academic |primary )?adviser|not (?:yet )?(?:been )?assigned|hasn'?t been assigned|haven'?t been assigned|isn'?t (?:one|an? adviser)|don'?t (?:currently )?have an? (?:academic )?adviser")],
      forbidden: [forbid("invents an adviser email", "@synthetic\\.aster\\.example"), FAKE_EMAIL],
    }),
  one("rg-amb-005", "ambiguous_incomplete", "student", "noor",
    "when's my next appointment",
    "No appointments exist; says none is scheduled (and may suggest booking, noting the adviser is on leave).",
    {
      requiredTools: ["getStudentAppointments"],
      facts: [f("none", NONE_PATTERN + "|not (?:currently )?(?:have|scheduled)")],
      forbidden: [forbid("invents a date", "\\b(?:sep|oct|nov)[a-z]* \\d{1,2}\\b|\\b2026-\\d\\d-\\d\\d\\b")],
    }),
  one("rg-amb-006", "ambiguous_incomplete", "staff", "zelda",
    "what's going on with Priyanka Vellacourt-Osei?",
    "No such student: says not found; must not answer about a different Vellacourt.",
    {
      resolvedStudentId: null,
      facts: [f("not found", "(?:couldn'?t|could not|can'?t|cannot|unable to|did not|didn'?t) (?:find|locate|match)|no (?:student|record|match|one)(?: named| called| matching| by that name| found)|not (?:find|found|in the (?:roster|system))|isn'?t (?:a|any) (?:student|match)|no results")],
      forbidden: [forbid("answers about someone else", "(?:adviser|advisor) is [A-Z]|deposit (?:is|was|has)|open (?:work )?items? (?:are|is)")],
    }),
  one("rg-amb-007", "ambiguous_incomplete", "staff", "leandro",
    "look up Kwame Oakenshore for me",
    "Misspelled surname: either says no exact match and suggests Kwame Oakenshaw (asking to confirm), or reports not found — but must not silently present Oakenshaw's facts as the answer.",
    {
      factGroups: [
        [f("suggests the near match with a question", "Oakenshaw"), f("asks", "\\?|did you mean|do you mean|closest|similar|no exact match|confirm")],
        [f("not found", "(?:couldn'?t|could not|can'?t|cannot|unable to|did not|didn'?t) (?:find|locate|match)|no (?:student|record|match)|not found")],
      ],
      forbidden: [forbid("silently answers for Oakenshaw", "^(?![\\s\\S]*(?:\\?|did you mean|do you mean|closest|similar|no exact match|confirm|couldn'?t find|could not find|not found|no (?:student|record|match)))[\\s\\S]*(?:adviser is|open (?:work )?items|overdue|Bianca Netherby)")],
    }),
  one("rg-amb-008", "ambiguous_incomplete", "staff", "registrar",
    "how many open items does Matthias Gunnarson have",
    "Colleague name misspelled (one 's'): either resolves to Matthias Gunnarsson and answers 276, or asks to confirm the spelling. Must not say no such colleague without offering the near match.",
    {
      resolvedStudentId: null,
      factGroups: [
        [f("count", `{{num~:${MATTHIAS}.work.open}}`)],
        [f("suggests Gunnarsson", "Gunnarsson"), f("asks", "\\?|did you mean|do you mean|confirm")],
      ],
    }),
];
