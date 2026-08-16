#!/usr/bin/env node
/**
 * Inspect one (or more) graded cases from a stored batch — no model calls,
 * no re-running, free.
 *
 *   node tools/edward-eval/inspect.mjs --batch python-edward-baseline-4 --id xd-001
 *   node tools/edward-eval/inspect.mjs --batch smoke --failures
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { deriveFacts } from "./src/facts.mjs";
import { printInspection } from "./src/inspect-view.mjs";

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), "..", "..");

function parseArgs(argv) {
  const args = { batch: null, ids: [], failures: false };
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    if (flag === "--batch") args.batch = argv[++index];
    else if (flag === "--id" || flag === "--ids") args.ids.push(...argv[++index].split(","));
    else if (flag === "--failures") args.failures = true;
    else {
      console.error(`Unknown flag ${flag}`);
      process.exit(2);
    }
  }
  return args;
}

const args = parseArgs(process.argv.slice(2));
if (!args.batch || (args.ids.length === 0 && !args.failures)) {
  console.error(
    "Usage: inspect.mjs --batch <name> (--id <case-id>[,<case-id>…] | --failures)",
  );
  process.exit(2);
}

const directory = join(repoRoot, "artifacts", "runs", args.batch);
const transcript = JSON.parse(readFileSync(join(directory, "transcript.json"), "utf8"));
let snapshots = {};
try {
  snapshots = JSON.parse(readFileSync(join(directory, "snapshots.json"), "utf8"));
} catch {
  /* older batches carry no snapshots; the state line will show as absent */
}

const wanted = args.failures
  ? transcript.filter((record) => !record.deterministicPass)
  : transcript.filter((record) => args.ids.includes(record.id));

if (wanted.length === 0) {
  console.error(
    args.failures
      ? "No deterministic failures in this batch."
      : `No case matching [${args.ids.join(", ")}] in batch ${args.batch}.`,
  );
  process.exit(1);
}

/** Pre-v2 batches stored flat single-turn records; lift them into turn shape. */
function liftLegacy(record) {
  if (Array.isArray(record.turns)) return record;
  return {
    tags: [],
    history: record.history ?? [],
    capability: record.category,
    ...record,
    turns: [
      {
        question: record.question,
        answer: record.answer,
        response: record.response,
        requestId: record.requestId,
        latencyMs: null,
        error: record.error ?? null,
        contractFailures: record.contractFailures ?? [],
        checkFailures: (record.checkFailures ?? []).map((failure) => ({
          check: { kind: "legacy" },
          failure,
        })),
        toolCodes: [],
        judgement: record.judgement ?? null,
        taxonomy: [],
      },
    ],
  };
}

for (const record of wanted) {
  const snapshot = snapshots[record.persona];
  printInspection(liftLegacy(record), snapshot ? deriveFacts(snapshot) : null);
}
