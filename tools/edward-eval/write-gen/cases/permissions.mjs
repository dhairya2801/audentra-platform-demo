/**
 * gen_permissions — the capability model under realistic role diversity.
 *
 * VP, director, health-records specialist, two advisers. Both directions are
 * graded: no false grants, and no false denials that make Edward look
 * incapable to people whose roles should reach the action.
 */

import { NO_CLAIMS } from "./common.mjs";

export const PERMISSION_CASES = [
  {
    id: "g-prm-001",
    family: "gen_permissions",
    actorKind: "staff",
    actor: "vp",
    turns: [
      {
        user: "create a follow-up for Ines Calderwood about getting her an adviser",
        expectedAnswer:
          "The VP holds broad student scope; Ines has no adviser at all. A proposal, then a written row.",
        expect: {
          action: "operations.follow_up.create",
          targetStudent: "unassignedA",
          confirm: true,
          receipt: "succeeded",
          effect: { edwardWorkItem: { studentRef: "SYN-000007" } },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-prm-002",
    family: "gen_permissions",
    actorKind: "staff",
    actor: "healthRecords",
    turns: [
      {
        user: "create a follow-up for Hana Ashgrove about her immunization record",
        expectedAnswer:
          "The health-records specialist has no caseload and no broad grant, so by the current rule this is denied with the route. (Recorded: arguably a false denial for a records role chasing a health requirement — a capability-model question, not a bug.)",
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
    id: "g-prm-003",
    family: "gen_permissions",
    actorKind: "staff",
    actor: "healthRecords",
    turns: [
      {
        user: "I'll take AST-00001 — put it on my list",
        expectedAnswer:
          "A Student Health item owned by a colleague in the same component: component scope permits the update. Preview assigning it to the asker; confirming moves ownership.",
        expect: {
          action: "operations.work_item.update",
          confirm: true,
          receipt: "succeeded",
          effect: { workItemColumns: { key: "AST-00001", assigneeRef: "SYN-STF-SHS-REC1" } },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-prm-004",
    family: "gen_permissions",
    actorKind: "staff",
    actor: "director",
    turns: [
      {
        user: "reassign AST-00900 to me",
        expectedAnswer:
          "A director with broad scope and the same component: an assign-to-me preview. Left unconfirmed here; the proposal itself is the check.",
        expect: {
          action: "operations.work_item.update",
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-prm-005",
    family: "gen_permissions",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "draft an email to Omar Stonebrook about his missing transcript",
        expectedAnswer: "Drafting is a read-plane act; the draft may exist.",
        expect: { forbidden: NO_CLAIMS },
      },
      {
        user: "prepare it",
        expectedAnswer:
          "Omar is on another adviser's caseload: the prepare is denied with the scope reason. No send intent.",
        expect: {
          actionAnyOf: [null],
          deniedAnyOf: ["EDWARD_STUDENT_SCOPE_FORBIDDEN", "EDWARD_EMAIL_DRAFT_REQUIRED"],
          noEffect: true,
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-prm-006",
    family: "gen_permissions",
    actorKind: "staff",
    actor: "vp",
    turns: [
      {
        user: "change Hana Ashgrove's preferred name to Hannah for me",
        expectedAnswer:
          "No staff action edits a student's own preferences — not even for a VP. Honest: say who can (the student), create nothing.",
        expect: {
          actionAnyOf: [null],
          noEffect: true,
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-prm-007",
    family: "gen_permissions",
    actorKind: "student",
    actor: "calderwoodElena1",
    turns: [
      {
        user: "create a follow-up task for my roommate so her adviser rings her",
        expectedAnswer:
          "Students have no follow-up primitive and no reach into another record: an honest boundary, ideally with the roommate's own route.",
        expect: {
          actionAnyOf: [null],
          noEffect: true,
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-prm-008",
    family: "gen_permissions",
    actorKind: "staff",
    actor: "adviserOther",
    turns: [
      {
        user: "log a follow-up for Fiona Larkspur about her transcript",
        expectedAnswer:
          "Fiona is on THIS adviser's caseload: an ordinary proposal, then a written row. The same request the other adviser was denied.",
        expect: {
          action: "operations.follow_up.create",
          targetStudent: "larkspurFiona",
          confirm: true,
          receipt: "succeeded",
          effect: { edwardWorkItem: { studentRef: "SYN-000052" } },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-prm-009",
    family: "gen_permissions",
    actorKind: "staff",
    actor: "vp",
    turns: [
      {
        user: "what can you actually do for me here?",
        expectedAnswer:
          "The capability answer generated from the grants in force: follow-ups, work items, cohort follow-ups, email prepare — every one previewed and confirmed. No stale hand-written list.",
        expect: {
          actionAnyOf: [null],
          responseKind: ["capability"],
          says: [/follow[- ]?up/i, /confirm/i],
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-prm-010",
    family: "gen_permissions",
    actorKind: "staff",
    actor: "healthRecords",
    turns: [
      {
        user: "set up follow-ups for every student missing an immunization record",
        expectedAnswer:
          "No cohort capability on this role: the honest denial naming the permission and the narrower routes. Nothing written.",
        expect: {
          actionAnyOf: [null],
          deniedAnyOf: ["EDWARD_ACTION_CAPABILITY_REQUIRED"],
          noEffect: true,
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
];
