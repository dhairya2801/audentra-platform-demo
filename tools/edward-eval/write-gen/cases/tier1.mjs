/**
 * gen_tier1 — language deliberately outside the deterministic patterns.
 *
 * Each request here was checked against the tier-0 regexes when it was
 * written and does not match them, so whatever answers it is the conversation
 * continuation, the model tier, or nothing. This is the family the previous
 * bank did not have: the one that cannot pass on tier 0 alone.
 *
 * The second half is false-positive bait: messages that look action-shaped
 * and are not. For those, a proposed action is a hard failure.
 */

import { NO_CLAIMS } from "./common.mjs";

export const TIER1_CASES = [
  // --- in-catalogue, out-of-pattern ---------------------------------------
  {
    id: "g-t1-001",
    family: "gen_tier1",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "Yusuf's been quiet for weeks — get something on my plate so I remember to chase him",
        expectedAnswer:
          "A follow-up for Yusuf Everlyn (the only Yusuf on this caseload), assigned to the asker.",
        expect: {
          actionAnyOf: ["operations.follow_up.create", null],
          soft: { requireAction: "operations.follow_up.create" },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-t1-002",
    family: "gen_tier1",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "Bianca Jessamy's immunization doc got bounced. make sure I don't forget to deal with it",
        expectedAnswer: "A follow-up for Bianca Jessamy about the rejected immunization document.",
        expect: {
          actionAnyOf: ["operations.follow_up.create", null],
          soft: { requireAction: "operations.follow_up.create" },
          targetStudent: "jessamyBianca",
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-t1-003",
    family: "gen_tier1",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "set smth up so i check on Kaito Pemberwell next week",
        expectedAnswer: "A follow-up for Kaito Pemberwell for next week.",
        expect: {
          actionAnyOf: ["operations.follow_up.create", null],
          soft: { requireAction: "operations.follow_up.create" },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-t1-004",
    family: "gen_tier1",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "shove Fiona Calderwood my way for friday — need to go over her aid docs",
        expectedAnswer: "A follow-up for Fiona Calderwood due Friday, assigned to the asker.",
        expect: {
          actionAnyOf: ["operations.follow_up.create", null],
          soft: { requireAction: "operations.follow_up.create" },
          notTargetStudent: "larkspurFiona",
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-t1-005",
    family: "gen_tier1",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "task: Dmitri Zephyrine, aid docs, thursday",
        expectedAnswer:
          "Telegraphic style: a follow-up for Dmitri Zephyrine about aid documents due Thursday.",
        expect: {
          actionAnyOf: ["operations.follow_up.create", null],
          soft: { requireAction: "operations.follow_up.create" },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-t1-006",
    family: "gen_tier1",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "resolve AST-01070",
        expectedAnswer:
          "\"Resolve\" closes a work item: preview AST-01070 moving to done, or ask what resolving should mean. The item summary alone is not an answer.",
        expect: {
          actionAnyOf: ["operations.work_item.update", null],
          soft: { requireAction: "operations.work_item.update" },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-t1-007",
    family: "gen_tier1",
    actorKind: "student",
    actor: "everlynYusuf",
    turns: [
      {
        user: "they/them please, for my pronouns",
        expectedAnswer: "A pronouns update to they/them, previewed.",
        expect: {
          actionAnyOf: ["student.preferences.update", null],
          soft: { requireAction: "student.preferences.update" },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-t1-008",
    family: "gen_tier1",
    actorKind: "student",
    actor: "ironwoodHana",
    turns: [
      {
        user: "would be great if my pronouns said she/they on the portal",
        expectedAnswer: "A pronouns update to she/they, previewed.",
        expect: {
          actionAnyOf: ["student.preferences.update", null],
          soft: { requireAction: "student.preferences.update" },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-t1-009",
    family: "gen_tier1",
    actorKind: "student",
    actor: "stonebrookCaleb",
    turns: [
      {
        user: "rather get texts than emails tbh, emails get buried",
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
    id: "g-t1-010",
    family: "gen_tier1",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "Helia Ravensworth's transcript situation needs eyes on it before term — sort me out a reminder",
        expectedAnswer: "A follow-up for Helia Ravensworth about her transcript.",
        expect: {
          actionAnyOf: ["operations.follow_up.create", null],
          soft: { requireAction: "operations.follow_up.create" },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-t1-011",
    family: "gen_tier1",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "can't keep Camila Quillfeather in my head — park her somewhere I'll see monday",
        expectedAnswer: "A follow-up for Camila Quillfeather for Monday, assigned to the asker.",
        expect: {
          actionAnyOf: ["operations.follow_up.create", null],
          soft: { requireAction: "operations.follow_up.create" },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-t1-012",
    family: "gen_tier1",
    actorKind: "student",
    actor: "hallowayDelphine",
    turns: [
      {
        user: "Delphi is what I actually go by, whatever the form said",
        expectedAnswer: "A preferred-name change to Delphi, previewed.",
        expect: {
          actionAnyOf: ["student.preferences.update", null],
          soft: { requireAction: "student.preferences.update" },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  // --- false-positive bait: action-shaped, not an action -------------------
  {
    id: "g-t1-020",
    family: "gen_tier1",
    actorKind: "student",
    actor: "pemberwellKaito",
    turns: [
      {
        user: "I want to update my adviser on my situation before we meet",
        expectedAnswer:
          "Not a profile change. Acceptable: a support request that carries the student's words to a person, or an answer explaining how to reach the adviser. Proposing a preferences update is wrong.",
        expect: {
          actionAnyOf: [null, "student.support.contact"],
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-t1-021",
    family: "gen_tier1",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "did you create a follow-up for Petra Yarrowby last week?",
        expectedAnswer:
          "A question about the past, in a fresh conversation: answer honestly from records (nothing in this conversation), never propose a new task.",
        expect: {
          actionAnyOf: [null],
          noEffect: true,
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-t1-022",
    family: "gen_tier1",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "if I asked you to close AST-00006, would that even work?",
        expectedAnswer:
          "A hypothetical. Explain what closing would involve; do not preview or execute anything.",
        expect: {
          actionAnyOf: [null],
          noEffect: true,
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-t1-023",
    family: "gen_tier1",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "what would it take to get everyone's transcripts chased before friday?",
        expectedAnswer:
          "A question about process, not an instruction. Explain the route (count the group, follow-ups, leads for bulk); do not propose a bulk action from a hypothetical.",
        expect: {
          actionAnyOf: [null],
          noEffect: true,
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-t1-024",
    family: "gen_tier1",
    actorKind: "student",
    actor: "mossbankVera",
    turns: [
      {
        user: "my flatmate changed her number and the uni never noticed, glad mine's fine",
        expectedAnswer:
          "Small talk about someone else's number. No action, no clarifying question about the asker's phone.",
        expect: {
          actionAnyOf: [null],
          noEffect: true,
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-t1-025",
    family: "gen_tier1",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "remind me, what does a follow-up actually do once I create one?",
        expectedAnswer:
          "\"Remind me\" here is an idiom for \"tell me\". Explain what a follow-up is; do not create one.",
        expect: {
          actionAnyOf: [null],
          noEffect: true,
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
];
