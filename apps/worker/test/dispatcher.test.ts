import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  EventDispatcher,
  portalEventsWithoutWorkerProjection,
} from "../src/dispatcher.js";
import type { DomainEventEnvelope, Logger } from "../src/types.js";

const logger: Logger = {
  debug: () => undefined,
  info: () => undefined,
  warn: () => undefined,
  error: () => undefined,
};

const event: DomainEventEnvelope = {
  eventId: "event-1",
  eventName: "enrollment.journey_created.v1",
  occurredAt: new Date("2026-07-24T12:00:00.000Z"),
  tenantId: "tenant-1",
  aggregateType: "enrollment_journey",
  aggregateId: "journey-1",
  aggregateVersion: 1,
  actor: { type: "student", id: "student-1" },
  correlationId: "request-1",
  causationId: "command-1",
  data: {},
};

describe("EventDispatcher", () => {
  it("routes a named event to exactly one handler", async () => {
    let handled = 0;
    const dispatcher = new EventDispatcher(logger).register(
      event.eventName,
      async (received) => {
        assert.equal(received, event);
        handled += 1;
      },
    );

    assert.equal(await dispatcher.dispatch(event), "handled");
    assert.equal(handled, 1);
  });

  it("only acknowledges an event when it is explicitly ignored", async () => {
    const dispatcher = new EventDispatcher(logger).registerIgnored(
      event.eventName,
    );
    assert.equal(await dispatcher.dispatch(event), "ignored");
  });

  it("explicitly acknowledges every portal event that has no projection yet", async () => {
    const dispatcher = new EventDispatcher(logger);
    for (const eventName of portalEventsWithoutWorkerProjection) {
      dispatcher.registerIgnored(eventName);
    }

    for (const eventName of portalEventsWithoutWorkerProjection) {
      assert.equal(
        await dispatcher.dispatch({ ...event, eventName }),
        "ignored",
      );
    }
  });

  it("rejects unknown event types instead of silently losing them", async () => {
    const dispatcher = new EventDispatcher(logger);
    await assert.rejects(
      () => dispatcher.dispatch(event),
      /No outbox handler registered/,
    );
  });

  it("refuses an ambiguous duplicate registration", () => {
    const dispatcher = new EventDispatcher(logger).register(
      event.eventName,
      async () => undefined,
    );
    assert.throws(
      () =>
        dispatcher.register(event.eventName, async () => undefined),
      /already registered/,
    );
  });
});
