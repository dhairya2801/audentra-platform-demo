import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { loadConfig } from "../src/config.js";
import { calculateRetryDelay } from "../src/outbox-repository.js";

describe("worker configuration", () => {
  it("loads safe defaults", () => {
    const config = loadConfig({ DATABASE_URL: "postgres://local/test" });
    assert.equal(config.batchSize, 20);
    assert.equal(config.maxAttempts, 10);
    assert.equal(config.healthPort, 3_002);
  });

  it("rejects invalid numeric bounds and retry relationships", () => {
    assert.throws(
      () =>
        loadConfig({
          DATABASE_URL: "postgres://local/test",
          WORKER_BATCH_SIZE: "0",
        }),
      /WORKER_BATCH_SIZE/,
    );
    assert.throws(
      () =>
        loadConfig({
          DATABASE_URL: "postgres://local/test",
          WORKER_BASE_RETRY_MS: "2000",
          WORKER_MAX_RETRY_MS: "1000",
        }),
      /cannot exceed/,
    );
  });
});

describe("calculateRetryDelay", () => {
  it("is deterministic, exponential, and bounded", () => {
    const first = calculateRetryDelay(1, 1_000, 10_000, "event-1");
    const second = calculateRetryDelay(2, 1_000, 10_000, "event-1");
    const capped = calculateRetryDelay(20, 1_000, 10_000, "event-1");

    assert.equal(first, calculateRetryDelay(1, 1_000, 10_000, "event-1"));
    assert.ok(first >= 1_000);
    assert.ok(second >= first);
    assert.ok(capped <= 10_000);
  });
});
