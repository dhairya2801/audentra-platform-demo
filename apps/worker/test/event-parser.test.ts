import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { parseOutboxEvent } from "../src/event-parser.js";

describe("parseOutboxEvent", () => {
  it("normalizes the API outbox row and embedded event envelope", () => {
    const event = parseOutboxEvent({
      id: "72bdd9aa-1ee7-435f-8229-c622f4911640",
      tenant_id: "efc63955-a427-403e-a5f2-66b5023f356e",
      event_name: "enrollment.journey_created.v1",
      aggregate_type: "enrollment_journey",
      aggregate_id: "fc887442-99d7-40a0-8f7d-26c8e402ba38",
      aggregate_version: 1,
      actor_type: "student",
      actor_id: "5fa823a6-5c44-4594-80cd-1170c5712d12",
      occurred_at: new Date("2026-07-24T12:00:00.000Z"),
      correlation_id: "request-1",
      causation_id: "command-1",
      attempts: 2,
      payload: {
        eventId: "domain-event-1",
        data: {
          studentId: "5fa823a6-5c44-4594-80cd-1170c5712d12",
          journeyId: "fc887442-99d7-40a0-8f7d-26c8e402ba38",
          offerId: "59660c09-a4d6-4867-b866-00468c416159",
        },
      },
    });

    assert.equal(event.eventId, "domain-event-1");
    assert.equal(event.outboxId, "72bdd9aa-1ee7-435f-8229-c622f4911640");
    assert.equal(event.eventName, "enrollment.journey_created.v1");
    assert.equal(event.attempts, 2);
    assert.deepEqual(event.actor, {
      type: "student",
      id: "5fa823a6-5c44-4594-80cd-1170c5712d12",
    });
    assert.equal(
      event.data.journeyId,
      "fc887442-99d7-40a0-8f7d-26c8e402ba38",
    );
  });

  it("accepts a serialized envelope and falls back to envelope metadata", () => {
    const event = parseOutboxEvent({
      id: "outbox-1",
      created_at: "2026-07-24T12:00:00.000Z",
      payload: JSON.stringify({
        event_id: "event-1",
        event_name: "requirement.submitted.v1",
        tenant_id: "tenant-1",
        aggregate_type: "student_requirement",
        aggregate_id: "requirement-1",
        aggregate_version: 6,
        actor: { type: "student", id: "student-1" },
        data: { journey_id: "journey-1" },
      }),
    });

    assert.equal(event.eventId, "event-1");
    assert.equal(event.aggregateVersion, 6);
    assert.deepEqual(event.data, { journey_id: "journey-1" });
  });

  it("rejects malformed rows before dispatch", () => {
    assert.throws(
      () =>
        parseOutboxEvent({
          id: "outbox-1",
          payload: "{bad",
        }),
      /malformed JSON/,
    );
  });
});
