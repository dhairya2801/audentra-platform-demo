/**
 * Hand labels for judge calibration — v2 harness.
 *
 * Keys are `<caseId>` for turn 1 or `<caseId>#t<turnNumber>` for later turns
 * of a multi-turn case. Each label was produced by reading the actual answer
 * in the batch named below against the persona's canonical snapshot, using
 * the explicit rubrics in src/judge.mjs — the same rubrics the judge gets,
 * applied by a human reader.
 *
 * Labeled batch: python-edward-baseline-4
 *
 * verdict: the holistic call a reviewer would make —
 *   good  ship it as-is
 *   weak  acceptable but flawed (imprecise, incomplete, or padded)
 *   bad   a student would be misled or blocked
 * dimensions: 0/1/2 per rubric, only for dimensions the labeler scored.
 */

export const LABELED_BATCH = "python-edward-baseline-4";

export const HAND_LABELS_V2 = {
  /* ---- honest institutional-gap refusals (correct behaviour) ---- */
  s5: {
    verdict: "good",
    dimensions: { factual_correctness: 2, evidence_grounding: 2, completeness: 2, response_mode: 2, hallucination_free: 2, helpfulness: 1 },
    note: "No term calendar exists; refusing + routing IS the right answer.",
  },
  p5: {
    verdict: "good",
    dimensions: { factual_correctness: 2, evidence_grounding: 2, completeness: 2, response_mode: 2, hallucination_free: 2, helpfulness: 1 },
    note: "No policy source exists; honest refusal + route is correct.",
  },

  /* ---- clean answers ---- */
  s11: { verdict: "good", dimensions: { factual_correctness: 2, completeness: 2, hallucination_free: 2, helpfulness: 1 }, note: "Correct gates; repeats the document list twice." },
  c8: { verdict: "good", dimensions: { factual_correctness: 2, hallucination_free: 2, helpfulness: 2 } },
  m9: { verdict: "good", dimensions: { factual_correctness: 2, reasoning: 2, next_step: 2 } },
  t3: { verdict: "good", dimensions: { factual_correctness: 2, hallucination_free: 2 } },
  t6: { verdict: "good", dimensions: { factual_correctness: 2, completeness: 2 } },
  a4: { verdict: "good", dimensions: { factual_correctness: 2, completeness: 2 } },
  f2: { verdict: "good", dimensions: { evidence_grounding: 2, hallucination_free: 2 } },
  f3: { verdict: "good", dimensions: { factual_correctness: 2, helpfulness: 2, response_mode: 2 } },
  x4: { verdict: "good", dimensions: { factual_correctness: 2, evidence_grounding: 2 } },
  g8: { verdict: "good", dimensions: { factual_correctness: 2, next_step: 2 } },
  "aid-017": { verdict: "good", dimensions: { factual_correctness: 2, next_step: 2 } },
  d5: { verdict: "good", dimensions: { factual_correctness: 2, completeness: 2 } },
  d8: { verdict: "good", dimensions: { factual_correctness: 2, completeness: 2 } },
  "doc-018": { verdict: "good", dimensions: { factual_correctness: 2, hallucination_free: 2 } },
  "dep-016": { verdict: "good", dimensions: { factual_correctness: 2 } },
  "hou-014": { verdict: "good", dimensions: { factual_correctness: 2, response_mode: 2 } },
  "reg-009": { verdict: "good", dimensions: { factual_correctness: 2, reasoning: 2 } },
  "acad-010": { verdict: "good", dimensions: { factual_correctness: 2, reasoning: 2, helpfulness: 2 } },
  "camp-012": { verdict: "good", dimensions: { factual_correctness: 2, response_mode: 2 } },
  "conv-013": { verdict: "good", dimensions: { factual_correctness: 2, helpfulness: 2 } },
  "conv-014": { verdict: "good", dimensions: { factual_correctness: 2, next_step: 2 } },
  "xd-010": { verdict: "good", dimensions: { factual_correctness: 2, reasoning: 2, completeness: 2 } },
  "conf-008": { verdict: "good", dimensions: { factual_correctness: 2, evidence_grounding: 2 } },
  "amb-014": {
    verdict: "weak",
    dimensions: { factual_correctness: 1, reasoning: 1, hallucination_free: 2 },
    note: "Pending state right, but 'you need to pay the deposit' contradicts the pending payment it just described.",
  },

  /* ---- subtle failures the judge should catch ---- */
  c6: {
    verdict: "weak",
    dimensions: { factual_correctness: 2, reasoning: 1, completeness: 2 },
    note: "Invents sequencing: housing is ready now, not gated on aid verification.",
  },
  m7: {
    verdict: "bad",
    dimensions: { factual_correctness: 1, reasoning: 0, hallucination_free: 1 },
    note: "Invents 'cannot move in before orientation because no preference selected' — no move-in data exists and the causation is made up.",
  },
  p6: {
    verdict: "weak",
    dimensions: { factual_correctness: 1, response_mode: 1, hallucination_free: 1 },
    note: "No payment-plan product exists; reframing the deposit as 'the payment plan' misleads.",
  },
  a1: {
    verdict: "weak",
    dimensions: { response_mode: 1, reasoning: 1 },
    note: "Assumes 'it' = registration without asking; confident guess on an empty referent.",
  },
  g11: {
    verdict: "weak",
    dimensions: { response_mode: 0, helpfulness: 1 },
    note: "'quick question' is not a question; dumping a next step instead of inviting the question misses the mode.",
  },
  x3: {
    verdict: "bad",
    dimensions: { factual_correctness: 0, evidence_grounding: 1, hallucination_free: 1 },
    note: "Says identity/transcript deadlines are 'coming up soon' — they already passed.",
  },
  "enr-028": {
    verdict: "weak",
    dimensions: { factual_correctness: 2, completeness: 1 },
    note: "An 'honest status check' that never mentions the returned transcript omits the headline.",
  },
  "conf-001": {
    verdict: "weak",
    dimensions: { factual_correctness: 1, evidence_grounding: 1 },
    note: "'May still be pending verification' hedges toward the claim; no payment exists at all.",
  },

  /* ---- clear failures (judge should stay low) ---- */
  "aid-018": {
    verdict: "bad",
    dimensions: { factual_correctness: 0, evidence_grounding: 0, hallucination_free: 0 },
    note: "Fabricates $0 award amounts and denies the refund state outright.",
  },
  "enr-014": {
    verdict: "bad",
    dimensions: { helpfulness: 0, completeness: 0 },
    note: "Template stub '6 checklist step(s) still need attention' answers nothing.",
  },
  "doc-016": {
    verdict: "bad",
    dimensions: { response_mode: 0, helpfulness: 0 },
    note: "Navigational question refused as a write request; boilerplate names no page.",
  },
  "dep-011": {
    verdict: "bad",
    dimensions: { factual_correctness: 0, hallucination_free: 0 },
    note: "Fabricates a tuition due date from the award-acceptance deadline.",
  },
  "hou-005": {
    verdict: "bad",
    dimensions: { factual_correctness: 0, hallucination_free: 0 },
    note: "Invents a housing assignment; only a preference exists.",
  },
  "reg-008": {
    verdict: "bad",
    dimensions: { factual_correctness: 0, reasoning: 0, response_mode: 1 },
    note: "Makes aid verification a registration prerequisite (false) and never names a course.",
  },
  "acad-003": {
    verdict: "bad",
    dimensions: { helpfulness: 0, completeness: 0, reasoning: 0 },
    note: "Stub answer; the CS 101 prerequisite never appears.",
  },
  "camp-011": {
    verdict: "bad",
    dimensions: { factual_correctness: 0, reasoning: 0, response_mode: 1 },
    note: "Invents 'involvement gated on checklist'; no clubs named.",
  },
  "amb-010": {
    verdict: "bad",
    dimensions: { response_mode: 0, reasoning: 0 },
    note: "Confident 'yes, again' with no referent and nothing ever submitted.",
  },
  "xd-007": {
    verdict: "bad",
    dimensions: { factual_correctness: 0, hallucination_free: 0, completeness: 1 },
    note: "Fabricates $0 amounts; wrong tool for a disbursement question.",
  },
};

/** Map a judge score fraction onto the reviewer verdict scale. */
export function verdictFromScore(total, maximum) {
  if (!maximum) return null;
  const fraction = total / maximum;
  if (fraction >= 0.85) return "good";
  if (fraction >= 0.6) return "weak";
  return "bad";
}
