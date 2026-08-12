import assert from "node:assert/strict";
import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, it } from "node:test";
import {
  ABSOLUTE_CEILING_USD,
  DEFAULT_RETRY,
  MODEL_PRICING,
  RETRYABLE_STATUSES,
  SpendCeilingExceededError,
  SpendLedger,
  TRACKED_CEILING_USD,
  UnpricedModelError,
  costTrackedFetch,
  createConcurrencyLimiter,
  retryDelayMs,
} from "../src/spend-ledger.mjs";

async function ledgerFile(name = "spend.json") {
  const directory = await mkdtemp(join(tmpdir(), "edward-spend-"));
  return join(directory, name);
}

function completion({ model = "gpt-4o-mini-2024-07-18", promptTokens, completionTokens }) {
  return new Response(
    JSON.stringify({
      model,
      choices: [{ message: { content: "{}" } }],
      usage: {
        prompt_tokens: promptTokens,
        completion_tokens: completionTokens,
        total_tokens: promptTokens + completionTokens,
      },
    }),
    { status: 200, headers: { "content-type": "application/json" } },
  );
}

const chatUrl = "https://api.openai.com/v1/chat/completions";
const request = (model = "gpt-4o-mini") => ({
  method: "POST",
  body: JSON.stringify({ model, messages: [] }),
});

