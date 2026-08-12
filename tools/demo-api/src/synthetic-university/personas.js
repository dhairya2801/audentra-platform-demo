/**
 * Ten named synthetic students, each of which makes at least one Edward
 * question interesting to ask.
 *
 * Personas exist so a demo or an eval can say "ask Edward this, as Priya" and
 * get a reproducible answer. That only works if the persona's student id is
 * knowable without generating the universe first, so ids here are derived from
 * the persona key rather than drawn from the generator's stream: the generator
 * reserves the first ten student slots and stamps these ids onto them.
 *
 * Every name is deliberately invented. None is intended to resemble a real
 * person, and the surnames are constructed so they cannot be mistaken for a
 * real public figure.
 *
 * @typedef {object} SyntheticPersona
 * @property {string} key kebab-case identifier
 * @property {string} name display name
 * @property {string} firstName
 * @property {string} lastName
 * @property {string} preferredName
 * @property {string} studentId uuid, also present in `universe.students`
 * @property {string} externalRef "SYN-######"
 * @property {string} headline one line, safe to show in a picker
 * @property {string} scenario what state this student is in and why it matters
 * @property {string[]} interestingQuestions copy-pasteable questions for Edward
 * @property {string[]} stateKeys keys from STATE_MATRIX applied to this student
 */

import { uuidFromString } from "./random.js";

const DEFINITIONS = [
  {
    key: "clean-and-complete",
    firstName: "Wren",
    lastName: "Halloway",
    preferredName: "Wren",
    stateKeys: ["clean-complete", "residential"],
    headline:
      "Nothing outstanding: every document accepted, aid finalised, room assigned",
    scenario:
      "Wren deposited early, submitted every required document, and had all of them accepted. Verification completed, the award package is final rather than estimated, and the accepted awards plus the deposit settle the account to zero. A residence hall room and meal plan are assigned and move-in is scheduled. This persona is the control case: Edward should be able to say plainly that there is nothing left to do, and should not invent a task, a hold, or a deadline in order to sound useful.",
    interestingQuestions: [
      "Is there anything I still need to do before classes start?",
      "What is my current account balance?",
      "When can I move into my room?",
      "Has my financial aid been finalised or is it still an estimate?",
    ],
  },
  {
    key: "missing-documents",
    firstName: "Tobias",
    lastName: "Quillfeather",
    preferredName: "Toby",
    stateKeys: ["missing-documents", "single-hold"],
    headline:
      "Final transcript and immunisation record never submitted, one hold open",
    scenario:
      "Tobias never uploaded his final official transcript or his immunisation record; both sit at NOT_SUBMITTED with a status history that contains only the initial system entry. One hold is open against the account. The interesting failure mode is Edward reporting a document as 'being reviewed' when nothing was ever received, or listing causes for the registration block that the data does not support.",
    interestingQuestions: [
      "Which documents am I still missing?",
      "Why can't I register for classes?",
      "Who do I send my final transcript to?",
      "What happens if my immunisation record arrives after classes begin?",
    ],
  },
  {
    key: "document-under-review",
    firstName: "Marisol",
    lastName: "Fennwick",
    preferredName: "Mari",
    stateKeys: ["document-under-review", "no-holds"],
    headline: "Transcript uploaded and sitting in review, not yet accepted",
    scenario:
      "Marisol uploaded her transcript and it moved to UNDER_REVIEW, with the upload and the review both recorded in the status history. Submitted is not the same as accepted: the registration gate that depends on the transcript is still unsatisfied. Edward should be able to hold that distinction without either alarming her or telling her she is done.",
    interestingQuestions: [
      "Did you get my transcript?",
      "My transcript says under review — does that mean I am cleared?",
      "How long does document review usually take?",
      "Can I register while my transcript is still being reviewed?",
    ],
  },
  {
    key: "document-rejected",
    firstName: "Devon",
    lastName: "Ashgrove",
    preferredName: "Devon",
    stateKeys: ["document-rejected", "no-holds"],
    headline: "Submitted transcript was reviewed and rejected",
    scenario:
      "Devon's transcript was uploaded, reviewed, and rejected; the status history records all three steps and the final entry matches the document's current REJECTED status. Nothing has been resubmitted. The question worth watching is whether Edward distinguishes rejection from 'still processing', and whether it names the office that actually handles the resubmission.",
    interestingQuestions: [
      "Why was my transcript rejected?",
      "What do I need to do to fix my rejected document?",
      "Does a rejected transcript stop me from registering?",
      "Who can I talk to about my transcript?",
    ],
  },
  {
    key: "multiple-holds-blocked",
    firstName: "Ingrid",
    lastName: "Thistlebrook",
    preferredName: "Ingrid",
    stateKeys: ["multiple-holds", "partial-tuition-coverage"],
    headline:
      "Three open holds from three offices, aid finalised but only partly covering charges",
    scenario:
      "Ingrid has three simultaneous open holds placed by different offices. Her award package is final rather than estimated, but it covers only part of her charges, so a real balance is outstanding. Not all three holds block registration, and only some block transcripts. This persona punishes hand-waving: the correct answer enumerates exactly the holds present, says which of them actually blocks what, and routes each to its own resolution office rather than offering one generic contact.",
    interestingQuestions: [
      "Why can't I register for classes?",
      "What holds are on my account right now?",
      "Which office do I contact to clear each hold?",
      "If I pay my balance today, does everything unblock?",
      "Can I still request a transcript with these holds?",
    ],
  },
  {
    key: "no-aid-self-pay",
    firstName: "Cassius",
    lastName: "Pemberwell",
    preferredName: "Cass",
    stateKeys: ["no-aid", "balance-owed"],
    headline: "No FAFSA, no awards, paying the full balance out of pocket",
    scenario:
      "Cassius never filed a FAFSA, so there is no aid record of any kind: no FAFSA row, no verification requirements, no awards, no disbursements. His full charges stand against the account. The tempting error is for Edward to talk about 'pending aid' or a 'disbursement date' for a student who has no aid file at all, or to imply that aid is on its way.",
    interestingQuestions: [
      "When will my financial aid be disbursed?",
      "How much do I owe and when is it due?",
      "Is it too late to apply for financial aid?",
      "Can I set up a payment plan?",
    ],
  },
  {
    key: "disbursed-with-refund",
    firstName: "Odalys",
    lastName: "Brightwater",
    preferredName: "Oda",
    stateKeys: ["aid-disbursed", "refund-due", "no-holds"],
    headline: "Aid disbursed above charges, leaving a credit balance to refund",
    scenario:
      "Odalys completed verification, accepted her full award package, and every accepted award has disbursed to her account. The disbursed total exceeds her charges, so the ledger carries a credit balance and no refund has been issued yet. A credit balance is not the same as money already in her bank account, and a negative account balance is not a debt: both readings are easy to get backwards.",
    interestingQuestions: [
      "My balance is negative — do I owe money or does the university owe me?",
      "When will I get my refund?",
      "How much of my aid has actually disbursed?",
      "Can I use my credit balance to buy books?",
    ],
  },
  {
    key: "international-check-in",
    firstName: "Ines",
    lastName: "Calderwood",
    preferredName: "Ines",
    stateKeys: ["international-student", "selected-for-verification", "no-holds"],
    headline:
      "International admit selected for verification with immigration steps open",
    scenario:
      "Ines is an international admit, so she carries I-20, SEVIS fee, visa interview, and English proficiency requirements alongside the ordinary checklist, and her registration eligibility includes an international check-in gate the domestic students do not have. On top of that her FAFSA was selected for verification, so her aid is estimated rather than final. Two independent workflows are outstanding at once and they block different things, which is exactly where an assistant tends to conflate them.",
    interestingQuestions: [
      "What do I still need to complete before I can register?",
      "Does my verification hold up my visa check-in, or are they separate?",
      "Is my financial aid amount final?",
      "When is international check-in and what do I bring?",
    ],
  },
  {
    key: "transfer-credit-review",
    firstName: "Rufus",
    lastName: "Tanglewood",
    preferredName: "Rufus",
    stateKeys: ["transfer-student", "document-under-review", "no-holds"],
    headline: "Transfer admit whose prior transcript is still under review",
    scenario:
      "Rufus transferred in, and the transcript that carries his prior credit is uploaded but still under review. Until it is accepted, the credit it would grant is not recorded anywhere in the data. The interesting question is whether Edward answers 'how many credits transferred' with a number it cannot source, or correctly says the evaluation has not completed.",
    interestingQuestions: [
      "How many of my credits transferred?",
      "Is my transfer transcript still being evaluated?",
      "Do I need to retake anything I already passed?",
      "Can I register before my transfer credit is posted?",
    ],
  },
  {
    key: "deposit-deadline-passed",
    firstName: "Georgina",
    lastName: "Underhollow",
    preferredName: "Georgie",
    stateKeys: ["deadline-passed", "no-holds"],
    headline: "Admitted, deposit deadline passed, nothing paid, no housing",
    scenario:
      "Georgina was admitted but her deposit deadline has already passed and no deposit has posted. Because the deposit gates both housing and registration, she has no housing application, no residence hall assignment, and an ineligible registration record. Every downstream question about move-in, roommates, or course selection has the same single root cause, and the honest answer says so rather than listing symptoms.",
    interestingQuestions: [
      "Why can't I apply for housing?",
      "My deposit deadline passed — can I still enrol?",
      "When can I pick my classes?",
      "Have I lost my spot?",
    ],
  },
];

