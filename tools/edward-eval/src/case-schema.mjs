/**
 * Evaluation case schema, normalization, and validation.
 *
 * Two authoring shapes are accepted:
 *
 *   Single-turn (the original shape, unchanged):
 *     { id, category, persona, question, checks?, history?, judged?, ... }
 *
 *   Multi-turn (a durable conversation, evaluated per turn):
 *     { id, category, persona, turns: [{ question, checks?, expect?, judged? }, ...], ... }
 *
 * Case-level fields shared by both:
 *   capability        which capability the case exercises (request-type family)
 *   tags              coverage dimensions: multi_turn, cross_domain, edge_state,
 *                     tool_failure, no_tool, conflict, ambiguous, personalization,
 *                     happy_path, ...
 *   critical          a CI invariant: the run fails if this case fails, no
 *                     matter what the aggregate score does
 *   faults            { primitive: "error"|"timeout"|"empty" } injected into the host
 *   expect            tool-selection expectations (single-turn sugar; per-turn
 *                     in the turns shape)
 *   expectedBehavior  one or two sentences the judge grades against
 *   judgeFacts        ground-truth fact groups handed to the judge
 *                     (checklist, documents, deposit, account, registration,
 *                     aid, housing, appointments, academics, campus,
 *                     institutional_gaps)
 *
 * expect := {
 *   requestTypes?:   [..]   the classification must land in this set
 *   requiredTools?:  [..]   every one must execute (any round)
 *   anyOfTools?:     [[..]] at least one from each group must execute
 *   forbiddenTools?: [..]   none may execute
 *   maxTools?:       n      executed-tool ceiling across rounds
 *   dependencyTools?:[..]   reads expected to arrive via the dependency round
 * }
 */

import { PERSONA_NAMES } from "./runner.mjs";

export const TOOL_NAMES = [
  "getStudentProfile",
  "getEnrollmentState",
  "getOnboardingResponses",
  "getOnboardingChecklist",
  "getDocumentStatuses",
  "getEnrollmentHolds",
  "getStudentDeadlines",
  "getSupportOptions",
  "getFinancialAidStatus",
  "getFinancialAidSummary",
  "getAidDisbursements",
  "getFinancialAidSupportOptions",
  "getStudentHousingStatus",
  "getStudentHousingEligibility",
  "getHousingOptions",
  "getRegistrationStatus",
  "getStudentAccountSummary",
  "getAcademicStanding",
  "getStudentSupportRequests",
  "getStudentAppointments",
  "getAcademicPlan",
  "getCampusLife",
  "getStudentMessages",
];

/** Mirrors UNIVERSAL_CONTEXT_TOOLS in planner.py: legitimate for any intent. */
export const UNIVERSAL_CONTEXT_TOOLS = [
  "getStudentProfile",
  "getOnboardingChecklist",
  "getEnrollmentHolds",
  "getStudentDeadlines",
];

export const FAULT_PRIMITIVES = [
  "profile",
  "requirements",
  "documents",
  "payments",
  "financials",
  "dashboard",
  "onboarding",
  "housing_plan",
  "appointments",
  "help",
  "academics",
  "campus_life",
  "messages",
];
export const FAULT_MODES = ["error", "timeout", "empty"];

export const KNOWN_TAGS = new Set([
  "multi_turn",
  "cross_domain",
  "edge_state",
  "tool_failure",
  "no_tool",
  "conflict",
  "ambiguous",
  "ambiguous_no_context",
  "personalization",
  "comparison",
  "discovery",
  "recommendation",
  "happy_path",
  "unknown_information",
  "adversarial",
  "follow_up",
  "smoke",
]);

const CHECK_KINDS = new Set([
  // static kinds (assertions.mjs)
  "mentions",
  "not_mentions",
  "not_mentions_pattern",
  "read_only_refusal",
  "no_other_student_data",
  "no_false_causation",
  "no_invented_policy",
  "request_type",
  "max_tools",
  "block_types",
  "max_sentences",
  // fact-backed kinds (resolved against the persona snapshot)
  "mentions_any_fact",
  "mentions_all_fact",
  "not_mentions_fact",
  "mentions_amount",
  "deposit_state_consistent",
  "acknowledges_unavailable",
]);

export function normalizeCase(raw) {
  const turns = Array.isArray(raw.turns)
    ? raw.turns.map((turn) => ({
        question: turn.question,
        checks: turn.checks ?? [],
        expect: turn.expect ?? null,
        judged: turn.judged ?? raw.judged ?? true,
      }))
    : [
        {
          question: raw.question,
          checks: raw.checks ?? [],
          expect: raw.expect ?? null,
          judged: raw.judged ?? true,
        },
      ];
  const tags = new Set(raw.tags ?? []);
  if (turns.length > 1) tags.add("multi_turn");
  if (raw.faults && Object.keys(raw.faults).length > 0) tags.add("tool_failure");
  return {
    id: raw.id,
    category: raw.category,
    capability: raw.capability ?? raw.category,
    persona: raw.persona,
    faults: raw.faults ?? null,
    judged: raw.judged ?? true,
    critical: raw.critical ?? false,
    tags: [...tags],
    note: raw.note ?? null,
    expectedBehavior: raw.expectedBehavior ?? null,
    judgeFacts: raw.judgeFacts ?? null,
    history: raw.history ?? [],
    turns,
  };
}

export function validateCases(cases) {
  const problems = [];
  const seen = new Set();
  for (const item of cases) {
    const where = `case ${item.id ?? "<missing id>"}`;
    if (!item.id) problems.push(`${where}: missing id`);
    else if (seen.has(item.id)) problems.push(`${where}: duplicate id`);
    seen.add(item.id);
    if (!item.category) problems.push(`${where}: missing category`);
    if (!PERSONA_NAMES.includes(item.persona))
      problems.push(`${where}: unknown persona "${item.persona}"`);
    if (item.history.length > 0 && item.turns.length > 1)
      problems.push(`${where}: seeded history and multi-turn are mutually exclusive`);
    for (const tag of item.tags) {
      if (!KNOWN_TAGS.has(tag)) problems.push(`${where}: unknown tag "${tag}"`);
    }
    if (item.faults) {
      for (const [primitive, mode] of Object.entries(item.faults)) {
        if (!FAULT_PRIMITIVES.includes(primitive))
          problems.push(`${where}: unknown fault primitive "${primitive}"`);
        if (!FAULT_MODES.includes(mode))
          problems.push(`${where}: unknown fault mode "${mode}"`);
      }
    }
    item.turns.forEach((turn, index) => {
      const turnWhere = `${where} turn ${index + 1}`;
      if (!turn.question || typeof turn.question !== "string")
        problems.push(`${turnWhere}: missing question`);
      for (const check of turn.checks) {
        if (!CHECK_KINDS.has(check.kind))
          problems.push(`${turnWhere}: unknown check kind "${check.kind}"`);
      }
      if (turn.expect) {
        for (const key of [
          "requiredTools",
          "forbiddenTools",
          "dependencyTools",
        ]) {
          for (const tool of turn.expect[key] ?? []) {
            if (!TOOL_NAMES.includes(tool))
              problems.push(`${turnWhere}: unknown tool "${tool}" in ${key}`);
          }
        }
        for (const group of turn.expect.anyOfTools ?? []) {
          for (const tool of group) {
            if (!TOOL_NAMES.includes(tool))
              problems.push(`${turnWhere}: unknown tool "${tool}" in anyOfTools`);
          }
        }
      }
    });
  }
  return problems;
}
