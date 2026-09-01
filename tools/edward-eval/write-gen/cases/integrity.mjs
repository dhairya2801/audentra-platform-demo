/**
 * gen_integrity — the gap between preview and confirmation.
 *
 * Replay, tampering, expiry, cross-actor confirmation, and canonical state
 * changing under a pending card. The gateway claims hash pinning, actor
 * binding, one-shot confirmation and re-authorization; here they are
 * exercised rather than assumed.
 */

import { NO_CLAIMS } from "./common.mjs";

export const INTEGRITY_CASES = [
  {
    id: "g-int-001",
    family: "gen_integrity",
    actorKind: "student",
    actor: "calderwoodFiona",
    turns: [
      {
        user: "set my pronouns to she/her",
        expectedAnswer: "Confirming twice yields one receipt and one write.",
        expect: {
          action: "student.preferences.update",
          confirm: true,
          confirmTwice: true,
          receipt: "succeeded",
          effect: { profile: { pronouns: "she/her" }, receiptCount: 1 },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-int-002",
    family: "gen_integrity",
    actorKind: "student",
    actor: "larkspurQuentin",
    turns: [
      {
        user: "change my preferred name to Quen",
        expectedAnswer:
          "A confirm with the wrong content hash is rejected; nothing is written; the intent stays pending.",
        expect: {
          action: "student.preferences.update",
          confirmWithBadHash: true,
          // BANK CORRECTION (2026-09-01, after first contact): the intent
          // status vocabulary is "pending_confirmation", not "pending" — a
          // wrong data expectation in the frozen bank, not an Edward defect.
          intentStatus: "pending_confirmation",
          noEffect: true,
          effect: { profileUnchanged: true },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-int-003",
    family: "gen_integrity",
    actorKind: "student",
    actor: "everlynHana1",
    turns: [
      {
        user: "update my number to 07700 333444",
        expectedAnswer: "Another student's confirmation of this intent must be rejected outright.",
        expect: {
          action: "student.preferences.update",
          confirmAsOtherStudent: "everlynHana2",
          noEffect: true,
          effect: { profileUnchanged: true },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-int-004",
    family: "gen_integrity",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "create a follow-up for Georgina Underhollow about her class registration",
        expectedAnswer:
          "Another staff member confirming this adviser's intent must be rejected; nothing written.",
        expect: {
          action: "operations.follow_up.create",
          targetStudent: "underhollow",
          confirmAsOtherStaff: "adviserOther",
          noEffect: true,
          effect: { workItemsCreated: 0 },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-int-005",
    family: "gen_integrity",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "create a follow-up for Dmitri Zephyrine about advising",
        expectedAnswer:
          "An expired card cannot execute: the confirmation fails with the honest expiry answer and nothing is written.",
        expect: {
          action: "operations.follow_up.create",
          targetStudent: "zephyrineDmitri",
          confirm: true,
          expireBeforeConfirm: true,
          noEffect: true,
          effect: { workItemsCreated: 0 },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-int-006",
    family: "gen_integrity",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "mark AST-01481 as done — Jolene's sorted",
        expectedAnswer:
          "Between preview and confirmation the item changes under it (another session edits it). The stale confirmation must fail durably — a failed receipt, not a silent overwrite and not a rollback to pending.",
        expect: {
          action: "operations.work_item.update",
          targetWorkItem: "ownInProgressThird",
          confirm: true,
          receipt: "failed",
          intentStatus: "failed",
          forbidden: NO_CLAIMS,
        },
        mutateBeforeConfirm: [
          `UPDATE staff_work_item SET next_step = 'edited elsewhere', version = version + 1, updated_at = now()
           WHERE tenant_id = '00000000-0000-7000-8000-000000000003' AND key = 'AST-01481'`,
        ],
      },
    ],
  },
  {
    id: "g-int-007",
    family: "gen_integrity",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "create a follow-up for Camila Quillfeather about her aid verification",
        expectedAnswer:
          "The capability is withdrawn after the preview. Confirmation re-checks authorization and must refuse; nothing is written.",
        expect: {
          action: "operations.follow_up.create",
          targetStudent: "quillfeatherCamila",
          confirm: true,
          noEffect: true,
          effect: { workItemsCreated: 0 },
          forbidden: NO_CLAIMS,
        },
        mutateBeforeConfirm: [
          `DELETE FROM staff_role_capability
           WHERE tenant_id = '00000000-0000-7000-8000-000000000003'
             AND role_code = 'academic_adviser' AND capability = 'edward.follow_up.create'`,
        ],
      },
    ],
  },
  {
    id: "g-int-008",
    family: "gen_integrity",
    actorKind: "staff",
    actor: "adviser",
    setupSql: [
      // Restore the capability the previous case removed, so later cases are
      // unaffected regardless of execution order.
      `INSERT INTO staff_role_capability (tenant_id, role_code, capability)
       SELECT '00000000-0000-7000-8000-000000000003', 'academic_adviser', 'edward.follow_up.create'
       WHERE NOT EXISTS (
         SELECT 1 FROM staff_role_capability
         WHERE tenant_id = '00000000-0000-7000-8000-000000000003'
           AND role_code = 'academic_adviser' AND capability = 'edward.follow_up.create')`,
    ],
    turns: [
      {
        user: "create a follow-up for Bruno Oakenshaw about his transcript",
        expectedAnswer:
          "The student moves to another adviser after the preview. Confirmation re-checks scope and must refuse; nothing is written.",
        expect: {
          action: "operations.follow_up.create",
          targetStudent: "oakenshawBruno",
          confirm: true,
          noEffect: true,
          effect: { workItemsCreated: 0 },
          forbidden: NO_CLAIMS,
        },
        mutateBeforeConfirm: [
          `UPDATE student_staff_assignment SET ended_at = now()
           WHERE tenant_id = '00000000-0000-7000-8000-000000000003'
             AND role = 'primary_advisor' AND ended_at IS NULL
             AND student_id = (SELECT id FROM student WHERE tenant_id = '00000000-0000-7000-8000-000000000003' AND external_ref = 'SYN-000655')`,
        ],
      },
    ],
  },
  {
    id: "g-int-009",
    family: "gen_integrity",
    actorKind: "student",
    actor: "stonebrookCaleb",
    turns: [
      {
        user: "change my preferred name to Ana",
        expectedAnswer: "First proposal: Ana.",
        expect: {
          action: "student.preferences.update",
          preview: { changes: [{ field: "preferredName", after: "Ana" }] },
          forbidden: NO_CLAIMS,
        },
      },
      {
        user: "wait — make that Anya",
        expectedAnswer:
          "The amendment supersedes: a fresh card for Anya. Confirming the OLD card afterwards is the integrity question: whatever executes must match its own preview exactly — and a superseded card silently writing a value the user corrected away is a product defect worth recording.",
        expect: {
          action: "student.preferences.update",
          preview: { changes: [{ field: "preferredName", after: "Anya" }] },
          forbidden: NO_CLAIMS,
        },
      },
      {
        user: "go ahead",
        expectedAnswer:
          "Confirming the first (superseded) card: if it executes, the row must say Ana (its own preview) — never a blend; if it refuses, that is the safer answer.",
        expect: {
          confirmIntentIndex: 0,
          confirm: true,
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-int-010",
    family: "gen_integrity",
    actorKind: "student",
    actor: "ravensworthHelia",
    turns: [
      {
        user: "set my pronouns to they/she",
        expectedAnswer:
          "A confirm request smuggling replacement fields changes nothing: the server reads only the intent id, version and hash. The row holds the previewed value.",
        expect: {
          action: "student.preferences.update",
          confirmWithMutatedBody: { fields: { pronouns: "he/him" }, pronouns: "he/him" },
          // BANK CORRECTION (2026-09-01, after first contact): the server
          // REJECTS a confirm body carrying unknown fields (fail-closed)
          // rather than ignoring them — a stricter contract than the bank
          // assumed. The property that matters: the smuggled value must
          // never reach the row.
          effect: { profileFieldNot: { field: "pronouns", value: "he/him" } },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-int-011",
    family: "gen_integrity",
    actorKind: "student",
    actor: "glimmerlyTessa",
    turns: [
      {
        user: "switch my contact preference to text messages",
        expectedAnswer:
          "A confirm with a wrong expectedVersion is rejected; nothing changes.",
        expect: {
          action: "student.preferences.update",
          confirmWithWrongVersion: true,
          noEffect: true,
          effect: { profileUnchanged: true },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
];
