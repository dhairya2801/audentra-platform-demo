#!/usr/bin/env node
/**
 * Re-grade a stored batch deterministically — no model calls, free.
 *
 *   node tools/edward-eval/regrade.mjs --batch python-edward-baseline-4
 *
 * Recomputes contract checks, per-case checks (against the full
 * student-visible answer), tool-selection codes, taxonomy, and the summary
 * from transcript.json + snapshots.json, keeping existing judge scores.
 * Useful after tightening checks or fixing a check bug: the expensive
 * asking/judging is never repeated.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { accountRun } from "./src/accounting.mjs";
import { deriveFacts } from "./src/facts.mjs";
import { gradeTranscripts, finalizeRecords } from "./src/grade.mjs";
import { QUESTIONS } from "./src/questions.mjs";
import { summarise, printSummary } from "./src/summarize.mjs";

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), "..", "..");

const batchIndex = process.argv.indexOf("--batch");
const batch = batchIndex >= 0 ? process.argv[batchIndex + 1] : process.argv[2];
if (!batch) {
  console.error("Usage: regrade.mjs --batch <name>");
  process.exit(2);
}

const directory = join(repoRoot, "artifacts", "runs", batch);
const transcript = JSON.parse(readFileSync(join(directory, "transcript.json"), "utf8"));
const snapshots = JSON.parse(readFileSync(join(directory, "snapshots.json"), "utf8"));
const previousSummary = JSON.parse(readFileSync(join(directory, "summary.json"), "utf8"));

const factsByPersona = Object.fromEntries(
  Object.entries(snapshots).map(([persona, snapshot]) => [persona, deriveFacts(snapshot)]),
);

// Expectations may have been corrected since the batch ran; grade the stored
// answers against the CURRENT case definitions (matched by id + turn index),
// so a check fix never requires re-asking.
const currentById = new Map(QUESTIONS.map((item) => [item.id, item]));
const refreshed = transcript.map((record) => {
  const current = currentById.get(record.id);
  if (!current) return record;
  return {
    ...record,
    judged: current.judged,
    critical: current.critical,
    tags: current.tags,
    note: current.note,
    expectedBehavior: current.expectedBehavior,
    judgeFacts: current.judgeFacts,
    turns: record.turns.map((turn, index) => ({
      ...turn,
      checks: current.turns[index]?.checks ?? turn.checks,
      expect: current.turns[index]?.expect ?? turn.expect,
    })),
  };
});

const results = finalizeRecords(gradeTranscripts(refreshed, factsByPersona));
const accounting = accountRun(results, previousSummary.accounting?.operations?.judge ?? {});
const summary = summarise(results, batch, accounting);
summary.spend = previousSummary.spend;
summary.regradedAt = new Date().toISOString();

writeFileSync(join(directory, "transcript.json"), `${JSON.stringify(results, null, 2)}\n`);
writeFileSync(join(directory, "summary.json"), `${JSON.stringify(summary, null, 2)}\n`);
printSummary(summary, results);

const criticalFailures = results.filter((record) => record.critical && !record.deterministicPass);
if (criticalFailures.length > 0) {
  console.error(
    `\nCRITICAL INVARIANT FAILURES (${criticalFailures.length}): ${criticalFailures.map((record) => record.id).join(", ")}`,
  );
  process.exitCode = 1;
}
