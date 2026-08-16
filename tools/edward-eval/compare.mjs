#!/usr/bin/env node
/**
 * Compare two evaluation runs case by case.
 *
 *   node tools/edward-eval/compare.mjs --baseline python-edward-baseline-3 --candidate my-branch
 *   node tools/edward-eval/compare.mjs baseline-run candidate-run        (positional form)
 *
 * Only cases present in both runs are compared (a mean built from different
 * case counts is not a comparison), and the compared count is always printed.
 * Output: overall + per-dimension + per-category deltas, operational metric
 * deltas (tool selection, hallucination, latency, cost), every individual
 * regression, and every fix. Exits non-zero on any deterministic regression.
 */
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = join(dirname(fileURLToPath(import.meta.url)), "..", "..");

function parseArgs(argv) {
  const args = { baseline: null, candidate: null };
  const positional = [];
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    if (flag === "--baseline") args.baseline = argv[++index];
    else if (flag === "--candidate") args.candidate = argv[++index];
    else positional.push(flag);
  }
  args.baseline ??= positional[0] ?? "python-edward-baseline-3";
  args.candidate ??= positional[1] ?? "adhoc";
  return args;
}

const { baseline: baselineName, candidate: candidateName } = parseArgs(process.argv.slice(2));

const load = (name, file) => {
  for (const candidate of [
    join(repoRoot, "artifacts", "runs", name, file),
    join(repoRoot, "artifacts", name, file),
  ]) {
    try {
      return JSON.parse(readFileSync(candidate, "utf8"));
    } catch {
      /* try the next location */
    }
  }
  return null;
};

const before = load(baselineName, "transcript.json");
const after = load(candidateName, "transcript.json");
if (!before || !after) {
  console.error(
    `Missing transcript for ${!before ? baselineName : candidateName}. Run the eval first.`,
  );
  process.exit(2);
}
const beforeSummary = load(baselineName, "summary.json");
const afterSummary = load(candidateName, "summary.json");

/** v1 and v2 records both expose id/category/deterministicPass/judgement. */
const score = (row) =>
  row?.judgement && row.judgement.maximum > 0
    ? row.judgement.total / row.judgement.maximum
    : null;
const questionOf = (row) => row.question ?? row.turns?.[0]?.question ?? "";
const failuresOf = (row) => {
  if (Array.isArray(row.turns)) {
    return row.turns
      .flatMap((turn) => [
        ...(turn.contractFailures ?? []),
        ...(turn.checkFailures ?? []).map((entry) => entry.failure ?? entry),
        ...(turn.toolCodes ?? []).map((code) => `${code.code}: ${code.detail}`),
        turn.error,
      ])
      .filter(Boolean);
  }
  return [...(row.contractFailures ?? []), ...(row.checkFailures ?? []), row.error].filter(Boolean);
};

const index = (rows) => new Map(rows.map((row) => [row.id, row]));
const beforeById = index(before);
const afterById = index(after);
const sharedIds = [...beforeById.keys()].filter((id) => afterById.has(id));

const categories = new Map();
const regressions = [];
const fixes = [];
const qualityDrops = [];
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
  };
  bucket.total += 1;
  if (b.deterministicPass) bucket.beforePass += 1;
  if (a.deterministicPass) bucket.afterPass += 1;
  if (b.deterministicPass && !a.deterministicPass) {
    regressions.push({ id, question: questionOf(b), reason: failuresOf(a).join("; ") });
  }
  if (!b.deterministicPass && a.deterministicPass) {
    fixes.push({ id, question: questionOf(b) });
  }
  const bs = score(b);
  const as = score(a);
  if (bs !== null && as !== null) {
    bucket.judged += 1;
    bucket.beforeScore += bs;
    bucket.afterScore += as;
    if (as <= bs - 0.2) {
      qualityDrops.push({
        id,
        question: questionOf(b),
        before: Math.round(bs * 100),
        after: Math.round(as * 100),
      });
    }
  }
  categories.set(b.category, bucket);
}

const pct = (value) => (value === null || value === undefined ? "–" : `${value}`);
const delta = (b, a, suffix = "") =>
  b === null || b === undefined || a === null || a === undefined
    ? "–"
    : `${b}${suffix} → ${a}${suffix} (${a - b >= 0 ? "+" : ""}${Number((a - b).toFixed(2))})`;

const lines = [];
lines.push(`# Regression comparison: ${baselineName} → ${candidateName}`);
lines.push("");
lines.push(
  `${sharedIds.length} cases present in both runs (${before.length} in ${baselineName}, ${after.length} in ${candidateName}). Only these are compared.`,
);
lines.push("");

