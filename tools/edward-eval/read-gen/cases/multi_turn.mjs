import { S, ST, f, soft, forbid, conversation, ASKS_WHICH, NONE_PATTERN, FAKE_EMAIL, NO_UUID } from "./common.mjs";

const LUCIA = S("lucia");
const LUCIA_DESIGN = S("lucia_design");
const KWAME = S("kwame");
const HANA = S("hana");
const NOOR = S("noor");
const GUSTAV = S("gustav");
const ADRIA = S("adria");
const GRETA = ST("greta");
const MATTHIAS = ST("matthias");

export const MULTI_TURN_CASES = [
  conversation("rg-mt-001", "multi_turn", "student", "lucia",
    "Adviser → pronoun follow-up for email → switch to the FA counselor.",
    [
      { question: "who's my adviser?", expect: { facts: [f("adviser", `{{re:${LUCIA}.primaryAdviser.namePattern}}`)], forbidden: [FAKE_EMAIL] } },
      { question: "and their email?", expect: { facts: [f("adviser email", `{{gt:${LUCIA}.primaryAdviser.email}}`)], forbidden: [FAKE_EMAIL] } },
      { question: "what about my financial aid counselor?", expect: { facts: [f("FA counselor", `{{re:${LUCIA}.financialAidCounselor.namePattern}}`)], forbidden: [FAKE_EMAIL] } },
    ]),
  conversation("rg-mt-002", "multi_turn", "student", "kwame",
    "Overdue → ellipsis to documents → ellipsis to the next appointment.",
    [
      { question: "what's overdue for me", expect: { facts: [f("immunization", "immuni[sz]ation"), f("verification", "verification")] } },
      { question: "what about documents?", expect: { facts: [f("rejected immunization doc", "immuni[sz]ation[^.]{0,80}reject|reject[^.]{0,80}immuni[sz]ation")] } },
      { question: "and my next appointment?", expect: { facts: [f("date", `{{date:${KWAME}.appointments.next.date}}`), soft("with", `{{re:${KWAME}.appointments.next.staffNamePattern}}`)] } },
    ]),
  conversation("rg-mt-003", "multi_turn", "student", "hana",
    "List appointments → 'the second one' must pick the international check-in.",
    [
      { question: "what appointments do i have", expect: { facts: [f("first", `{{re:${HANA}.appointments.upcoming.0.staffNamePattern}}`), f("second", `{{re:${HANA}.appointments.upcoming.1.staffNamePattern}}`)] } },
      { question: "the second one — who is that with and where?", expect: { facts: [f("second staff", `{{re:${HANA}.appointments.upcoming.1.staffNamePattern}}`), soft("location", `{{gt:${HANA}.appointments.upcoming.1.location}}`)] } },
    ]),
  conversation("rg-mt-004", "multi_turn", "staff", "leandro",
    "Pull up a student → 'her adviser' → 'her housing item' all resolve to the same student.",
    [
      { question: "pull up Noor Zephyrine", expect: { resolvedStudentId: `gt:${NOOR}.id`, facts: [f("name", `{{gt:${NOOR}.firstName}}`)] } },
      { question: "is her adviser around?", expect: { resolvedStudentId: `gt:${NOOR}.id`, facts: [f("on leave", "on leave|leave|away|not (?:currently )?available")] } },
      { question: "who owns her housing item?", expect: { resolvedStudentId: `gt:${NOOR}.id`, facts: [f("owner", `{{re:${NOOR}.work.open.0.assigneePattern}}`)] } },
    ]),
  conversation("rg-mt-005", "multi_turn", "staff", "matthias",
    "Queue → a student → back to the queue; the third turn must not stay pinned to the student.",
    [
      { question: "what's on my plate", expect: { resolvedStudentId: null, facts: [f("open count", `{{num~:${MATTHIAS}.work.open}}`)] } },
      { question: "what's overdue for Kwame Oakenshaw?", expect: { resolvedStudentId: `gt:${KWAME}.id`, facts: [f("immunization", "immuni[sz]ation")] } },
      { question: "ok back to my queue — how many are urgent?", expect: { facts: [f("urgent count", `{{num~:${MATTHIAS}.work.urgent}}`)], forbidden: [forbid("answers about Kwame instead", `Kwame[^.]{0,40}urgent|{{num:${KWAME}.work.urgentCount}} urgent item`)] } },
    ]),
  conversation("rg-mt-006", "multi_turn", "staff", "priya",
    "Ambiguous name → ask which → 'the one in Design' picks SYN-001898.",
    [
      { question: "who advises Lucia Zephyrine?", expect: { resolvedStudentId: null, facts: [ASKS_WHICH] } },
      { question: "the one in Design", expect: { resolvedStudentId: `gt:${LUCIA_DESIGN}.id`, facts: [f("adviser", `{{re:${LUCIA_DESIGN}.primaryAdviser.namePattern}}`)] } },
    ]),
  conversation("rg-mt-007", "multi_turn", "staff", "zelda",
    "Ambiguous name → 'the first one' must pick one of the listed Omar Vellacourts (any of the four) and answer for that one.",
    [
      { question: "tell me about Omar Vellacourt", expect: { resolvedStudentId: null, facts: [ASKS_WHICH] } },
      { question: "the first one", expect: { resolvedStudentIn: "cohorts.sameName.Omar Vellacourt.entries", forbidden: [NO_UUID] } },
    ]),
  conversation("rg-mt-008", "multi_turn", "staff", "greta",
    "Queue count → subset (blocked) → superlative (oldest) all against her own queue.",
    [
      { question: "what's on my plate", expect: { resolvedStudentId: null, facts: [f("open count", `{{num:${GRETA}.work.open}}`)] } },
      { question: "which of those are blocked?", expect: { facts: [f("blocked count", `{{num:${GRETA}.work.blocked}}|{{any:${GRETA}.work.blockedItems|key}}`)] } },
      { question: "and the oldest?", expect: { factGroups: [[f("oldest key", `{{gt:${GRETA}.work.oldestOpen.key}}`)], [f("oldest student", `{{gt:${GRETA}.work.oldestOpen.student}}`)]] } },
    ]),
  conversation("rg-mt-009", "multi_turn", "student", "gustav",
    "Overdue → 'and my deposit?' → 'who can help me with that' (no adviser; FA counselor or admissions counselor or the accounts office are all honest).",
    [
      { question: "whats overdue", expect: { facts: [f("transcript", "transcript"), f("orientation", "orientation")] } },
      { question: "and my deposit?", expect: { facts: [f("unpaid", `{{re:${GUSTAV}.deposit.pattern}}`)], forbidden: [forbid("says paid", "(?:has been|was|is) (?:paid|received)")] } },
      { question: "who can help me with that", expect: { factGroups: [[f("FA counselor", `{{re:${GUSTAV}.financialAidCounselor.namePattern}}`)], [f("admissions counselor", `{{gt:${GUSTAV}.admissionsCounselor.name}}`)], [f("student accounts / payments", "student accounts|payments page|/payments|enrollment services")]], forbidden: [FAKE_EMAIL, forbid("invents an academic adviser", "your (?:academic )?adviser,? [A-Z][a-z]+ [A-Z]")] } },
    ]),
  conversation("rg-mt-010", "multi_turn", "staff", "registrar",
    "Adviser (departed) → 'what is she waiting on' → 'and her documents?' — all for Adria Kettleby.",
    [
      { question: "who is Adria Kettleby's adviser", expect: { resolvedStudentId: `gt:${ADRIA}.id`, facts: [f("adviser", `{{re:${ADRIA}.primaryAdviser.namePattern}}`), soft("departed", "departed|no longer|left")] } },
      { question: "what is she waiting on?", expect: { resolvedStudentId: `gt:${ADRIA}.id`, facts: [f("transcript", "transcript"), f("housing", "housing")] } },
      { question: "and her documents?", expect: { resolvedStudentId: `gt:${ADRIA}.id`, facts: [f("under review", "under review|in review|being reviewed|pending"), soft("i20", "i-?20")] } },
    ]),
];
