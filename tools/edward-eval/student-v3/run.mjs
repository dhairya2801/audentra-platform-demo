#!/usr/bin/env node
/**
 * Student Edward v3 evaluation runner.
 *
 * Drives the canonical Python Edward (the same FastAPI routes, pipeline,
 * tools, guards and tracing production uses) through
 * `tools/edward-eval/src/runner.mjs`, and grades every turn **deterministically**
 * against ground truth derived from the same host's REST snapshot
 * (`src/facts.mjs`) — the product's own reads, never a hand-written belief.
 *
 *   node tools/edward-eval/student-v3/run.mjs                     # dev suite
 *   node tools/edward-eval/student-v3/run.mjs --holdout
 *   node tools/edward-eval/student-v3/run.mjs --category housing -v
 *   node tools/edward-eval/student-v3/run.mjs --id stu-nav-001 -v
 *
 * Grades: PASS (every check), PARTIAL (intent answered, a non-critical fact
 * missed or an intent mismatch), FAIL (critical fact missed, forbidden claim,
 * required read never executed).
 *
 * No LLM judge: the only model spend is Edward's own planner/composer calls,
 * metered from the per-turn trace and reported in summary.json.
 */

import { foldTypography } from "../src/typography.mjs";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { runCases } from "../src/runner.mjs";
import { deriveFacts } from "../src/facts.mjs";
import { normalizeCase } from "../src/case-schema.mjs";
import { CASES } from "./cases.mjs";
import { HOLDOUT_CASES } from "./holdout-cases.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(HERE, "..", "..", "..");
const SNAPSHOT_CACHE = join(HERE, "..", "..", "..", "artifacts", "student-v3-snapshots.json");
import { pricingForModel } from "../src/pricing.mjs";
const PRICING = pricingForModel();
const PRICE_PROMPT = PRICING.input;
const PRICE_COMPLETION = PRICING.output;

function parseArgs(argv) {
  const args = {
    batch: "student-v3-adhoc",
    ids: [],
    categories: [],
    verbose: false,
    holdout: false,
    // Re-grade a stored batch against the current case specs, without
    // re-running Edward (free, and how a spec correction is applied to an
    // already-recorded baseline).
    regrade: null,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const flag = argv[i];
    if (flag === "--batch") args.batch = argv[++i];
    else if (flag === "--id" || flag === "--ids") args.ids.push(...argv[++i].split(","));
    else if (flag === "--category" || flag === "--categories")
      args.categories.push(...argv[++i].split(","));
    else if (flag === "--holdout") args.holdout = true;
    else if (flag === "--regrade") args.regrade = argv[++i];
    else if (flag === "-v" || flag === "--verbose") args.verbose = true;
    else throw new Error(`Unknown flag: ${flag}`);
  }
  return args;
}

// ---------------------------------------------------------------------------
// Fact templates: expectations reference the persona's derived ground truth
// ---------------------------------------------------------------------------