if (beforeSummary && afterSummary) {
  lines.push("## Headline metrics");
  lines.push("");
  lines.push(`- Overall quality: ${delta(beforeSummary.meanScorePercent, afterSummary.meanScorePercent, "%")}`);
  const dims = new Set([
    ...Object.keys(beforeSummary.byDimension ?? beforeSummary.byCriterion ?? {}),
    ...Object.keys(afterSummary.byDimension ?? afterSummary.byCriterion ?? {}),
  ]);
  for (const dimension of dims) {
    const b = (beforeSummary.byDimension ?? beforeSummary.byCriterion ?? {})[dimension];
    const a = (afterSummary.byDimension ?? afterSummary.byCriterion ?? {})[dimension];
    if (b !== null && b !== undefined && a !== null && a !== undefined) {
      lines.push(`- ${dimension}: ${delta(b, a)}`);
    }
  }
  if (afterSummary.toolSelection) {
    lines.push(
      `- Tool-selection accuracy: ${delta(beforeSummary.toolSelection?.accuracyPercent ?? null, afterSummary.toolSelection.accuracyPercent, "%")}`,
    );
    lines.push(
      `- Unnecessary-tool rate: ${delta(beforeSummary.toolSelection?.unnecessaryToolRatePercent ?? null, afterSummary.toolSelection.unnecessaryToolRatePercent, "%")}`,
    );
  }
  if (afterSummary.hallucinationRatePercent !== undefined) {
    lines.push(
      `- Hallucination rate: ${delta(beforeSummary.hallucinationRatePercent ?? null, afterSummary.hallucinationRatePercent, "%")}`,
    );
  }
  if (afterSummary.accounting) {
    lines.push(
      `- Latency p50/p95 (server): ${pct(beforeSummary.accounting?.latencyMs?.server?.p50)}→${pct(afterSummary.accounting.latencyMs.server.p50)}ms / ${pct(beforeSummary.accounting?.latencyMs?.server?.p95)}→${pct(afterSummary.accounting.latencyMs.server.p95)}ms`,
    );
    lines.push(
      `- Cost per turn: $${pct(beforeSummary.accounting?.usdPerTurn)} → $${pct(afterSummary.accounting.usdPerTurn)}`,
    );
  }
  lines.push("");
}

lines.push("## Per category");
lines.push("");
lines.push("| Category | Cases | Deterministic before | after | Quality before | after | Δ |");
lines.push("|---|---|---|---|---|---|---|");
for (const [category, bucket] of categories) {
  const bq = bucket.judged > 0 ? (bucket.beforeScore / bucket.judged) * 100 : null;
  const aq = bucket.judged > 0 ? (bucket.afterScore / bucket.judged) * 100 : null;
  lines.push(
    `| ${category} | ${bucket.total} | ${bucket.beforePass}/${bucket.total} | ${bucket.afterPass}/${bucket.total} | ${
      bq === null ? "–" : `${bq.toFixed(1)}%`
    } | ${aq === null ? "–" : `${aq.toFixed(1)}%`} | ${bq === null || aq === null ? "–" : (aq - bq).toFixed(1)} |`,
  );
}

lines.push("");
lines.push(
  regressions.length === 0
    ? "**No deterministic regressions.** Every case that passed before still passes."
    : `**${regressions.length} deterministic regression(s):**`,
);
for (const item of regressions) {
  lines.push(`- \`${item.id}\` "${item.question}" — ${item.reason}`);
}
if (qualityDrops.length > 0) {
  lines.push("");
  lines.push(`**${qualityDrops.length} case(s) with a judged-quality drop ≥ 20 points:**`);
  for (const item of qualityDrops) {
    lines.push(`- \`${item.id}\` "${item.question}" — ${item.before}% → ${item.after}%`);
  }
}
if (fixes.length > 0) {
  lines.push("");
  lines.push(`**${fixes.length} case(s) newly passing:**`);
  for (const item of fixes) {
    lines.push(`- \`${item.id}\` "${item.question}"`);
  }
}

const output = `${lines.join("\n")}\n`;
const target = join(repoRoot, "artifacts", "regression-comparison.md");
mkdirSync(dirname(target), { recursive: true });
writeFileSync(target, output);
console.log(output);
console.log(`written to artifacts/regression-comparison.md`);
if (regressions.length > 0) process.exitCode = 1;
