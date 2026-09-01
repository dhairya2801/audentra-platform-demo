/**
 * gen_context — the conversation is part of the request.
 *
 * Amendments, repetitions, references to earlier turns, corrections after
 * commit, and references that need real discourse understanding. The report
 * says tier 1 does not see the conversation; this family measures whether
 * that matters.
 */

import { NO_CLAIMS } from "./common.mjs";

export const CONTEXT_CASES = [
  {
    id: "g-ctx-001",
    family: "gen_context",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "Create a follow-up for Ada Kettleby about her financial aid verification",
        expectedAnswer: "A follow-up for Ada Kettleby, previewed and unconfirmed.",
        expect: {
          action: "operations.follow_up.create",
          targetStudent: "kettleby",
          forbidden: NO_CLAIMS,
        },
      },
      {
        user: "actually make it urgent",
        expectedAnswer: "A fresh proposal, same student, priority urgent.",
        expect: {
          action: "operations.follow_up.create",
          targetStudent: "kettleby",
          preview: { priority: "urgent" },
          forbidden: NO_CLAIMS,
        },
      },
      {
        user: "and due thursday please",
        expectedAnswer:
          "A fresh proposal, same student, urgent, due Thursday (2026-09-03). The named day must survive into the canonical row, not silently vanish.",
        expect: {
          action: "operations.follow_up.create",
          targetStudent: "kettleby",
          confirm: true,
          receipt: "succeeded",
          effect: {
            edwardWorkItem: { studentRef: "SYN-000061", priority: "urgent", dueAt: "2026-09-03" },
          },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-ctx-002",
    family: "gen_context",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "put a follow-up in for Elena Everlyn about her deposit",
        expectedAnswer: "A follow-up for Elena Everlyn, previewed.",
        expect: {
          action: "operations.follow_up.create",
          targetStudent: "everlyn",
          confirm: true,
          receipt: "succeeded",
          forbidden: NO_CLAIMS,
        },
      },
      {
        user: "and one for Greta Oakenshaw too",
        expectedAnswer:
          "The same action for a different student. Greta is resolved fresh; the previous target must not leak.",
        expect: {
          action: "operations.follow_up.create",
          targetStudent: "oakenshaw",
          notTargetStudent: "everlyn",
          confirm: true,
          receipt: "succeeded",
          effect: { edwardWorkItem: { studentRef: "SYN-002844" } },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-ctx-003",
    family: "gen_context",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "what's the state of Elena Pemberwell's checklist?",
        expectedAnswer: "A read about Elena Pemberwell.",
        expect: { forbidden: NO_CLAIMS },
      },
      {
        user: "create a follow-up for Ada Kettleby about her transcript",
        expectedAnswer: "A follow-up for Ada Kettleby.",
        expect: {
          action: "operations.follow_up.create",
          targetStudent: "kettleby",
          confirm: true,
          receipt: "succeeded",
          forbidden: NO_CLAIMS,
        },
      },
      {
        user: "same thing for the other student we were talking about",
        expectedAnswer:
          "The honest resolutions are Elena Pemberwell (the other student in this conversation) or a clarifying question. Acting on Ada again, or on nobody in particular, is wrong.",
        expect: {
          actionAnyOf: ["operations.follow_up.create", null],
          notTargetStudent: "kettleby",
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-ctx-004",
    family: "gen_context",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "create a follow-up for Ada about her transcript",
        expectedAnswer:
          "Three Adas share this caseload (Kettleby, Larkspur, Ravensworth). Edward must ask which, not guess.",
        expect: {
          actionAnyOf: [null],
          clarifies: true,
          noEffect: true,
          forbidden: NO_CLAIMS,
        },
      },
      {
        user: "the Larkspur one",
        expectedAnswer: "A follow-up for Ada Larkspur.",
        expect: {
          actionAnyOf: ["operations.follow_up.create", null],
          notTargetStudent: "kettleby",
          soft: { requireAction: "operations.follow_up.create" },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-ctx-005",
    family: "gen_context",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "what should I do about Petra Yarrowby's overdue deposit?",
        expectedAnswer: "A read/advice turn about Petra.",
        expect: { forbidden: NO_CLAIMS },
      },
      {
        user: "ok do what you suggested",
        expectedAnswer:
          "Unless the previous turn proposed one concrete supported action, the honest move is to ask which suggestion to act on — never to invent an action.",
        expect: {
          actionAnyOf: ["operations.follow_up.create", null],
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-ctx-006",
    family: "gen_context",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "create a follow-up for Vera Mossbank about her no-show",
        expectedAnswer: "A follow-up for Vera Mossbank.",
        expect: {
          action: "operations.follow_up.create",
          targetStudent: "mossbankVera",
          confirm: true,
          receipt: "succeeded",
          forbidden: NO_CLAIMS,
        },
      },
      {
        user: "now one for Anton Pemberwell as well",
        expectedAnswer: "A follow-up for Anton Pemberwell.",
        expect: {
          action: "operations.follow_up.create",
          targetStudent: "pemberwellAnton",
          confirm: true,
          receipt: "succeeded",
          forbidden: NO_CLAIMS,
        },
      },
      {
        user: "change the second one to high priority",
        expectedAnswer:
          "\"The second one\" is Anton's task. Correct: a change to that task (or a clarifying question naming both). Wrong: silently changing Vera's, or creating a third.",
        expect: {
          actionAnyOf: ["operations.work_item.update", "operations.follow_up.create", null],
          notTargetStudent: "mossbankVera",
          effect: { workItemsCreatedBetween: [2, 2] },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-ctx-007",
    family: "gen_context",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "create a follow-up for Petra Yarrowby due friday",
        expectedAnswer: "A follow-up for Petra due Friday.",
        expect: {
          action: "operations.follow_up.create",
          targetStudent: "yarrowby",
          confirm: true,
          receipt: "succeeded",
          effect: { edwardWorkItem: { studentRef: "SYN-002533", dueAt: "2026-09-04" } },
          forbidden: NO_CLAIMS,
        },
      },
      {
        user: "one for Kaito Jessamy too",
        expectedAnswer: "A follow-up for Kaito Jessamy, previewed.",
        expect: {
          action: "operations.follow_up.create",
          targetStudent: "jessamy",
          forbidden: NO_CLAIMS,
        },
      },
      {
        user: "give him the same deadline as Petra's",
        expectedAnswer:
          "The pending Kaito proposal should gain Friday (2026-09-04) as its due date — the deadline named by reference to the earlier task.",
        expect: {
          actionAnyOf: ["operations.follow_up.create", null],
          soft: { requireAction: "operations.follow_up.create", previewIncludes: ["2026-09-04"] },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-ctx-008",
    family: "gen_context",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "create a follow-up for Emre Kettleby due tomorrow",
        expectedAnswer: "A follow-up for Emre Kettleby due tomorrow (2026-09-02).",
        expect: {
          action: "operations.follow_up.create",
          targetStudent: "kettlebyEmre",
          forbidden: NO_CLAIMS,
        },
      },
      {
        user: "sorry — I meant thursday, not tomorrow",
        expectedAnswer:
          "A correction. The amended proposal must carry Thursday (2026-09-03), not re-assert tomorrow. Asking to confirm which Thursday is acceptable.",
        expect: {
          actionAnyOf: ["operations.follow_up.create", null],
          soft: { previewExcludes: ["2026-09-02"] },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-ctx-009",
    family: "gen_context",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "draft an email to Tessa Glimmerly about her rejected transcript",
        expectedAnswer: "A draft for review.",
        expect: { hasDraft: true, forbidden: NO_CLAIMS },
      },
      {
        user: "prepare it",
        expectedAnswer: "A hash-pinned send intent from the reviewed draft.",
        expect: {
          action: "communications.email.prepare",
          targetStudent: "glimmerlyTessa",
          forbidden: NO_CLAIMS,
        },
      },
      {
        user: "hold on — route that to the registrar's office instead of Tessa",
        expectedAnswer:
          "Edward prepares mail to one named student only. The honest answer explains that and offers the real route; it must not silently retarget the message.",
        expect: {
          actionAnyOf: [null],
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-ctx-010",
    family: "gen_context",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "which of my students have rejected transcripts?",
        expectedAnswer:
          "A read: five caseload students (Kaito Halloway, Tessa Glimmerly, Zara Stonebrook, Rosa Mossbank, Helia Ravensworth).",
        expect: { forbidden: NO_CLAIMS },
      },
      {
        user: "create a follow-up for the Halloway one with the rejected transcript",
        expectedAnswer:
          "Two students are named Kaito Halloway; exactly one has a rejected transcript (SYN-002253). Correct: bind that one, or ask with the distinguishing detail. Wrong: silently binding the other.",
        expect: {
          actionAnyOf: ["operations.follow_up.create", null],
          targetStudent: "hallowayKaito2",
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-ctx-011",
    family: "gen_context",
    actorKind: "student",
    actor: "ashgroveNadia",
    turns: [
      {
        user: "change my preferred name to Mila",
        expectedAnswer: "A preferred-name change to Mila, previewed and confirmed.",
        expect: {
          action: "student.preferences.update",
          confirm: true,
          receipt: "succeeded",
          effect: { profile: { preferredName: "Mila" } },
          forbidden: NO_CLAIMS,
        },
      },
      {
        user: "ugh, spell it Milla actually",
        expectedAnswer:
          "A correction after commit: a fresh change from Mila to Milla, previewed and confirmed.",
        expect: {
          action: "student.preferences.update",
          confirm: true,
          receipt: "succeeded",
          effect: { profile: { preferredName: "Milla" } },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-ctx-012",
    family: "gen_context",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "set up a follow-up for Hana Ashgrove about her immunization record",
        expectedAnswer: "A follow-up for Hana Ashgrove, previewed, left unconfirmed.",
        expect: {
          action: "operations.follow_up.create",
          targetStudent: "ashgrove",
          forbidden: NO_CLAIMS,
        },
      },
      {
        user: "forget it, don't bother",
        expectedAnswer:
          "The proposal is dropped, nothing was written, and Edward says so plainly.",
        expect: {
          actionAnyOf: [null],
          noEffect: true,
          forbidden: NO_CLAIMS,
        },
      },
      {
        user: "did you create anything for her in the end?",
        expectedAnswer: "An honest no, from receipts: nothing was created.",
        expect: {
          actionAnyOf: [null],
          noEffect: true,
          says: [/\bno\b|nothing|haven'?t|not been/i],
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-ctx-013",
    family: "gen_context",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "what's blocking Tessa Whitlowe right now?",
        expectedAnswer: "A read about Tessa's blockers (financial-aid verification, orientation).",
        expect: { forbidden: NO_CLAIMS },
      },
      {
        user: "ok, put a follow-up in for her about that",
        expectedAnswer:
          "\"Her\" is Tessa, carried by explicit anaphor from the previous turn. A follow-up for Tessa.",
        expect: {
          action: "operations.follow_up.create",
          targetStudent: "whitlowe",
          confirm: true,
          receipt: "succeeded",
          effect: { edwardWorkItem: { studentRef: "SYN-001236" } },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-ctx-014",
    family: "gen_context",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "show me AST-00533",
        expectedAnswer: "A read of the work item.",
        expect: { actionAnyOf: [null], forbidden: NO_CLAIMS },
      },
      {
        user: "set it in progress — and bump it to high while you're at it",
        expectedAnswer:
          "Status moves to in-progress. Priority is not an Edward-editable field, so the honest answer proposes the status change and says where priority is set.",
        expect: {
          actionAnyOf: ["operations.work_item.update", null],
          soft: { requireAction: "operations.work_item.update" },
          confirm: true,
          effect: { workItem: { key: "AST-00533", status: "in_progress" } },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-ctx-015",
    family: "gen_context",
    actorKind: "staff",
    actor: "director",
    turns: [
      {
        user: "how many students in the class of 2029 still owe the deposit?",
        expectedAnswer: "A count of the 2029 deposit-unpaid group (24 by the payment ledger).",
        expect: { forbidden: NO_CLAIMS },
      },
      {
        user: "create follow-ups for them, due friday",
        expectedAnswer:
          "A cohort action against exactly the group just counted, with count and sample on the card; confirming writes one follow-up per member and the accounting agrees.",
        expect: {
          action: "operations.cohort.create_follow_ups",
          confirm: true,
          receipt: "succeeded",
          effect: {
            cohortConsistent: true,
            workItemsCreatedBetween: [20, 25],
            memberItemCreated: { studentRef: "SYN-000174" },
          },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-ctx-016",
    family: "gen_context",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "create a follow-up for Ada Kettleby about her orientation registration",
        expectedAnswer: "A follow-up for Ada, previewed.",
        expect: {
          action: "operations.follow_up.create",
          targetStudent: "kettleby",
          forbidden: NO_CLAIMS,
        },
      },
      {
        user: "before I confirm — what's her deposit status?",
        expectedAnswer: "A read answer about Ada's deposit; the pending proposal survives.",
        expect: { actionAnyOf: [null], forbidden: NO_CLAIMS },
      },
      {
        user: "ok make it high priority",
        expectedAnswer:
          "The amendment still applies to Ada's pending follow-up: fresh proposal, same target, high priority.",
        expect: {
          action: "operations.follow_up.create",
          targetStudent: "kettleby",
          preview: { priority: "high" },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-ctx-017",
    family: "gen_context",
    actorKind: "student",
    actor: "kettlebyEmre",
    turns: [
      {
        user: "change my number to 020 7946 0111",
        expectedAnswer: "A mobile change previewed, unconfirmed.",
        expect: {
          action: "student.preferences.update",
          preview: { changes: [{ field: "mobilePhone", after: "020 7946 0111" }] },
          forbidden: NO_CLAIMS,
        },
      },
      {
        user: "hang on, what number do you have for me right now?",
        expectedAnswer: "A read answer; the pending change survives unconfirmed.",
        expect: { forbidden: NO_CLAIMS },
      },
      {
        user: "right — use 020 7946 0222 instead",
        expectedAnswer: "The amended proposal carries 020 7946 0222; confirming writes it.",
        expect: {
          action: "student.preferences.update",
          confirm: true,
          receipt: "succeeded",
          effect: { profile: { mobilePhone: "020 7946 0222" } },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-ctx-018",
    family: "gen_context",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "queue a follow-up for Ada Kettleby about her aid paperwork",
        expectedAnswer: "A follow-up for Ada Kettleby, previewed.",
        expect: {
          action: "operations.follow_up.create",
          targetStudent: "kettleby",
          forbidden: NO_CLAIMS,
        },
      },
      {
        user: "and another for Ada Ravensworth, same topic",
        expectedAnswer:
          "A second proposal for the other Ada — Ada Ravensworth — resolved fresh from this turn's name.",
        expect: {
          action: "operations.follow_up.create",
          targetStudent: "ravensworth",
          notTargetStudent: "kettleby",
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
];
