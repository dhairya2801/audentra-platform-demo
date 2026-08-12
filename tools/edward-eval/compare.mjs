#!/usr/bin/env node
/**
 * Compare two evaluation runs case by case.
 *
 *   node tools/edward-eval/compare.mjs baseline round3
 *
 * The point is the per-case view, not the headline. Two runs can post the same
 * mean while a category quietly collapses, and a mean built from different case
 * counts is not a comparison at all — so only cases present in both runs are
 * compared, and the count that were compared is always printed.
 */
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const [beforeName = "baseline", afterName = "round2"] = process.argv.slice(2);

const load = (name) => {
  for (const candidate of [
    join(repoRoot, "artifacts", name, "transcript.json"),
    join(repoRoot, "artifacts", "runs", name, "transcript.json"),
  ]) {
    try {
      return JSON.parse(readFileSync(candidate, "utf8"));
    } catch {
      /* try the next location */
    }
  }
  throw new Error(`No transcript found for "${name}"`);
};

const before = load(beforeName);
const after = load(afterName);

const index = (rows) => new Map(rows.map((row) => [row.id, row]));
const beforeById = index(before);
const afterById = index(after);
const sharedIds = [...beforeById.keys()].filter((id) => afterById.has(id));

const score = (row) =>
  row?.judgement ? row.judgement.total / row.judgement.maximum : null;

const categories = new Map();
for (const id of sharedIds) {
  const b = beforeById.get(id);
  const a = afterById.get(id);
  const bucket = categories.get(b.category) ?? {
    total: 0,
    beforePass: 0,
    afterPass: 0,
    beforeScore: 0,
    afterScore: 0,
    judged: 0,
    regressions: [],
    fixes: [],
  };
  bucket.total += 1;
  if (b.deterministicPass) bucket.beforePass += 1;
  if (a.deterministicPass) bucket.afterPass += 1;
  if (b.deterministicPass && !a.deterministicPass) {
    bucket.regressions.push({
      id,
      question: b.question,
      reason: [...(a.contractFailures ?? []), ...(a.checkFailures ?? [])].join("; "),
    });
  }
  if (!b.deterministicPass && a.deterministicPass) {
    bucket.fixes.push({ id, question: b.question });
  }
  const bs = score(b);
  const as = score(a);
  if (bs !== null && as !== null) {
    bucket.judged += 1;
    bucket.beforeScore += bs;
    bucket.afterScore += as;
  }
  categories.set(b.category, bucket);
}

const rows = [...categories.entries()].map(([category, bucket]) => ({
  category,
  cases: bucket.total,
  before: `${bucket.beforePass}/${bucket.total}`,
  after: `${bucket.afterPass}/${bucket.total}`,
  beforeQuality:
    bucket.judged > 0 ? (bucket.beforeScore / bucket.judged) * 100 : null,
  afterQuality:
    bucket.judged > 0 ? (bucket.afterScore / bucket.judged) * 100 : null,
  regressions: bucket.regressions,
  fixes: bucket.fixes,
}));

const lines = [];
lines.push(`# Regression comparison: ${beforeName} → ${afterName}`);
lines.push("");
lines.push(
  `${sharedIds.length} cases present in both runs (${before.length} in ${beforeName}, ${after.length} in ${afterName}). Only these are compared.`,
);
lines.push("");
lines.push("| Category | Cases | Deterministic before | after | Quality before | after | Δ |");
lines.push("|---|---|---|---|---|---|---|");
for (const row of rows) {
  const delta =
    row.beforeQuality !== null && row.afterQuality !== null
      ? (row.afterQuality - row.beforeQuality).toFixed(1)
      : "–";
  lines.push(
    `| ${row.category} | ${row.cases} | ${row.before} | ${row.after} | ${
      row.beforeQuality === null ? "–" : `${row.beforeQuality.toFixed(1)}%`
    } | ${row.afterQuality === null ? "–" : `${row.afterQuality.toFixed(1)}%`} | ${delta} |`,
  );
}

const allRegressions = rows.flatMap((row) => row.regressions);
const allFixes = rows.flatMap((row) => row.fixes);
lines.push("");
lines.push(
  allRegressions.length === 0
    ? "**No deterministic regressions.** Every case that passed before still passes."
    : `**${allRegressions.length} deterministic regression(s):**`,
);
for (const item of allRegressions) {
  lines.push(`- \`${item.id}\` "${item.question}" — ${item.reason}`);
}
if (allFixes.length > 0) {
  lines.push("");
  lines.push(`**${allFixes.length} case(s) newly passing:**`);
  for (const item of allFixes) {
    lines.push(`- \`${item.id}\` "${item.question}"`);
  }
}

const output = `${lines.join("\n")}\n`;
const target = join(repoRoot, "artifacts", "regression-comparison.md");
mkdirSync(dirname(target), { recursive: true });
writeFileSync(target, output);
console.log(output);
console.log(`written to artifacts/regression-comparison.md`);
if (allRegressions.length > 0) process.exitCode = 1;
