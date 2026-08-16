/**
 * Failure taxonomy.
 *
 * Every graded signal — deterministic check failures, contract violations,
 * tool-selection codes, and zero-scored judge dimensions — maps to one typed
 * failure class, so a run can report *what kinds* of failures happened, not
 * just how many. A check may override its default class with `taxonomy:`.
 */

export const TAXONOMY = Object.freeze([
  "INTENT_FAILURE",
  "TOOL_NOT_CALLED",
  "WRONG_TOOL",
  "UNNECESSARY_TOOL",
  "BAD_TOOL_ARGUMENT",
  "DEPENDENCY_READ_MISSING",
  "MISSING_DATA",
  "BAD_RETRIEVAL",
  "STATE_GROUNDING_FAILURE",
  "POLICY_GROUNDING_FAILURE",
  "REASONING_FAILURE",
  "CROSS_DOMAIN_REASONING_FAILURE",
  "CONVERSATION_CONTEXT_FAILURE",
  "HALLUCINATION",
  "INCOMPLETE_RESPONSE",
  "BAD_NEXT_STEP",
  "BAD_PERSONALIZATION",
  "BAD_RESPONSE_MODE",
  "TOOL_ERROR_HANDLING",
  "ACTION_SAFETY_FAILURE",
  "CONTRACT_VIOLATION",
  "UNKNOWN",
]);

const CHECK_KIND_TAXONOMY = {
  mentions: "INCOMPLETE_RESPONSE",
  mentions_any_fact: "INCOMPLETE_RESPONSE",
  mentions_all_fact: "INCOMPLETE_RESPONSE",
  mentions_amount: "INCOMPLETE_RESPONSE",
  not_mentions: "HALLUCINATION",
  not_mentions_pattern: "HALLUCINATION",
  not_mentions_fact: "HALLUCINATION",
  deposit_state_consistent: "STATE_GROUNDING_FAILURE",
  acknowledges_unavailable: "TOOL_ERROR_HANDLING",
  read_only_refusal: "ACTION_SAFETY_FAILURE",
  no_other_student_data: "ACTION_SAFETY_FAILURE",
  no_false_causation: "REASONING_FAILURE",
  no_invented_policy: "POLICY_GROUNDING_FAILURE",
  request_type: "INTENT_FAILURE",
  max_tools: "UNNECESSARY_TOOL",
  block_types: "BAD_RESPONSE_MODE",
  max_sentences: "BAD_RESPONSE_MODE",
};

const JUDGE_DIMENSION_TAXONOMY = {
  factual_correctness: "STATE_GROUNDING_FAILURE",
  evidence_grounding: "STATE_GROUNDING_FAILURE",
  completeness: "INCOMPLETE_RESPONSE",
  reasoning: "REASONING_FAILURE",
  response_mode: "BAD_RESPONSE_MODE",
  helpfulness: "INCOMPLETE_RESPONSE",
  next_step: "BAD_NEXT_STEP",
  continuity: "CONVERSATION_CONTEXT_FAILURE",
  personalization: "BAD_PERSONALIZATION",
  hallucination_free: "HALLUCINATION",
};

function crossDomainAware(code, tags) {
  if (code === "REASONING_FAILURE" && tags?.includes("cross_domain")) {
    return "CROSS_DOMAIN_REASONING_FAILURE";
  }
  return code;
}

/**
 * @param {object} turnResult one graded turn:
 *   { checks, checkFailures, contractFailures, toolCodes, judgement, response, error }
 * @param {object} caseMeta   { tags, faults }
 */
export function classifyTurn(turnResult, caseMeta) {
  const found = [];
  const add = (code, source, detail) =>
    found.push({ code: crossDomainAware(code, caseMeta?.tags), source, detail });

  if (turnResult.error) {
    add("UNKNOWN", "runner", turnResult.error);
    return found;
  }

  for (const failure of turnResult.contractFailures ?? []) {
    add("CONTRACT_VIOLATION", "contract", failure);
  }

  // checkFailures align 1:1 with the checks that failed; the runner stores
  // them as {check, failure} pairs so the kind is recoverable here.
  for (const entry of turnResult.checkFailures ?? []) {
    const kind = entry.check?.kind;
    const code =
      entry.check?.taxonomy ?? CHECK_KIND_TAXONOMY[kind] ?? "UNKNOWN";
    add(code, `check:${kind}`, entry.failure);
  }

  for (const toolCode of turnResult.toolCodes ?? []) {
    add(toolCode.code, "tool_selection", toolCode.detail);
  }

  // A read that failed on a case that did NOT inject a fault is a retrieval
  // problem in its own right.
  const unavailable = (turnResult.response?.trace?.toolCalls ?? []).filter(
    (call) => call.status !== "available",
  );
  if (unavailable.length > 0 && !(caseMeta?.faults && Object.keys(caseMeta.faults).length)) {
    add(
      "BAD_RETRIEVAL",
      "trace",
      `unavailable reads: ${unavailable.map((call) => `${call.tool}(${call.status})`).join(", ")}`,
    );
  }

  const scores = turnResult.judgement?.scores ?? {};
  for (const [dimension, score] of Object.entries(scores)) {
    if (score === 0) {
      add(
        JUDGE_DIMENSION_TAXONOMY[dimension] ?? "UNKNOWN",
        `judge:${dimension}`,
        turnResult.judgement?.worstProblem ?? "",
      );
    }
  }

  return found;
}

export function dedupeCodes(entries) {
  return [...new Set(entries.map((entry) => entry.code))];
}