function escapeRegex(text) {
  return String(text).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function lookup(facts, path) {
  let value = facts;
  for (const part of path.split(".")) {
    if (value == null) return undefined;
    value = Array.isArray(value) ? value[Number(part)] : value[part];
  }
  return value;
}

function numberPattern(value) {
  const digits = String(Math.trunc(Number(value)));
  if (digits.length <= 3) return `\\b${digits}\\b`;
  let out = "";
  for (let i = 0; i < digits.length; i += 1) {
    out += digits[i];
    const remaining = digits.length - 1 - i;
    if (remaining > 0 && remaining % 3 === 0) out += ",?";
  }
  return `\\b${out}\\b`;
}

/**
 * `{{f:path}}`    a scalar fact, regex-escaped
 * `{{n:path}}`    a number, thousands-separator tolerant
 * `{{any:path}}`  alternation over a list — "at least one of these"
 * `{{all:path}}`  every list entry must appear (expands to a lookahead chain)
 */
function resolveTemplate(pattern, facts) {
  return pattern
    .replace(/\{\{n:([^}]+)\}\}/g, (_, path) => {
      const value = lookup(facts, path.trim());
      if (value === undefined || value === null || Number.isNaN(Number(value))) {
        throw new Error(`Ground truth missing number at ${path}`);
      }
      return numberPattern(value);
    })
    .replace(/\{\{any:([^}]+)\}\}/g, (_, path) => {
      const value = lookup(facts, path.trim());
      if (!Array.isArray(value) || value.length === 0) {
        throw new Error(`Ground truth missing non-empty list at ${path}`);
      }
      return `(?:${value.map((entry) => escapeRegex(String(entry))).join("|")})`;
    })
    .replace(/\{\{all:([^}]+)\}\}/g, (_, path) => {
      const value = lookup(facts, path.trim());
      if (!Array.isArray(value) || value.length === 0) {
        throw new Error(`Ground truth missing non-empty list at ${path}`);
      }
      return value.map((entry) => `(?=[\\s\\S]*${escapeRegex(String(entry))})`).join("");
    })
    .replace(/\{\{f:([^}]+)\}\}/g, (_, path) => {
      const value = lookup(facts, path.trim());
      if (value === undefined || value === null) {
        throw new Error(`Ground truth missing value at ${path}`);
      }
      return escapeRegex(String(value));
    });
}

// ---------------------------------------------------------------------------
// Grading
// ---------------------------------------------------------------------------

function visibleText(turn) {
  const blocks = turn.response?.blocks ?? [];
  const extras = blocks.map((block) => block.fallbackText ?? "").filter(Boolean);
  return foldTypography([turn.answer ?? "", ...extras].join("\n"));
}

function executedTools(turn) {
  return (turn.response?.trace?.toolCalls ?? [])
    .filter((call) => call.status === "available")
    .map((call) => call.tool);
}

function matches(pattern, corpus, facts) {
  return new RegExp(resolveTemplate(pattern, facts), "i").test(corpus);
}

