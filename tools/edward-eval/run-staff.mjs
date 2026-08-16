#!/usr/bin/env node
/**
 * Staff Edward evaluation CLI (deterministic suite).
 *
 *   node tools/edward-eval/run-staff.mjs
 *   node tools/edward-eval/run-staff.mjs --batch staff-baseline
 *   node tools/edward-eval/run-staff.mjs --id staff-draft-email-012 --verbose
 *
 * Boots the same `audentra-eval-api` in-memory host the student harness
 * uses, drives the canonical staff endpoint with demo staff identity, and
 * applies deterministic checks (request type, tools, content, drafts,
 * read-only, no fabricated metrics). Works with or without a provider key:
 * with no key the pipeline is fully deterministic; with one, the rewrite
 * path is exercised and the same invariants must still hold.
 *
 * Writes transcript.json and summary.json to artifacts/runs/<batch>/ and
 * exits non-zero when any `critical` case fails.
 */
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { STAFF_CASES } from "./src/staff-cases.mjs";
import { runStaffCases } from "./src/staff-runner.mjs";

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const artifacts = join(repoRoot, "artifacts");

function parseArgs(argv) {
  const args = { batch: "staff-adhoc", ids: [], verbose: false };
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    if (flag === "--batch") args.batch = argv[++index];
    else if (flag === "--id" || flag === "--ids") args.ids.push(...argv[++index].split(","));
    else if (flag === "--verbose") args.verbose = true;
    else throw new Error(`Unknown flag: ${flag}`);
  }
  return args;
}

const args = parseArgs(process.argv.slice(2));
const cases = args.ids.length
  ? STAFF_CASES.filter((item) => args.ids.includes(item.id))
  : STAFF_CASES;
if (cases.length === 0) {
  console.error("No matching cases.");
  process.exit(2);
}

console.log(`staff-edward eval: ${cases.length} case(s)`);
const transcripts = await runStaffCases(cases, {
  onProgress: (record) => {
    const marker = record.passed ? "PASS" : "FAIL";
    console.log(`  ${marker}  ${record.id}`);
    if (!record.passed && args.verbose) {
      for (const turn of record.turns) {
        for (const failure of turn.failures) {
          console.log(`        ${turn.question}`);
          console.log(`          ${failure.kind}: ${failure.detail}`);
        }
      }
    }
  },
});

const passed = transcripts.filter((record) => record.passed);
const criticalFailures = transcripts.filter(
  (record) => record.critical && !record.passed,
);
const totalTurns = transcripts.reduce((sum, record) => sum + record.turns.length, 0);
const failedChecks = transcripts.flatMap((record) =>
  record.turns.flatMap((turn) =>
    turn.failures.map((failure) => ({ id: record.id, ...failure })),
  ),
);

const summary = {
  batch: args.batch,
  generatedAt: new Date().toISOString(),
  cases: transcripts.length,
  turns: totalTurns,
  passedCases: passed.length,
  passRate: Number((passed.length / transcripts.length).toFixed(3)),
  criticalFailures: criticalFailures.map((record) => record.id),
  failureKinds: failedChecks.reduce((acc, failure) => {
    acc[failure.kind] = (acc[failure.kind] ?? 0) + 1;
    return acc;
  }, {}),
};

const outDir = join(artifacts, "runs", args.batch);
mkdirSync(outDir, { recursive: true });
writeFileSync(join(outDir, "transcript.json"), JSON.stringify(transcripts, null, 2));
writeFileSync(join(outDir, "summary.json"), JSON.stringify(summary, null, 2));

console.log(
  `\n${passed.length}/${transcripts.length} cases passed ` +
    `(${totalTurns} turns). Artifacts: artifacts/runs/${args.batch}/`,
);
if (Object.keys(summary.failureKinds).length > 0) {
  console.log("failure kinds:", summary.failureKinds);
}
if (criticalFailures.length > 0) {
  console.error(`CRITICAL failures: ${summary.criticalFailures.join(", ")}`);
  process.exit(1);
}
