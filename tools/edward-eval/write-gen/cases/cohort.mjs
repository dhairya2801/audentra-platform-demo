/**
 * gen_cohort — acting on a described group.
 *
 * Membership, caps, drift, emptiness, capability, and the honesty of the
 * accounting. The happy path lives in gen_context (g-ctx-015); this family
 * is the edges.
 */

import { NO_CLAIMS } from "./common.mjs";

/** Gives the drift member a succeeded deposit payment mid-flight. */
const PAY_NADIA_IRONWOOD = `
  INSERT INTO payment_transaction
    (id, tenant_id, student_id, offer_id, type, amount_cents, status, processor, processor_reference)
  SELECT gen_random_uuid(), s.tenant_id, s.id, o.id, 'enrollment_deposit', 50000,
         'succeeded', 'dummy', 'eval-drift-' || s.external_ref
  FROM student s
  JOIN admission_offer o ON o.student_id = s.id AND o.tenant_id = s.tenant_id
  WHERE s.tenant_id = '00000000-0000-7000-8000-000000000003'
    AND s.external_ref = 'SYN-002854'
  LIMIT 1`;

export const COHORT_CASES = [
  {
    id: "g-coh-001",
    family: "gen_cohort",
    actorKind: "staff",
    actor: "director",
    turns: [
      {
        user: "create follow-ups for the class of 2029 students who still owe their deposit",
        expectedAnswer:
          "A cohort preview: filter restated, count, sample. Then the group changes under it — one member pays — and the confirmation must abort on drift with nothing written.",
        expect: {
          action: "operations.cohort.create_follow_ups",
          confirm: true,
          effect: { workItemsCreated: 0 },
          forbidden: NO_CLAIMS,
        },
        mutateBeforeConfirm: [PAY_NADIA_IRONWOOD],
      },
    ],
  },
  {
    id: "g-coh-002",
    family: "gen_cohort",
    actorKind: "staff",
    actor: "director",
    turns: [
      {
        user: "put follow-up tasks on every class of 2030 student who hasn't paid their deposit yet",
        expectedAnswer:
          "That group is ~444 students — far over the 25 cap. An honest refusal naming the limit and how to narrow. Nothing written.",
        expect: {
          actionAnyOf: [null],
          deniedAnyOf: ["EDWARD_COHORT_LIMIT_EXCEEDED"],
          noEffect: true,
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-coh-003",
    family: "gen_cohort",
    actorKind: "staff",
    actor: "director",
    turns: [
      {
        user: "create follow-ups for students in the class of 2040 who are missing transcripts",
        expectedAnswer: "No such students: the honest empty-cohort answer, nothing created.",
        expect: {
          actionAnyOf: [null],
          deniedAnyOf: ["EDWARD_COHORT_EMPTY"],
          noEffect: true,
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-coh-004",
    family: "gen_cohort",
    actorKind: "staff",
    actor: "director",
    turns: [
      {
        user: "create a follow-up for everybody",
        expectedAnswer: "Unconstrained: refuse and ask for a described group.",
        expect: {
          actionAnyOf: [null],
          deniedAnyOf: ["EDWARD_COHORT_UNCONSTRAINED", "EDWARD_COHORT_CONTEXT_REQUIRED"],
          noEffect: true,
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-coh-005",
    family: "gen_cohort",
    actorKind: "staff",
    actor: "adviser",
    turns: [
      {
        user: "chase everyone on my caseload who's missing an immunization record",
        expectedAnswer:
          "This adviser has no bulk capability: the honest denial says which permission covers it, who has it, and what she can do instead (one at a time). Nothing written.",
        expect: {
          actionAnyOf: [null],
          deniedAnyOf: ["EDWARD_ACTION_CAPABILITY_REQUIRED"],
          noEffect: true,
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-coh-006",
    family: "gen_cohort",
    actorKind: "staff",
    actor: "vp",
    turns: [
      {
        user: "the class of 2027 students who haven't responded to their offer — put a follow-up on each of them, assigned to me",
        expectedAnswer:
          "BANK CORRECTION (2026-09-01, after first contact): by the cohort's offer semantics (latest offer per student) this filter is genuinely empty — the raw offer rows that suggested ~3 members double-counted students with newer offers. Edward's honest empty-cohort answer is the correct behavior; the original expectation was a data error.",
        expect: {
          actionAnyOf: [null],
          deniedAnyOf: ["EDWARD_COHORT_EMPTY", "EDWARD_COHORT_UNCONSTRAINED"],
          noEffect: true,
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-coh-007",
    family: "gen_cohort",
    actorKind: "staff",
    actor: "director",
    turns: [
      {
        user: "how many Data Science students in the class of 2031 are missing immunization records?",
        expectedAnswer: "A count for the described group.",
        expect: { forbidden: NO_CLAIMS },
      },
      {
        user: "ok — follow-ups for exactly that group please, due next week",
        expectedAnswer:
          "The cohort just described, re-counted at proposal; if it exceeds the cap, an honest refusal; if within, a preview whose accounting holds after confirmation. Nothing may be created for a different group than the one shown.",
        expect: {
          actionAnyOf: ["operations.cohort.create_follow_ups", null],
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
  {
    id: "g-coh-008",
    family: "gen_cohort",
    actorKind: "staff",
    actor: "director",
    turns: [
      {
        user: "list the students in the class of 2029 who still owe the deposit",
        expectedAnswer: "The list (23 after the drift case's payment).",
        expect: { forbidden: NO_CLAIMS },
      },
      {
        user: "make follow-ups for the students we just talked about",
        expectedAnswer:
          "The active described group carries forward; a preview with its count, then per-member creation that agrees with its own receipt.",
        expect: {
          action: "operations.cohort.create_follow_ups",
          confirm: true,
          receipt: "succeeded",
          effect: { cohortConsistent: true, workItemsCreatedBetween: [20, 24] },
          forbidden: NO_CLAIMS,
        },
      },
    ],
  },
];
