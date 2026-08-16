#!/usr/bin/env node
/**
 * Judge calibration: compare the model judge against hand labels.
 *
 *   node tools/edward-eval/calibrate.mjs                       # labeled batch, v2 labels
 *   node tools/edward-eval/calibrate.mjs --batch other-batch
 *   node tools/edward-eval/calibrate.mjs --sample 40           # print a stratified sample to label
 *
 * Reports per-dimension exact and within-one agreement, overall verdict
 * agreement, and — most importantly — whether the judge catches the answers
 * a human rejected. Costs nothing to run.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  HAND_LABELS_V2,
  LABELED_BATCH,
  verdictFromScore,
} from "./src/hand-labels-v2.mjs";

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), "..", "..");

function parseArgs(argv) {
  const args = { batch: LABELED_BATCH, sample: 0 };
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    if (flag === "--batch") args.batch = argv[++index];
    else if (flag === "--sample") args.sample = Number(argv[++index]);
    else if (!flag.startsWith("--")) args.batch = flag;
  }
  return args;
}

const args = parseArgs(process.argv.slice(2));
const transcript = JSON.parse(
  readFileSync(join(repoRoot, "artifacts", "runs", args.batch, "transcript.json"), "utf8"),
);

/** Every judged turn keyed the way hand labels reference them. */
function judgedTurns() {
  const rows = [];
  for (const record of transcript) {
    const turns = Array.isArray(record.turns)
      ? record.turns
      : [{ question: record.question, answer: record.answer, judgement: record.judgement }];
    turns.forEach((turn, index) => {
      if (!turn.judgement?.scores) return;
      rows.push({
        key: index === 0 ? record.id : `${record.id}#t${index + 1}`,
        record,
        turn,
      });
    });
  }
  return rows;
}

const turns = judgedTurns();

if (args.sample > 0) {
  // Stratified sample for labeling: spread across categories, biased toward
  // low scores (disagreement lives there) but including clean answers.
  const byCategory = new Map();
  for (const row of turns) {
    const list = byCategory.get(row.record.category) ?? [];
    list.push(row);
    byCategory.set(row.record.category, list);
  }
  const picked = [];
  const perCategory = Math.max(1, Math.ceil(args.sample / byCategory.size));
  for (const list of byCategory.values()) {
    const sorted = [...list].sort(
      (a, b) =>
        a.turn.judgement.total / a.turn.judgement.maximum -
        b.turn.judgement.total / b.turn.judgement.maximum,
    );
    picked.push(...sorted.slice(0, Math.ceil(perCategory / 2)));
    picked.push(...sorted.slice(-Math.floor(perCategory / 2)));
  }
  const unique = [...new Map(picked.map((row) => [row.key, row])).values()].slice(0, args.sample);
  for (const row of unique) {
    console.log(`--- ${row.key} [${row.record.category}/${row.record.persona}] judge ${row.turn.judgement.total}/${row.turn.judgement.maximum}`);
    console.log(`Q: ${row.turn.question}`);
    console.log(`A: ${row.turn.answer}\n`);
  }
  console.log(`${unique.length} turns sampled for labeling (of ${turns.length} judged).`);
  process.exit(0);
}

const labels = Object.entries(HAND_LABELS_V2);
if (labels.length === 0) {
  console.error(
    "No hand labels yet. Run with --sample 40, label the sample in src/hand-labels-v2.mjs, then re-run.",
  );
  process.exit(1);
}

const byKey = new Map(turns.map((row) => [row.key, row]));
const perDimension = new Map();
let verdictAgree = 0;
let verdictTotal = 0;
const handBad = [];
const disagreements = [];

for (const [key, label] of labels) {
  const row = byKey.get(key);
  if (!row) continue;
  const judgeVerdict = verdictFromScore(row.turn.judgement.total, row.turn.judgement.maximum);
  verdictTotal += 1;
  if (judgeVerdict === label.verdict) verdictAgree += 1;
  if (label.verdict === "bad") handBad.push({ key, judgeVerdict });
  for (const [dimension, handScore] of Object.entries(label.dimensions ?? {})) {
    const judgeScore = row.turn.judgement.scores[dimension];
    if (judgeScore === undefined) continue;
    const entry = perDimension.get(dimension) ?? { total: 0, exact: 0, adjacent: 0, lenient: 0, harsh: 0 };
    entry.total += 1;
    if (judgeScore === handScore) entry.exact += 1;
    if (Math.abs(judgeScore - handScore) <= 1) entry.adjacent += 1;
    if (judgeScore > handScore) {
      entry.lenient += 1;
      disagreements.push({ key, dimension, hand: handScore, judge: judgeScore });
    }
    if (judgeScore < handScore) {
      entry.harsh += 1;
      disagreements.push({ key, dimension, hand: handScore, judge: judgeScore });
    }
    perDimension.set(dimension, entry);
  }
}

const pct = (part, total) => (total === 0 ? "–" : `${((part / total) * 100).toFixed(0)}%`);

console.log(`calibration against batch "${args.batch}" — ${verdictTotal} hand-labelled turns\n`);
console.log("per-dimension agreement (exact / within one; lenient = judge scored higher than human):");
for (const [dimension, entry] of [...perDimension.entries()].sort()) {
  console.log(
    `  ${dimension.padEnd(22)} exact ${pct(entry.exact, entry.total).padStart(4)}  ±1 ${pct(entry.adjacent, entry.total).padStart(4)}  lenient ${entry.lenient}  harsh ${entry.harsh}  (n=${entry.total})`,
  );
}
console.log(`\noverall verdict (good/weak/bad) agreement: ${verdictAgree}/${verdictTotal} (${pct(verdictAgree, verdictTotal)})`);
const caught = handBad.filter((row) => row.judgeVerdict !== "good").length;
console.log(`answers a human rejected: ${handBad.length}; judge scored non-good on ${caught} of them`);
if (disagreements.length > 0) {
  console.log("\ndisagreements:");
  for (const item of disagreements.sort((a, b) => a.key.localeCompare(b.key))) {
    console.log(
      `  ${item.key.padEnd(14)} ${item.dimension.padEnd(22)} hand ${item.hand}  judge ${item.judge}  (${item.judge > item.hand ? "judge too lenient" : "judge too harsh"})`,
    );
  }
}
