#!/usr/bin/env node
/**
 * Edward READ generalization suite runner.
 *
 * Drives both canonical endpoints of an already-running host that is bound
 * to the frozen aster-demo snapshot — POST /v1/student/assistant/messages as
 * a student persona and POST /v1/staff/assistant/messages as a staff persona
 * — fetches every turn's trace, and grades deterministically against
 * `artifacts/read-gen-eval/ground-truth.json` (see ground_truth.py: plain SQL
 * over the same database). No LLM judge: the only model spend is Edward's
 * own planner/composer calls, metered from the traces.
 *
 *   node tools/edward-eval/read-gen/run.mjs --batch rg-4o-mini-default
 *   node tools/edward-eval/read-gen/run.mjs --ids rg-adv-001,rg-act-003 -v
 *   node tools/edward-eval/read-gen/run.mjs --category deadlines_blockers --actor-kind staff
 *   node tools/edward-eval/read-gen/run.mjs --holdout --batch rg-holdout-luna
 *   node tools/edward-eval/read-gen/run.mjs --deterministic --batch rg-det
 *   node tools/edward-eval/read-gen/run.mjs --planner model --batch rg-model-planner
 *   node tools/edward-eval/read-gen/run.mjs --regrade rg-4o-mini-default   # no host needed
 *   node tools/edward-eval/read-gen/run.mjs --lint                          # templates vs ground truth
 *
 * Lab controls: `--deterministic` sends `x-edward-mode: deterministic`;
 * `--planner deterministic|hybrid|model` sends `x-edward-read-planner`.
 * Neither header is sent unless asked for, so a plain run takes exactly the
 * production path.
 *
 * Grades: PASS (every check), PARTIAL (only soft misses — request type,
 * non-critical fact), FAIL (critical fact missing, forbidden claim, wrong or
 * arbitrary entity, required read never executed, action falsely claimed),
 * SKIP (a time-relative ground-truth value no longer exists today).
 * A `failureClass` (timeout / routing / tool / query / composition /
 * hallucination / entity / product_data / action) is derived per failing
 * turn from the trace, not the prose.
 */

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { randomUUID } from "node:crypto";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { CASES } from "./cases.mjs";
import { HOLDOUT_CASES } from "./holdout-cases.mjs";
import { actorRef } from "./personas.mjs";
import { pricingForModel, priceUsd, MODEL_PRICING } from "../src/pricing.mjs";
import {
  gtLookup,
  resolveTemplate,
  resolveQuestion,
  resolveExpectedStudent,
  answerCorpus,
  gradeTurn,
  classifyFailure,
  toolCalls,
  executedTools,
  actionIntentsOf,
  evidenceTextOf,
} from "./grade.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(HERE, "..", "..", "..");
const BASE_URL = process.env.READ_GEN_BASE_URL ?? "http://127.0.0.1:45710";
const TENANT_ID = process.env.READ_GEN_TENANT_ID ?? "00000000-0000-7000-8000-000000000003";
const WORKER_TOKEN =
  process.env.READ_GEN_WORKER_TOKEN ?? "local-development-document-worker-token";
const GROUND_TRUTH_PATH =
  process.env.READ_GEN_GROUND_TRUTH ??
  join(REPO_ROOT, "artifacts", "read-gen-eval", "ground-truth.json");
const HOST_MODEL = process.env.OPENAI_MODEL || "gpt-4o-mini";
const DEFAULT_PRICING = pricingForModel(HOST_MODEL);
const PAGE_CONTEXT = { path: "/dashboard", label: "Dashboard" };

function parseArgs(argv) {
  const args = {
    batch: "read-gen-adhoc",
    ids: [],
    categories: [],
    actorKind: null,
    holdout: false,
    regrade: null,
    verbose: false,
    executionMode: null,
    planner: null,
    // Resolve every template against the ground truth without calling the
    // host: proves the bank and ground-truth.json agree before a run.
    lint: false,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    if (flag === "--batch") args.batch = argv[++index];
    else if (flag === "--id" || flag === "--ids") args.ids.push(...argv[++index].split(","));
    else if (flag === "--category" || flag === "--categories")
      args.categories.push(...argv[++index].split(","));
    else if (flag === "--actor-kind") args.actorKind = argv[++index];
    else if (flag === "--holdout") args.holdout = true;
    else if (flag === "--regrade") args.regrade = argv[++index];
    else if (flag === "--deterministic") args.executionMode = "deterministic";
    else if (flag === "--planner") args.planner = argv[++index];
    else if (flag === "--lint") args.lint = true;
    else if (flag === "-v" || flag === "--verbose") args.verbose = true;
    else throw new Error(`Unknown flag: ${flag}`);
  }
  if (args.planner && !["deterministic", "hybrid", "model"].includes(args.planner)) {
    throw new Error(`--planner must be deterministic|hybrid|model, got ${args.planner}`);
  }
  if (args.actorKind && !["student", "staff"].includes(args.actorKind)) {
    throw new Error(`--actor-kind must be student|staff, got ${args.actorKind}`);
  }
  return args;
}

