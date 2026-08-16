/**
 * Run summary construction and terminal rendering, shared by run.mjs and
 * regrade.mjs.
 */

import { JUDGE_DIMENSIONS } from "./judge.mjs";

function bucketInit() {
  return { cases: 0, pass: 0, judgedTurns: 0, scoreSum: 0, scoreMax: 0 };
}

function bucketAdd(bucket, record) {
  bucket.cases += 1;
  if (record.deterministicPass) bucket.pass += 1;
  for (const turn of record.turns) {
    if (turn.judgement) {
      bucket.judgedTurns += 1;
      bucket.scoreSum += turn.judgement.total;
      bucket.scoreMax += turn.judgement.maximum;
    }
  }
}

function bucketFinish(bucket) {
  return {
    ...bucket,
    meanScorePercent:
      bucket.scoreMax > 0 ? Number(((bucket.scoreSum / bucket.scoreMax) * 100).toFixed(1)) : null,
  };
}

function groupBy(records, keyFor) {
  const groups = {};
  for (const record of records) {
    for (const key of [keyFor(record)].flat().filter(Boolean)) {
      bucketAdd((groups[key] ??= bucketInit()), record);
    }
  }
  return Object.fromEntries(
    Object.entries(groups).map(([key, bucket]) => [key, bucketFinish(bucket)]),
  );
}

export function summarise(records, batch, accountingResult) {
  const allTurns = records.flatMap((record) => record.turns);
  const judgedTurns = allTurns.filter((turn) => turn.judgement);

  const dimensionTotals = {};
  for (const turn of judgedTurns) {
    for (const [dimension, score] of Object.entries(turn.judgement.scores)) {
      const entry = (dimensionTotals[dimension] ??= { sum: 0, count: 0 });
      entry.sum += score;
      entry.count += 1;
    }
  }

  const taxonomyCounts = {};
  for (const turn of allTurns) {
    for (const code of new Set((turn.taxonomy ?? []).map((entry) => entry.code))) {
      taxonomyCounts[code] = (taxonomyCounts[code] ?? 0) + 1;
    }
  }

  const turnsWithExpect = allTurns.filter((turn) => turn.expect && !turn.error);
  const cleanToolTurns = turnsWithExpect.filter((turn) => (turn.toolCodes ?? []).length === 0);
  const unnecessary = allTurns.filter((turn) =>
    (turn.taxonomy ?? []).some((entry) => entry.code === "UNNECESSARY_TOOL"),
  );
  const hallucinated = allTurns.filter((turn) =>
    (turn.taxonomy ?? []).some((entry) => entry.code === "HALLUCINATION"),
  );
  const executedCounts = allTurns
    .filter((turn) => turn.toolStats)
    .map((turn) => turn.toolStats.executed);

  return {
    batch,
    generatedAt: new Date().toISOString(),
    cases: records.length,
    turns: allTurns.length,
    deterministicPass: records.filter((record) => record.deterministicPass).length,
    deterministicFail: records.filter((record) => !record.deterministicPass).length,
    judgedTurns: judgedTurns.length,
    meanScorePercent:
      judgedTurns.length > 0
        ? Number(
            (
              (judgedTurns.reduce((sum, turn) => sum + turn.judgement.total, 0) /
                judgedTurns.reduce((sum, turn) => sum + turn.judgement.maximum, 0)) *
              100
            ).toFixed(1),
          )
        : null,
    byDimension: Object.fromEntries(
      JUDGE_DIMENSIONS.map((dimension) => [
        dimension.key,
        dimensionTotals[dimension.key]
          ? Number(
              (dimensionTotals[dimension.key].sum / dimensionTotals[dimension.key].count).toFixed(2),
            )
          : null,
      ]),
    ),
    byCategory: groupBy(records, (record) => record.category),
    byCapability: groupBy(records, (record) => record.capability),
    byPersona: groupBy(records, (record) => record.persona),
    singleVsMultiTurn: groupBy(records, (record) =>
      record.turns.length > 1 ? "multi_turn" : "single_turn",
    ),
    crossDomain: groupBy(records, (record) =>
      record.tags.includes("cross_domain") ? "cross_domain" : "single_domain",
    ),
    toolSelection: {
      turnsWithExpectations: turnsWithExpect.length,
      cleanTurns: cleanToolTurns.length,
      accuracyPercent:
        turnsWithExpect.length > 0
          ? Number(((cleanToolTurns.length / turnsWithExpect.length) * 100).toFixed(1))
          : null,
      unnecessaryToolRatePercent: Number(
        ((unnecessary.length / Math.max(1, allTurns.length)) * 100).toFixed(1),
      ),
      meanExecutedTools:
        executedCounts.length > 0
          ? Number(
              (executedCounts.reduce((a, b) => a + b, 0) / executedCounts.length).toFixed(2),
            )
          : null,
      dependencyRoundTurns: allTurns.filter((turn) => turn.toolStats?.dependencyRound > 0).length,
    },
    hallucinationRatePercent: Number(
      ((hallucinated.length / Math.max(1, allTurns.length)) * 100).toFixed(1),
    ),
    taxonomyCounts,
    accounting: accountingResult,
    criticalFailures: records
      .filter((record) => record.critical && !record.deterministicPass)
      .map((record) => record.id),
  };
}