describe("spend ledger", () => {
  it("prices gpt-4o-mini usage and persists cumulative spend across processes", async () => {
    const file = await ledgerFile();
    const first = new SpendLedger({ file });
    const usd = first.record({
      model: "gpt-4o-mini",
      promptTokens: 1_000_000,
      completionTokens: 1_000_000,
    });

    assert.equal(usd, MODEL_PRICING["gpt-4o-mini"].input * 1_000_000 + MODEL_PRICING["gpt-4o-mini"].output * 1_000_000);
    assert.equal(Number(usd.toFixed(4)), 0.75);

    const reloaded = new SpendLedger({ file });
    assert.equal(Number(reloaded.totalUsd.toFixed(4)), 0.75);
    assert.equal(reloaded.totalCalls, 1);

    const persisted = JSON.parse(await readFile(file, "utf8"));
    assert.equal(persisted.totalCalls, 1);
    assert.equal(persisted.promptTokens, 1_000_000);
  });

  it("tracks per-batch spend separately from the cumulative total", async () => {
    const file = await ledgerFile();
    const ledger = new SpendLedger({ file, batch: "batch-1" });
    ledger.record({ model: "gpt-4o-mini", promptTokens: 1_000, completionTokens: 100 });
    ledger.record({
      model: "gpt-4o-mini",
      promptTokens: 2_000,
      completionTokens: 200,
      batch: "batch-2",
    });

    assert.equal(ledger.summary().batches["batch-1"].calls, 1);
    assert.equal(ledger.summary().batches["batch-2"].calls, 1);
    assert.ok(ledger.batchSpend("batch-2") > ledger.batchSpend("batch-1"));
    assert.equal(ledger.totalCalls, 2);
  });

  it("hard-aborts before issuing a call once the ceiling is reached", async () => {
    const file = await ledgerFile();
    const ledger = new SpendLedger({ file, ceilingUsd: 0.001 });
    let calls = 0;
    const tracked = costTrackedFetch(ledger, {
      fetch: async () => {
        calls += 1;
        return completion({ promptTokens: 10_000, completionTokens: 1_000 });
      },
    });

    await tracked(chatUrl, request());
    assert.equal(calls, 1);
    assert.ok(ledger.totalUsd >= 0.001, "first call should exhaust the tiny ceiling");

    await assert.rejects(
      () => tracked(chatUrl, request()),
      SpendCeilingExceededError,
    );
    assert.equal(calls, 1, "no provider request may be issued after the ceiling");
  });

  it("refuses a model that is not on the priced allowlist", async () => {
    const file = await ledgerFile();
    const ledger = new SpendLedger({ file });
    let calls = 0;
    const tracked = costTrackedFetch(ledger, {
      fetch: async () => {
        calls += 1;
        return completion({ promptTokens: 10, completionTokens: 10 });
      },
    });

    await assert.rejects(() => tracked(chatUrl, request("gpt-4o")), UnpricedModelError);
    await assert.rejects(() => tracked(chatUrl, request("gpt-5")), UnpricedModelError);
    assert.equal(calls, 0, "an unpriced model must never reach the provider");
  });

  it("records provider-reported usage and leaves the response body readable", async () => {
    const file = await ledgerFile();
    const ledger = new SpendLedger({ file });
    const observed = [];
    const tracked = costTrackedFetch(ledger, {
      fetch: async () => completion({ promptTokens: 1_200, completionTokens: 80 }),
      onCall: (event) => observed.push(event),
    });

    const response = await tracked(chatUrl, request());
    const payload = await response.json();

    assert.equal(payload.usage.prompt_tokens, 1_200);
    assert.equal(observed.length, 1);
    assert.equal(observed[0].promptTokens, 1_200);
    assert.equal(
      Number(ledger.totalUsd.toFixed(8)),
      Number((1_200 * 0.15e-6 + 80 * 0.6e-6).toFixed(8)),
    );
  });

  it("does not meter or gate non-completion requests", async () => {
    const file = await ledgerFile();
    const ledger = new SpendLedger({ file, ceilingUsd: 0 });
    const tracked = costTrackedFetch(ledger, {
      fetch: async () => new Response("ok", { status: 200 }),
    });

    const response = await tracked("https://example.test/health", { method: "GET" });
    assert.equal(await response.text(), "ok");
    assert.equal(ledger.totalCalls, 0);
  });

  it("charges the worst known rate for an unrecognised model rather than nothing", async () => {
    const file = await ledgerFile();
    const ledger = new SpendLedger({ file });
    const usd = ledger.record({
      model: "some-unknown-snapshot",
      promptTokens: 1_000,
      completionTokens: 1_000,
    });
    assert.ok(usd > 0, "unknown models must still cost something");
  });

  it("stops at the configured maximum call count", async () => {
    const file = await ledgerFile();
    const ledger = new SpendLedger({ file, maxCalls: 2 });
    const tracked = costTrackedFetch(ledger, {
      fetch: async () => completion({ promptTokens: 1, completionTokens: 1 }),
    });

    await tracked(chatUrl, request());
    await tracked(chatUrl, request());
    await assert.rejects(() => tracked(chatUrl, request()), SpendCeilingExceededError);
  });

  it("defaults to the $0.85 tracked ceiling with a $1.00 absolute backstop", async () => {
    const file = await ledgerFile();
    const ledger = new SpendLedger({ file });

    assert.equal(TRACKED_CEILING_USD, 0.85);
    assert.equal(ABSOLUTE_CEILING_USD, 1);
    assert.equal(ledger.ceilingUsd, 0.85);
    assert.equal(ledger.absoluteCeilingUsd, 1);
    assert.ok(
      ABSOLUTE_CEILING_USD - TRACKED_CEILING_USD > 0.1,
      "the backstop must leave more headroom than any single call can consume",
    );
  });

  it("refuses further calls on the absolute backstop even if the tracked ceiling is raised", async () => {
    const file = await ledgerFile();
    const ledger = new SpendLedger({
      file,
      ceilingUsd: 100,
      absoluteCeilingUsd: 0.001,
    });
    let calls = 0;
    const tracked = costTrackedFetch(ledger, {
      fetch: async () => {
        calls += 1;
        return completion({ promptTokens: 10_000, completionTokens: 1_000 });
      },
    });

    await tracked(chatUrl, request());
    const error = await tracked(chatUrl, request()).catch((cause) => cause);

    assert.ok(error instanceof SpendCeilingExceededError);
    assert.equal(error.kind, "absolute");
    assert.equal(calls, 1, "the absolute backstop must block the provider request");
  });
});

