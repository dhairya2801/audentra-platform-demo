import { S, ST, f, soft, forbid, one, FAKE_EMAIL, NO_UUID } from "./common.mjs";

const LUCIA = S("lucia");
const GRETA_E = S("greta_e");
const NOOR = S("noor");
const ADRIA = S("adria");
const ZELDA = ST("zelda");
const HANA_D = ST("hana_d");
const JUNIA = ST("junia");
const ADA_A = ST("ada_a");

export const AVAILABILITY_CASES = [
  one("rg-avl-001", "availability", "student", "lucia",
    "when can I actually see my adviser",
    "Names Caleb Mossbank and says he is bookable (or gives a next open slot / the booking page); no invented slot if none exists.",
    {
      anyOfTools: [["getStudentAdvising", "getStudentAppointments"]],
      facts: [
        f("adviser", `{{re:${LUCIA}.primaryAdviser.namePattern}}`),
        f("bookability statement", `{{re:${LUCIA}.primaryAdviser.bookablePattern}}`),
      ],
      forbidden: [FAKE_EMAIL],
    }),
  one("rg-avl-002", "availability", "student", "greta_e",
    "any chance i can get in with my adviser this week??",
    "Adviser Harriet Vasquez is on vacation this week (booking-blocking time off), so the honest answer is not this week / not right now, with the return date if given.",
    {
      anyOfTools: [["getStudentAdvising", "getStudentAppointments"]],
      facts: [
        f("adviser", `{{re:${GRETA_E}.primaryAdviser.namePattern}}`),
        f("availability statement matches data", `{{re:${GRETA_E}.primaryAdviser.bookablePattern}}`),
      ],
      forbidden: [FAKE_EMAIL],
    }),
  one("rg-avl-003", "availability", "student", "noor",
    "is my adviser even bookable",
    "Says no — Junia Pemberwell is on leave (until the recorded date) — and points to a way forward rather than inventing a slot.",
    {
      anyOfTools: [["getStudentAdvising", "getStudentAppointments"]],
      facts: [
        f("on leave / not bookable", `{{re:${NOOR}.primaryAdviser.bookablePattern}}`),
        soft("return date", `{{date:${NOOR}.primaryAdviser.leaveUntil}}|{{date:${NOOR}.primaryAdviser.currentAbsenceEnds}}`),
      ],
      forbidden: [forbid("invents a slot", "next (?:open|available) slot is|available (?:on|at) (?:mon|tue|wed|thu|fri)[a-z]* \\d")],
    }),
  one("rg-avl-004", "availability", "student", "adria",
    "Can I book time with my adviser?",
    "Adviser Quentin Zephyrine has departed: says he is no longer available / a new adviser has not been assigned, does not offer his slots.",
    {
      anyOfTools: [["getStudentAdvising", "getStudentAppointments"]],
      facts: [f("departed / not bookable", `{{re:${ADRIA}.primaryAdviser.bookablePattern}}`)],
      forbidden: [forbid("offers the departed adviser's slots", `{{re:${ADRIA}.primaryAdviser.namePattern}}[^.]{0,60}(?:next (?:open|available)|open slot|is available (?:on|at))`)],
    }),
  one("rg-avl-005", "availability", "staff", "priya",
    "Is Zelda Jokinen bookable right now?",
    "Reads the colleague's availability: active, student-facing, weekly pattern present, no current absence → yes.",
    {
      resolvedStudentId: null,
      anyOfTools: [["getStaffAvailability", "getStaffProfile", "searchStaff"]],
      facts: [f("bookable statement", `{{re:${ZELDA}.availability.bookablePattern}}`), soft("colleague", `{{re:${ZELDA}.namePattern}}`)],
      forbidden: [NO_UUID],
    }),
  one("rg-avl-006", "availability", "staff", "leandro",
    "what does Hana Dunmire's Wednesday look like",
    "Describes her Wednesday pattern from staff_availability (a morning in-person block and an afternoon virtual block) and/or her scheduled Wednesday appointments; must not say she has no availability.",
    {
      resolvedStudentId: null,
      anyOfTools: [["getStaffAvailability", "getStaffAppointments", "getStaffProfile"]],
      factGroups: [
        [f("morning start", `{{re:${HANA_D}.availability.wednesday.0.startPattern}}`)],
        [f("afternoon start", `{{re:${HANA_D}.availability.wednesday.1.startPattern}}`)],
        [f("appointment count for the week", `{{num~:${HANA_D}.appointments.next7}}|{{num~:${HANA_D}.appointments.thisWeek}}`)],
        [f("names a Wednesday appointment", `{{any:${HANA_D}.appointments.next14List|student}}`)],
      ],
      forbidden: [forbid("says no availability", "no availability|not available on wednesday|doesn'?t (?:work|have hours) (?:on )?wednesday")],
    }),
  one("rg-avl-007", "availability", "staff", "registrar",
    "when is junia pemberwell next available",
    "She is on leave; gives the recorded return date rather than a slot.",
    {
      resolvedStudentId: null,
      anyOfTools: [["getStaffAvailability", "getStaffProfile", "searchStaff"]],
      facts: [
        f("on leave", "on leave|leave|away|absen"),
        f("return date", `{{date:${JUNIA}.leaveUntil}}|{{date:${JUNIA}.availability.currentAbsenceEnds}}`),
      ],
      forbidden: [forbid("invents a slot", "next (?:open|available) slot is (?:mon|tue|wed|thu|fri|sat|sun)|tomorrow at \\d")],
    }),
  one("rg-avl-008", "availability", "staff", "matthias",
    "Is Ada Ashgrove taking appointments next week? Need to hand a student to her.",
    "Reads her availability and time off: reports her weekly pattern / bookability and the recorded vacation window; must not claim she has left or is on leave.",
    {
      resolvedStudentId: null,
      anyOfTools: [["getStaffAvailability", "getStaffProfile", "searchStaff", "getStaffAppointments"]],
      facts: [f("colleague", `{{re:${ADA_A}.namePattern}}`)],
      factGroups: [
        [f("mentions the vacation window", `{{date:${ADA_A}.availability.timeOff.0.startDate}}|{{date:${ADA_A}.availability.timeOff.0.endDate}}|vacation|time off`)],
        [f("bookability statement", `{{re:${ADA_A}.availability.bookablePattern}}`)],
      ],
      forbidden: [forbid("claims she left or is on leave", "on leave|departed|no longer with")],
    }),
];