export function printSummary(value, records) {
  console.log("\n=== summary ===");
  console.log(
    `cases ${value.cases} (${value.turns} turns) | deterministic pass ${value.deterministicPass}/${value.cases} | judged turns ${value.judgedTurns} | mean quality ${value.meanScorePercent ?? "-"}%`,
  );
  console.log(
    `tool selection ${value.toolSelection.accuracyPercent ?? "-"}% clean (${value.toolSelection.cleanTurns}/${value.toolSelection.turnsWithExpectations}) | unnecessary-tool ${value.toolSelection.unnecessaryToolRatePercent}% | hallucination ${value.hallucinationRatePercent}% | avg tools/turn ${value.toolSelection.meanExecutedTools ?? "-"}`,
  );
  const { accounting } = value;
  console.log(
    `latency p50 ${accounting.latencyMs.server.p50 ?? "-"}ms p95 ${accounting.latencyMs.server.p95 ?? "-"}ms (server) | cost $${accounting.totalUsd} (planner $${accounting.operations.planner.usd}, composer $${accounting.operations.composer.usd}, judge $${accounting.operations.judge.usd}) | $${accounting.usdPerTurn ?? "-"}/turn`,
  );
  console.log(
    `spend: batch $${value.spend.batchUsd.toFixed(4)} | cumulative $${value.spend.cumulativeUsd.toFixed(4)} | remaining $${value.spend.remainingUsd.toFixed(4)}`,
  );
  console.log("\nby dimension (mean of 2):");
  for (const [key, mean] of Object.entries(value.byDimension)) {
    if (mean !== null) console.log(`  ${key.padEnd(22)} ${mean}`);
  }
  console.log("\nby category:");
  for (const [name, bucket] of Object.entries(value.byCategory)) {
    console.log(
      `  ${name.padEnd(20)} pass ${String(bucket.pass).padStart(3)}/${String(bucket.cases).padEnd(4)} quality ${bucket.meanScorePercent ?? "-"}%`,
    );
  }
  if (Object.keys(value.taxonomyCounts).length > 0) {
    console.log("\nfailure taxonomy (turns affected):");
    for (const [code, count] of Object.entries(value.taxonomyCounts).sort((a, b) => b[1] - a[1])) {
      console.log(`  ${code.padEnd(32)} ${count}`);
    }
  }
  const failures = records.filter((record) => !record.deterministicPass);
  if (failures.length > 0) {
    console.log(`\ndeterministic failures (${failures.length}):`);
    for (const failure of failures.slice(0, 40)) {
      const reasons = failure.turns
        .flatMap((turn) => [
          ...(turn.contractFailures ?? []),
          ...(turn.checkFailures ?? []).map((entry) => entry.failure),
          ...(turn.toolCodes ?? []).map((code) => `${code.code}: ${code.detail}`),
          turn.error,
        ])
        .filter(Boolean);
      console.log(`  [${failure.id}] ${failure.turns[0].question}\n      ${reasons.join("; ")}`);
    }
    if (failures.length > 40) console.log(`  … and ${failures.length - 40} more`);
  }
  console.log(`\nwritten to artifacts/runs/${value.batch}/`);
}
