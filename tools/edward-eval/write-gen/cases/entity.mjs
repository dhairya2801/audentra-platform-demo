/**
 * gen_entity — who, exactly?
 *
 * Same names, near-names, first names, misspellings, keys that do not exist,
 * students on someone else's caseload. The recognizer may guess at language;
 * the gateway must never guess at identity.
 */

import { NO_CLAIMS } from "./common.mjs";

export const ENTITY_CASES = [
  {
    id: "g-ent-001",
    family: "gen_entity",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "create a follow-up for Hana Everlyn about her deposit",
        expectedAnswer:
          "Two Hana Everlyns share this caseload. Edward must ask which — with something that distinguishes them — not pick one.",
        expect: {
          actionAnyOf: [null],
          clarifies: true,
          noEffect: true,
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-ent-002",
    family: "gen_entity",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "make a task for Caleb Dunmire to book his advising session",
        expectedAnswer:
          "Eight Caleb Dunmires exist in the university; exactly one is on this caseload. Resolving to the caseload one, or asking, are both right. Resolving to any other is wrong.",
        expect: {
          actionAnyOf: ["operations.follow_up.create", null],
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-ent-003",
    family: "gen_entity",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "put a follow-up in for Georgena Underhollow",
        expectedAnswer:
          "A one-letter misspelling of Georgina Underhollow. Resolve her (saying so), or ask — never invent a person and never fail silently.",
        expect: {
          actionAnyOf: ["operations.follow_up.create", null],
          targetStudent: "underhollow",
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-ent-004",
    family: "gen_entity",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "queue up a task for Delphine, she missed advising",
        expectedAnswer:
          "One Delphine on the caseload (Delphine Halloway): resolve her by first name.",
        expect: {
          actionAnyOf: ["operations.follow_up.create", null],
          targetStudent: "hallowayDelphine",
          soft: { requireAction: "operations.follow_up.create" },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-ent-005",
    family: "gen_entity",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "add a reminder to check on Milo",
        expectedAnswer:
          "Two Milos (Netherby, Underhollow). Ask which; do not pick.",
        expect: {
          actionAnyOf: [null],
          clarifies: true,
          noEffect: true,
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-ent-006",
    family: "gen_entity",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "create a follow-up for Liora Glimmerly about her adviser assignment",
        expectedAnswer:
          "Liora Glimmerly has no adviser and is not on this caseload: an honest scope denial naming who can act, plus the offer to show her record.",
        expect: {
          actionAnyOf: [null],
          deniedAnyOf: ["EDWARD_STUDENT_SCOPE_FORBIDDEN"],
          noEffect: true,
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-ent-007",
    family: "gen_entity",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "log a follow-up for Fiona Larkspur, she needs her transcript chased",
        expectedAnswer:
          "Fiona Larkspur is on Elena Larkspur's caseload, not this adviser's: honest scope denial with the route.",
        expect: {
          actionAnyOf: [null],
          deniedAnyOf: ["EDWARD_STUDENT_SCOPE_FORBIDDEN"],
          noEffect: true,
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-ent-008",
    family: "gen_entity",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "move AST-99999 to done",
        expectedAnswer: "No such work item. An honest not-found — no invented target.",
        expect: {
          actionAnyOf: [null],
          noEffect: true,
          previewOrDenialExplains: true,
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-ent-009",
    family: "gen_entity",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "mark MAN-0F00 as done",
        expectedAnswer: "A foreign-format key that resolves to nothing: honest not-found.",
        expect: {
          actionAnyOf: [null],
          noEffect: true,
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-ent-010",
    family: "gen_entity",
    actorKind: "student",
    actor: "whitlowe",
    turns: [
      {
        user: "I did the orientation registration already — tick it off for me",
        expectedAnswer:
          "Orientation registration is a structured form, not a your-word step: the honest answer says the form completes it and routes there. No forged completion.",
        expect: {
          actionAnyOf: [null, "student.requirement.submit_response"],
          noEffect: true,
          says: [/form|page|portal|register/i],
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-ent-011",
    family: "gen_entity",
    actorKind: "student",
    actor: "ironwoodHana",
    turns: [
      {
        user: "tell Hana Dunmire I need help with my health record thing",
        expectedAnswer:
          "The student's own adviser, named: a support request routed to her, previewed with the destination.",
        expect: {
          action: "student.support.contact",
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-ent-012",
    family: "gen_entity",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "create a follow-up for SYN-000033",
        expectedAnswer:
          "An external reference to a student with no adviser, outside this caseload: scope denial or a clarifying question — never a silent write.",
        expect: {
          actionAnyOf: [null],
          noEffect: true,
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-ent-013",
    family: "gen_entity",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "set up a follow-up for her, due tomorrow",
        expectedAnswer:
          "\"Her\" with no antecedent in a fresh conversation: ask which student.",
        expect: {
          actionAnyOf: [null],
          clarifies: true,
          noEffect: true,
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-ent-014",
    family: "gen_entity",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "make a task for Elena about the aid deadline",
        expectedAnswer:
          "Four caseload students and a staff colleague answer to Elena. Ask which; never pick one silently.",
        expect: {
          actionAnyOf: [null],
          clarifies: true,
          noEffect: true,
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
];