describe("rate limit handling", () => {
  const rateLimited = (retryAfter) =>
    new Response(JSON.stringify({ error: { message: "slow down" } }), {
      status: 429,
      headers: retryAfter ? { "retry-after": String(retryAfter) } : {},
    });

  it("retries 429 and 5xx with backoff and returns the eventual success", async () => {
    const file = await ledgerFile();
    const ledger = new SpendLedger({ file });
    const statuses = [429, 503, 500];
    const slept = [];
    let calls = 0;
    const tracked = costTrackedFetch(ledger, {
      fetch: async () => {
        calls += 1;
        const status = statuses.shift();
        return status
          ? new Response("{}", { status })
          : completion({ promptTokens: 100, completionTokens: 10 });
      },
      sleep: async (ms) => slept.push(ms),
    });

    const response = await tracked(chatUrl, request());

    assert.equal(response.status, 200);
    assert.equal(calls, 4, "three failures then one success");
    assert.equal(slept.length, 3, "each failure backs off before the next attempt");
    assert.equal(ledger.totalCalls, 1, "only the attempt that reported usage is billed");
  });

  it("honours Retry-After rather than its own backoff curve", async () => {
    const file = await ledgerFile();
    const ledger = new SpendLedger({ file });
    const slept = [];
    let first = true;
    const tracked = costTrackedFetch(ledger, {
      fetch: async () => {
        if (first) {
          first = false;
          return rateLimited(7);
        }
        return completion({ promptTokens: 10, completionTokens: 1 });
      },
      sleep: async (ms) => slept.push(ms),
    });

    await tracked(chatUrl, request());
    assert.deepEqual(slept, [7_000]);
  });

  it("caps retries and surfaces the provider's last response instead of dropping the question", async () => {
    const file = await ledgerFile();
    const ledger = new SpendLedger({ file });
    let calls = 0;
    const tracked = costTrackedFetch(ledger, {
      fetch: async () => {
        calls += 1;
        return rateLimited();
      },
      sleep: async () => {},
    });

    const response = await tracked(chatUrl, request());

    assert.equal(response.status, 429);
    assert.equal(calls, DEFAULT_RETRY.maxAttempts, "retries are capped, never unbounded");
  });

  it("stops retrying when the ceiling is reached mid-retry rather than spinning", async () => {
    const file = await ledgerFile();
    const ledger = new SpendLedger({ file, ceilingUsd: 0.0001 });
    let calls = 0;
    const tracked = costTrackedFetch(ledger, {
      fetch: async () => {
        calls += 1;
        // Reports usage *and* a retryable status, so the retry loop spends.
        return new Response(
          JSON.stringify({
            model: "gpt-4o-mini",
            usage: { prompt_tokens: 10_000, completion_tokens: 1_000 },
          }),
          { status: 503, headers: { "content-type": "application/json" } },
        );
      },
      sleep: async () => {},
    });

    await assert.rejects(() => tracked(chatUrl, request()), SpendCeilingExceededError);
    assert.equal(calls, 1, "the second attempt must be refused, not issued");
  });

  it("does not retry a client error the provider will never accept", async () => {
    const file = await ledgerFile();
    const ledger = new SpendLedger({ file });
    let calls = 0;
    const tracked = costTrackedFetch(ledger, {
      fetch: async () => {
        calls += 1;
        return new Response("{}", { status: 400 });
      },
      sleep: async () => {},
    });

    const response = await tracked(chatUrl, request());
    assert.equal(response.status, 400);
    assert.equal(calls, 1);
    assert.ok(!RETRYABLE_STATUSES.includes(400));
  });

  it("grows the backoff ceiling exponentially and clamps it", () => {
    const policy = { maxAttempts: 6, baseDelayMs: 500, maxDelayMs: 4_000 };
    const atMax = (attempt) => retryDelayMs(attempt, null, policy, () => 1);

    assert.equal(atMax(0), 500);
    assert.equal(atMax(1), 1_000);
    assert.equal(atMax(2), 2_000);
    assert.equal(atMax(3), 4_000);
    assert.equal(atMax(9), 4_000, "clamped, never unbounded");
    assert.ok(retryDelayMs(3, null, policy, () => 0) <= atMax(3), "jitter never exceeds the ceiling");
  });
});

describe("bounded concurrency", () => {
  it("never runs more than the configured number of tasks at once", async () => {
    const run = createConcurrencyLimiter(3);
    let active = 0;
    let peak = 0;

    await Promise.all(
      Array.from({ length: 20 }, () =>
        run(async () => {
          active += 1;
          peak = Math.max(peak, active);
          await new Promise((resolve) => setTimeout(resolve, 1));
          active -= 1;
        }),
      ),
    );

    assert.equal(peak, 3);
    assert.equal(active, 0);
  });

  it("keeps draining after a task rejects", async () => {
    const run = createConcurrencyLimiter(2);
    const results = await Promise.allSettled([
      run(async () => "a"),
      run(async () => {
        throw new Error("boom");
      }),
      run(async () => "c"),
    ]);

    assert.deepEqual(
      results.map((result) => result.status),
      ["fulfilled", "rejected", "fulfilled"],
    );
    assert.equal(results[2].value, "c");
  });
});
