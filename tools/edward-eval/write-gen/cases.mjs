/**
 * The Edward write generalization bank.
 *
 * FROZEN 2026-09-01 before any implementation change. The first-contact run
 * against this bank is the generalization measurement; later runs are labeled
 * post-fix and never overwrite it. Do not edit cases to make a run pass — a
 * wrong *data* expectation may be corrected, with the correction recorded in
 * the report.
 *
 * Sixteen families, ~200 cases. Independence from the regression bank:
 * different phrasings throughout, different students where the property
 * allows, and families (tier-1 challenge, conversation context, compound,
 * read→reason→write, delegation contrasts, collisions, integrity staging)
 * the regression bank does not contain.
 */

import { COLLOQUIAL_CASES } from "./cases/colloquial.mjs";
import { TIER1_CASES } from "./cases/tier1.mjs";
import { CONTEXT_CASES } from "./cases/context.mjs";
import { COMPOUND_CASES } from "./cases/compound.mjs";
import { REASON_CASES } from "./cases/reason.mjs";
import { DELEGATION_CASES } from "./cases/delegation.mjs";
import { AMBIGUITY_CASES } from "./cases/ambiguity.mjs";
import { ENTITY_CASES } from "./cases/entity.mjs";
import { COHORT_CASES } from "./cases/cohort.mjs";
import { PARITY_CASES } from "./cases/parity.mjs";
import { INTEGRITY_CASES } from "./cases/integrity.mjs";
import { INJECTION_CASES } from "./cases/injection.mjs";
import { PERMISSION_CASES } from "./cases/permissions.mjs";
import { BLOCKER_CASES } from "./cases/blockers.mjs";
import { UNDO_CASES } from "./cases/undo.mjs";
import { UNSUPPORTED_CASES } from "./cases/unsupported.mjs";

export const CASES = [
  ...COLLOQUIAL_CASES,
  ...TIER1_CASES,
  ...CONTEXT_CASES,
  ...COMPOUND_CASES,
  ...REASON_CASES,
  ...DELEGATION_CASES,
  ...AMBIGUITY_CASES,
  ...ENTITY_CASES,
  ...COHORT_CASES,
  ...PARITY_CASES,
  ...INTEGRITY_CASES,
  ...INJECTION_CASES,
  ...PERMISSION_CASES,
  ...BLOCKER_CASES,
  ...UNDO_CASES,
  ...UNSUPPORTED_CASES,
];
