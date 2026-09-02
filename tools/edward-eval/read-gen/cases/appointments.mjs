import { S, ST, f, soft, forbid, one, NONE_PATTERN, NO_UUID } from "./common.mjs";

const HANA = S("hana");
const NOOR = S("noor");
const KWAME = S("kwame");
const ADA = S("ada");
const GRETA = ST("greta");
const MATTHIAS = ST("matthias");
const ZELDA = ST("zelda");

export const APPOINTMENTS_CASES = [
  one("rg-apt-001", "appointments", "student", "hana",
    "what appointments do I have coming up",
    "Two upcoming: academic advising with Omar Glimmerly and an international check-in with Matthias Gunnarsson, with dates.",
    {
      requiredTools: ["getStudentAppointments"],
      facts: [
        f("adviser appointment", `{{re:${HANA}.appointments.upcoming.0.staffNamePattern}}`),
        f("international check-in", `{{re:${HANA}.appointments.upcoming.1.staffNamePattern}}`),
        f("first date", `{{date:${HANA}.appointments.upcoming.0.date}}`),
        soft("second date", `{{date:${HANA}.appointments.upcoming.1.date}}`),
      ],
      forbidden: [NO_UUID],
    }),
  one("rg-apt-002", "appointments", "student", "noor",
    "anything on my calendar this month?",
    "Nothing scheduled — this student has never had an appointment. Must not invent one.",
    {
      requiredTools: ["getStudentAppointments"],
      facts: [f("none", NONE_PATTERN)],
      forbidden: [forbid("invents an appointment", "(?:scheduled|booked|have) (?:an? )?(?:appointment|meeting)[^.]{0,40}(?:on|at|with) [A-Z]|\\b(?:sep|oct)[a-z]* \\d{1,2}\\b")],
    }),
  one("rg-apt-003", "appointments", "student", "kwame",
    "when's my next meeting w/ my adviser",
    "The scheduled academic advising appointment with Bianca Netherby, with its date.",
    {
      requiredTools: ["getStudentAppointments"],
      facts: [
        f("adviser", `{{re:${KWAME}.appointments.next.staffNamePattern}}`),
        f("date", `{{date:${KWAME}.appointments.next.date}}`),
      ],
    }),
  one("rg-apt-004", "appointments", "student", "ada",
    "have I ever no-showed on anything?",
    "Yes, one no-show (an advising appointment with Noor Brightwater on the recorded date).",
    {
      requiredTools: ["getStudentAppointments"],
      facts: [f("yes one", "\\byes\\b|one no[- ]show|\\b1 no[- ]show|missed one|no[- ]show(?:ed)? (?:on|for)")],
      forbidden: [forbid("denies the no-show", "no (?:recorded )?no[- ]shows|haven'?t (?:missed|no-showed)|never (?:missed|no-showed)")],
    }),
  one("rg-apt-005", "appointments", "staff", "greta",
    "what meetings do i have today",
    "Today's scheduled appointments (count from the calendar) with student names.",
    {
      resolvedStudentId: null,
      anyOfTools: [["getStaffAppointments", "getMorningBriefing", "getStaffProfile"]],
      facts: [
        f("count", `{{num:${GRETA}.appointments.today}}`),
        f("a student on today's list", `{{any:${GRETA}.appointments.todayStudents}}`),
      ],
      forbidden: [NO_UUID],
    }),
  one("rg-apt-006", "appointments", "staff", "matthias",
    "whats on my calendar this week",
    "The count of scheduled appointments this week (Mon–Sun or next 7 days — either reading is accepted) with at least one student named.",
    {
      resolvedStudentId: null,
      anyOfTools: [["getStaffAppointments", "getMorningBriefing", "getStaffProfile"]],
      factGroups: [
        [f("this-week count", `{{num~:${MATTHIAS}.appointments.thisWeek}}`)],
        [f("next-7-day count", `{{num~:${MATTHIAS}.appointments.next7}}`)],
      ],
      facts: [soft("names a student", `{{any:${MATTHIAS}.appointments.next14List|student}}`)],
    }),
  one("rg-apt-007", "appointments", "staff", "zelda",
    "who am I meeting on {{gt:" + ZELDA + ".appointments.next14List.0.date}}?",
    "Names the student scheduled with her on that date.",
    {
      resolvedStudentId: null,
      anyOfTools: [["getStaffAppointments", "getStaffProfile"]],
      facts: [f("student", `{{gt:${ZELDA}.appointments.next14List.0.student}}`)],
    }),
  one("rg-apt-008", "appointments", "staff", "priya",
    "do I have any appointments coming up",
    "None — she has no availability pattern and no scheduled appointments. Honest empty.",
    {
      resolvedStudentId: null,
      anyOfTools: [["getStaffAppointments", "getStaffProfile", "getMorningBriefing"]],
      facts: [f("none", NONE_PATTERN)],
      forbidden: [forbid("invents an appointment", "(?:appointment|meeting) (?:with|on) [A-Z][a-z]+ [A-Z]")],
    }),
];
