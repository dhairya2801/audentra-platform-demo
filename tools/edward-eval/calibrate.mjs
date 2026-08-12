#!/usr/bin/env node
/**
 * Judge calibration: compare the model judge against hand labels.
 *
 *   node tools/edward-eval/calibrate.mjs round4
 *
 * Reports exact and adjacent agreement on the `separates_confirmed` criterion,
 * and agreement on an overall good/weak/bad verdict. Costs nothing to run.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { HAND_LABELS, verdictFromJudgeTotal } from "./src/hand-labels.mjs";

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const batch = process.argv[2] ?? "round4";
const transcript = JSON.parse(
  readFileSync(join(repoRoot, "artifacts", "runs", batch, "transcript.json"), "utf8"),
);

const rows = [];
for (const [id, label] of Object.entries(HAND_LABELS)) {
  const record = transcript.find((item) => item.id === id);
  if (!record?.judgement) continue;
  rows.push({
    id,
    handSeparates: label.separatesConfirmed,
    judgeSeparates: record.judgement.scores.separates_confirmed,
    handVerdict: label.verdict,
    judgeVerdict: verdictFromJudgeTotal(
      record.judgement.total,
      record.judgement.maximum,
    ),
  });
}

const exact = rows.filter((row) => row.handSeparates === row.judgeSeparates).length;
const adjacent = rows.filter(
  (row) => Math.abs(row.handSeparates - row.judgeSeparates) <= 1,
).length;
const verdictAgree = rows.filter((row) => row.handVerdict === row.judgeVerdict).length;

// Does the judge at least catch the answers a human would reject?
const handBad = rows.filter((row) => row.handVerdict === "bad");
const caught = handBad.filter((row) => row.judgeVerdict !== "good").length;

console.log(`calibration against batch "${batch}" — ${rows.length} hand-labelled cases\n`);
console.log("separates_confirmed (0/1/2):");
console.log(`  exact agreement    ${exact}/${rows.length}  (${pct(exact, rows.length)}%)`);
console.log(`  within one point   ${adjacent}/${rows.length}  (${pct(adjacent, rows.length)}%)`);
console.log("\noverall verdict (good/weak/bad):");
console.log(`  exact agreement    ${verdictAgree}/${rows.length}  (${pct(verdictAgree, rows.length)}%)`);
console.log(
  `\nanswers a human rejected: ${handBad.length}; judge scored non-good on ${caught} of them`,
);
console.log("\ndisagreements on separates_confirmed:");
for (const row of rows) {
  if (row.handSeparates === row.judgeSeparates) continue;
  console.log(
    `  ${row.id.padEnd(5)} hand ${row.handSeparates}  judge ${row.judgeSeparates}  (${row.judgeSeparates > row.handSeparates ? "judge too lenient" : "judge too harsh"})`,
  );
}

function pct(part, total) {
  return total === 0 ? "0" : ((part / total) * 100).toFixed(0);
}
