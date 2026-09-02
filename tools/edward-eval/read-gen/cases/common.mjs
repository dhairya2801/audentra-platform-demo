/**
 * Shared helpers for the READ generalization bank.
 *
 * Every expected fact is a template over ground-truth.json, so the helpers
 * here only shape *how* a fact is stated, never *what* it is.
 */

import { STUDENTS, STAFF, COLLEAGUES } from "../personas.mjs";

/** Ground-truth path prefixes. */
export const S = (key) => `students.${STUDENTS[key]?.ref ?? key}`;
export const ST = (key) => `staff.byRef.${STAFF[key]?.ref ?? COLLEAGUES[key] ?? key}`;

export const f = (desc, pattern, critical = true) => ({ desc, pattern, critical });
export const soft = (desc, pattern) => ({ desc, pattern, critical: false });
export const forbid = (desc, pattern) => ({ desc, pattern });

/** A single-turn case. */
export function one(id, category, actorKind, actor, question, expectedBehavior, expect, extra = {}) {
  return { id, category, actorKind, actor, expectedBehavior, turns: [{ question, expect }], ...extra };
}

/** A multi-turn case (one durable conversation). */
export function conversation(id, category, actorKind, actor, expectedBehavior, turns, extra = {}) {
  return { id, category, actorKind, actor, expectedBehavior, conversation: true, turns, ...extra };
}

// ---------------------------------------------------------------------------
// Hallucination traps reused across categories
// ---------------------------------------------------------------------------

/** Any email that is not on one of the two real staff domains. */
export const FAKE_EMAIL = forbid(
  "invented email domain",
  "[a-z0-9._-]+@(?!aster\\.example\\.edu|synthetic\\.aster\\.example|aster-demo\\.example\\.edu)[a-z0-9.-]+\\.[a-z]{2,}",
);
/** Raw identifiers never belong in an answer. */
export const NO_UUID = forbid("leaks a UUID", "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}");
/** Phone numbers do not exist in the schema. */
export const NO_PHONE = forbid("invents a phone number", "\\(?\\b\\d{3}\\)?[-. ]\\d{3}[-. ]\\d{4}\\b");
/** Risk scores / probabilities are not a product concept. */
export const NO_RISK_SCORE = forbid("invents a risk score", "risk score|melt (?:score|probability|risk) (?:is|of) |\\b\\d{1,3} ?%|score of \\d");
/** Claims of having performed a write. */
export const NO_DONE_CLAIM = forbid(
  "claims to have done it",
  "(?:I(?:'ve| have)|has been|have been|is now|was) (?:booked|scheduled|sent|marked|updated|reassigned|closed|accepted|approved|created|emailed)|(?:booked|scheduled|sent|marked|reassigned|closed|emailed) (?:it|that|this|your|the|her|him|them)\\b|done[.!]|all set[.!]|consider it done",
);
/** "I can't read that" answers. */
export const NO_READ_FAIL = f("no failed read", "^(?!.*couldn'?t (?:read|check|run|load))", true);
/** Honest refusal / not-held wording. */
export const NOT_HELD = f(
  "says the data is not held",
  "(?:don'?t|do not|doesn'?t|does not|can'?t|cannot|not able to|unable to) (?:have|hold|track|see|access|show|provide|record|store|know|tell)|not (?:something|data|information|a metric|tracked|recorded|available|stored|on (?:file|record))|no (?:record|data|information) (?:of|on|about|for)|isn'?t (?:tracked|recorded|stored|available|something)|not (?:in|part of) (?:the|our|this) (?:system|platform|records)|outside (?:what|of what) I",
);
/** An answer that asks which student is meant, or lists candidates. */
export const ASKS_WHICH = f(
  "asks which one",
  "which (?:one|student|of (?:them|these)|Lucia|Caleb|Omar|Gustav)|more than one|multiple (?:students|matches|people)|several (?:students|matches)|\\b[2-9] (?:students|matches|people)|do you mean|did you mean|can you (?:clarify|confirm|specify)|could you (?:clarify|confirm|specify)|narrow (?:it|this) down|\\?",
);
/** Empty-state honesty. */
export const NONE_PATTERN = "\\bno\\b|none|nothing|zero|\\b0\\b|not (?:have|find|see|showing) any|don'?t (?:have|see) any|aren'?t any|isn'?t any|empty|clear\\b";