/** @type {readonly SyntheticPersona[]} */
export const SYNTHETIC_PERSONAS = Object.freeze(
  DEFINITIONS.map((definition, index) =>
    Object.freeze({
      key: definition.key,
      name: `${definition.firstName} ${definition.lastName}`,
      firstName: definition.firstName,
      lastName: definition.lastName,
      preferredName: definition.preferredName,
      // Derived from the key so the id is a constant of this file, not of the
      // generator's draw order.
      studentId: uuidFromString(`synthetic-university:persona:${definition.key}`),
      externalRef: `SYN-${String(index).padStart(6, "0")}`,
      headline: definition.headline,
      scenario: definition.scenario,
      interestingQuestions: Object.freeze([...definition.interestingQuestions]),
      stateKeys: Object.freeze([...definition.stateKeys]),
      isSynthetic: true,
    }),
  ),
);

/** @type {ReadonlyMap<string, SyntheticPersona>} */
export const SYNTHETIC_PERSONAS_BY_KEY = new Map(
  SYNTHETIC_PERSONAS.map((persona) => [persona.key, persona]),
);

/**
 * @param {any} universe
 * @param {string} key
 * @returns {any | null} the generated student behind a persona, if present
 */
export function personaStudent(universe, key) {
  const persona = SYNTHETIC_PERSONAS_BY_KEY.get(key);
  if (!persona) return null;
  return (
    universe.students.find((student) => student.id === persona.studentId) ?? null
  );
}
