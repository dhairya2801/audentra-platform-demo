/**
 * gen_colloquial — supported requests in the words people actually use.
 *
 * Slang, fragments, typos, trailing verbs, values behind commas. Every case
 * here is an action Edward supports, asked by someone who has never read the
 * documentation. Recognition tier is recorded, never asserted: the user does
 * not care which tier understood them.
 */

import { NO_CLAIMS } from "./common.mjs";

export const COLLOQUIAL_CASES = [
  {
    id: "g-col-001",
    family: "gen_colloquial",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "pop Emre Kettleby on my list for thursday",
        expectedAnswer:
          "A follow-up for Emre Kettleby, assigned to the asker, due Thursday, previewed and unconfirmed.",
        expect: {
          action: "operations.follow_up.create",
          targetStudent: "kettlebyEmre",
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-col-002",
    family: "gen_colloquial",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "Vera Mossbank could use a nudge about coming back in — sometime this week",
        expectedAnswer:
          "\"Needs a nudge\" is a follow-up request. Edward should propose an internal follow-up for Vera Mossbank (it cannot nudge her directly), or at minimum ask one clarifying question — not answer with a student summary.",
        expect: {
          actionAnyOf: ["operations.follow_up.create", null],
          notTargetStudent: "mossbankRosa",
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-col-003",
    family: "gen_colloquial",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "could you get a task going for Anton Pemberwell? nothing urgent",
        expectedAnswer: "A follow-up for Anton Pemberwell, previewed and unconfirmed.",
        expect: {
          action: "operations.follow_up.create",
          targetStudent: "pemberwellAnton",
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-col-004",
    family: "gen_colloquial",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "gotta remember to ring Tessa Whitlowe friday — stick it in the system",
        expectedAnswer:
          "A follow-up for Tessa Whitlowe due Friday, assigned to the asker, previewed. Confirming it must write the row.",
        expect: {
          action: "operations.follow_up.create",
          targetStudent: "whitlowe",
          confirm: true,
          receipt: "succeeded",
          effect: { edwardWorkItem: { studentRef: "SYN-001236", dueAt: "2026-09-04" } },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-col-005",
    family: "gen_colloquial",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "bung a reminder in for Kaito Jessamy re orientation",
        expectedAnswer: "A follow-up for Kaito Jessamy about orientation, previewed.",
        expect: {
          action: "operations.follow_up.create",
          targetStudent: "jessamy",
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-col-006",
    family: "gen_colloquial",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "creat a follow up for Greta Oakenshaw pls",
        expectedAnswer: "The typo changes nothing: a follow-up for Greta Oakenshaw, previewed.",
        expect: {
          action: "operations.follow_up.create",
          targetStudent: "oakenshaw",
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-col-007",
    family: "gen_colloquial",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "Elena Pemberwell — the deposit thing. queue it up for me.",
        expectedAnswer:
          "Fragments with no main verb: a follow-up for Elena Pemberwell about her deposit, assigned to the asker. A clarifying question is acceptable; a student summary is not.",
        expect: {
          actionAnyOf: ["operations.follow_up.create", null],
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-col-008",
    family: "gen_colloquial",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "need AST-01794 shifted to friday chase",
        expectedAnswer:
          "A work-item update: AST-01794 moves to follow-up with a Friday date. Preview, confirm, and the row must hold both.",
        expect: {
          action: "operations.work_item.update",
          targetWorkItem: "ownTodoFifth",
          confirm: true,
          receipt: "succeeded",
          effect: { workItem: { key: "AST-01794", status: "follow_up_required", followUpAt: "2026-09-04" } },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-col-009",
    family: "gen_colloquial",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "AST-00183 — done and dusted",
        expectedAnswer: "Close AST-00183: preview the move to done, confirm, and the row is done.",
        expect: {
          action: "operations.work_item.update",
          targetWorkItem: "ownTodo",
          confirm: true,
          receipt: "succeeded",
          effect: { workItem: { key: "AST-00183", status: "done" } },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-col-010",
    family: "gen_colloquial",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "wrap up AST-01344, spoke to Vera this morning and it's sorted",
        expectedAnswer:
          "\"Wrap up\" is a close. Edward should preview AST-01344 moving to done — or at minimum recognise a work-item update and ask what should change. Answering with the item summary loses the request.",
        expect: {
          actionAnyOf: ["operations.work_item.update", null],
          soft: { requireAction: "operations.work_item.update" },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-col-011",
    family: "gen_colloquial",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "im taking over ast-00507",
        expectedAnswer:
          "Lowercase key, no formal verb: assign AST-00507 to the asker. Preview and confirm must both work.",
        expect: {
          actionAnyOf: ["operations.work_item.update", null],
          soft: { requireAction: "operations.work_item.update" },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-col-012",
    family: "gen_colloquial",
    actorKind: "student",
    actor: "stonebrookZara",
    turns: [
      {
        user: "everyone's been calling me Zee since forever, can that go on my file?",
        expectedAnswer:
          "A preferred-name change to Zee, with an exact before/after preview. Not a checklist answer.",
        expect: {
          action: "student.preferences.update",
          preview: { changes: [{ field: "preferredName", after: "Zee" }] },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-col-013",
    family: "gen_colloquial",
    actorKind: "student",
    actor: "netherbyMilo",
    turns: [
      {
        user: "hey so my number changed, its 07911 123456 now",
        expectedAnswer:
          "A mobile-number change to 07911 123456, previewed. Confirming must write it.",
        expect: {
          action: "student.preferences.update",
          preview: { changes: [{ field: "mobilePhone", after: "07911 123456" }] },
          confirm: true,
          receipt: "succeeded",
          effect: { profile: { mobilePhone: "07911 123456" } },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-col-014",
    family: "gen_colloquial",
    actorKind: "student",
    actor: "everlynYusuf",
    turns: [
      {
        user: "put me down as Yus",
        expectedAnswer:
          "A preferred-name change to Yus. Short, but unambiguous — \"put me down as X\" names the value.",
        expect: {
          actionAnyOf: ["student.preferences.update", null],
          soft: { requireAction: "student.preferences.update" },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-col-015",
    family: "gen_colloquial",
    actorKind: "student",
    actor: "kettlebyEmre",
    turns: [
      {
        user: "swap me over to texts yeah? my email inbox is a mess",
        expectedAnswer: "A contact-preference change to SMS, previewed.",
        expect: {
          actionAnyOf: ["student.preferences.update", null],
          soft: { requireAction: "student.preferences.update" },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-col-016",
    family: "gen_colloquial",
    actorKind: "student",
    actor: "jessamyBianca",
    turns: [
      {
        user: "i've been going round in circles with this immunization thing for days, can someone just sort it out",
        expectedAnswer:
          "A support request routed to a person, linked to the immunization topic, previewed before anything is sent.",
        expect: {
          action: "student.support.contact",
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-col-017",
    family: "gen_colloquial",
    actorKind: "student",
    actor: "pemberwellKaito",
    turns: [
      {
        user: "who do i even talk to about my aid being late?? can you get a person on it please",
        expectedAnswer: "A support request about financial aid, previewed, with the destination named.",
        expect: {
          action: "student.support.contact",
          confirm: true,
          receipt: "succeeded",
          effect: { helpRequests: 1 },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-col-018",
    family: "gen_colloquial",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "Milo Netherby needs chasing about that rejected health form",
        expectedAnswer: "A follow-up for Milo Netherby, previewed.",
        expect: {
          action: "operations.follow_up.create",
          targetStudent: "netherbyMilo",
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-col-019",
    family: "gen_colloquial",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "log it: called Hana Ashgrove, no answer, will try again tomorrow",
        expectedAnswer:
          "A follow-up for Hana Ashgrove due tomorrow recording the failed call. A clarifying question is acceptable; ignoring the request is not.",
        expect: {
          actionAnyOf: ["operations.follow_up.create", null],
          soft: { requireAction: "operations.follow_up.create" },
          notTargetStudent: "kettleby",
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-col-020",
    family: "gen_colloquial",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "Quentin Larkspur hasn't done orientation. whack a task in for it, high prio",
        expectedAnswer: "A high-priority follow-up for Quentin Larkspur about orientation.",
        expect: {
          action: "operations.follow_up.create",
          targetStudent: "larkspurQuentin",
          preview: { priority: "high" },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-col-021",
    family: "gen_colloquial",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "write up a short note to Rosa Mossbank about her rejected transcript — I'll look it over before anything goes out",
        expectedAnswer:
          "BANK CORRECTION (2026-09-01, after first contact): two Rosa Mossbanks share this " +
          "caseload, so the correct first response is the clarifying question naming both — " +
          "not a draft for a silently chosen one. The author missed the twin.",
        expect: {
          actionAnyOf: [null],
          clarifies: true,
          noEffect: true,
          forbidden: NO_CLAIMS,
        },
      },
      {
        user: "looks good — get it ready to go",
        expectedAnswer:
          "With the ambiguity unresolved there is no draft; the honest answer keeps asking " +
          "(who the email goes to, or which Rosa) and prepares nothing. Nothing is sent.",
        expect: {
          actionAnyOf: [null, "communications.email.prepare"],
          noEffect: true,
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-col-022",
    family: "gen_colloquial",
    actorKind: "student",
    actor: "quillfeatherCamila",
    turns: [
      {
        user: "my mum says the uni keeps texting an old number for me. fix it — new one is 07700 900123",
        expectedAnswer:
          "A mobile-number change to 07700 900123, previewed. The mention of the old number changes nothing.",
        expect: {
          actionAnyOf: ["student.preferences.update", null],
          soft: { requireAction: "student.preferences.update" },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
];
