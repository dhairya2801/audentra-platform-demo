#!/usr/bin/env node
/**
 * Edward write-ability eval runner.
 *
 * Drives the canonical student and staff assistant endpoints of a running host
 * bound to a *writable* clone of the deployed synthetic university, then reads
 * the canonical tables to find out whether the writes Edward described are the
 * writes that happened.
 *
 *   node tools/edward-eval/write/run.mjs --batch write-baseline
 *   node tools/edward-eval/write/run.mjs --holdout --batch write-holdout
 *   node tools/edward-eval/write/run.mjs --id w-fup-002 -v
 *   node tools/edward-eval/write/run.mjs --category staff_follow_up -v
 *
 * The database is reset from the base template before every run, because a
 * write suite that grades effects cannot tolerate the effects of the last one.
 *
 * Artifacts: artifacts/runs/<batch>/{transcript.json,summary.json,bank.csv}
 */

import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { CASES } from "./cases.mjs";
import { HOLDOUT_CASES } from "./holdout-cases.mjs";
import { STAFF, STUDENTS, WORK_ITEMS } from "./fixtures.mjs";
import * as db from "./db.mjs";
import { CODES, answerCorpus, gradeTurn, verdictFor } from "./grade.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(HERE, "..", "..", "..");
const BASE_URL = process.env.WRITE_EVAL_BASE_URL ?? "http://127.0.0.1:45720";
const WORKER_TOKEN =
  process.env.WRITE_EVAL_WORKER_TOKEN ?? "local-development-document-worker-token";
const RESET_SCRIPT = process.env.WRITE_EVAL_RESET ?? join(HERE, "reset-db.sh");

// gpt-4o-mini pricing (USD per token), for the spend report.
import { pricingForModel } from "../src/pricing.mjs";
const PRICING = pricingForModel();
const PRICE_PROMPT = PRICING.input;
const PRICE_COMPLETION = PRICING.output;

function parseArgs(argv) {
  const args = { batch: "write-adhoc", ids: [], categories: [], verbose: false, holdout: false, reset: true };
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    if (flag === "--batch") args.batch = argv[++index];
    else if (flag === "--id" || flag === "--ids") args.ids.push(...argv[++index].split(","));
    else if (flag === "--category") args.categories.push(...argv[++index].split(","));
    else if (flag === "--holdout") args.holdout = true;
    else if (flag === "--no-reset") args.reset = false;
    else if (flag === "--deterministic") executionMode = "deterministic";
    else if (flag === "-v" || flag === "--verbose") args.verbose = true;
    else throw new Error(`Unknown flag: ${flag}`);
  }
  return args;
}

// ---------------------------------------------------------------------------
// HTTP
// ---------------------------------------------------------------------------

// `--deterministic` runs the whole suite with the platform's zero-model
// execution mode, which is the control that says how much of the write plane
// depends on a provider at all.
let executionMode = process.env.WRITE_EVAL_MODE ?? null;

function headersFor(actor) {
  const base = { "content-type": "application/json", "x-demo-tenant-id": db.TENANT_ID };
  if (executionMode) base["x-edward-mode"] = executionMode;
  if (actor.kind === "staff") {
    return { ...base, "x-demo-actor-type": "staff", "x-demo-actor-id": actor.id };
  }
  return { ...base, "x-demo-student-id": actor.id, "x-demo-actor-id": actor.personId };
}

async function post(path, actor, body) {
  const started = Date.now();
  const response = await fetch(`${BASE_URL}${path}`, {
    method: "POST",
    headers: headersFor(actor),
    body: JSON.stringify(body ?? {}),
  });
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  return { status: response.status, payload, latencyMs: Date.now() - started };
}

async function createConversation(actor) {
  const path =
    actor.kind === "staff" ? "/v1/staff/assistant/conversations" : "/v1/student/assistant/conversations";
  const body = actor.kind === "staff" ? {} : { pageContext: { path: "/dashboard", label: "Dashboard" } };
  const { payload } = await post(path, actor, body);
  return payload?.id ?? null;
}

