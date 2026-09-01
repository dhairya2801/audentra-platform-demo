#!/usr/bin/env node
/**
 * Export the write-ability question bank as one flat CSV.
 *
 * Merges a development batch and a holdout batch into
 * `docs/edward-write-eval-question-bank.csv`: one row per user turn, carrying
 * the question, the expected answer in prose, and what Edward actually said on
 * that run.
 *
 * The bank is generated rather than maintained by hand so the three columns
 * can never drift apart — the question and the expected answer come from the
 * case module, the actual answer from the transcript of the run that graded
 * it, and the grade from the same grader.
 *
 *   node tools/edward-eval/write/export-bank.mjs --dev write-v5 --holdout write-holdout
 */

import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(HERE, "..", "..", "..");

function parseArgs(argv) {
  const args = { dev: null, holdout: null, out: null };
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    if (flag === "--dev") args.dev = argv[++index];
    else if (flag === "--holdout") args.holdout = argv[++index];
    else if (flag === "--out") args.out = argv[++index];
    else throw new Error(`Unknown flag: ${flag}`);
  }
  if (!args.dev) throw new Error("--dev <batch> is required");
  return args;
}

function readBatch(batch) {
  if (!batch) return [];
  const path = join(REPO_ROOT, "artifacts", "runs", batch, "transcript.json");
  return JSON.parse(readFileSync(path, "utf8"));
}

function cell(value) {
  const text = String(value ?? "")
    .replace(/\r?\n/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return `"${text.replace(/"/g, '""')}"`;
}

const HEADER = [
  "id",
  "suite",
  "actor_kind",
  "actor",
  "category",
  "turn",
  "question",
  "expected_answer",
  "actual_answer",
  "action_proposed",
  "grade",
  "failures",
];

function rowsFor(results) {
  const rows = [];
  for (const result of results) {
    for (const turn of result.turns) {
      rows.push([
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
        turn.findings.map((finding) => finding.code).join(" | "),
      ]);
    }
  }
  return rows;
}

const args = parseArgs(process.argv.slice(2));
const rows = [...rowsFor(readBatch(args.dev)), ...rowsFor(readBatch(args.holdout))];
const csv = [HEADER, ...rows].map((row) => row.map(cell).join(",")).join("\n");
const out = args.out ?? join(REPO_ROOT, "docs", "edward-write-eval-question-bank.csv");
mkdirSync(dirname(out), { recursive: true });
writeFileSync(out, `${csv}\n`);

const grades = rows.reduce((counts, row) => {
  counts[row[10]] = (counts[row[10]] ?? 0) + 1;
  return counts;
}, {});
process.stdout.write(`${rows.length} turns → ${out}\n`);
process.stdout.write(`${JSON.stringify(grades)}\n`);