function gradeTurn(turn, facts) {
  const expect = turn.expect ?? {};
  const corpus = visibleText(turn);
  const failures = [];
  const softMisses = [];

  if (turn.error || turn.httpStatus !== 200) {
    return {
      grade: "FAIL",
      failures: [{ kind: "transport", detail: turn.error ?? `http ${turn.httpStatus}` }],
      softMisses: [],
    };
  }

  if (expect.requestTypes) {
    const actual = turn.response?.requestType ?? null;
    const all = turn.response?.requestTypes ?? [];
    if (!expect.requestTypes.includes(actual) && !all.some((t) => expect.requestTypes.includes(t))) {
      softMisses.push({ kind: "request_type", detail: `${actual} ∉ ${expect.requestTypes}` });
    }
  }

  const tools = new Set(executedTools(turn));
  for (const tool of expect.requiredTools ?? []) {
    if (!tools.has(tool)) failures.push({ kind: "tool_not_called", detail: tool });
  }
  for (const group of expect.anyOfTools ?? []) {
    if (!group.some((tool) => tools.has(tool))) {
      failures.push({ kind: "tool_group_not_called", detail: group.join("|") });
    }
  }
  for (const tool of expect.forbiddenTools ?? []) {
    if (tools.has(tool)) failures.push({ kind: "forbidden_tool_called", detail: tool });
  }
  if (typeof expect.maxTools === "number" && tools.size > expect.maxTools) {
    softMisses.push({ kind: "too_many_tools", detail: `${tools.size} > ${expect.maxTools}` });
  }

  for (const fact of expect.facts ?? []) {
    if (!matches(fact.pattern, corpus, facts)) {
      const record = { kind: "fact_missing", detail: fact.desc, pattern: fact.pattern };
      (fact.critical ? failures : softMisses).push(record);
    }
  }
  for (const group of expect.factGroups ?? []) {
    if (!group.some((fact) => matches(fact.pattern, corpus, facts))) {
      failures.push({
        kind: "fact_missing",
        detail: group.map((fact) => fact.desc).join(" | "),
      });
    }
  }
  for (const fact of expect.forbidden ?? []) {
    if (matches(fact.pattern, corpus, facts)) {
      failures.push({ kind: "forbidden_claim", detail: fact.desc, pattern: fact.pattern });
    }
  }

  return {
    grade: failures.length > 0 ? "FAIL" : softMisses.length > 0 ? "PARTIAL" : "PASS",
    failures,
    softMisses,
  };
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

const args = parseArgs(process.argv.slice(2));
const suite = args.holdout ? HOLDOUT_CASES : CASES;
let selected = args.ids.length ? suite.filter((item) => args.ids.includes(item.id)) : suite;
if (args.categories.length) {
  selected = selected.filter((item) => args.categories.includes(item.category));
}
if (selected.length === 0) {
  console.error("No matching cases.");
  process.exit(2);
}

const normalized = selected.map((item) => normalizeCase({ ...item, checks: [] }));

let transcripts;
let snapshots;
if (args.regrade) {
  const stored = JSON.parse(
    readFileSync(join(REPO_ROOT, "artifacts", "runs", args.regrade, "transcript.json"), "utf8"),
  );
  const wanted = new Set(normalized.map((item) => item.id));
  transcripts = stored
    .filter((record) => wanted.has(record.id))
    .map((record) => ({
      ...record,
      turns: record.turns.map((turn) => ({
        question: turn.question,
        httpStatus: 200,
        error: null,
        answer: turn.message,
        latencyMs: turn.latencyMs,
        response: {
          requestType: turn.requestType,
          requestTypes: turn.requestTypes ?? (turn.requestType ? [turn.requestType] : []),
          blocks: turn.blocks ?? [],
          provider: turn.provider ?? null,
          graphExecution: { toolSelectionSource: turn.toolSelectionSource ?? null },
          trace: { toolCalls: (turn.tools ?? []).map((tool) => ({ tool, status: "available" })) },
        },
      })),
    }));
  snapshots = JSON.parse(readFileSync(SNAPSHOT_CACHE, "utf8"));
  console.log(`student-v3 regrade: ${transcripts.length} stored case(s) from ${args.regrade}`);
} else {
  console.log(`student-v3 eval: ${normalized.length} case(s)`);
  ({ transcripts, snapshots } = await runCases({
    cases: normalized,
    ledger: null,
    onProgress: (record, done, total) => {
      process.stdout.write(`\r  ${done}/${total} turns`);
    },
  }));
  process.stdout.write("\n");
  // Merge rather than replace: a category- or holdout-scoped run only boots
  // the personas it needs, and overwriting the cache with that subset breaks
  // a later `--regrade` (or export) of a run that used the others.
  mkdirSync(dirname(SNAPSHOT_CACHE), { recursive: true });
  const cached = existsSync(SNAPSHOT_CACHE)
    ? JSON.parse(readFileSync(SNAPSHOT_CACHE, "utf8"))
    : {};
  writeFileSync(SNAPSHOT_CACHE, JSON.stringify({ ...cached, ...snapshots }));
}

const factsByPersona = Object.fromEntries(
  Object.entries(snapshots).map(([persona, snapshot]) => [persona, deriveFacts(snapshot)]),
);

let promptTokens = 0;
let completionTokens = 0;
let modelCalls = 0;
const records = [];
const byId = new Map(selected.map((item) => [item.id, item]));

for (const transcript of transcripts) {
  const facts = factsByPersona[transcript.persona] ?? {};
  const source = byId.get(transcript.id);
  let caseGrade = "PASS";
  const turnRecords = [];
  transcript.turns.forEach((turn, index) => {
    for (const call of turn.response?.trace?.modelCalls ?? []) {
      modelCalls += 1;
      promptTokens += call.usage?.promptTokens ?? 0;
      completionTokens += call.usage?.completionTokens ?? 0;
    }
    const expect = source?.turns?.[index]?.expect ?? {};
    const graded = gradeTurn({ ...turn, expect }, facts);
    if (graded.grade === "FAIL") caseGrade = "FAIL";
    else if (graded.grade === "PARTIAL" && caseGrade === "PASS") caseGrade = "PARTIAL";
    turnRecords.push({
      question: turn.question,
      message: turn.answer,
      requestType: turn.response?.requestType ?? null,
      requestTypes: turn.response?.requestTypes ?? [],
      toolSelectionSource: turn.response?.graphExecution?.toolSelectionSource ?? null,
      tools: executedTools(turn),
      provider: turn.response?.provider ?? null,
      latencyMs: turn.latencyMs,
      grade: graded.grade,
      failures: graded.failures,
      softMisses: graded.softMisses,
      blocks: turn.response?.blocks ?? [],
    });
  });
  records.push({
    id: transcript.id,
    category: transcript.category,
    persona: transcript.persona,
    grade: caseGrade,
    turns: turnRecords,
  });
}

records.sort((a, b) => a.id.localeCompare(b.id));
for (const record of records) {
  console.log(`  ${record.grade.padEnd(7)} ${record.id}`);
  if (args.verbose && record.grade !== "PASS") {
    for (const turn of record.turns) {
      if (turn.grade === "PASS") continue;
      console.log(`      Q: ${turn.question}`);
      console.log(`      intent=${turn.requestType} tools=${turn.tools.join(",")}`);
      console.log(`      A: ${String(turn.message).slice(0, 400)}`);
      for (const failure of turn.failures) console.log(`      ✗ ${failure.kind}: ${failure.detail}`);
      for (const miss of turn.softMisses) console.log(`      ~ ${miss.kind}: ${miss.detail}`);
    }
  }
}

const byCategory = {};
for (const record of records) {
  const bucket = (byCategory[record.category] ??= { PASS: 0, PARTIAL: 0, FAIL: 0 });
  bucket[record.grade] += 1;
}
const totals = { PASS: 0, PARTIAL: 0, FAIL: 0 };
console.log("\nCategory            PASS PARTIAL FAIL");
for (const [category, bucket] of Object.entries(byCategory).sort()) {
  console.log(
    `${category.padEnd(20)}${String(bucket.PASS).padStart(4)}${String(bucket.PARTIAL).padStart(8)}${String(bucket.FAIL).padStart(5)}`,
  );
  totals.PASS += bucket.PASS;
  totals.PARTIAL += bucket.PARTIAL;
  totals.FAIL += bucket.FAIL;
}
console.log(
  `${"TOTAL".padEnd(20)}${String(totals.PASS).padStart(4)}${String(totals.PARTIAL).padStart(8)}${String(totals.FAIL).padStart(5)}`,
);

const estimatedUsd = promptTokens * PRICE_PROMPT + completionTokens * PRICE_COMPLETION;
console.log(
  `\nmodel calls ${modelCalls}  prompt ${promptTokens}  completion ${completionTokens}  ≈ $${estimatedUsd.toFixed(4)}`,
);

const outDir = join(REPO_ROOT, "artifacts", "runs", args.batch);
mkdirSync(outDir, { recursive: true });
writeFileSync(join(outDir, "transcript.json"), JSON.stringify(records, null, 2));
writeFileSync(
  join(outDir, "summary.json"),
  JSON.stringify(
    {
      batch: args.batch,
      suite: args.holdout ? "holdout" : "development",
      generatedAt: new Date().toISOString(),
      totals,
      byCategory,
      model: { calls: modelCalls, promptTokens, completionTokens, estimatedUsd },
    },
    null,
    2,
  ),
);
console.log(`\nartifacts → ${outDir}`);
process.exit(totals.FAIL > 0 ? 1 : 0);
