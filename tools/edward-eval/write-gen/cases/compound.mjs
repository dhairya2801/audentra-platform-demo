/**
 * gen_compound — one turn, a read half and a write half.
 *
 * The report's known limitation 1: the student pipeline answers only the
 * write half. Both halves are graded: the action must be proposed AND the
 * question must be answered.
 */

import { NO_CLAIMS } from "./common.mjs";

export const COMPOUND_CASES = [
  {
    id: "g-cmp-001",
    family: "gen_compound",
    actorKind: "student",
    actor: "ashgroveNadia",
    turns: [
      {
        user: "Who is my adviser, and can you put in a request for them to give me a call?",
        expectedAnswer:
          "Both halves: the adviser's name (Hana Dunmire) in the answer, and a support request previewed with its destination.",
        expect: {
          action: "student.support.contact",
          says: [/Dunmire/],
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-cmp-002",
    family: "gen_compound",
    actorKind: "student",
    actor: "ashgroveNadia",
    turns: [
      {
        user: "what's my preferred name set to right now? change it to Naddy either way",
        expectedAnswer:
          "The current value (Mila if the earlier case ran, otherwise Nadia) stated, and a change to Naddy previewed with before/after.",
        expect: {
          action: "student.preferences.update",
          preview: { changes: [{ field: "preferredName", after: "Naddy" }] },
          // BANK CORRECTION (2026-09-01): the read half must state the
          // CURRENT name, which an earlier case in the same batch may have
          // changed to Milla — the original /Nadia/ pattern hard-coded batch
          // state. Meaning unchanged: the current value must be said.
          says: [/Milla|Nadia/],
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-cmp-003",
    family: "gen_compound",
    actorKind: "student",
    actor: "ashgrove",
    turns: [
      {
        user: "Is my transcript still outstanding? If it is, get someone to look into it.",
        expectedAnswer:
          "Read half: yes, the official transcript is still owed. Write half: a support request previewed.",
        expect: {
          action: "student.support.contact",
          says: [/transcript/i],
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-cmp-004",
    family: "gen_compound",
    actorKind: "student",
    actor: "yarrowby",
    turns: [
      {
        user: "Do I still owe the deposit? If I do, remind me tomorrow.",
        expectedAnswer:
          "Read half: yes, the deposit is still owed. Write half: reminders are not a student capability, so the honest answer says so and offers the real routes (deadline on the checklist, support request). No fake reminder.",
        expect: {
          actionAnyOf: [null, "student.support.contact"],
          says: [/deposit/i],
          forbidden: [
            ...NO_CLAIMS,
            /\bI(?:'ll| will) remind you\b/i,
            /\breminder (?:is )?set\b/i,
          ],
        },
      },
    ],
  },
  {
    id: "g-cmp-005",
    family: "gen_compound",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "who's got AST-00001 at the moment? and can you mark it done for me",
        expectedAnswer:
          "The item belongs to Student Health, not this adviser. The denial should also answer the read half — who holds it — instead of only refusing.",
        expect: {
          actionAnyOf: [null],
          deniedAnyOf: ["EDWARD_WORK_ITEM_SCOPE_FORBIDDEN"],
          says: [/Student Health|another owner|belongs/i],
          noEffect: true,
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-cmp-006",
    family: "gen_compound",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "What is Hana Ashgrove still missing, and create a follow-up about the most important one.",
        expectedAnswer:
          "Read half: her open requirements (aid verification, immunization, transcript, orientation). Write half: a follow-up bound to the most urgent blocking requirement, previewed with the canonical topic.",
        expect: {
          action: "operations.follow_up.create",
          targetStudent: "ashgrove",
          preview: { topicIsCanonical: true },
          says: [/verification|immuni|transcript|orientation/i],
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-cmp-007",
    family: "gen_compound",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "Is Petra Yarrowby's deposit still unpaid? If so, put a task on my list to ring her wednesday.",
        expectedAnswer:
          "Read half: yes, unpaid. Write half: a follow-up assigned to the asker for Wednesday. The condition is true, so the task should be proposed.",
        expect: {
          action: "operations.follow_up.create",
          targetStudent: "yarrowby",
          says: [/deposit/i],
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-cmp-008",
    family: "gen_compound",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "does Emre Netherby have anything open with me? if it's the blocked one, mark AST-01482 in progress — I got through to him",
        expectedAnswer:
          "Read half: yes, AST-01482. Write half: the item moves to in-progress; confirming writes it.",
        expect: {
          action: "operations.work_item.update",
          targetWorkItem: "ownBlockedSecond",
          confirm: true,
          receipt: "succeeded",
          effect: { workItem: { key: "AST-01482", status: "in_progress" } },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-cmp-009",
    family: "gen_compound",
    actorKind: "student",
    actor: "whitlowe",
    turns: [
      {
        user: "how long have I got until the orientation deadline? also switch me to text messages so I don't miss things",
        expectedAnswer:
          "Read half: the orientation registration deadline (2026-08-11, already past). Write half: contact preference to SMS, previewed.",
        expect: {
          action: "student.preferences.update",
          preview: { changes: [{ field: "communicationPreference", after: "sms" }] },
          says: [/orientation|deadline|overdue|august|aug/i],
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-cmp-010",
    family: "gen_compound",
    actorKind: "student",
    actor: "netherbyMilo",
    turns: [
      {
        user: "am I flagged for anything health-wise? and my new mobile is +44 7700 900456, save it",
        expectedAnswer:
          "Read half: the rejected health/immunization document. Write half: the mobile change previewed and, after confirmation, written.",
        expect: {
          action: "student.preferences.update",
          says: [/health|immuni|reject/i],
          confirm: true,
          receipt: "succeeded",
          effect: { profile: { mobilePhone: "+44 7700 900456" } },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-cmp-011",
    family: "gen_compound",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "how many of my students still haven't paid the deposit — and get follow-ups going for the worst three",
        expectedAnswer:
          "Read half: the caseload count. Write half: \"the worst three\" is a ranking Edward does not do — the honest answer gives the count, explains it cannot rank, and offers the real routes (name three, or a lead can act on a described group). No arbitrary silent selection.",
        expect: {
          actionAnyOf: [null],
          noEffect: true,
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-cmp-012",
    family: "gen_compound",
    actorKind: "student",
    actor: "jessamy",
    turns: [
      {
        user: "update me: name Kai, pronouns he/him, and switch me over to texts",
        expectedAnswer:
          "All three fields in one preview — preferred name Kai, pronouns he/him, contact preference SMS — then one confirmation writes all three.",
        expect: {
          action: "student.preferences.update",
          preview: {
            changes: [
              { field: "preferredName", after: "Kai" },
              { field: "pronouns", after: "he/him" },
              { field: "communicationPreference", after: "sms" },
            ],
          },
          confirm: true,
          receipt: "succeeded",
          effect: {
            profile: { preferredName: "Kai", pronouns: "he/him", communicationPreference: "sms" },
          },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-cmp-013",
    family: "gen_compound",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "sanity check: is Greta Oakenshaw on my caseload? if yes, flag her for a check-in monday",
        expectedAnswer:
          "Read half: yes. Write half: a follow-up for Greta for Monday. The Monday date must survive into the row.",
        expect: {
          action: "operations.follow_up.create",
          targetStudent: "oakenshaw",
          confirm: true,
          receipt: "succeeded",
          effect: { edwardWorkItem: { studentRef: "SYN-002844", dueAt: "2026-09-07" } },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-cmp-014",
    family: "gen_compound",
    actorKind: "student",
    actor: "glimmerlyTessa",
    turns: [
      {
        user: "my transcript came back rejected?? what happened, and can you get an actual person to explain it to me",
        expectedAnswer:
          "Read half: the transcript document was rejected. Write half: a support request previewed. No forged state, no 'I fixed it'.",
        expect: {
          action: "student.support.contact",
          says: [/reject/i],
          confirm: true,
          receipt: "succeeded",
          effect: { helpRequests: 1 },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
];
