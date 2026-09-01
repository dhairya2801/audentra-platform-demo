/**
 * gen_ambiguity — one word, several operations.
 *
 * "Note", "flag", "ticket", "update", "cancel", "resolve" each name
 * different actions in different sentences. Edward must choose correctly,
 * ask when genuinely ambiguous, and never silently run the wrong operation.
 */

import { NO_CLAIMS } from "./common.mjs";

export const AMBIGUITY_CASES = [
  {
    id: "g-amb-001",
    family: "gen_ambiguity",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "write up a little note to Petra Yarrowby about her overdue deposit — I'll send it myself",
        expectedAnswer:
          "A request to compose: a draft for the human to send. Not a follow-up task, not a prepared send.",
        expect: {
          actionAnyOf: [null],
          hasDraft: true,
          noEffect: true,
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-amb-002",
    family: "gen_ambiguity",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "make a note to call Petra Yarrowby thursday",
        expectedAnswer:
          "A note-to-self is a follow-up task for the asker, due Thursday — not a message to Petra.",
        expect: {
          action: "operations.follow_up.create",
          targetStudent: "yarrowby",
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-amb-003",
    family: "gen_ambiguity",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "note on AST-01344 that Vera rang back this morning",
        expectedAnswer:
          "A record on the existing item: its next step gains the fact. An update proposal with the text, or one question. Not a new task, not a message.",
        expect: {
          actionAnyOf: ["operations.work_item.update", null],
          effect: { workItemsCreated: 0 },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-amb-004",
    family: "gen_ambiguity",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "send Kaito Jessamy a note about orientation",
        expectedAnswer:
          "\"Send\" makes it a message. Edward does not send: the boundary explains draft → prepare → confirmed send.",
        expect: {
          actionAnyOf: [null],
          responseKind: ["boundary:send_message", "boundary:sms", ""],
          noEffect: true,
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-amb-005",
    family: "gen_ambiguity",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "flag Bruno Oakenshaw for me",
        expectedAnswer:
          "\"Flag\" with no object: most plausibly a follow-up for Bruno assigned to the asker; a clarifying question is equally right. A student summary is neither.",
        expect: {
          actionAnyOf: ["operations.follow_up.create", null],
          targetStudent: "oakenshawBruno",
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-amb-006",
    family: "gen_ambiguity",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "can you ticket the deposit chase for Liora Brightwater?",
        expectedAnswer: "\"Ticket\" as a verb: a follow-up for Liora Brightwater about the deposit.",
        expect: {
          action: "operations.follow_up.create",
          targetStudent: "brightwaterLiora",
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-amb-007",
    family: "gen_ambiguity",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "close out the Elena Calderwood thing",
        expectedAnswer:
          "Two Elena Calderwoods, each with an open item (AST-00507, AST-01070). Edward must ask which — closing either silently is wrong.",
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
    id: "g-amb-008",
    family: "gen_ambiguity",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "cancel AST-01936 — duplicate, I already track Kwame elsewhere",
        expectedAnswer:
          "A cancellation with its reason in the sentence. Ideal: a cancel preview carrying the reason. Acceptable: one question asking for the reason for the record.",
        expect: {
          actionAnyOf: ["operations.work_item.update", null],
          noEffect: true,
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-amb-009",
    family: "gen_ambiguity",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "update Petra Yarrowby — she's thinking about deferring to spring",
        expectedAnswer:
          "\"Update\" with no updatable object: Petra has no work item of the asker's, and staff cannot edit her record. Ask what to update, or offer a follow-up. Nothing silent.",
        expect: {
          actionAnyOf: [null, "operations.follow_up.create"],
          noEffect: true,
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-amb-010",
    family: "gen_ambiguity",
    actorKind: "student",
    actor: "calderwoodFiona",
    turns: [
      {
        user: "make a note on my file that I'm away til the 15th",
        expectedAnswer:
          "Students have no free-text note primitive. Honest: say so; a support request can carry the message to a person — offer it.",
        expect: {
          actionAnyOf: [null, "student.support.contact"],
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-amb-011",
    family: "gen_ambiguity",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "email myself a reminder about Greta Oakenshaw's housing form",
        expectedAnswer:
          "Edward prepares mail to students, not to staff. The equivalent that exists is a follow-up for Greta — offer that instead of failing silently.",
        expect: {
          actionAnyOf: [null, "operations.follow_up.create"],
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-amb-012",
    family: "gen_ambiguity",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "draft a follow-up plan for Camila Quillfeather",
        expectedAnswer:
          "Genuinely ambiguous between composing a plan (draft) and creating a follow-up task. Either a draft, a proposal, or one clarifying question is defensible; pretending to have done both, or neither, is not.",
        expect: {
          actionAnyOf: ["operations.follow_up.create", null],
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-amb-013",
    family: "gen_ambiguity",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "chase up that Netherby item when you get a chance",
        expectedAnswer:
          "Emre Netherby's AST-01482 is the only Netherby item of the asker's. A move to follow-up (with a date question) or a clarifying question about which change.",
        expect: {
          actionAnyOf: ["operations.work_item.update", "operations.follow_up.create", null],
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-amb-014",
    family: "gen_ambiguity",
    actorKind: "student",
    actor: "everlynHana1",
    turns: [
      {
        user: "flag my financial aid verification — it's been sitting there for ages",
        expectedAnswer:
          "From a student, \"flag\" means get attention on it: a support request linked to the aid verification requirement, previewed.",
        expect: {
          actionAnyOf: ["student.support.contact", null],
          soft: { requireAction: "student.support.contact" },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
];
