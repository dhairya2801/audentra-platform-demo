#!/usr/bin/env node
/**
 * Print one comparable row per stored batch: grades, model calls, tokens,
 * estimated USD and latency, whatever suite produced it.
 *
 *   node tools/edward-eval/summarize-batches.mjs base-4o-* v1-luna-*   # glob prefixes
 *   node tools/edward-eval/summarize-batches.mjs --md base-4o-staffdb-v2-dev v1-luna-staffdb-v2-dev
 *
 * The suites store their summaries in slightly different shapes (student-v3
 * `totals`, staff-db `grades`, university top-level PASS/FAIL, write
 * `pass/partial/fail`); this normalises them so a report table can be built
 * without hand-copying numbers.
 */

import { readdirSync, readFileSync, existsSync } from "node:fs";
import { join } from "node:path";

const ROOT = new URL("../../artifacts/runs/", import.meta.url).pathname;
const args = process.argv.slice(2);
const markdown = args.includes("--md");
const patterns = args.filter((a) => !a.startsWith("--"));

function percentile(values, q) {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.floor(q * (sorted.length - 1)))];
}

function matches(name) {
  if (patterns.length === 0) return true;
  return patterns.some((p) => (p.endsWith("*") ? name.startsWith(p.slice(0, -1)) : name === p));
}

function normalise(batch, summary, transcriptPath) {
  const g = summary.totals ?? summary.grades ?? summary;
  const pass = g.PASS ?? g.pass ?? null;
  const partial = g.PARTIAL ?? g.partial ?? 0;
  const fail = g.FAIL ?? g.fail ?? null;
  const skip = g.SKIP ?? 0;
  const cases = summary.cases ?? (pass !== null ? pass + partial + fail + skip : null);
  const model = summary.model ?? summary.spend ?? {};
  const calls = model.calls ?? model.modelCalls ?? summary.modelCalls ?? null;
  const prompt = model.promptTokens ?? model.prompt ?? null;
  const completion = model.completionTokens ?? model.completion ?? null;
  const usd = model.estimatedUsd ?? summary.estimatedUsd ?? summary.spend?.estimatedUsd ?? null;
  const latency = summary.latency ?? summary.latencyMs ?? summary.latencyServerMs ?? {};
  let hallucinations = null;
  let entity = null;
  let toolFailures = null;
  const latencies = [];
  if (existsSync(transcriptPath)) {
    try {
      const transcript = JSON.parse(readFileSync(transcriptPath, "utf8"));
      const records = Array.isArray(transcript) ? transcript : transcript.cases ?? transcript.records ?? [];
      hallucinations = 0;
      entity = 0;
      toolFailures = 0;
      for (const record of records) {
        for (const turn of record.turns ?? [record]) {
          const ms = turn.latencyMs ?? turn.latency?.serverMs ?? turn.response?.latencyMs ?? null;
          if (typeof ms === "number") latencies.push(ms);
          for (const f of turn.failures ?? turn.findings ?? []) {
            const kind = f.kind ?? f.code ?? "";
            if (/forbidden|hallucination|UNGROUNDED_SUCCESS|invented/i.test(kind)) hallucinations += 1;
            if (/wrong_student|arbitrary_resolution|WRONG_TARGET|entity/i.test(kind)) entity += 1;
            if (/tool_not_called|tool_arguments|wrong_tool|UNNECESSARY_TOOL/i.test(kind)) toolFailures += 1;
          }
        }
      }
    } catch {
      /* leave nulls */
    }
  }
  return {
    batch,
    cases,
    pass,
    partial,
    fail,
    skip,
    passRate: cases ? (pass / (cases - skip)) : null,
    hallucinations,
    entity,
    toolFailures,
    calls,
    prompt,
    completion,
    usd,
    p50: latency.p50 ?? percentile(latencies, 0.5),
    p95: latency.p95 ?? latency.p90 ?? percentile(latencies, 0.95),
  };
}

const rows = [];
for (const name of readdirSync(ROOT).sort()) {
  if (!matches(name)) continue;
  const summaryPath = join(ROOT, name, "summary.json");
  if (!existsSync(summaryPath)) continue;
  const summary = JSON.parse(readFileSync(summaryPath, "utf8"));
  rows.push(normalise(name, summary, join(ROOT, name, "transcript.json")));
}

const fmt = (v, digits = 0) =>
  v === null || v === undefined ? "—" : typeof v === "number" ? v.toFixed(digits) : String(v);
const header = [
  "batch", "cases", "pass", "partial", "fail", "skip", "pass%", "halluc", "entity", "toolsel",
  "calls", "prompt", "completion", "usd", "p50ms", "p95ms",
];
const line = (r) => [
  r.batch, fmt(r.cases), fmt(r.pass), fmt(r.partial), fmt(r.fail), fmt(r.skip),
  r.passRate === null ? "—" : (r.passRate * 100).toFixed(1), fmt(r.hallucinations), fmt(r.entity),
  fmt(r.toolFailures), fmt(r.calls), fmt(r.prompt), fmt(r.completion), fmt(r.usd, 4), fmt(r.p50),
  fmt(r.p95),
];
if (markdown) {
  console.log(`| ${header.join(" | ")} |`);
  console.log(`|${header.map(() => "---").join("|")}|`);
  for (const r of rows) console.log(`| ${line(r).join(" | ")} |`);
} else {
  console.log(header.join("\t"));
  for (const r of rows) console.log(line(r).join("\t"));
}
