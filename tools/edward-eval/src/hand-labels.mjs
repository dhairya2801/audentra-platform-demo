/**
 * Hand labels for judge calibration.
 *
 * These were assigned by reading the round-4 answers directly, before looking
 * at the judge's scores for the two criteria under test. They exist to answer
 * one question: can the model judge's per-criterion scores be trusted?
 *
 * `separatesConfirmed` uses the same 0/1/2 scale as the judge:
 *   2 - every claim in the reply is supported by the retrieved evidence
 *   1 - mostly supported, with an overreach that a student would not be harmed by
 *   0 - asserts something the evidence does not support, including inventing a
 *       causal link between two facts that are merely both true
 *
 * `verdict` is an overall shipping judgement: would this answer be acceptable
 * to send to a real student?
 */
export const HAND_LABELS = Object.freeze({
  a1: { separatesConfirmed: 2, verdict: "good" },
  a2: { separatesConfirmed: 2, verdict: "good" },
  a3: { separatesConfirmed: 2, verdict: "good" },
  a4: { separatesConfirmed: 2, verdict: "good" },
  c1: { separatesConfirmed: 2, verdict: "good" },
  c2: { separatesConfirmed: 2, verdict: "good" },
  // "your outstanding account balance is blocking your financial aid" - the
  // balance is not an aid gate. Invented causation.
  c3: { separatesConfirmed: 0, verdict: "bad" },
  // Immunisation blocks registration and housing, not orientation.
  c8: { separatesConfirmed: 1, verdict: "weak" },
  x1: { separatesConfirmed: 2, verdict: "good" },
  x2: { separatesConfirmed: 2, verdict: "good" },
  x3: { separatesConfirmed: 2, verdict: "good" },
  x4: { separatesConfirmed: 2, verdict: "good" },
  // Grounded, but answers the earlier question rather than "how do I fix that".
  f1: { separatesConfirmed: 2, verdict: "weak" },
  f2: { separatesConfirmed: 2, verdict: "weak" },
  f3: { separatesConfirmed: 2, verdict: "good" },
  // Claims two remaining onboarding requirements when the checklist has seven.
  m2: { separatesConfirmed: 0, verdict: "bad" },
  m3: { separatesConfirmed: 2, verdict: "good" },
  // "You cannot register because your financial aid is not complete" contradicts
  // the registration policy and the gate list.
  m4: { separatesConfirmed: 0, verdict: "bad" },
  m5: { separatesConfirmed: 0, verdict: "bad" },
  p1: { separatesConfirmed: 2, verdict: "good" },
  p2: { separatesConfirmed: 2, verdict: "good" },
  p3: { separatesConfirmed: 2, verdict: "good" },
  p4: { separatesConfirmed: 2, verdict: "good" },
  t1: { separatesConfirmed: 2, verdict: "good" },
  t2: { separatesConfirmed: 2, verdict: "good" },
  // Lists documents that are not part of the verification worksheet.
  t3: { separatesConfirmed: 1, verdict: "weak" },
  // Leaks the internal word "conflicting" into student-facing prose.
  t4: { separatesConfirmed: 2, verdict: "weak" },
  s1: { separatesConfirmed: 2, verdict: "good" },
  s2: { separatesConfirmed: 2, verdict: "good" },
  s4: { separatesConfirmed: 2, verdict: "good" },
});

/** Judge total mapped onto the same coarse verdict scale. */
export function verdictFromJudgeTotal(total, maximum) {
  const ratio = total / maximum;
  if (ratio >= 0.8) return "good";
  if (ratio >= 0.6) return "weak";
  return "bad";
}