async function ask(actor, message, conversationId) {
  const path =
    actor.kind === "staff" ? "/v1/staff/assistant/messages" : "/v1/student/assistant/messages";
  const body = { message, conversationId };
  if (actor.kind === "student") body.pageContext = { path: "/dashboard", label: "Dashboard" };
  return post(path, actor, body);
}

function actionPath(actor, intentId, suffix) {
  const root = actor.kind === "staff" ? "staff" : "student";
  return `/v1/${root}/assistant/action-intents/${intentId}${suffix}`;
}

async function confirmIntent(actor, intent, { hash, version } = {}) {
  return post(actionPath(actor, intent.id, "/confirm"), actor, {
    expectedVersion: version ?? intent.version,
    contentSha256: hash ?? intent.contentSha256,
  });
}

async function cancelIntent(actor, intent) {
  return post(actionPath(actor, intent.id, "/cancel"), actor, { expectedVersion: intent.version });
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
// Effect probes
// ---------------------------------------------------------------------------

/**
 * Evaluate one `expect.effect` block against the canonical tables.
 *
 * Effects are measured from the moment the *case* started, so a later turn can
 * assert on what an earlier turn committed. `noEffect` is separate and is
 * measured around a single turn.
 */
function evaluateEffects(spec, { caseStartedAt, actor, students, intentId }) {
  const results = {};
  for (const [key, wanted] of Object.entries(spec)) {
    if (key === "profile") {
      const profile = db.studentProfile(actor.id);
      const wrong = Object.entries(wanted).filter(
        ([field, value]) => String(profile?.[field] ?? "") !== String(value ?? ""),
      );
      results[key] = wrong.length === 0
        ? { ok: true }
        : { ok: false, code: "EFFECT_WRONG", wanted, got: profile };
    } else if (key === "profileUnchanged") {
      const changed = db.mutationCensus(caseStartedAt).profileUpdates;
      results[key] = changed === 0 ? { ok: true } : { ok: false, code: "EFFECT_UNEXPECTED", profileUpdates: changed };
    } else if (key === "workItem") {
      const item = db.workItemByKey(wanted.key);
      const wrong = Object.entries(wanted)
        .filter(([field]) => field !== "key")
        .filter(([field, value]) => String(item?.[field] ?? "") !== String(value ?? ""));
      results[key] = wrong.length === 0
        ? { ok: true }
        : { ok: false, code: "EFFECT_WRONG", wanted, got: item };
    } else if (key === "edwardWorkItem") {
      const created = db.edwardWorkItems(caseStartedAt);
      const match = created.find((item) =>
        Object.entries(wanted).every(
          ([field, value]) => String(item[field] ?? "") === String(value ?? ""),
        ),
      );
      results[key] = match ? { ok: true } : { ok: false, code: "EFFECT_MISSING", wanted, created };
    } else if (key === "helpRequests") {
      const rows = db.helpRequests(actor.id, caseStartedAt);
      results[key] = rows.length === Number(wanted)
        ? { ok: true }
        : { ok: false, code: "EFFECT_WRONG", wanted, got: rows.length };
    } else if (key === "receiptCount") {
      const rows = db.sql(
        `SELECT count(*) AS n FROM agent_action_receipt WHERE tenant_id = ${db.quote(db.TENANT_ID)} AND committed_at > ${db.quote(caseStartedAt)}`,
      );
      const got = Number(rows[0]?.n ?? 0);
      results[key] = got === Number(wanted)
        ? { ok: true }
        : { ok: false, code: "EFFECT_WRONG", wanted, got };
    } else if (key === "sendIntentPending") {
      const intents = db.sendIntents(caseStartedAt);
      const pending = intents.filter((row) => row.sentAt === null);
      results[key] = pending.length > 0 && intents.every((row) => row.sentAt === null)
        ? { ok: true }
        : { ok: false, code: "EFFECT_WRONG", wanted, got: intents };
    } else {
      results[key] = { ok: false, code: "HARNESS_ERROR", detail: `unknown effect probe: ${key}` };
    }
  }
  return results;
}

// ---------------------------------------------------------------------------
// Runner
// ---------------------------------------------------------------------------

function usageOf(trace) {
  let prompt = 0;
  let completion = 0;
  let calls = 0;
  for (const call of trace?.modelCalls ?? []) {
    calls += 1;
    prompt += Number(call?.usage?.promptTokens ?? call?.usage?.prompt_tokens ?? 0);
    completion += Number(call?.usage?.completionTokens ?? call?.usage?.completion_tokens ?? 0);
  }
  return { calls, prompt, completion };
}

async function runCase(testCase, context) {
  const actor =
    testCase.actorKind === "staff"
      ? { kind: "staff", ...context.staff[testCase.actor] }
      : { kind: "student", ...context.students[testCase.actor] };
  const caseStartedAt = db.nowIso();
  const conversationId = await createConversation(actor);
  const turns = [];
  let lastIntent = null;

  for (const [index, turn] of testCase.turns.entries()) {
    const turnStartedAt = db.nowIso();
    const before = db.mutationCensus(turnStartedAt);
    let observed;
    try {
      const { status, payload, latencyMs } = await ask(actor, turn.user, conversationId);
      const trace = await fetchTrace(payload?.requestId);
      const intent = (payload?.actionIntents ?? [])[0] ?? null;
      if (intent) lastIntent = intent;
      const hasDraft = (payload?.blocks ?? []).some((block) => block?.type === "draft");
      const spec = turn.expect ?? {};

      let confirmation = null;
      let receipt = null;
      let intentRow = null;

      if (intent && (spec.confirm || spec.cancel || spec.confirmWithBadHash || spec.confirmAsOtherStudent)) {
        if (spec.confirmWithBadHash) {
          confirmation = await confirmIntent(actor, intent, {
            hash: "0".repeat(64),
          });
        } else if (spec.confirmAsOtherStudent) {
          const other = { kind: "student", ...context.students[spec.confirmAsOtherStudent] };
          confirmation = await confirmIntent(other, intent);
        } else if (spec.cancel) {
          confirmation = await cancelIntent(actor, intent);
        } else {
          confirmation = await confirmIntent(actor, intent);
          if (spec.confirmTwice) await confirmIntent(actor, intent);
        }
        receipt = db.receiptForIntent(intent.id);
        intentRow = db.intentRow(intent.id);
      } else if (intent) {
        intentRow = db.intentRow(intent.id);
      }

      const after = db.mutationCensus(turnStartedAt);
      const delta = Object.fromEntries(
        Object.keys(after).map((key) => [key, after[key] - (before[key] ?? 0)]),
      );

      const effects = spec.effect
        ? evaluateEffects(spec.effect, {
            caseStartedAt,
            actor,
            students: context.students,
            intentId: intent?.id ?? lastIntent?.id ?? null,
          })
        : null;

      observed = {
        status,
        payload,
        trace,
        intent,
        actionError: payload?.actionError ?? null,
        hasDraft,
        confirmation,
        receipt,
        intentRow,
        effects,
        delta,
        latencyMs,
      };
    } catch (error) {
      observed = {
        status: 0,
        payload: { message: `harness error: ${error.message}` },
        trace: null,
        intent: null,
        actionError: null,
        hasDraft: false,
        confirmation: null,
        receipt: null,
        intentRow: null,
        effects: null,
        delta: null,
        latencyMs: 0,
        harnessError: error.message,
      };
    }

    const findings = observed.harnessError
      ? [{ code: "HARNESS_ERROR", severity: "hard", detail: observed.harnessError }]
      : gradeTurn({ expect: turn.expect, observed, context: { ...context, actor } });

    turns.push({
      index: index + 1,
      user: turn.user,
      expectedAnswer: turn.expectedAnswer,
      answer: answerCorpus(observed.payload),
      action: observed.intent?.action ?? null,
      actionError: observed.actionError?.code ?? null,
      preview: observed.intent?.preview ?? null,
      confirmation: observed.confirmation
        ? { status: observed.confirmation.status, body: observed.confirmation.payload }
        : null,
      receipt: observed.receipt,
      intentStatus: observed.intentRow?.status ?? null,
      delta: observed.delta,
      effects: observed.effects,
      tracePath: observed.trace?.path ?? null,
      usage: usageOf(observed.trace),
      latencyMs: observed.latencyMs,
      findings,
      verdict: verdictFor(findings),
    });
  }

  const allFindings = turns.flatMap((turn) => turn.findings);
  return {
    id: testCase.id,
    category: testCase.category,
    actorKind: testCase.actorKind,
    actor: testCase.actor,
    actorName: actor.name ?? actor.fullName,
    conversationId,
    turns,
    findings: allFindings,
    verdict: verdictFor(allFindings),
  };
}

// ---------------------------------------------------------------------------
// Reporting
// ---------------------------------------------------------------------------

function csvCell(value) {
  const text = String(value ?? "").replace(/\r?\n/g, " ").replace(/\s+/g, " ").trim();
  return `"${text.replace(/"/g, '""')}"`;
}

/**
 * The human-facing bank: one row per user turn, carrying the question, the
 * expected answer in prose, and what Edward actually said. The identifying
 * columns are there because a question asked by nobody in particular has no
 * right answer in a 2,577-student university.
 */
function toCsv(results) {
  const header = [
    "id", "suite", "actor_kind", "actor", "category", "turn",
    "question", "expected_answer", "actual_answer",
    "action_proposed", "grade", "failures",
  ];
  const rows = [header.map(csvCell).join(",")];
  for (const result of results) {
    for (const turn of result.turns) {
      rows.push(
        [
          result.id,
          result.suite,
          result.actorKind,
          `${result.actor} (${result.actorName})`,
          result.category,
          `${turn.index} of ${result.turns.length}`,
          turn.user,
          turn.expectedAnswer ?? "",
          turn.answer,
          turn.action ?? turn.actionError ?? "",
          turn.verdict,
          turn.findings.map((f) => f.code).join(" | "),
        ].map(csvCell).join(","),
      );
    }
  }
  return `${rows.join("\n")}\n`;
}

function summarize(results) {
  const byCategory = new Map();
  const byCode = new Map();
  let pass = 0;
  let partial = 0;
  let fail = 0;
  let calls = 0;
  let prompt = 0;
  let completion = 0;
  let turnCount = 0;
  const latencies = [];

  for (const result of results) {
    if (result.verdict === "PASS") pass += 1;
    else if (result.verdict === "PARTIAL") partial += 1;
    else fail += 1;
    const bucket = byCategory.get(result.category) ?? { pass: 0, partial: 0, fail: 0 };
    bucket[result.verdict.toLowerCase()] += 1;
    byCategory.set(result.category, bucket);
    for (const finding of result.findings) {
      byCode.set(finding.code, (byCode.get(finding.code) ?? 0) + 1);
    }
    for (const turn of result.turns) {
      turnCount += 1;
      calls += turn.usage.calls;
      prompt += turn.usage.prompt;
      completion += turn.usage.completion;
      latencies.push(turn.latencyMs);
    }
  }
  latencies.sort((a, b) => a - b);
  const percentile = (p) => latencies[Math.min(latencies.length - 1, Math.floor(latencies.length * p))] ?? 0;

  return {
    cases: results.length,
    turns: turnCount,
    pass,
    partial,
    fail,
    passRate: results.length ? Number((pass / results.length).toFixed(3)) : 0,
    byCategory: Object.fromEntries(
      [...byCategory.entries()].sort().map(([key, value]) => [key, value]),
    ),
    byFailureCode: Object.fromEntries(
      [...byCode.entries()].sort((a, b) => b[1] - a[1]),
    ),
    spend: {
      modelCalls: calls,
      promptTokens: prompt,
      completionTokens: completion,
      estimatedUsd: Number((prompt * PRICE_PROMPT + completion * PRICE_COMPLETION).toFixed(6)),
    },
    latencyMs: { p50: percentile(0.5), p95: percentile(0.95) },
  };
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const suite = args.holdout ? "holdout" : "development";
  let cases = args.holdout ? HOLDOUT_CASES : CASES;
  if (args.ids.length) cases = cases.filter((item) => args.ids.includes(item.id));
  if (args.categories.length) cases = cases.filter((item) => args.categories.includes(item.category));
  if (cases.length === 0) throw new Error("no cases selected");

  const health = await fetch(`${BASE_URL}/health`).then(
    (response) => response.ok,
    () => false,
  );
  if (!health) throw new Error(`no API host at ${BASE_URL} — start one against the write-eval database`);

  if (args.reset) {
    process.stdout.write(`resetting ${db.databaseName()} … `);
    execFileSync("bash", [RESET_SCRIPT], { stdio: "pipe" });
    process.stdout.write("done\n");
  }

  const context = db.resolveFixtures({ staff: STAFF, students: STUDENTS, workItems: WORK_ITEMS });
  const results = [];
  for (const [index, testCase] of cases.entries()) {
    const result = await runCase(testCase, context);
    result.suite = suite;
    results.push(result);
    const mark = result.verdict === "PASS" ? "·" : result.verdict === "PARTIAL" ? "~" : "✗";
    const codes = [...new Set(result.findings.map((f) => f.code))].join(",");
    process.stdout.write(
      `${mark} [${index + 1}/${cases.length}] ${result.id} ${result.verdict}${codes ? ` (${codes})` : ""}\n`,
    );
    if (args.verbose && result.verdict !== "PASS") {
      for (const turn of result.turns) {
        process.stdout.write(`    Q${turn.index}: ${turn.user}\n`);
        process.stdout.write(`    A${turn.index}: ${turn.answer.slice(0, 400).replace(/\n/g, " ")}\n`);
        if (turn.action) process.stdout.write(`    action: ${turn.action}\n`);
        for (const finding of turn.findings) {
          process.stdout.write(`    ! ${finding.code} ${JSON.stringify(finding.detail).slice(0, 300)}\n`);
        }
      }
    }
  }

  const summary = summarize(results);
  const outDir = join(REPO_ROOT, "artifacts", "runs", args.batch);
  mkdirSync(outDir, { recursive: true });
  writeFileSync(join(outDir, "transcript.json"), `${JSON.stringify(results, null, 2)}\n`);
  writeFileSync(join(outDir, "summary.json"), `${JSON.stringify(summary, null, 2)}\n`);
  writeFileSync(join(outDir, "bank.csv"), toCsv(results));

  process.stdout.write(
    `\n${summary.pass} pass · ${summary.partial} partial · ${summary.fail} fail  (${summary.cases} cases, ${summary.turns} turns)\n`,
  );
  process.stdout.write(`failures: ${JSON.stringify(summary.byFailureCode)}\n`);
  process.stdout.write(
    `spend: ${summary.spend.modelCalls} calls, $${summary.spend.estimatedUsd} · latency p50 ${summary.latencyMs.p50}ms p95 ${summary.latencyMs.p95}ms\n`,
  );
  process.stdout.write(`artifacts: ${outDir}\n`);
  process.exitCode = summary.fail > 0 ? 1 : 0;
}

main().catch((error) => {
  process.stderr.write(`${error.stack ?? error.message}\n`);
  process.exitCode = 2;
});