// ---------------------------------------------------------------------------
// Actors
// ---------------------------------------------------------------------------

function resolveActor(testCase, truth) {
  const ref = actorRef(testCase.actorKind, testCase.actor);
  if (testCase.actorKind === "student") {
    const entry = truth.students?.[ref];
    if (!entry?.id || !entry?.personId) throw new Error(`Ground truth has no student ${ref}`);
    return { kind: "student", ref, id: entry.id, personId: entry.personId, name: entry.name };
  }
  const entry = truth.staff?.byRef?.[ref];
  if (!entry?.id) throw new Error(`Ground truth has no staff member ${ref}`);
  return { kind: "staff", ref, id: entry.id, name: entry.name };
}

function headersFor(actor, lab) {
  const base = { "content-type": "application/json", "x-demo-tenant-id": TENANT_ID };
  if (actor.kind === "student") {
    base["x-demo-student-id"] = actor.id;
    base["x-demo-actor-id"] = actor.personId;
  } else {
    base["x-demo-actor-type"] = "staff";
    base["x-demo-actor-id"] = actor.id;
  }
  if (lab.executionMode) base["x-edward-mode"] = lab.executionMode;
  if (lab.planner) base["x-edward-read-planner"] = lab.planner;
  return base;
}

async function createConversation(actor, lab) {
  const path =
    actor.kind === "student" ? "/v1/student/assistant/conversations" : "/v1/staff/assistant/conversations";
  const body = actor.kind === "student" ? { pageContext: PAGE_CONTEXT } : {};
  const response = await fetch(`${BASE_URL}${path}`, {
    method: "POST",
    headers: headersFor(actor, lab),
    body: JSON.stringify(body),
  });
  let payload = {};
  try {
    payload = await response.json();
  } catch {
    payload = {};
  }
  return payload.conversationId ?? payload.id ?? null;
}

async function postMessage(actor, message, conversationId, lab) {
  const path =
    actor.kind === "student" ? "/v1/student/assistant/messages" : "/v1/staff/assistant/messages";
  const body = { message };
  if (actor.kind === "student") body.pageContext = PAGE_CONTEXT;
  if (conversationId) {
    body.conversationId = conversationId;
    if (actor.kind === "student") body.clientMessageId = randomUUID();
  }
  const started = performance.now();
  const response = await fetch(`${BASE_URL}${path}`, {
    method: "POST",
    headers: headersFor(actor, lab),
    body: JSON.stringify(body),
  });
  let payload;
  try {
    payload = await response.json();
  } catch {
    payload = { message: "" };
  }
  return { status: response.status, payload, latencyMs: Math.round(performance.now() - started) };
}

