#!/usr/bin/env node
/**
 * Staff Edward database-backed eval runner.
 *
 * Drives the canonical POST /v1/staff/assistant/messages endpoint of an
 * already-running host (default http://127.0.0.1:45710) that is connected
 * to the frozen snapshot database, and grades every turn deterministically
 * against `ground-truth.json` (produced by ground_truth.py from the same
 * canonical reads the product serves). No LLM judge is involved; the only
 * model spend is Staff Edward's own planner/composer calls.
 *
 *   node tools/edward-eval/staff-db/run.mjs                       # dev suite
 *   node tools/edward-eval/staff-db/run.mjs --holdout             # holdout suite
 *   node tools/edward-eval/staff-db/run.mjs --id sdb-agg-001 -v   # one case
 *   node tools/edward-eval/staff-db/run.mjs --batch staff-db-baseline
 *
 * Grades: PASS (all checks), PARTIAL (intent answered, minor facts missed),
 * FAIL (critical fact missed, forbidden claim, wrong entity, wrong tools).
 * Writes transcript.json + summary.json under artifacts/runs/<batch>/.
 */

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { CASES } from "./cases.mjs";
import { HOLDOUT_CASES } from "./holdout-cases.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(HERE, "..", "..", "..");
const BASE_URL = process.env.STAFF_EVAL_BASE_URL ?? "http://127.0.0.1:45710";
const STAFF_ACTOR_ID =
  process.env.STAFF_EVAL_ACTOR_ID ?? "30000000-0000-7000-8000-000000000901";
const WORKER_TOKEN =
  process.env.STAFF_EVAL_WORKER_TOKEN ?? "local-development-document-worker-token";
const GROUND_TRUTH_PATH =
  process.env.STAFF_EVAL_GROUND_TRUTH ??
  join(REPO_ROOT, "artifacts", "staff-db-eval", "ground-truth.json");

// gpt-4o-mini pricing (USD per token), for the spend report.
const PRICE_PROMPT = 0.15e-6;
const PRICE_COMPLETION = 0.6e-6;

function parseArgs(argv) {
  const args = { batch: "staff-db-adhoc", ids: [], verbose: false, holdout: false };
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    if (flag === "--batch") args.batch = argv[++index];
    else if (flag === "--id" || flag === "--ids") args.ids.push(...argv[++index].split(","));
    else if (flag === "--category") args.category = argv[++index];
    else if (flag === "--holdout") args.holdout = true;
    else if (flag === "-v" || flag === "--verbose") args.verbose = true;
    else throw new Error(`Unknown flag: ${flag}`);
  }
  return args;
}

// ---------------------------------------------------------------------------
// Ground-truth template resolution
// ---------------------------------------------------------------------------

function gtLookup(truth, path) {
  let value = truth;
  for (const part of path.split(".")) {
    if (value == null) return undefined;
    value = Array.isArray(value) ? value[Number(part)] : value[part];
  }
  return value;
}

function escapeRegex(text) {
  return String(text).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Number pattern tolerant of thousands separators: 1027 → 1,?027 */
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

function resolveTemplate(pattern, truth) {
  return pattern
    .replace(/\{\{num:([^}]+)\}\}/g, (_, path) => {
      const value = gtLookup(truth, path.trim());
      if (value === undefined || value === null || Number.isNaN(Number(value))) {
        throw new Error(`Ground truth missing number at ${path}`);
      }
      return numberPattern(value);
    })
    .replace(/\{\{gt:([^}]+)\}\}/g, (_, path) => {
      const value = gtLookup(truth, path.trim());
      if (value === undefined || value === null) {
        throw new Error(`Ground truth missing value at ${path}`);
      }
      return escapeRegex(String(value));
    });
}

function resolveExpectedStudent(reference, truth) {
  if (reference === null) return null; // must NOT resolve
  if (reference === undefined) return undefined; // unchecked
  if (typeof reference === "string" && reference.startsWith("gt:")) {
    const value = gtLookup(truth, reference.slice(3));
    if (!value) throw new Error(`Ground truth missing student id at ${reference}`);
    return String(value);
  }
  return String(reference);
}

// ---------------------------------------------------------------------------
// HTTP driver
// ---------------------------------------------------------------------------

function staffHeaders() {
  return {
    "content-type": "application/json",
    "x-demo-actor-type": "staff",
    "x-demo-actor-id": STAFF_ACTOR_ID,
  };
}

async function postMessage(message, conversationId) {
  const body = { message };
  if (conversationId) body.conversationId = conversationId;
  const started = Date.now();
  const response = await fetch(`${BASE_URL}/v1/staff/assistant/messages`, {
    method: "POST",
    headers: staffHeaders(),
    body: JSON.stringify(body),
  });
  const payload = await response.json();
  return { status: response.status, payload, latencyMs: Date.now() - started };
}

