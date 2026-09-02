#!/usr/bin/env node
/**
 * Export the READ generalization question bank as one flat CSV.
 *
 * One row per user turn (dev + holdout), carrying the question, the expected
 * behaviour in prose, the expected values resolved from ground-truth.json
 * (facts / fact groups / structural checks), and the forbidden claims. The
 * bank is generated from the case modules so the columns can never drift
 * from what the runner grades.
 *
 *   node tools/edward-eval/read-gen/export-bank.mjs
 *   node tools/edward-eval/read-gen/export-bank.mjs --out /tmp/bank.csv
 */

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { CASES } from "./cases.mjs";
import { HOLDOUT_CASES } from "./holdout-cases.mjs";
import { actorRef } from "./personas.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(HERE, "..", "..", "..");
const GROUND_TRUTH_PATH =
  process.env.READ_GEN_GROUND_TRUTH ?? join(REPO_ROOT, "artifacts", "read-gen-eval", "ground-truth.json");

function parseArgs(argv) {
  const args = { out: null };
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    if (flag === "--out") args.out = argv[++index];
    else throw new Error(`Unknown flag: ${flag}`);
  }
  return args;
}

function gtLookup(truth, path) {
  let value = truth;
  for (const part of path.split(".")) {
    if (value == null) return undefined;
    value = Array.isArray(value) ? value[Number(part)] : value[part];
  }
  return value;
}

/** Render a template with the raw ground-truth values (human-readable, not regex). */
function render(pattern, truth) {
  if (!truth) return pattern;
  return pattern.replace(/\{\{(gt|re|num~?|date|any|all):([^}]+)\}\}/g, (_, kind, spec) => {
    const [path, field] = spec.split("|").map((s) => s.trim());
    const value = gtLookup(truth, path);
    if (value === undefined || value === null) return `<missing ${path}>`;
    if (Array.isArray(value)) {
      const items = value.map((entry) => (field ? entry?.[field] : entry)).filter((v) => v != null);
      return `${kind === "all" ? "ALL OF" : "ANY OF"}[${items.join("; ")}]`;
    }
    if (kind === "num~") return `~${value}`;
    return String(value);
  });
}

function cell(value) {
  const text = String(value ?? "").replace(/\r?\n/g, " ").replace(/\s+/g, " ").trim();
  return `"${text.replace(/"/g, '""')}"`;
}

function describeExpect(expect, truth) {
  const parts = [];
  for (const fact of expect.facts ?? []) parts.push(`${fact.critical === false ? "soft " : ""}${fact.desc}: ${render(fact.pattern, truth)}`);
  for (const group of expect.factGroups ?? []) parts.push(`one of {${group.map((f) => `${f.desc}: ${render(f.pattern, truth)}`).join(" AND ")}}`);
  if ("resolvedStudentId" in expect) parts.push(expect.resolvedStudentId === null ? "must not resolve a student" : `resolves ${render(`{{gt:${String(expect.resolvedStudentId).slice(3)}}}`, truth)}`);
  if (expect.resolvedStudentIn) parts.push(`resolves one of ${expect.resolvedStudentIn}`);
  if (expect.requiredTools) parts.push(`tools: ${expect.requiredTools.join(",")}`);
  if (expect.anyOfTools) parts.push(`any tool of: ${expect.anyOfTools.map((g) => g.join("|")).join(" ; ")}`);
  if (expect.actionIntents) parts.push(`action intents: ${expect.actionIntents}`);
  if (expect.proposeOrClarify) parts.push("proposes an action or asks a clarifying question");
  if (expect.mustAsk) parts.push("must ask a clarifying question");
  return parts.join(" | ");
}

const args = parseArgs(process.argv.slice(2));
let truth = null;
try {
  truth = JSON.parse(readFileSync(GROUND_TRUTH_PATH, "utf8"));
} catch {
  process.stderr.write(`ground truth not found at ${GROUND_TRUTH_PATH}; exporting raw templates\n`);
}

const HEADER = ["id", "suite", "actor_kind", "actor", "category", "turn", "question", "expected_behaviour", "expected_values_from_backend", "forbidden"];
const rows = [];
for (const [suite, cases] of [["dev", CASES], ["holdout", HOLDOUT_CASES]]) {
  for (const item of cases) {
    const ref = actorRef(item.actorKind, item.actor);
    const entry = item.actorKind === "student" ? truth?.students?.[ref] : truth?.staff?.byRef?.[ref];
    const actorLabel = entry?.name ? `${ref} (${entry.name})` : ref;
    item.turns.forEach((turn, index) => {
      rows.push([
        item.id,
        suite,
        item.actorKind,
        actorLabel,
        item.category,
        `${index + 1} of ${item.turns.length}`,
        render(turn.question, truth),
        item.expectedBehavior ?? "",
        describeExpect(turn.expect ?? {}, truth),
        (turn.expect?.forbidden ?? []).map((f) => `${f.desc}: ${render(f.pattern, truth)}`).join(" | "),
      ]);
    });
  }
}
const csv = [HEADER, ...rows].map((row) => row.map(cell).join(",")).join("\n");
const out = args.out ?? join(REPO_ROOT, "docs", "edward-read-gen-question-bank.csv");
mkdirSync(dirname(out), { recursive: true });
writeFileSync(out, `${csv}\n`);
process.stdout.write(`${rows.length} turns (${CASES.length} dev + ${HOLDOUT_CASES.length} holdout cases) → ${out}\n`);
