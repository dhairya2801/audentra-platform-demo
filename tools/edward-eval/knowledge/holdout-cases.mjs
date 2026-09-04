/**
 * Institutional-knowledge suite — holdout bank. Unseen phrasings, some
 * unseen personas, run only after development-bank fixes are finished.
 */

import { STUDENTS } from "./personas.mjs";
import { f, soft, forbid, one, conversation } from "./cases.mjs";

const S = (key) => `students.${STUDENTS[key].ref}`;
const C = "corpus";
const K_STUDENT = "getInstitutionalPolicies";
const K_STAFF = "searchInstitutionalKnowledge";
const NO_UUID = forbid("leaks a UUID", "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}");
const NO_PHONE = forbid("invents a phone number", "\\(?\\b\\d{3}\\)?[-. ]\\d{3}[-. ]\\d{4}\\b");
const NO_REFUSAL = forbid("refuses institutional questions", "can only answer from approved sources, which I don'?t have");
const STANDARD = [NO_UUID, NO_PHONE, NO_REFUSAL];

export const HOLDOUT_CASES = [
  one("kh-001", "deadline_consequence", "student", "gustav",
    "everything's late for me. do i even still have a place at aster or is my offer gone?",
    "Offer kept; late items gate housing/adviser/registration; deposit follow-up cadence; extension possible via Admissions.",
    {
      requiredTools: [K_STUDENT],
      facts: [f("offer not gone", "still have|keep|not (?:gone|cancelled|canceled|withdrawn|lost)|hasn'?t been (?:cancelled|withdrawn)")],
      forbidden: [...STANDARD, forbid("says the offer is gone", "offer (?:is|has been|was) (?:gone|cancelled|canceled|rescinded|withdrawn)")],
    }),
  one("kh-002", "applies_to_me", "student", "greta_e",
    "im from abroad and starting in the spring — can i skip the health insurance charge if i already have a plan from home?",
    "No: F-1/J-1 cannot waive; prorated $1,110 for Spring entrants.",
    {
      requiredTools: [K_STUDENT],
      facts: [f("cannot waive", "cannot|can'?t|not (?:able|eligible|permitted)|\\bno\\b")],
      forbidden: [...STANDARD, forbid("lets her waive", "(?:yes|you can) (?:skip|waive)")],
    }),
  one("kh-003", "calendar", "student", "ximena",
    "last day to drop a class without it showing up on my transcript this spring?",
    "Spring 2027 add/drop deadline 29 January 2027.",
    {
      requiredTools: [K_STUDENT],
      facts: [f("date", `{{date:${C}.calendar.spring2027-add-drop-deadline}}`)],
      forbidden: [...STANDARD, forbid("fall date", "11 September|September 11")],
    }),
  one("kh-004", "record_plus_policy", "student", "ximena",
    "my transcript got rejected and my adviser is away — who fixes what?",
    "Registrar handles the rejected transcript (resubmit); the covering Assistant Director handles advising while Junia Pemberwell is on leave.",
    {
      requiredTools: [K_STUDENT],
      anyOfTools: [["getDocumentStatuses", "getOnboardingChecklist", "getStudentAdvising"]],
      facts: [
        f("registrar for the transcript", "registrar"),
        f("covering adviser", "Assistant Director|covering|covers"),
      ],
      forbidden: [...STANDARD],
    }),
  one("kh-005", "policy", "student", "bruno",
    "how many hours a week am i allowed to work on campus during the semester?",
    "20 hours a week in term (35 in breaks) once the SEVIS record is active.",
    {
      requiredTools: [K_STUDENT],
      facts: [f("20 hours", "20 hours|twenty hours")],
      forbidden: [...STANDARD, forbid("wrong limit", "\\b(?:10|15|25|30|40) hours a week")],
    }),
  one("kh-006", "applies_to_me", "student", "ivo",
    "as a transfer, do i need to go to orientation?",
    "Yes: attendance required for all new undergraduates; Transfer Orientation (Session D) 27 August 2026.",
    {
      requiredTools: [K_STUDENT],
      facts: [f("required", "\\byes\\b|required|must|mandatory|need to attend"), soft("transfer session", "Transfer Orientation|Session D|27 August|August 27")],
      forbidden: [...STANDARD, forbid("exempts transfers", "transfers? (?:are|is) exempt|not required for transfer")],
    }),
  one("kh-007", "deadline_consequence", "student", "noor",
    "what if i cant make my advising meeting before registration opens",
    "The first advising meeting gates registration; time tickets follow meeting completion; any same-department adviser at drop-in (Mon/Wed/Fri 13:00–15:00) counts; covering adviser while hers is on leave.",
    {
      requiredTools: [K_STUDENT],
      facts: [f("gate", "regist"), soft("drop-in or another adviser", "drop-in|any adviser|another adviser|same department|Assistant Director")],
      forbidden: [...STANDARD],
    }),
  one("kh-008", "policy", "student", "milo",
    "is there a fee for ordering my official transcript from aster, and can i order one if i owe money?",
    "$8; not released while a billing hold (past-due over $250) or conduct hold is on record.",
    {
      requiredTools: [K_STUDENT],
      facts: [f("fee", `{{gt:${C}.amounts.transcriptFee}}`), f("hold blocks", "hold|past[- ]due|\\$250")],
      forbidden: [...STANDARD],
    }),
  one("kh-009", "office_routing", "student", "camila",
    "i think i need a single room for medical reasons. who decides that and when did i need to ask?",
    "Accessibility Services (Wellness Center suite 110) approves housing accommodations; requests 30 days before move-in (Spring: before 16 January), later ones as space allows.",
    {
      requiredTools: [K_STUDENT],
      facts: [f("accessibility services", "Accessibility Services"), soft("30 days", "30 days")],
      forbidden: [...STANDARD],
    }),
  one("kh-010", "record_plus_policy", "student", "hana",
    "im on sap probation and thinking of dropping to 9 credits. bad idea?",
    "Probation means an academic plan; dropping below 12 affects aid (census recalculation), institutional scholarships require full time, and as an F-1 student she needs a DSO-authorised reduced course load first.",
    {
      requiredTools: [K_STUDENT],
      facts: [f("F-1 / DSO", "DSO|F-1|International Student Services|immigration"), soft("academic plan", "academic plan|probation")],
      forbidden: [...STANDARD, forbid("says fine", "^(?:sure|yes|no problem)")],
    }),
  one("kh-011", "staff_procedure", "staff", "uma",
    "student says they escalated a help request 3 days ago and nothing happened. what's the rule for escalations?",
    "A request past the office's service level escalates to the office's director the same day; Enrollment Services replies within one business day.",
    {
      requiredTools: [K_STAFF],
      facts: [f("director", "director"), soft("same day", "same (?:business )?day|one business day")],
      forbidden: [...STANDARD],
    }),
  one("kh-012", "staff_applies_to_student", "staff", "matthias",
    "Adria Kettleby wants to drop to 8 credits for medical reasons. what do I need before I can authorise it?",
    "Medical RCL: letter from a licensed physician/psychologist, adviser confirmation; decide within two business days; may be zero credits; up to 12 months.",
    {
      requiredTools: [K_STAFF],
      resolvedStudentId: `gt:${S("adria")}.id`,
      facts: [f("medical letter", "physician|psychologist|medical (?:letter|documentation)|licensed"), soft("two business days", "two business days|2 business days")],
      forbidden: [...STANDARD],
    }),
  one("kh-013", "staff_procedure", "staff", "housing",
    "how long does a student have to accept a waitlist offer, and what if they don't reply?",
    "48 hours; an unanswered offer moves them to the bottom of the list.",
    {
      requiredTools: [K_STAFF],
      facts: [f("48 hours", "48 hours|two days|2 days"), soft("bottom of list", "bottom|end of the (?:list|waitlist)")],
      forbidden: [...STANDARD],
    }),
  one("kh-014", "staff_applies_to_student", "staff", "greta",
    "Greta Everlyn asked whether her aid will pay before her spring bill is due. what do I tell her?",
    "Spring bill due 8 January 2027; aid disburses 22 January 2027 (after classes begin); anticipated aid reduces the amount due if conditions are clear; payment plan otherwise. (Greta Everlyn is a unique name; Omar Vellacourt is not.)",
    {
      requiredTools: [K_STAFF],
      resolvedStudentId: `gt:${S("greta_e")}.id`,
      facts: [f("disbursement date", `{{date:${C}.calendar.spring2027-aid-disbursement}}`), soft("anticipated aid", "anticipated|payment plan|deferment")],
      forbidden: [...STANDARD],
    }),
  one("kh-015", "staff_procedure", "staff", "zubin",
    "a student is asking for a religious exemption from vaccines. what do they submit and how long do we take?",
    "The exemption form with the student's signed statement (no clergy letter); Health Compliance Manager decides within seven business days; recorded as waived.",
    {
      requiredTools: [K_STAFF],
      facts: [f("signed statement", "signed statement|statement"), f("seven business days", "seven business days|7 business days")],
      forbidden: [...STANDARD, forbid("requires clergy letter", "(?<!no )(?<!No )clergy letter (?:is )?required|must (?:provide|submit) a clergy")],
    }),
  one("kh-016", "honesty", "staff", "registrar",
    "what's the average number of days our transcript evaluations are actually taking this month?",
    "Not measured: honest refusal (the published 15-business-day service level may be mentioned).",
    {
      facts: [f("not measured", "not (?:measured|tracked|recorded|something|available|provided)|doesn'?t (?:measure|track|record)|no (?:metric|data|record)|(?:can'?t|cannot|could ?n[o']t|unable to) (?:measure|calculate|tell|determine|find|provide)|do not provide")],
      forbidden: [...STANDARD, forbid("invents an average", "averag\\w* (?:of |is |are )?\\d+(?:\\.\\d+)? days")],
    }),
  conversation("kh-017", "multi_turn", "student", "lucia",
    "Calendar question then the personal follow-up.",
    [
      {
        question: "when's the census date and why does it matter?",
        expect: {
          requiredTools: [K_STUDENT],
          facts: [f("date", `{{date:${C}.calendar.fall2026-census}}`), soft("aid recalculated", "aid|credits|enrolled")],
          forbidden: [...STANDARD],
        },
      },
      {
        question: "and does it change anything about my visa status?",
        expect: {
          facts: [f("12 credits / full time", "12 credits|full[- ]time|twelve")],
          forbidden: [...STANDARD],
        },
      },
    ]),
  one("kh-018", "policy", "student", "ada",
    "can i use ai tools on my assignments?",
    "Depends on the syllabus; where silent, brainstorming/editing allowed but not submitted text or code, with acknowledgement; violations under the integrity policy.",
    {
      requiredTools: [K_STUDENT],
      facts: [f("syllabus rule", "syllabus|course(?:'s)? rule|instructor"), soft("acknowledge", "acknowledg|disclos")],
      forbidden: [...STANDARD, forbid("blanket ban", "(?:never|always) (?:allowed|prohibited|banned) (?:in every|across all)")],
    }),
];