async function createConversation() {
  const response = await fetch(`${BASE_URL}/v1/staff/assistant/conversations`, {
    method: "POST",
    headers: staffHeaders(),
  });
  const payload = await response.json();
  return payload.conversationId ?? payload.id ?? null;
}

async function fetchTrace(requestId) {
  if (!requestId) return null;
  const response = await fetch(`${BASE_URL}/internal/assistant/traces/${requestId}`, {
    headers: { "x-vv-worker-token": WORKER_TOKEN },
  });
  if (!response.ok) return null;
  return response.json();
}

// ---------------------------------------------------------------------------
// Grading
// ---------------------------------------------------------------------------

function blockText(block) {
  const parts = [];
  if (typeof block?.fallbackText === "string") parts.push(block.fallbackText);
  if (typeof block?.text === "string") parts.push(block.text);
  return parts.join("\n");
}

function answerCorpus(payload) {
  const blocks = Array.isArray(payload.blocks) ? payload.blocks : [];
  return [payload.message ?? "", ...blocks.map(blockText)].join("\n");
}

function matchFact(fact, corpus, truth) {
  const source = resolveTemplate(fact.pattern, truth);
  return new RegExp(source, "i").test(corpus);
}

function executedTools(trace) {
  const calls = Array.isArray(trace?.toolCalls) ? trace.toolCalls : [];
  return calls.filter((call) => call.status === "available").map((call) => call.tool);
}

function toolArgumentsText(trace, tool) {
  const calls = Array.isArray(trace?.toolCalls) ? trace.toolCalls : [];
  return calls
    .filter((call) => call.tool === tool)
    .map((call) => JSON.stringify(call.arguments ?? {}))
    .join("\n");
}

