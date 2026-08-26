#!/usr/bin/env node
/**
 * Staff Edward university benchmark runner.
 *
 * Drives POST /v1/staff/assistant/messages on a running host connected to
 * the frozen snapshot of the mock-university tenant, signs in as the persona
 * each case names, fetches the per-turn trace, and grades deterministically
 * against `ground-truth.json` (see ground_truth.py). No LLM judge: the only
 * model spend is Edward's own planner/composer calls, reported per run.
 *
 *   node tools/edward-eval/university/run.mjs --batch univ-baseline
 *   node tools/edward-eval/university/run.mjs --id u-cc-001 -v
 *   node tools/edward-eval/university/run.mjs --category caseload_capacity
 *   node tools/edward-eval/university/run.mjs --regrade univ-baseline   # re-grade a stored batch
 *   node tools/edward-eval/university/run.mjs --holdout --batch univ-holdout
 *
 * Every turn keeps: the request type Edward formed, tool calls with
 * arguments/status/latency/record counts, evidence lines, model calls, the
 * answer, the expected facts, and a pass/fail reason. A `failureClass`
 * (routing / tool / query / timeout / product_data / hallucination /
 * composition) is derived per failing turn from that evidence.
 */

import { mkdirSync, readFileSync, writeFileSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { CASES } from "./cases.mjs";
import { HOLDOUT_CASES } from "./holdout-cases.mjs";
import { FAMILY_OF_REQUEST_TYPE, TOOL_FAMILIES } from "./taxonomy.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(HERE, "..", "..", "..");
const BASE_URL = process.env.STAFF_EVAL_BASE_URL ?? "http://127.0.0.1:45710";
const WORKER_TOKEN =
  process.env.STAFF_EVAL_WORKER_TOKEN ?? "local-development-document-worker-token";
const GROUND_TRUTH_PATH =
  process.env.STAFF_EVAL_GROUND_TRUTH ??
  join(REPO_ROOT, "artifacts", "university-eval", "ground-truth.json");
const DEFAULT_ACTOR = "SYN-STF-ADV-DIR";
const PRICE_PROMPT = 0.15e-6;
const PRICE_COMPLETION = 0.6e-6;

function parseArgs(argv) {
  const args = {
    batch: "univ-adhoc",
    ids: [],
    verbose: false,
    regrade: null,
    category: null,
    holdout: false,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    if (flag === "--batch") args.batch = argv[++index];
    else if (flag === "--id" || flag === "--ids") args.ids.push(...argv[++index].split(","));
    else if (flag === "--category") args.category = argv[++index];
    else if (flag === "--regrade") args.regrade = argv[++index];
    else if (flag === "--holdout") args.holdout = true;
    else if (flag === "-v" || flag === "--verbose") args.verbose = true;
    else throw new Error(`Unknown flag: ${flag}`);
  }
  return args;
}

// ---------------------------------------------------------------------------
// Ground-truth templates
// ---------------------------------------------------------------------------

function gtLookup(truth, path) {
  let value = truth;
  for (const part of path.split(".")) {
    if (value == null) return undefined;
    value = Array.isArray(value) ? value[Number(part)] : value[part];
  }
  return value;
}

const escapeRegex = (text) => String(text).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

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

const MONTHS = [
  "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
];

/** A date tolerant of ISO / "Sep 11" / "September 11, 2026" / "9/11" forms. */
function datePattern(value) {
  const iso = String(value).slice(0, 10);
  const [year, month, day] = iso.split("-").map(Number);
  if (!year || !month || !day) return escapeRegex(iso);
  const monthName = MONTHS[month - 1];
  return (
    `(?:${iso}|${monthName}[a-z]*\\.?\\s+${day}(?:st|nd|rd|th)?(?:,?\\s+${year})?` +
    `|${day}(?:st|nd|rd|th)?\\s+${monthName}[a-z]*(?:,?\\s+${year})?|\\b${month}/${day}(?:/${year})?\\b)`
  );
}

/** ±2 % (min ±2) alternation for counts that move with now(): overdue, due today, ages. */
function tolerantNumberPattern(value) {
  const number = Math.trunc(Number(value));
  const slack = Math.max(2, Math.round(number * 0.02));
  const options = [];
  for (let candidate = number - slack; candidate <= number + slack; candidate += 1) {
    if (candidate >= 0) options.push(numberPattern(candidate));
  }
  return `(?:${options.join("|")})`;
}

function resolveTemplate(pattern, truth) {
  return pattern
    .replace(/\{\{num~:([^}]+)\}\}/g, (_, path) => {
      const value = gtLookup(truth, path.trim());
      if (value === undefined || value === null || Number.isNaN(Number(value))) {
        throw new Error(`Ground truth missing number at ${path}`);
      }
      return tolerantNumberPattern(value);
    })
    .replace(/\{\{num:([^}]+)\}\}/g, (_, path) => {
      const value = gtLookup(truth, path.trim());
      if (value === undefined || value === null || Number.isNaN(Number(value))) {
        throw new Error(`Ground truth missing number at ${path}`);
      }
      return numberPattern(value);
    })
    .replace(/\{\{date:([^}]+)\}\}/g, (_, path) => {
      const value = gtLookup(truth, path.trim());
      if (!value) throw new Error(`Ground truth missing date at ${path}`);
      return datePattern(value);
    })
    .replace(/\{\{gt:([^}]+)\}\}/g, (_, path) => {
      const value = gtLookup(truth, path.trim());
      if (value === undefined || value === null) {
        throw new Error(`Ground truth missing value at ${path}`);
      }
      return escapeRegex(String(value));
    });
}

