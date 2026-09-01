/**
 * gen_blockers — free-text reasons meeting a coded vocabulary.
 *
 * The blocker code is what the Action Center filters by; the detail text is
 * what a colleague reads. The phrase table maps three codes and defaults to
 * awaiting_student. Two stages are measured separately: does the stated
 * reason survive into the record at all, and does the canonical code come
 * out semantically right?
 */

import { NO_CLAIMS } from "./common.mjs";

export const BLOCKER_CASES = [
  {
    id: "g-blk-001",
    family: "gen_blockers",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "mark AST-02441 blocked — the financial aid office hasn't sent their determination yet",
        expectedAnswer:
          "The reason is in the sentence. Ideal: a blocked preview carrying it. Acceptable: one question asking for the reason.",
        expect: {
          actionAnyOf: ["operations.work_item.update", null],
          forbidden: NO_CLAIMS,
        },
      },
      {
        user: "mark AST-02441 blocked — waiting on the financial aid office for their determination",
        expectedAnswer:
          "Waiting on another office is awaiting_external; the detail is stored verbatim. Defaulting the code to awaiting_student mis-files the item for every board filter.",
        expect: {
          actionAnyOf: ["operations.work_item.update", null],
          soft: { requireAction: "operations.work_item.update" },
          confirm: true,
          effect: {
            workItem: { key: "AST-02441", status: "blocked" },
            workItemBlocker: { key: "AST-02441", code: "awaiting_external" },
          },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-blk-002",
    family: "gen_blockers",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "block AST-01070 — waiting on IT, the portal upload keeps erroring for Elena",
        expectedAnswer: "A technical failure: system_hold, detail verbatim.",
        expect: {
          actionAnyOf: ["operations.work_item.update", null],
          soft: { requireAction: "operations.work_item.update" },
          confirm: true,
          effect: {
            workItem: { key: "AST-01070", status: "blocked" },
            workItemBlocker: { key: "AST-01070", code: "system_hold" },
          },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-blk-003",
    family: "gen_blockers",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "AST-01481 is blocked, im waiting on the registrar",
        expectedAnswer:
          "Ideal: blocked + awaiting_external in one step. Acceptable: a question for the reason.",
        expect: {
          actionAnyOf: ["operations.work_item.update", null],
          forbidden: NO_CLAIMS,
        },
      },
      {
        user: "mark it blocked — waiting on the registrar's office",
        expectedAnswer: "Registrar: awaiting_external, detail verbatim, on AST-01481.",
        expect: {
          actionAnyOf: ["operations.work_item.update", null],
          soft: { requireAction: "operations.work_item.update" },
          confirm: true,
          effect: {
            workItemBlocker: { key: "AST-01481", code: "awaiting_external" },
          },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-blk-004",
    family: "gen_blockers",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "put AST-01794 on hold — Fiona's mum is in hospital and she asked for two weeks' grace",
        expectedAnswer:
          "Semantically awaiting_student: the student asked for time. Two traps measured: \"on hold\" is not in the status vocabulary, and \"hold\" matches the system_hold phrase. The sensitive detail must be carried faithfully if anything is written.",
        expect: {
          actionAnyOf: ["operations.work_item.update", null],
          confirm: true,
          effect: {
            workItemBlocker: { key: "AST-01794", code: "awaiting_student" },
          },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-blk-005",
    family: "gen_blockers",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "make a follow-up for Zara Whitlowe about her financial aid worksheet",
        expectedAnswer: "An ordinary create, confirmed, so the next turn has an item to block.",
        expect: {
          action: "operations.follow_up.create",
          confirm: true,
          receipt: "succeeded",
          forbidden: NO_CLAIMS,
        },
      },
      {
        user: "actually mark that task blocked — waiting on IT because her uploaded doc won't parse",
        expectedAnswer:
          "The canonical vocabulary has document_parse_failure for exactly this; the phrase table does not map it (IT-wait reads as system_hold at best). Measured, not assumed.",
        expect: {
          actionAnyOf: ["operations.work_item.update", null],
          confirm: true,
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-blk-006",
    family: "gen_blockers",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "mark AST-00533 blocked, waiting for Anton to come back from leave",
        expectedAnswer: "Waiting on the student: awaiting_student, detail verbatim.",
        expect: {
          actionAnyOf: ["operations.work_item.update", null],
          soft: { requireAction: "operations.work_item.update" },
          confirm: true,
          effect: {
            workItem: { key: "AST-00533", status: "blocked" },
            workItemBlocker: { key: "AST-00533", code: "awaiting_student" },
          },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
];
