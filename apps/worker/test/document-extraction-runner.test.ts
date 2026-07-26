import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { DocumentExtractionRunner } from "../src/document-extraction-runner.js";
import type { WorkerConfig } from "../src/config.js";
import type { DomainEventEnvelope, Logger } from "../src/types.js";

const logger: Logger = {
  debug: () => undefined,
  info: () => undefined,
  warn: () => undefined,
  error: () => undefined,
};

const config: WorkerConfig = {
  databaseUrl: "postgres://unused/test",
  apiInternalUrl: "http://api.internal:4000",
  documentWorkerToken: "worker-token",
  workerId: "worker-1",
  consumerName: "consumer-1",
  batchSize: 1,
  pollIntervalMs: 1_000,
  leaseSeconds: 60,
  maxAttempts: 10,
  baseRetryMs: 1_000,
  maxRetryMs: 3_000,
  statementTimeoutMs: 15_000,
  shutdownTimeoutMs: 25_000,
  healthPort: 3_002,
  heartbeatIntervalMs: 30_000,
  logLevel: "info",
};

const event: DomainEventEnvelope = {
  eventId: "event-1",
  eventName: "document.extraction_requested.v1",
  occurredAt: new Date("2026-07-25T12:00:00.000Z"),
  tenantId: "00000000-0000-7000-8000-000000000001",
  aggregateType: "document_record",
  aggregateId: "00000000-0000-7000-8000-000000000601",
  aggregateVersion: 2,
  actor: {
    type: "student",
    id: "00000000-0000-7000-8000-000000000100",
  },
  correlationId: "request.document.0001",
  causationId: "command-1",
  data: { studentId: "00000000-0000-7000-8000-000000000101" },
};

describe("DocumentExtractionRunner", () => {
  it("issues the private command with the event identity and correlation id", async () => {
    let receivedUrl = "";
    let receivedInit: RequestInit | undefined;
    const runner = new DocumentExtractionRunner(
      config,
      logger,
      async (input, init) => {
        receivedUrl = String(input);
        receivedInit = init;
        return new Response("{}", { status: 200 });
      },
    );

    await runner.handle(event);

    assert.equal(
      receivedUrl,
      "http://api.internal:4000/v1/student/internal/document-extractions/00000000-0000-7000-8000-000000000601",
    );
    const headers = new Headers(receivedInit?.headers);
    assert.equal(headers.get("x-vv-worker-token"), "worker-token");
    assert.equal(headers.get("x-demo-tenant-id"), event.tenantId);
    assert.equal(headers.get("x-demo-student-id"), event.data.studentId);
    assert.equal(headers.get("x-demo-actor-id"), event.actor?.id);
    assert.equal(headers.get("x-correlation-id"), event.correlationId);
  });

  it("fails closed when a required event identity is missing", async () => {
    const runner = new DocumentExtractionRunner(config, logger, async () => {
      throw new Error("should not send");
    });
    await assert.rejects(
      () => runner.handle({ ...event, actor: null }),
      /missing an actor id/,
    );
  });
});