/** Templates inside a *question* are substituted with the raw value. */
function resolveQuestion(question, truth) {
  return question.replace(/\{\{gt:([^}]+)\}\}/g, (_, path) => {
    const value = gtLookup(truth, path.trim());
    if (value === undefined || value === null) throw new Error(`Ground truth missing value at ${path}`);
    return String(value);
  });
}

function resolveExpectedStudent(reference, truth) {
  if (reference === null) return null;
  if (reference === undefined) return undefined;
  if (typeof reference === "string" && reference.startsWith("gt:")) {
    const value = gtLookup(truth, reference.slice(3));
    if (!value) throw new Error(`Ground truth missing student id at ${reference}`);
    return String(value);
  }
  return String(reference);
}

function actorId(ref, truth) {
  const entry = gtLookup(truth, `staff.byRef.${ref}`);
  if (!entry?.id) throw new Error(`Unknown actor ${ref}`);
  return entry.id;
}

// ---------------------------------------------------------------------------
// HTTP driver
// ---------------------------------------------------------------------------

function staffHeaders(actor) {
  return {
    "content-type": "application/json",
    "x-demo-actor-type": "staff",
    "x-demo-actor-id": actor,
  };
}

async function postMessage(message, actor, conversationId) {
  const body = { message };
  if (conversationId) body.conversationId = conversationId;
  const started = Date.now();
  const response = await fetch(`${BASE_URL}/v1/staff/assistant/messages`, {
    method: "POST",
    headers: staffHeaders(actor),
    body: JSON.stringify(body),
  });
  let payload;
  try {
    payload = await response.json();
  } catch {
    payload = { message: "" };
  }
  return { status: response.status, payload, latencyMs: Date.now() - started };
}