async function fetchTrace(requestId) {
  if (!requestId) return null;
  try {
    const response = await fetch(`${BASE_URL}/internal/assistant/traces/${requestId}`, {
      headers: { "x-vv-worker-token": WORKER_TOKEN },
    });
    if (!response.ok) return null;
    return await response.json();
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

const args = parseArgs(process.argv.slice(2));
const SUITE = args.holdout ? HOLDOUT_CASES : CASES;
let cases = args.ids.length ? SUITE.filter((item) => args.ids.includes(item.id)) : SUITE;
if (args.categories.length) cases = cases.filter((item) => args.categories.includes(item.category));
if (args.actorKind) cases = cases.filter((item) => item.actorKind === args.actorKind);
if (cases.length === 0) {
  console.error("No matching cases.");
  process.exit(2);
}

const truth = JSON.parse(readFileSync(GROUND_TRUTH_PATH, "utf8"));
const lab = { executionMode: args.executionMode, planner: args.planner };

if (args.lint) {
  let problems = 0;
  const patternsOf = (expect) => [
    ...(expect.facts ?? []),
    ...(expect.factGroups ?? []).flat(),
    ...(expect.forbidden ?? []),
  ];
  for (const testCase of cases) {
    try {
      resolveActor(testCase, truth);
    } catch (error) {
      problems += 1;
      console.log(`  ${testCase.id}: ${error.message}`);
    }
    for (const turn of testCase.turns) {
      try {
        resolveQuestion(turn.question, truth);
        if ("resolvedStudentId" in (turn.expect ?? {})) resolveExpectedStudent(turn.expect.resolvedStudentId, truth);
        if (turn.expect?.resolvedStudentIn && !Array.isArray(gtLookup(truth, turn.expect.resolvedStudentIn))) {
          throw new Error(`resolvedStudentIn is not a list: ${turn.expect.resolvedStudentIn}`);
        }
      } catch (error) {
        problems += 1;
        console.log(`  ${testCase.id}: ${error.message}`);
      }
      for (const fact of patternsOf(turn.expect ?? {})) {
        try {
          new RegExp(resolveTemplate(fact.pattern, truth), "i");
        } catch (error) {
          problems += 1;
          console.log(`  ${testCase.id} [${fact.desc}]: ${error.message}`);
        }
      }
    }
  }
  const turns = cases.reduce((n, c) => n + c.turns.length, 0);
  console.log(`lint: ${cases.length} case(s), ${turns} turn(s), ${problems} problem(s)`);
  process.exit(problems > 0 ? 1 : 0);
}

const stored = args.regrade
  ? new Map(
      JSON.parse(readFileSync(join(REPO_ROOT, "artifacts", "runs", args.regrade, "transcript.json"), "utf8")).map(
        (record) => [record.id, record],
      ),
    )
  : null;

if (stored) {
  // A stored batch may be a subset (an --ids or --category run): re-grade
  // only what it recorded rather than failing the rest as missing.
  cases = cases.filter((item) => stored.has(item.id));
  if (cases.length === 0) {
    console.error(`Batch ${args.regrade} recorded none of the selected cases.`);
    process.exit(2);
  }
}

if (!stored) {
  const healthy = await fetch(`${BASE_URL}/health`).then((r) => r.ok, () => false);
  if (!healthy) {
    console.error(`No healthy Edward host at ${BASE_URL}. Start it against the snapshot DB.`);
    process.exit(2);
  }
}

console.log(
  `read-gen ${args.holdout ? "holdout" : "dev"}: ${cases.length} case(s) ` +
    (stored ? `re-graded from ${args.regrade}` : `against ${BASE_URL}`) +
    (lab.executionMode ? ` mode=${lab.executionMode}` : "") +
    (lab.planner ? ` planner=${lab.planner}` : ""),
);

async function observeTurn(testCase, turn, index, actor, conversationId) {
  if (stored) {
    const record = stored.get(testCase.id);
    const replay = record?.turns?.[index];
    if (!replay) throw new Error(`No stored turn ${index} for ${testCase.id}`);
    return {
      status: replay.httpStatus ?? 200,
      latencyMs: replay.latencyMs ?? null,
      payload: {
        message: replay.message,
        blocks: replay.blocks ?? [],
        resolvedStudent: replay.resolvedStudent ?? null,
        actionIntents: replay.actionIntents ?? [],
        provider: replay.provider,
        model: replay.model ?? null,
      },
      trace: {
        classification: replay.classification ?? { requestType: replay.requestType },
        toolSelectionSource: replay.toolSelectionSource ?? null,
        toolCalls: replay.toolCalls ?? [],
        evidence: replay.evidence ?? [],
        modelCalls: [],
        readPlanner: replay.readPlanner ?? null,
        readLoop: replay.readLoop ?? null,
        executionMode: replay.executionMode ?? null,
      },
    };
  }
  const { status, payload, latencyMs } = await postMessage(
    actor,
    resolveQuestion(turn.question, truth),
    conversationId,
    lab,
  );
  const trace = await fetchTrace(payload.requestId);
  return { status, payload, latencyMs, trace };
}

const records = [];
let promptTokens = 0;
let completionTokens = 0;
let modelCalls = 0;
let spendUsd = 0;
const modelsSeen = new Set();

for (const testCase of cases) {
  const actor = resolveActor(testCase, truth);
  const multiTurn = testCase.turns.length > 1 || testCase.conversation;
  const conversationId = multiTurn && !stored ? await createConversation(actor, lab) : null;
  const turnRecords = [];
  let caseGrade = "PASS";
  let turnIndex = 0;
  for (const turn of testCase.turns) {
    let observed;
    try {
      observed = await observeTurn(testCase, turn, turnIndex, actor, conversationId);
    } catch (error) {
      observed = {
        status: 0,
        latencyMs: null,
        payload: { message: "" },
        trace: null,
        transportError: String(error?.message ?? error),
      };
    }
    const { status, payload, latencyMs, trace } = observed;
    turnIndex += 1;
    for (const call of trace?.modelCalls ?? []) {
      modelCalls += 1;
      const prompt = call.usage?.promptTokens ?? 0;
      const completion = call.usage?.completionTokens ?? 0;
      promptTokens += prompt;
      completionTokens += completion;
      const model = call.model ?? payload?.model ?? HOST_MODEL;
      modelsSeen.add(model);
      spendUsd += MODEL_PRICING[model]
        ? priceUsd(model, prompt, completion)
        : prompt * DEFAULT_PRICING.input + completion * DEFAULT_PRICING.output;
    }
    const evidenceText = evidenceTextOf(trace);
    let graded;
    try {
      graded =
        status === 200
          ? gradeTurn(turn, payload, trace, truth, testCase.actorKind)
          : {
              grade: "FAIL",
              failures: [{ kind: "http", detail: observed.transportError ?? String(status) }],
              softMisses: [],
            };
    } catch (error) {
      if (!/Ground truth missing/.test(String(error?.message))) throw error;
      graded = {
        grade: "SKIP",
        failures: [{ kind: "ground_truth_unavailable", detail: String(error.message) }],
        softMisses: [],
      };
    }
    const failureClass =
      graded.grade === "FAIL" ? classifyFailure(turn, graded.failures, trace, evidenceText, truth) : null;
    if (graded.grade === "FAIL") caseGrade = "FAIL";
    else if (graded.grade === "PARTIAL" && caseGrade === "PASS") caseGrade = "PARTIAL";
    else if (graded.grade === "SKIP" && caseGrade === "PASS") caseGrade = "SKIP";
    const calls = toolCalls(trace);
    turnRecords.push({
      question: resolveQuestion(turn.question, truth),
      expected: turn.expect ?? {},
      httpStatus: status,
      message: payload.message ?? "",
      blocks: payload.blocks ?? [],
      resolvedStudent: payload.resolvedStudent ?? null,
      actionIntents: actionIntentsOf(payload).map((intent) => ({
        kind: intent?.kind ?? intent?.actionType ?? intent?.type ?? null,
        status: intent?.status ?? null,
      })),
      requestType: trace?.classification?.requestType ?? null,
      classification: trace?.classification ?? null,
      entities: trace?.entities ?? null,
      toolSelectionSource: trace?.toolSelectionSource ?? null,
      readPlanner: trace?.readPlanner ?? null,
      readLoop: trace?.readLoop ?? null,
      executionMode: trace?.executionMode ?? null,
      tools: executedTools(trace),
      toolCalls: calls.map((call) => ({
        tool: call.tool,
        status: call.status,
        durationMs: call.durationMs ?? null,
        recordCount: call.recordCount ?? null,
        arguments: call.arguments ?? null,
        reason: call.reason ?? null,
        resultPreview:
          typeof call.resultPreview === "string"
            ? call.resultPreview
            : JSON.stringify(call.result ?? call.data ?? null).slice(0, 800),
      })),
      evidence: (trace?.evidence ?? []).slice(0, 60),
      modelCalls: (trace?.modelCalls ?? []).map((call) => ({
        operation: call.operation ?? null,
        outcome: call.outcome ?? null,
        model: call.model ?? null,
        durationMs: call.durationMs ?? null,
        usage: call.usage ?? null,
      })),
      failureCodes: trace?.failureCodes ?? [],
      provider: payload.provider ?? null,
      model: payload.model ?? null,
      latencyMs,
      serverDurationMs: trace?.durationMs ?? null,
      timedOut: calls.some((call) => call.status === "timeout"),
      grade: graded.grade,
      failures: graded.failures,
      softMisses: graded.softMisses,
      failureClass,
    });
    if (args.verbose) {
      console.log(`\n[${testCase.id}] (${testCase.actorKind}:${actor.name}) Q: ${turn.question}`);
      console.log(`  A: ${String(payload.message ?? "").replace(/\n/g, " ").slice(0, 500)}`);
      console.log(
        `  ${graded.grade} rt=${trace?.classification?.requestType ?? "?"} planner=${trace?.readPlanner ?? "?"} tools=${executedTools(trace).join(",")} ${latencyMs ?? "?"}ms` +
          (failureClass ? ` class=${failureClass}` : ""),
      );
      for (const failure of graded.failures) console.log(`    ✗ ${failure.kind}: ${failure.detail}`);
      for (const miss of graded.softMisses) console.log(`    ~ ${miss.kind}: ${miss.detail}`);
    }
  }
  records.push({
    id: testCase.id,
    category: testCase.category,
    actorKind: testCase.actorKind,
    actor: actor.ref,
    actorName: actor.name,
    expectedBehavior: testCase.expectedBehavior ?? "",
    conversationId,
    lab,
    grade: caseGrade,
    failureClass: turnRecords.find((t) => t.failureClass)?.failureClass ?? null,
    turns: turnRecords,
  });
  if (!args.verbose) console.log(`  ${caseGrade.padEnd(7)} ${testCase.id}`);
}

// ---------------------------------------------------------------------------
// Summary
// ---------------------------------------------------------------------------

const bucket = () => ({ cases: 0, PASS: 0, PARTIAL: 0, FAIL: 0, SKIP: 0 });
const byCategory = {};
const byActorKind = {};
for (const record of records) {
  const c = (byCategory[record.category] ??= bucket());
  c.cases += 1;
  c[record.grade] += 1;
  const a = (byActorKind[record.actorKind] ??= bucket());
  a.cases += 1;
  a[record.grade] += 1;
}
const allTurns = records.flatMap((r) => r.turns);
const latencies = allTurns
  .map((t) => t.latencyMs)
  .filter((v) => typeof v === "number")
  .sort((a, b) => a - b);
const pct = (p) =>
  latencies.length ? latencies[Math.min(latencies.length - 1, Math.floor(p * latencies.length))] : null;
const failureClasses = {};
for (const turn of allTurns) if (turn.failureClass) failureClasses[turn.failureClass] = (failureClasses[turn.failureClass] ?? 0) + 1;
const countKind = (kinds) =>
  allTurns.filter((t) => t.failures.some((f) => kinds.includes(f.kind))).length;
const graded = records.filter((r) => r.grade !== "SKIP");
const summary = {
  batch: args.batch,
  suite: args.holdout ? "holdout" : "dev",
  baseUrl: BASE_URL,
  lab,
  hostModel: HOST_MODEL,
  modelsSeen: [...modelsSeen],
  generatedAt: new Date().toISOString(),
  groundTruthGeneratedAt: truth.generatedAt,
  cases: records.length,
  turns: allTurns.length,
  PASS: records.filter((r) => r.grade === "PASS").length,
  PARTIAL: records.filter((r) => r.grade === "PARTIAL").length,
  FAIL: records.filter((r) => r.grade === "FAIL").length,
  SKIP: records.filter((r) => r.grade === "SKIP").length,
  passRate: graded.length ? records.filter((r) => r.grade === "PASS").length / graded.length : 0,
  passOrPartialRate: graded.length ? records.filter((r) => r.grade !== "FAIL" && r.grade !== "SKIP").length / graded.length : 0,
  turnPassRate: allTurns.filter((t) => t.grade !== "SKIP").length
    ? allTurns.filter((t) => t.grade === "PASS").length / allTurns.filter((t) => t.grade !== "SKIP").length
    : 0,
  byCategory,
  byActorKind,
  failureClasses,
  hallucinations: countKind(["forbidden_claim"]),
  entityFailures: countKind(["arbitrary_resolution", "wrong_student", "not_resolved", "no_clarification"]),
  toolSelectionFailures: countKind(["tool_not_called", "tool_group_not_called", "forbidden_tool_called", "read_failed"]),
  actionFailures: countKind(["action_proposed", "action_not_proposed"]),
  latency: {
    p50: pct(0.5),
    p95: pct(0.95),
    max: latencies.at(-1) ?? null,
    mean: latencies.length ? Math.round(latencies.reduce((a, b) => a + b, 0) / latencies.length) : null,
    timeouts: allTurns.filter((t) => t.timedOut).length,
    over4s: latencies.filter((v) => v > 4000).length,
  },
  spend: {
    modelCalls,
    promptTokens,
    completionTokens,
    estimatedUsd: Number(spendUsd.toFixed(4)),
    pricingModel: HOST_MODEL,
  },
  readPlanners: allTurns.reduce((acc, t) => {
    const key = t.readPlanner ?? "unknown";
    acc[key] = (acc[key] ?? 0) + 1;
    return acc;
  }, {}),
};

const outDir = join(REPO_ROOT, "artifacts", "runs", args.batch);
mkdirSync(outDir, { recursive: true });
writeFileSync(join(outDir, "transcript.json"), JSON.stringify(records, null, 2));
writeFileSync(join(outDir, "summary.json"), JSON.stringify(summary, null, 2));

const row = (k, v) => `| ${k} | ${v.cases} | ${v.PASS} | ${v.PARTIAL} | ${v.FAIL} | ${v.SKIP} |`;
const lines = [
  `# ${args.batch}`,
  "",
  `suite ${summary.suite} · host ${BASE_URL} · model ${HOST_MODEL}` +
    (lab.executionMode ? ` · mode ${lab.executionMode}` : "") +
    (lab.planner ? ` · planner ${lab.planner}` : "") +
    ` · ground truth ${truth.generatedAt}`,
  "",
  `${summary.PASS} PASS / ${summary.PARTIAL} PARTIAL / ${summary.FAIL} FAIL / ${summary.SKIP} SKIP of ${summary.cases} cases (${(summary.passRate * 100).toFixed(1)}% pass, ${(summary.passOrPartialRate * 100).toFixed(1)}% pass-or-partial; ${(summary.turnPassRate * 100).toFixed(1)}% of ${summary.turns} turns)`,
  `hallucinations ${summary.hallucinations} · entity failures ${summary.entityFailures} · tool-selection failures ${summary.toolSelectionFailures} · action failures ${summary.actionFailures}`,
  `latency p50 ${summary.latency.p50} ms · p95 ${summary.latency.p95} ms · max ${summary.latency.max} ms · timeouts ${summary.latency.timeouts} · turns >4 s ${summary.latency.over4s}`,
  `spend ${summary.spend.modelCalls} model calls · ${promptTokens} prompt + ${completionTokens} completion tokens · $${summary.spend.estimatedUsd}`,
  `read planners seen: ${Object.entries(summary.readPlanners).map(([k, v]) => `${k}=${v}`).join(", ")}`,
  "",
  "| category | cases | pass | partial | fail | skip |",
  "| --- | ---: | ---: | ---: | ---: | ---: |",
  ...Object.entries(byCategory).map(([k, v]) => row(k, v)),
  "",
  "| actor kind | cases | pass | partial | fail | skip |",
  "| --- | ---: | ---: | ---: | ---: | ---: |",
  ...Object.entries(byActorKind).map(([k, v]) => row(k, v)),
  "",
  "failure classes: " + (Object.entries(failureClasses).map(([k, v]) => `${k}=${v}`).join(", ") || "none"),
  "",
  "## Failures and partials",
  "",
];
for (const record of records) {
  if (record.grade === "PASS") continue;
  for (const turn of record.turns) {
    if (turn.grade === "PASS") continue;
    lines.push(
      `- **${record.id}** (${record.category}, ${record.actorKind}:${record.actorName}) [${turn.failureClass ?? turn.grade}] rt=${turn.requestType} planner=${turn.readPlanner ?? "?"} tools=${turn.tools.join(",")}`,
    );
    lines.push(`  - Q: ${turn.question}`);
    lines.push(`  - A: ${String(turn.message ?? "").replace(/\n/g, " ").slice(0, 320)}`);
    for (const failure of [...turn.failures, ...turn.softMisses]) lines.push(`  - ✗ ${failure.kind}: ${failure.detail}`);
  }
}
writeFileSync(join(outDir, "report.md"), lines.join("\n"));

console.log("");
console.log(lines.slice(4, 8).join("\n"));
console.log("failure classes: " + (Object.entries(failureClasses).map(([k, v]) => `${k}=${v}`).join(", ") || "none"));
for (const [category, v] of Object.entries(byCategory)) {
  console.log(`  ${category.padEnd(22)} PASS ${v.PASS} PARTIAL ${v.PARTIAL} FAIL ${v.FAIL} SKIP ${v.SKIP}`);
}
console.log(`artifacts: ${outDir}`);
