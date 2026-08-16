#!/usr/bin/env node
/**
 * Coverage matrix generator.
 *
 *   node tools/edward-eval/coverage.mjs            # full suite → COVERAGE.md
 *   node tools/edward-eval/coverage.mjs --legacy   # the original 115 only
 *
 * Builds Capability × (single-turn, multi-turn, edge state, cross-domain,
 * tool failure, no-tool, conflict, ambiguous, personas) from the normalized
 * dataset, maps every tool to the cases that exercise it, and flags gaps:
 * zero-coverage tools, weak request types, weak personas, and capabilities
 * tested only through happy paths.
 */
import { writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { QUESTIONS } from "./src/questions.mjs";
import { TOOL_NAMES } from "./src/case-schema.mjs";
import { PERSONA_NAMES } from "./src/runner.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const legacyOnly = process.argv.includes("--legacy");

/**
 * Mirror of planner.py _SELECTION_RULES (reporting only — the pipeline's own
 * table remains the source of truth for behaviour). Lets the matrix credit a
 * case to the tools its request types deterministically select, even where
 * the case declares no explicit tool expectations.
 */
const SELECTION_RULES = {
  greeting: ["getStudentProfile"],
  capability_overview: [],
  general_help: ["getOnboardingChecklist", "getEnrollmentHolds", "getStudentDeadlines"],
  remaining_steps: ["getOnboardingChecklist"],
  completed_steps: ["getOnboardingChecklist"],
  next_action: ["getOnboardingChecklist", "getEnrollmentHolds", "getStudentDeadlines"],
  missing_documents: ["getOnboardingChecklist", "getDocumentStatuses"],
  document_status: ["getOnboardingChecklist", "getDocumentStatuses"],
  onboarding_status: ["getOnboardingChecklist"],
  holds_and_blockers: ["getEnrollmentHolds"],
  deadlines: ["getStudentDeadlines"],
  request_support: ["getSupportOptions"],
  aid_status: ["getFinancialAidStatus"],
  aid_remaining_steps: ["getFinancialAidStatus"],
  aid_incomplete_reason: ["getFinancialAidStatus"],
  aid_missing_documents: ["getFinancialAidStatus"],
  aid_verification_status: ["getFinancialAidStatus"],
  aid_award_acceptance_status: ["getFinancialAidStatus", "getFinancialAidSummary"],
  aid_summary: ["getFinancialAidSummary", "getFinancialAidStatus"],
  aid_application_status: ["getFinancialAidSummary", "getFinancialAidStatus"],
  aid_disbursement: ["getAidDisbursements", "getFinancialAidSummary", "getEnrollmentHolds"],
  aid_coverage: ["getFinancialAidSummary", "getStudentAccountSummary"],
  aid_next_action: ["getFinancialAidStatus"],
  aid_support: ["getFinancialAidStatus", "getFinancialAidSupportOptions"],
  housing_status: ["getStudentHousingStatus"],
  housing_options: ["getHousingOptions"],
  housing_remaining_steps: ["getStudentHousingStatus"],
  housing_next_action: ["getStudentHousingStatus", "getStudentDeadlines"],
  housing_support: ["getStudentHousingStatus", "getSupportOptions"],
  housing_eligibility: [
    "getStudentHousingEligibility",
    "getStudentHousingStatus",
    "getOnboardingChecklist",
    "getEnrollmentHolds",
  ],
  policy_lookup: [],
  registration_status: ["getRegistrationStatus", "getEnrollmentHolds", "getOnboardingChecklist"],
  student_account: ["getStudentAccountSummary", "getEnrollmentHolds"],
  appointments: ["getStudentAppointments"],
  academic_plan: ["getAcademicPlan"],
  campus_life: ["getCampusLife"],
  messages_unread: ["getStudentMessages"],
  general_question: ["getOnboardingChecklist", "getEnrollmentHolds", "getStudentDeadlines"],
  unsupported_or_out_of_scope: [],
};

const { LEGACY_CASES } = await import("./src/cases/legacy.mjs");
const legacyIds = new Set(LEGACY_CASES.map((item) => item.id));
const cases = legacyOnly
  ? QUESTIONS.filter((item) => legacyIds.has(item.id))
  : QUESTIONS;

const DIMENSIONS = [
  ["single_turn", (item) => item.turns.length === 1 && item.history.length === 0],
  ["multi_turn", (item) => item.turns.length > 1 || item.history.length > 0],
  ["edge_state", (item) => item.tags.includes("edge_state")],
  ["cross_domain", (item) => item.tags.includes("cross_domain")],
  ["tool_failure", (item) => Boolean(item.faults)],
  [
    "no_tool",
    (item) =>
      item.tags.includes("no_tool") ||
      ["greeting", "capability_overview", "policy_lookup"].includes(item.capability),
  ],
  ["conflict", (item) => item.tags.includes("conflict") || item.category === "conflicting_state"],
  [
    "ambiguous",
    (item) =>
      item.tags.includes("ambiguous") ||
      item.tags.includes("ambiguous_no_context") ||
      item.category === "ambiguous",
  ],
];

/** Which tools a case exercises: explicit expectations, else its request types' rules. */
function toolsFor(item) {
  const tools = new Set();
  for (const turn of item.turns) {
    for (const tool of turn.expect?.requiredTools ?? []) tools.add(tool);
    for (const group of turn.expect?.anyOfTools ?? []) for (const tool of group) tools.add(tool);
    for (const tool of turn.expect?.dependencyTools ?? []) tools.add(tool);
    for (const type of turn.expect?.requestTypes ?? []) {
      for (const tool of SELECTION_RULES[type] ?? []) tools.add(tool);
    }
    // Legacy cases express intent via request_type checks rather than expect.
    for (const check of turn.checks ?? []) {
      if (check.kind === "request_type") {
        for (const type of check.any ?? []) {
          for (const tool of SELECTION_RULES[type] ?? []) tools.add(tool);
        }
      }
    }
  }
  if (tools.size === 0) {
    for (const tool of SELECTION_RULES[item.capability] ?? []) tools.add(tool);
  }
  return tools;
}

/* ---- capability × dimension matrix ---- */
const capabilities = [...new Set(cases.map((item) => item.capability))].sort();
const matrix = new Map();
for (const capability of capabilities) {
  const rows = cases.filter((item) => item.capability === capability);
  const entry = { cases: rows.length, personas: new Set(rows.map((row) => row.persona)) };
  for (const [dimension, test] of DIMENSIONS) entry[dimension] = rows.filter(test).length;
  matrix.set(capability, entry);
}

/* ---- tool → case mapping ---- */
const toolCases = new Map(TOOL_NAMES.map((tool) => [tool, []]));
for (const item of cases) {
  for (const tool of toolsFor(item)) toolCases.get(tool)?.push(item.id);
}

/* ---- persona coverage ---- */
const personaCases = new Map(PERSONA_NAMES.map((persona) => [persona, []]));
for (const item of cases) personaCases.get(item.persona)?.push(item.id);

/* ---- gap analysis ---- */
const gaps = [];
for (const [tool, ids] of toolCases) {
  if (ids.length === 0) gaps.push(`**Zero direct coverage — tool** \`${tool}\`: no case exercises it.`);
  else if (ids.length <= 2) gaps.push(`Weak coverage — tool \`${tool}\`: only ${ids.length} case(s) (${ids.join(", ")}).`);
}
for (const [persona, ids] of personaCases) {
  if (ids.length === 0) gaps.push(`**Zero coverage — persona** \`${persona}\`.`);
  else if (ids.length <= 3) gaps.push(`Weak coverage — persona \`${persona}\`: ${ids.length} case(s).`);
}
for (const [capability, entry] of matrix) {
  const nonHappy =
    entry.edge_state + entry.cross_domain + entry.tool_failure + entry.conflict + entry.ambiguous;
  if (entry.cases >= 3 && nonHappy === 0) {
    gaps.push(`Happy-path-only capability \`${capability}\` (${entry.cases} cases, no edge/conflict/failure variants).`);
  }
  if (entry.cases >= 3 && entry.multi_turn === 0) {
    gaps.push(`No multi-turn coverage for capability \`${capability}\`.`);
  }
}

/* ---- render ---- */
const total = cases.length;
const turns = cases.reduce((sum, item) => sum + item.turns.length, 0);
const lines = [];
lines.push(`# Edward evaluation coverage matrix${legacyOnly ? " — original 115-case suite" : ""}`);
lines.push("");
lines.push(`Generated by \`tools/edward-eval/coverage.mjs\`${legacyOnly ? " --legacy" : ""} — do not edit by hand.`);
lines.push("");
lines.push(`**${total} cases · ${turns} turns · ${capabilities.length} capabilities · ${[...personaCases.values()].filter((ids) => ids.length > 0).length}/${PERSONA_NAMES.length} personas · ${[...toolCases.values()].filter((ids) => ids.length > 0).length}/${TOOL_NAMES.length} tools covered**`);
lines.push("");
lines.push("## Capability × dimension");
lines.push("");
lines.push("| Capability | Cases | Single-turn | Multi-turn | Edge state | Cross-domain | Tool failure | No-tool | Conflict | Ambiguous | Personas |");
lines.push("|---|---|---|---|---|---|---|---|---|---|---|");
for (const [capability, entry] of matrix) {
  lines.push(
    `| ${capability} | ${entry.cases} | ${entry.single_turn} | ${entry.multi_turn} | ${entry.edge_state} | ${entry.cross_domain} | ${entry.tool_failure} | ${entry.no_tool} | ${entry.conflict} | ${entry.ambiguous} | ${entry.personas.size} |`,
  );
}
lines.push("");
lines.push("## Tool → evaluation cases");
lines.push("");
lines.push("| Tool | Cases | Examples |");
lines.push("|---|---|---|");
for (const [tool, ids] of toolCases) {
  lines.push(`| ${tool} | ${ids.length} | ${ids.slice(0, 6).join(", ")}${ids.length > 6 ? ", …" : ""} |`);
}
lines.push("");
lines.push("## Persona coverage");
lines.push("");
lines.push("| Persona | Cases |");
lines.push("|---|---|");
for (const [persona, ids] of personaCases) {
  lines.push(`| ${persona} | ${ids.length} |`);
}
lines.push("");
lines.push("## Gaps");
lines.push("");
if (gaps.length === 0) lines.push("No gaps by the criteria above.");
for (const gap of gaps) lines.push(`- ${gap}`);
lines.push("");

const target = join(here, legacyOnly ? "COVERAGE-legacy-115.md" : "COVERAGE.md");
writeFileSync(target, `${lines.join("\n")}\n`);
console.log(lines.join("\n"));
console.log(`\nwritten to ${target}`);