async function createConversation(actor) {
  const response = await fetch(`${BASE_URL}/v1/staff/assistant/conversations`, {
    method: "POST",
    headers: staffHeaders(actor),
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
  if (Array.isArray(block?.items)) {
    for (const item of block.items) if (typeof item?.text === "string") parts.push(item.text);
  }
  if (Array.isArray(block?.rows)) {
    for (const row of block.rows) parts.push(Object.values(row ?? {}).join(" "));
  }
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

function toolCalls(trace) {
  return Array.isArray(trace?.toolCalls) ? trace.toolCalls : [];
}

function executedTools(trace) {
  return toolCalls(trace)
    .filter((call) => call.status === "available")
    .map((call) => call.tool);
}

function familyOfTools(tools) {
  const families = new Set();
  for (const tool of tools) {
    for (const [family, members] of Object.entries(TOOL_FAMILIES)) {
      if (members.includes(tool)) families.add(family);
    }
  }
  return families;
}

/**
 * Where a failing turn went wrong, from the trace rather than the prose:
 *   timeout        a tool read timed out / errored
 *   routing        the request type belongs to a different family than expected
 *   tool           routing family right, but the expected tool family never ran
 *   query          the tool ran but its evidence does not carry the expected fact
 *   composition    the evidence carries the fact and the answer dropped it
 *   hallucination  a forbidden claim appeared
 *   product_data   the case is marked as needing data the product may not hold
 *   entity         wrong / arbitrary student resolution
 */
function classifyFailure(turn, failures, trace, evidenceText, truth) {
  const expect = turn.expect ?? {};
  const kinds = new Set(failures.map((f) => f.kind));
  const calls = toolCalls(trace);
  if (calls.some((call) => call.status === "timeout")) return "timeout";
  if (kinds.has("forbidden_claim")) return "hallucination";
  if (kinds.has("arbitrary_resolution") || kinds.has("wrong_student")) return "entity";
  const requestType = trace?.classification?.requestType ?? null;
  const family = FAMILY_OF_REQUEST_TYPE[requestType] ?? null;
  if (expect.family && family && !expect.family.includes(family)) return "routing";
  if (expect.family && !family) return "routing";
  if (calls.some((call) => call.status === "unavailable")) return "tool";
  if (expect.toolFamily) {
    const ran = familyOfTools(executedTools(trace));
    if (!expect.toolFamily.some((f) => ran.has(f))) return "tool";
  }
  if (turn.productGap) return "product_data";
  const missing = failures.filter((f) => f.kind === "fact_missing" && f.pattern);
  if (missing.length > 0) {
    const inEvidence = missing.every((f) =>
      new RegExp(resolveTemplate(f.pattern, truth), "i").test(evidenceText),
    );
    return inEvidence ? "composition" : "query";
  }
  return "query";
}

function gradeTurn(turn, payload, trace, truth) {
  const expect = turn.expect ?? {};
  const corpus = answerCorpus(payload);
  const failures = [];
  const softMisses = [];

  if (expect.requestTypes) {
    const actual = trace?.classification?.requestType ?? null;
    if (!expect.requestTypes.includes(actual)) {
      softMisses.push({ kind: "request_type", detail: `${actual} ∉ ${expect.requestTypes}` });
    }
  }
  const tools = new Set(executedTools(trace));
  for (const tool of expect.requiredTools ?? []) {
    if (!tools.has(tool)) failures.push({ kind: "tool_not_called", detail: tool });
  }
  for (const tool of expect.forbiddenTools ?? []) {
    if (tools.has(tool)) failures.push({ kind: "forbidden_tool_called", detail: tool });
  }
  if ("resolvedStudentId" in expect) {
    const expected = resolveExpectedStudent(expect.resolvedStudentId, truth);
    const actual = payload.resolvedStudent?.id ?? null;
    if (expected === null && actual !== null) {
      failures.push({ kind: "arbitrary_resolution", detail: `resolved ${actual}` });
    } else if (typeof expected === "string" && actual !== expected) {
      failures.push({ kind: "wrong_student", detail: `${actual} ≠ ${expected}` });
    }
  }
  for (const fact of expect.facts ?? []) {
    if (!matchFact(fact, corpus, truth)) {
      const record = { kind: "fact_missing", detail: fact.desc, pattern: fact.pattern };
      (fact.critical === false ? softMisses : failures).push(record);
    }
  }
  if (expect.factGroups) {
    const satisfied = expect.factGroups.some((group) =>
      group.every((fact) => matchFact(fact, corpus, truth)),
    );
    if (!satisfied) {
      failures.push({
        kind: "fact_missing",
        detail: "no acceptable fact group satisfied",
        pattern: expect.factGroups[0]?.[0]?.pattern,
      });
    }
  }
  for (const fact of expect.forbidden ?? []) {
    if (matchFact(fact, corpus, truth)) {
      failures.push({ kind: "forbidden_claim", detail: fact.desc, pattern: fact.pattern });
    }
  }
  if (
    /couldn'?t (?:read|check|run|find that work item)|not available right now|left it out/i.test(
      payload.message ?? "",
    ) &&
    !expect.allowUnavailable
  ) {
    failures.push({ kind: "read_failed", detail: "answer reports a failed read" });
  }
  const grade = failures.length > 0 ? "FAIL" : softMisses.length > 0 ? "PARTIAL" : "PASS";
  return { grade, failures, softMisses };
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

const args = parseArgs(process.argv.slice(2));
const SUITE = args.holdout ? HOLDOUT_CASES : CASES;
let cases = args.ids.length ? SUITE.filter((item) => args.ids.includes(item.id)) : SUITE;
if (args.category) cases = cases.filter((item) => item.category === args.category);
if (cases.length === 0) {
  console.error("No matching cases.");
  process.exit(2);
}
const truth = JSON.parse(readFileSync(GROUND_TRUTH_PATH, "utf8"));
const health = args.regrade
  ? true
  : await fetch(`${BASE_URL}/health`).then((r) => r.ok, () => false);
if (!health) {
  console.error(`No healthy Staff Edward host at ${BASE_URL}.`);
  process.exit(2);
}

const stored = args.regrade
  ? new Map(
      JSON.parse(
        readFileSync(join(REPO_ROOT, "artifacts", "runs", args.regrade, "transcript.json"), "utf8"),
      ).map((record) => [record.id, record]),
    )
  : null;

console.log(`university eval: ${cases.length} case(s) against ${BASE_URL}`);
const records = [];
let promptTokens = 0;
let completionTokens = 0;
let modelCalls = 0;

async function observeTurn(testCase, turn, index, actor, conversationId) {
  if (stored) {
    const record = stored.get(testCase.id);
    const replay = record?.turns?.[index];
    if (!replay) throw new Error(`No stored turn ${index} for ${testCase.id}`);
    return {
      status: 200,
      latencyMs: replay.latencyMs ?? null,
      payload: {
        message: replay.message,
        blocks: replay.blocks ?? [],
        resolvedStudent: replay.resolvedStudent ?? null,
        provider: replay.provider,
      },
      trace: {
        classification: { requestType: replay.requestType },
        toolSelectionSource: replay.toolSelectionSource ?? null,
        toolCalls: replay.toolCalls ?? [],
        evidence: replay.evidence ?? [],
        modelCalls: [],
        entities: replay.entities ?? null,
      },
    };
  }
  const { status, payload, latencyMs } = await postMessage(
    resolveQuestion(turn.question, truth),
    actor,
    conversationId,
  );
  const trace = await fetchTrace(payload.requestId);
  return { status, payload, latencyMs, trace };
}

for (const testCase of cases) {
  const actorRef = testCase.actor ?? DEFAULT_ACTOR;
  const actor = actorId(actorRef, truth);
  const conversationId = testCase.conversation && !stored ? await createConversation(actor) : null;
  const turnRecords = [];
  let caseGrade = "PASS";
  let turnIndex = 0;
  for (const turn of testCase.turns) {
    const { status, payload, latencyMs, trace } = await observeTurn(
      testCase,
      turn,
      turnIndex,
      actor,
      conversationId,
    );
    turnIndex += 1;
    for (const call of trace?.modelCalls ?? []) {
      modelCalls += 1;
      promptTokens += call.usage?.promptTokens ?? 0;
      completionTokens += call.usage?.completionTokens ?? 0;
    }
    const evidenceText = (trace?.evidence ?? []).join("\n");
    const graded =
      status === 200
        ? gradeTurn(turn, payload, trace, truth)
        : { grade: "FAIL", failures: [{ kind: "http", detail: String(status) }], softMisses: [] };
    const failureClass =
      graded.grade === "FAIL"
        ? classifyFailure(turn, graded.failures, trace, evidenceText, truth)
        : null;
    if (graded.grade === "FAIL") caseGrade = "FAIL";
    else if (graded.grade === "PARTIAL" && caseGrade === "PASS") caseGrade = "PARTIAL";
    const calls = toolCalls(trace);
    turnRecords.push({
      question: resolveQuestion(turn.question, truth),
      expected: turn.expect ?? {},
      message: payload.message,
      resolvedStudent: payload.resolvedStudent ?? null,
      requestType: trace?.classification?.requestType ?? null,
      classification: trace?.classification ?? null,
      entities: trace?.entities ?? null,
      toolSelectionSource: trace?.toolSelectionSource ?? null,
      tools: executedTools(trace),
      toolCalls: calls.map((call) => ({
        tool: call.tool,
        status: call.status,
        durationMs: call.durationMs ?? null,
        recordCount: call.recordCount ?? null,
        arguments: call.arguments ?? null,
        reason: call.reason ?? null,
        resultPreview: JSON.stringify(call.result ?? null).slice(0, 600),
      })),
      evidence: (trace?.evidence ?? []).slice(0, 40),
      modelCalls: (trace?.modelCalls ?? []).map((call) => ({
        operation: call.operation,
        outcome: call.outcome,
        durationMs: call.durationMs,
        usage: call.usage ?? null,
      })),
      failureCodes: trace?.failureCodes ?? [],
      provider: payload.provider,
      latencyMs,
      timedOut: calls.some((call) => call.status === "timeout"),
      grade: graded.grade,
      failures: graded.failures,
      softMisses: graded.softMisses,
      failureClass,
      blocks: payload.blocks,
    });
    if (args.verbose) {
      console.log(`\n[${testCase.id}] (${actorRef}) Q: ${turn.question}`);
      console.log(`  A: ${String(payload.message ?? "").slice(0, 400)}`);
      console.log(
        `  ${graded.grade} rt=${trace?.classification?.requestType} tools=${executedTools(trace).join(",")} ${latencyMs}ms` +
          (failureClass ? ` class=${failureClass}` : ""),
      );
      for (const failure of graded.failures) console.log(`    ✗ ${failure.kind}: ${failure.detail}`);
    }
  }
  records.push({
    id: testCase.id,
    category: testCase.category,
    actor: actorRef,
    grade: caseGrade,
    failureClass: turnRecords.find((t) => t.failureClass)?.failureClass ?? null,
    turns: turnRecords,
  });
  if (!args.verbose) console.log(`  ${caseGrade.padEnd(7)} ${testCase.id}`);
}

// ---------------------------------------------------------------------------
// Summary
// ---------------------------------------------------------------------------

const byCategory = {};
for (const record of records) {
  const bucket = (byCategory[record.category] ??= { cases: 0, PASS: 0, PARTIAL: 0, FAIL: 0 });
  bucket.cases += 1;
  bucket[record.grade] += 1;
}
const allTurns = records.flatMap((r) => r.turns);
const latencies = allTurns.map((t) => t.latencyMs).filter((v) => typeof v === "number").sort((a, b) => a - b);
const pct = (p) => (latencies.length ? latencies[Math.min(latencies.length - 1, Math.floor(p * latencies.length))] : null);
const failureClasses = {};
for (const turn of allTurns) if (turn.failureClass) failureClasses[turn.failureClass] = (failureClasses[turn.failureClass] ?? 0) + 1;
const summary = {
  batch: args.batch,
  baseUrl: BASE_URL,
  generatedAt: new Date().toISOString(),
  groundTruthGeneratedAt: truth.generatedAt,
  cases: records.length,
  turns: allTurns.length,
  PASS: records.filter((r) => r.grade === "PASS").length,
  PARTIAL: records.filter((r) => r.grade === "PARTIAL").length,
  FAIL: records.filter((r) => r.grade === "FAIL").length,
  passRate: records.length ? records.filter((r) => r.grade === "PASS").length / records.length : 0,
  turnPassRate: allTurns.length ? allTurns.filter((t) => t.grade === "PASS").length / allTurns.length : 0,
  byCategory,
  failureClasses,
  latency: {
    p50: pct(0.5),
    p90: pct(0.9),
    max: latencies.at(-1) ?? null,
    mean: latencies.length ? Math.round(latencies.reduce((a, b) => a + b, 0) / latencies.length) : null,
    timeouts: allTurns.filter((t) => t.timedOut).length,
    over4s: latencies.filter((v) => v > 4000).length,
  },
  spend: {
    modelCalls,
    promptTokens,
    completionTokens,
    estimatedUsd: Number((promptTokens * PRICE_PROMPT + completionTokens * PRICE_COMPLETION).toFixed(4)),
  },
};
const outDir = join(REPO_ROOT, "artifacts", "runs", args.batch);
mkdirSync(outDir, { recursive: true });
writeFileSync(join(outDir, "transcript.json"), JSON.stringify(records, null, 2));
writeFileSync(join(outDir, "summary.json"), JSON.stringify(summary, null, 2));

const lines = [
  `# ${args.batch}`,
  "",
  `${summary.PASS} PASS / ${summary.PARTIAL} PARTIAL / ${summary.FAIL} FAIL of ${summary.cases} cases (${(summary.passRate * 100).toFixed(1)}% pass; ${(summary.turnPassRate * 100).toFixed(1)}% of ${summary.turns} turns)`,
  `latency p50 ${summary.latency.p50} ms · p90 ${summary.latency.p90} ms · max ${summary.latency.max} ms · timeouts ${summary.latency.timeouts} · turns >4 s ${summary.latency.over4s}`,
  `spend ${summary.spend.modelCalls} model calls · $${summary.spend.estimatedUsd}`,
  "",
  "| category | cases | pass | partial | fail |",
  "| --- | ---: | ---: | ---: | ---: |",
  ...Object.entries(byCategory).map(([k, v]) => `| ${k} | ${v.cases} | ${v.PASS} | ${v.PARTIAL} | ${v.FAIL} |`),
  "",
  "failure classes: " + (Object.entries(failureClasses).map(([k, v]) => `${k}=${v}`).join(", ") || "none"),
  "",
  "## Failures",
  "",
];
for (const record of records) {
  if (record.grade === "PASS") continue;
  for (const turn of record.turns) {
    if (turn.grade === "PASS") continue;
    lines.push(`- **${record.id}** (${record.category}, ${record.actor}) [${turn.failureClass ?? turn.grade}] rt=${turn.requestType} tools=${turn.tools.join(",")}`);
    lines.push(`  - Q: ${turn.question}`);
    lines.push(`  - A: ${String(turn.message ?? "").replace(/\n/g, " ").slice(0, 300)}`);
    for (const failure of [...turn.failures, ...turn.softMisses]) lines.push(`  - ✗ ${failure.kind}: ${failure.detail}`);
  }
}
writeFileSync(join(outDir, "report.md"), lines.join("\n"));
console.log("");
console.log(lines.slice(2, 5).join("\n"));
console.log("failure classes: " + (Object.entries(failureClasses).map(([k, v]) => `${k}=${v}`).join(", ") || "none"));
console.log(`artifacts: ${outDir}`);
