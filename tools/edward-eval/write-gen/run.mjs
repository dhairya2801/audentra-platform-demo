#!/usr/bin/env node
/**
 * Edward write generalization suite runner.
 *
 * Same discipline as ../write/run.mjs — drive the canonical assistant
 * endpoints of a host bound to the writable university clone, then read the
 * canonical tables — with the additions the generalization families need:
 *
 *  - per-turn recognition attribution (`actionRecognitionSource` from the
 *    trace: pattern / continuation / model / model_none / none), so tier-1's
 *    contribution is measured instead of hidden;
 *  - staged canonical-state changes between preview and confirmation
 *    (`mutateBeforeConfirm`, `expireBeforeConfirm`) for race/stale testing;
 *  - confirmation of an *earlier* intent (`confirmIntentIndex`), as another
 *    staff actor (`confirmAsOtherStaff`), with a tampered body
 *    (`confirmWithMutatedBody`), or with a wrong version;
 *  - case-level `setupSql` for states the deployment cannot produce;
 *  - extra effect probes: `workItemsCreatedBetween`, `cohortConsistent`,
 *    `workItemBlocker`, `workItemOutcome`.
 *
 *   node tools/edward-eval/write-gen/run.mjs --batch gen-baseline
 *   node tools/edward-eval/write-gen/run.mjs --family gen_tier1 -v
 *   node tools/edward-eval/write-gen/run.mjs --id g-t1-004 -v
 */

import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { CASES } from "./cases.mjs";
import { STAFF, STUDENTS, WORK_ITEMS } from "./fixtures-gen.mjs";
import * as db from "../write/db.mjs";
import { answerCorpus, gradeTurn, verdictFor } from "./grade-gen.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(HERE, "..", "..", "..");
const BASE_URL = process.env.WRITE_EVAL_BASE_URL ?? "http://127.0.0.1:45720";
const WORKER_TOKEN =
  process.env.WRITE_EVAL_WORKER_TOKEN ?? "local-development-document-worker-token";
const RESET_SCRIPT = process.env.WRITE_EVAL_RESET ?? join(HERE, "..", "write", "reset-db.sh");

const PRICE_PROMPT = 0.15e-6;
const PRICE_COMPLETION = 0.6e-6;

const PSQL_COMMAND =
  process.env.WRITE_EVAL_PSQL ?? "docker exec -i audentra-platform-postgres-1 psql -U vv -d {db}";

/** Raw DML against the eval database — setup and race staging only. */
function exec(statement) {
  const [command, ...args] = PSQL_COMMAND.replace("{db}", db.databaseName()).split(/\s+/);
  execFileSync(command, [...args, "-v", "ON_ERROR_STOP=1", "-qc", statement], {
    encoding: "utf8",
  });
}

function parseArgs(argv) {
  const args = { batch: "gen-adhoc", ids: [], families: [], verbose: false, reset: true };
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    if (flag === "--batch") args.batch = argv[++index];
    else if (flag === "--id" || flag === "--ids") args.ids.push(...argv[++index].split(","));
    else if (flag === "--family" || flag === "--category") args.families.push(...argv[++index].split(","));
    else if (flag === "--no-reset") args.reset = false;
    else if (flag === "--deterministic") executionMode = "deterministic";
    else if (flag === "-v" || flag === "--verbose") args.verbose = true;
    else throw new Error(`Unknown flag: ${flag}`);
  }
  return args;
}

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

