/**
 * The evaluation suite loader.
 *
 * Cases live in `src/cases/*.mjs`, one file per domain. Every case names the
 * persona it runs against, so a question is always graded against a student
 * state where it has a definite right answer; fact-derived checks and judge
 * ground truth resolve against that persona's live snapshot at run time.
 *
 * Tiers (cumulative):
 *   smoke  deterministic invariants — safety, privacy, contract, formatting —
 *          plus every `critical` case; cheap enough to run routinely
 *   core   the Edward-regression set: everything except the long-tail cases
 *          explicitly marked `tier: "full"`
 *   full   the whole suite
 *
 * A case's default tier: `critical` or deterministic-only (judged:false,
 * no faults) cases land in smoke; everything else in core; `tier: "full"`
 * opts a case into the long tail.
 */

import { normalizeCase, validateCases } from "./case-schema.mjs";
import { LEGACY_CASES } from "./cases/legacy.mjs";
import { ENROLLMENT_CASES } from "./cases/enrollment.mjs";
import { DOCUMENT_CASES } from "./cases/documents.mjs";
import { DEPOSIT_ACCOUNT_CASES } from "./cases/deposit-account.mjs";
import { HOUSING_CASES } from "./cases/housing.mjs";
import { FINANCIAL_AID_CASES } from "./cases/financial-aid.mjs";
import { REGISTRATION_CASES } from "./cases/registration.mjs";
import { ACADEMIC_PLAN_CASES } from "./cases/academic-plan.mjs";
import { CAMPUS_LIFE_CASES } from "./cases/campus-life.mjs";
import { CONVERSATIONAL_CASES } from "./cases/conversational.mjs";
import { AMBIGUITY_CASES } from "./cases/ambiguity.mjs";
import { CROSS_DOMAIN_CASES } from "./cases/cross-domain.mjs";
import { CONFLICT_CASES } from "./cases/conflict.mjs";
import { TOOL_FAILURE_CASES } from "./cases/tool-failure.mjs";
import { MULTI_TURN_CASES } from "./cases/multi-turn.mjs";
import { MULTI_TURN_2_CASES } from "./cases/multi-turn-2.mjs";
import { REGRESSION_V2_CASES } from "./cases/regressions-v2.mjs";
import { READ_PARITY_CASES } from "./cases/read-parity.mjs";

const RAW = [
  ...LEGACY_CASES,
  ...ENROLLMENT_CASES,
  ...DOCUMENT_CASES,
  ...DEPOSIT_ACCOUNT_CASES,
  ...HOUSING_CASES,
  ...FINANCIAL_AID_CASES,
  ...REGISTRATION_CASES,
  ...ACADEMIC_PLAN_CASES,
  ...CAMPUS_LIFE_CASES,
  ...CONVERSATIONAL_CASES,
  ...AMBIGUITY_CASES,
  ...CROSS_DOMAIN_CASES,
  ...CONFLICT_CASES,
  ...TOOL_FAILURE_CASES,
  ...MULTI_TURN_CASES,
  ...MULTI_TURN_2_CASES,
  ...REGRESSION_V2_CASES,
  ...READ_PARITY_CASES,
];

export const QUESTIONS = RAW.map((raw) => {
  const normalized = normalizeCase(raw);
  normalized.tier =
    raw.tier ??
    (normalized.critical ||
    (!normalized.judged && !normalized.faults && normalized.turns.length === 1)
      ? "smoke"
      : "core");
  return normalized;
});

const problems = validateCases(QUESTIONS);
if (problems.length > 0) {
  throw new Error(`Invalid evaluation cases:\n  ${problems.join("\n  ")}`);
}

export const CATEGORIES = [...new Set(QUESTIONS.map((item) => item.category))];

export function questionsByCategory(categories) {
  if (!categories || categories.length === 0) return QUESTIONS;
  const wanted = new Set(categories);
  return QUESTIONS.filter((item) => wanted.has(item.category));
}

export function questionsByTier(tier) {
  if (!tier || tier === "full") return QUESTIONS;
  if (tier === "smoke") return QUESTIONS.filter((item) => item.tier === "smoke");
  if (tier === "core")
    return QUESTIONS.filter((item) => item.tier !== "full");
  throw new Error(`Unknown tier "${tier}" (smoke | core | full)`);
}

export function questionsByIds(ids) {
  const wanted = new Set(ids);
  return QUESTIONS.filter((item) => wanted.has(item.id));
}
