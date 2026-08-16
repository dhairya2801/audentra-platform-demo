/**
 * Cost and latency accounting for a run.
 *
 * Model calls made inside the Python process (planner + composer) are read
 * from each turn's trace `modelCalls`, which carry provider-reported usage
 * per operation — so planner tokens are finally part of the bill. Judge calls
 * are metered by the harness ledger and passed in separately. Latency comes
 * from the trace's own turn duration (server-side) plus the runner's
 * client-observed round-trip.
 */

import { priceUsd } from "./pricing.mjs";

export function percentile(sorted, fraction) {
  if (sorted.length === 0) return null;
  const index = Math.min(
    sorted.length - 1,
    Math.max(0, Math.ceil(fraction * sorted.length) - 1),
  );
  return sorted[index];
}

const emptyOperation = () => ({
  calls: 0,
  promptTokens: 0,
  completionTokens: 0,
  usd: 0,
});

/**
 * @param {Array} records graded case records (each with .turns)
 * @param {{calls:number, promptTokens:number, completionTokens:number, usd:number}} judgeTotals
 */
export function accountRun(records, judgeTotals) {
  const operations = {
    planner: emptyOperation(),
    composer: emptyOperation(),
    judge: { ...emptyOperation(), ...(judgeTotals ?? {}) },
  };
  const serverLatencies = [];
  const clientLatencies = [];
  let turnCount = 0;

  for (const record of records) {
    for (const turn of record.turns ?? []) {
      if (turn.error) continue;
      turnCount += 1;
      const trace = turn.response?.trace;
      if (typeof trace?.durationMs === "number") {
        serverLatencies.push(trace.durationMs);
      }
      if (typeof turn.latencyMs === "number") {
        clientLatencies.push(turn.latencyMs);
      }
      for (const call of trace?.modelCalls ?? []) {
        const bucket =
          call.operation === "assistant_planner"
            ? operations.planner
            : call.operation === "assistant_composer"
              ? operations.composer
              : null;
        if (!bucket) continue;
        bucket.calls += 1;
        const usage = call.usage ?? {};
        const prompt = usage.promptTokens ?? 0;
        const completion = usage.completionTokens ?? 0;
        bucket.promptTokens += prompt;
        bucket.completionTokens += completion;
        bucket.usd += priceUsd(call.model ?? "gpt-4o-mini", prompt, completion);
      }
    }
  }

  serverLatencies.sort((a, b) => a - b);
  clientLatencies.sort((a, b) => a - b);
  const totalUsd =
    operations.planner.usd + operations.composer.usd + operations.judge.usd;

  const round = (value) =>
    value === null ? null : Number(value.toFixed ? value.toFixed(1) : value);

  return {
    turns: turnCount,
    operations: Object.fromEntries(
      Object.entries(operations).map(([name, bucket]) => [
        name,
        { ...bucket, usd: Number(bucket.usd.toFixed(6)) },
      ]),
    ),
    totalUsd: Number(totalUsd.toFixed(6)),
    usdPerTurn: turnCount > 0 ? Number((totalUsd / turnCount).toFixed(6)) : null,
    latencyMs: {
      server: {
        mean: round(
          serverLatencies.length
            ? serverLatencies.reduce((a, b) => a + b, 0) / serverLatencies.length
            : null,
        ),
        p50: percentile(serverLatencies, 0.5),
        p95: percentile(serverLatencies, 0.95),
      },
      client: {
        mean: round(
          clientLatencies.length
            ? clientLatencies.reduce((a, b) => a + b, 0) / clientLatencies.length
            : null,
        ),
        p50: percentile(clientLatencies, 0.5),
        p95: percentile(clientLatencies, 0.95),
      },
    },
  };
}