async function confirmIntent(actor, intent, { hash, version, extraBody } = {}) {
  return post(actionPath(actor, intent.id, "/confirm"), actor, {
    expectedVersion: version ?? intent.version,
    contentSha256: hash ?? intent.contentSha256,
    ...(extraBody ?? {}),
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

/** Work items created since a moment, whatever created them (INQ- rows are
 * help-request mirrors, not tasks, and are excluded). */
function createdWorkItemCount(sinceIso) {
  return db.count(`
    SELECT count(*) AS n FROM staff_work_item
    WHERE tenant_id = ${db.quote(db.TENANT_ID)} AND created_at > ${db.quote(sinceIso)}
      AND key NOT LIKE 'INQ-%'`);
}

function createdItemForStudent(sinceIso, studentRef) {
  return db.count(`
    SELECT count(*) AS n FROM staff_work_item w
    JOIN student s ON s.id = w.student_id AND s.tenant_id = w.tenant_id
    WHERE w.tenant_id = ${db.quote(db.TENANT_ID)} AND w.created_at > ${db.quote(sinceIso)}
      AND s.external_ref = ${db.quote(studentRef)}`);
}

function workItemColumns(key) {
  return db.one(`
    SELECT w.key AS key, w.status AS status, w.blocker_code AS "blockerCode",
           w.blocker_detail AS "blockerDetail", w.outcome_code AS "outcomeCode",
           w.resolution_code AS "resolutionCode", w.next_step AS "nextStep",
           to_char(w.follow_up_at, 'YYYY-MM-DD') AS "followUpAt",
           to_char(w.due_at, 'YYYY-MM-DD') AS "dueAt",
           m.external_ref AS "assigneeRef"
    FROM staff_work_item w
    LEFT JOIN staff_member m ON m.id = w.assignee_id AND m.tenant_id = w.tenant_id
    WHERE w.tenant_id = ${db.quote(db.TENANT_ID)} AND w.key = ${db.quote(key)}`);
}

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
    } else if (key === "workItemColumns") {
      const item = workItemColumns(wanted.key);
      const wrong = Object.entries(wanted)
        .filter(([field]) => field !== "key")
        .filter(([field, value]) => String(item?.[field] ?? "") !== String(value ?? ""));
      results[key] = wrong.length === 0
        ? { ok: true }
        : { ok: false, code: "EFFECT_WRONG", wanted, got: item };
    } else if (key === "workItemBlocker") {
      const item = workItemColumns(wanted.key);
      results[key] = item?.blockerCode === wanted.code
        ? { ok: true }
        : { ok: false, code: "BLOCKER_CODE_WRONG", wanted: wanted.code, got: item?.blockerCode ?? null };
    } else if (key === "edwardWorkItem") {
      const created = db.edwardWorkItems(caseStartedAt);
      const match = created.find((item) =>
        Object.entries(wanted).every(
          ([field, value]) => String(item[field] ?? "") === String(value ?? ""),
        ),
      );
      results[key] = match ? { ok: true } : { ok: false, code: "EFFECT_MISSING", wanted, created };
    } else if (key === "edwardWorkItemAbsent") {
      const created = db.edwardWorkItems(caseStartedAt);
      const match = created.find((item) =>
        Object.entries(wanted).every(
          ([field, value]) => String(item[field] ?? "") === String(value ?? ""),
        ),
      );
      results[key] = match
        ? { ok: false, code: "EFFECT_UNEXPECTED", found: match }
        : { ok: true };
    } else if (key === "workItemsCreated") {
      const created = createdWorkItemCount(caseStartedAt);
      results[key] = created === Number(wanted)
        ? { ok: true }
        : { ok: false, code: "EFFECT_WRONG", wanted, got: created };
    } else if (key === "workItemsCreatedBetween") {
      const created = createdWorkItemCount(caseStartedAt);
      const [lo, hi] = wanted;
      results[key] = created >= lo && created <= hi
        ? { ok: true }
        : { ok: false, code: "EFFECT_WRONG", wanted: `[${lo},${hi}]`, got: created };
    } else if (key === "profileFieldNot") {
      const profile = db.studentProfile(actor.id);
      const got = String(profile?.[wanted.field] ?? "");
      results[key] = got !== String(wanted.value)
        ? { ok: true }
        : { ok: false, code: "EFFECT_WRONG", detail: "forbidden value written", wanted, got };
    } else if (key === "memberItemCreated") {
      const created = createdItemForStudent(caseStartedAt, wanted.studentRef);
      results[key] = created > 0
        ? { ok: true }
        : { ok: false, code: "EFFECT_MISSING", wanted, got: created };
    } else if (key === "cohortConsistent") {
      // The batch's own accounting must agree with the canonical rows: the
      // receipt's affected count, the per-member ledger, and the number of
      // work items actually created are three descriptions of one event.
      const id = intentId;
      if (!id) {
        results[key] = { ok: false, code: "EFFECT_MISSING", detail: "no intent to check" };
      } else {
        // Cohort-created rows do not carry the single-create description
        // marker, so the canonical-row leg counts the batch ledger's own
        // work-item links — each one a real row the runner can also read.
        const receipt = db.receiptForIntent(id);
        const items = db.batchItems(id);
        const succeeded = items.filter((row) => row.status === "succeeded").length;
        const linked = items.filter((row) => row.work_item_key).length;
        const affected = Number(receipt?.affected_count ?? -1);
        results[key] = affected === succeeded && succeeded === linked && affected > 0
          ? { ok: true, affected }
          : {
              ok: false,
              code: "EFFECT_WRONG",
              receiptAffected: affected,
              ledgerSucceeded: succeeded,
              ledgerLinkedRows: linked,
            };
      }
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
  let setupError = null;
  for (const statement of testCase.setupSql ?? []) {
    try {
      exec(statement);
    } catch (error) {
      setupError = String(error.message).slice(0, 400);
      break;
    }
  }
  if (setupError) {
    const findings = [{ code: "HARNESS_ERROR", severity: "hard", detail: `setupSql: ${setupError}` }];
    return {
      id: testCase.id,
      family: testCase.family,
      category: testCase.family,
      actorKind: testCase.actorKind,
      actor: testCase.actor,
      actorName: testCase.actor,
      conversationId: null,
      turns: [],
      findings,
      verdict: verdictFor(findings),
    };
  }
  const caseStartedAt = db.nowIso();
  const conversationId = await createConversation(actor);
  const turns = [];
  const intentsSeen = [];
  let lastIntent = null;

  for (const [index, turn] of testCase.turns.entries()) {
    const turnStartedAt = db.nowIso();
    const before = db.mutationCensus(turnStartedAt);
    let observed;
    try {
      const { status, payload, latencyMs } = await ask(actor, turn.user, conversationId);
      const trace = await fetchTrace(payload?.requestId);
      const intent = (payload?.actionIntents ?? [])[0] ?? null;
      if (intent) {
        lastIntent = intent;
        intentsSeen.push(intent);
      }
      const hasDraft = (payload?.blocks ?? []).some((block) => block?.type === "draft");
      const spec = turn.expect ?? {};

      let confirmation = null;
      let receipt = null;
      let intentRow = null;

      const wantsConfirm =
        spec.confirm || spec.cancel || spec.confirmWithBadHash || spec.confirmAsOtherStudent ||
        spec.confirmAsOtherStaff || spec.confirmWithMutatedBody || spec.confirmWithWrongVersion;
      const confirmTarget =
        spec.confirmIntentIndex !== undefined ? intentsSeen[spec.confirmIntentIndex] ?? null : intent;

      if (confirmTarget && wantsConfirm) {
        for (const statement of turn.mutateBeforeConfirm ?? []) {
          try {
            exec(statement);
          } catch (error) {
            throw new Error(`mutateBeforeConfirm failed: ${String(error.message).slice(0, 300)}`);
          }
        }
        if (spec.expireBeforeConfirm) {
          // The table checks expires_at > created_at, so "expired" is
          // expressed as one second after creation — deep in the past
          // relative to the confirm that follows.
          exec(
            `UPDATE agent_action_intent SET expires_at = created_at + interval '1 second' WHERE id = '${confirmTarget.id}'`,
          );
        }
        if (spec.confirmWithBadHash) {
          confirmation = await confirmIntent(actor, confirmTarget, { hash: "0".repeat(64) });
        } else if (spec.confirmWithWrongVersion) {
          confirmation = await confirmIntent(actor, confirmTarget, {
            version: Number(confirmTarget.version) + 7,
          });
        } else if (spec.confirmWithMutatedBody) {
          confirmation = await confirmIntent(actor, confirmTarget, {
            extraBody: spec.confirmWithMutatedBody,
          });
        } else if (spec.confirmAsOtherStudent) {
          const other = { kind: "student", ...context.students[spec.confirmAsOtherStudent] };
          confirmation = await confirmIntent(other, confirmTarget);
        } else if (spec.confirmAsOtherStaff) {
          const other = { kind: "staff", ...context.staff[spec.confirmAsOtherStaff] };
          confirmation = await confirmIntent(other, confirmTarget);
        } else if (spec.cancel) {
          confirmation = await cancelIntent(actor, confirmTarget);
        } else {
          confirmation = await confirmIntent(actor, confirmTarget);
          if (spec.confirmTwice) await confirmIntent(actor, confirmTarget);
        }
        receipt = db.receiptForIntent(confirmTarget.id);
        intentRow = db.intentRow(confirmTarget.id);
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
            intentId: confirmTarget?.id ?? intent?.id ?? lastIntent?.id ?? null,
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
      recognitionSource: observed.trace?.actionRecognitionSource ?? null,
      tracePath: observed.trace?.path ?? null,
      preview: observed.intent?.preview ?? null,
      confirmation: observed.confirmation
        ? { status: observed.confirmation.status, body: observed.confirmation.payload }
        : null,
      receipt: observed.receipt,
      intentStatus: observed.intentRow?.status ?? null,
      delta: observed.delta,
      effects: observed.effects,
      usage: usageOf(observed.trace),
      latencyMs: observed.latencyMs,
      findings,
      verdict: verdictFor(findings),
    });
  }

  const allFindings = turns.flatMap((turn) => turn.findings);
  return {
    id: testCase.id,
    family: testCase.family,
    category: testCase.family,
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

function toCsv(results) {
  const header = [
    "id", "actor_kind", "actor", "family", "turn",
    "question", "expected_answer", "actual_answer",
    "action_proposed", "recognition_source", "grade", "failures",
  ];
  const rows = [header.map(csvCell).join(",")];
  for (const result of results) {
    for (const turn of result.turns) {
      rows.push(
        [
          result.id,
          result.actorKind,
          `${result.actor} (${result.actorName})`,
          result.family,
          `${turn.index} of ${result.turns.length}`,
          turn.user,
          turn.expectedAnswer ?? "",
          turn.answer,
          turn.action ?? turn.actionError ?? "",
          turn.recognitionSource ?? "",
          turn.verdict,
          turn.findings.map((f) => f.code).join(" | "),
        ].map(csvCell).join(","),
      );
    }
  }
  return `${rows.join("\n")}\n`;
}

function summarize(results) {
  const byFamily = new Map();
  const byCode = new Map();
  const bySource = new Map();
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
    const bucket = byFamily.get(result.family) ?? { pass: 0, partial: 0, fail: 0 };
    bucket[result.verdict.toLowerCase()] += 1;
    byFamily.set(result.family, bucket);
    for (const finding of result.findings) {
      byCode.set(finding.code, (byCode.get(finding.code) ?? 0) + 1);
    }
    for (const turn of result.turns) {
      turnCount += 1;
      calls += turn.usage.calls;
      prompt += turn.usage.prompt;
      completion += turn.usage.completion;
      latencies.push(turn.latencyMs);
      const source = turn.recognitionSource ?? "none";
      bySource.set(source, (bySource.get(source) ?? 0) + 1);
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
    byFamily: Object.fromEntries([...byFamily.entries()].sort().map(([key, value]) => [key, value])),
    byFailureCode: Object.fromEntries([...byCode.entries()].sort((a, b) => b[1] - a[1])),
    byRecognitionSource: Object.fromEntries([...bySource.entries()].sort()),
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
  let cases = CASES;
  if (args.ids.length) cases = cases.filter((item) => args.ids.includes(item.id));
  if (args.families.length) cases = cases.filter((item) => args.families.includes(item.family));
  if (cases.length === 0) throw new Error("no cases selected");

  const duplicates = new Set();
  const seen = new Set();
  for (const item of cases) {
    if (seen.has(item.id)) duplicates.add(item.id);
    seen.add(item.id);
  }
  if (duplicates.size) throw new Error(`duplicate case ids: ${[...duplicates].join(", ")}`);

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
        if (turn.action) process.stdout.write(`    action: ${turn.action} (${turn.recognitionSource})\n`);
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
  process.stdout.write(`recognition: ${JSON.stringify(summary.byRecognitionSource)}\n`);
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