function gradeTurn(turn, payload, trace, truth) {
  const expect = turn.expect ?? {};
  const corpus = answerCorpus(payload);
  const failures = [];
  const softMisses = [];

  // Intent
  if (expect.requestTypes) {
    const actual = trace?.classification?.requestType ?? null;
    if (!expect.requestTypes.includes(actual)) {
      softMisses.push({ kind: "request_type", detail: `${actual} ∉ ${expect.requestTypes}` });
    }
  }

  // Tools
  const tools = new Set(executedTools(trace));
  for (const tool of expect.requiredTools ?? []) {
    if (!tools.has(tool)) failures.push({ kind: "tool_not_called", detail: tool });
  }
  for (const tool of expect.forbiddenTools ?? []) {
    if (tools.has(tool)) failures.push({ kind: "forbidden_tool_called", detail: tool });
  }
  for (const [tool, pattern] of Object.entries(expect.toolArgPatterns ?? {})) {
    const text = toolArgumentsText(trace, tool);
    if (!new RegExp(resolveTemplate(pattern, truth), "i").test(text)) {
      failures.push({ kind: "tool_arguments", detail: `${tool} !~ ${pattern}` });
    }
  }

  // Entity resolution
  if ("resolvedStudentId" in expect) {
    const expected = resolveExpectedStudent(expect.resolvedStudentId, truth);
    const actual = payload.resolvedStudent?.id ?? null;
    if (expected === null && actual !== null) {
      failures.push({ kind: "arbitrary_resolution", detail: `resolved ${actual}` });
    } else if (typeof expected === "string" && actual !== expected) {
      failures.push({ kind: "wrong_student", detail: `${actual} ≠ ${expected}` });
    }
  }

  // Facts
  for (const fact of expect.facts ?? []) {
    if (!matchFact(fact, corpus, truth)) {
      const record = { kind: "fact_missing", detail: fact.desc, pattern: fact.pattern };
      (fact.critical ? failures : softMisses).push(record);
    }
  }
  if (expect.factGroups) {
    const satisfied = expect.factGroups.some((group) =>
      group.every((fact) => matchFact(fact, corpus, truth)),
    );
    if (!satisfied) {
      failures.push({ kind: "fact_missing", detail: "no acceptable fact group satisfied" });
    }
  }

  // Forbidden claims
  for (const fact of expect.forbidden ?? []) {
    if (matchFact(fact, corpus, truth)) {
      failures.push({ kind: "forbidden_claim", detail: fact.desc, pattern: fact.pattern });
    }
  }

  const grade = failures.length > 0 ? "FAIL" : softMisses.length > 0 ? "PARTIAL" : "PASS";
  return { grade, failures, softMisses };
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

const args = parseArgs(process.argv.slice(2));
const suite = args.holdout ? HOLDOUT_CASES : CASES;
let cases = args.ids.length ? suite.filter((item) => args.ids.includes(item.id)) : suite;
if (args.category) cases = cases.filter((item) => item.category === args.category);
if (cases.length === 0) {
  console.error("No matching cases.");
  process.exit(2);
}

const truth = JSON.parse(readFileSync(GROUND_TRUTH_PATH, "utf8"));
const health = await fetch(`${BASE_URL}/health`).then(
  (response) => response.ok,
  () => false,
);
if (!health) {
  console.error(`No healthy Staff Edward host at ${BASE_URL}. Start it against the snapshot DB.`);
  process.exit(2);
}

console.log(`staff-db eval: ${cases.length} case(s) against ${BASE_URL}`);
const records = [];
let promptTokens = 0;
let completionTokens = 0;
let modelCalls = 0;

for (const testCase of cases) {
  const conversationId = testCase.conversation ? await createConversation() : null;
  const turnRecords = [];
  let caseGrade = "PASS";
  for (const turn of testCase.turns) {
    const { status, payload, latencyMs } = await postMessage(turn.question, conversationId);
    const trace = await fetchTrace(payload.requestId);
    for (const call of trace?.modelCalls ?? []) {
      modelCalls += 1;
      promptTokens += call.usage?.promptTokens ?? 0;
      completionTokens += call.usage?.completionTokens ?? 0;
    }
    const graded =
      status === 200
        ? gradeTurn(turn, payload, trace, truth)
        : { grade: "FAIL", failures: [{ kind: "http", detail: String(status) }], softMisses: [] };
    if (graded.grade === "FAIL") caseGrade = "FAIL";
    else if (graded.grade === "PARTIAL" && caseGrade === "PASS") caseGrade = "PARTIAL";
    turnRecords.push({
      question: turn.question,
      message: payload.message,
      resolvedStudent: payload.resolvedStudent ?? null,
      requestType: trace?.classification?.requestType ?? null,
      toolSelectionSource: trace?.toolSelectionSource ?? null,
      tools: executedTools(trace),
      provider: payload.provider,
      latencyMs,
      grade: graded.grade,
      failures: graded.failures,
      softMisses: graded.softMisses,
      blocks: payload.blocks,
    });
  }
  records.push({
    id: testCase.id,
    category: testCase.category,
    critical: Boolean(testCase.critical),
    grade: caseGrade,
    turns: turnRecords,
  });
  console.log(`  ${caseGrade.padEnd(7)} ${testCase.id}`);
  if (args.verbose && caseGrade !== "PASS") {
    for (const turn of turnRecords) {
      for (const failure of [...turn.failures, ...turn.softMisses]) {
        console.log(`          ${failure.kind}: ${failure.detail}`);
      }
    }
  }
}

const byGrade = { PASS: 0, PARTIAL: 0, FAIL: 0 };
const byCategory = {};
for (const record of records) {
  byGrade[record.grade] += 1;
  const bucket = (byCategory[record.category] ??= { PASS: 0, PARTIAL: 0, FAIL: 0 });
  bucket[record.grade] += 1;
}
const spend = promptTokens * PRICE_PROMPT + completionTokens * PRICE_COMPLETION;
const summary = {
  batch: args.batch,
  suite: args.holdout ? "holdout" : "dev",
  generatedAt: new Date().toISOString(),
  baseUrl: BASE_URL,
  cases: records.length,
  grades: byGrade,
  byCategory,
  criticalFailures: records
    .filter((record) => record.critical && record.grade === "FAIL")
    .map((record) => record.id),
  model: { calls: modelCalls, promptTokens, completionTokens, estimatedUsd: Number(spend.toFixed(4)) },
};

const outDir = join(REPO_ROOT, "artifacts", "runs", args.batch);
mkdirSync(outDir, { recursive: true });
writeFileSync(join(outDir, "transcript.json"), JSON.stringify(records, null, 2));
writeFileSync(join(outDir, "summary.json"), JSON.stringify(summary, null, 2));

console.log(
  `\nPASS ${byGrade.PASS} / PARTIAL ${byGrade.PARTIAL} / FAIL ${byGrade.FAIL} of ${records.length}` +
    ` — model: ${modelCalls} calls, ~$${spend.toFixed(4)}. Artifacts: artifacts/runs/${args.batch}/`,
);
for (const [category, bucket] of Object.entries(byCategory)) {
  console.log(
    `  ${category.padEnd(14)} PASS ${bucket.PASS} PARTIAL ${bucket.PARTIAL} FAIL ${bucket.FAIL}`,
  );
}
