import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { StudentDashboardProjector } from "../src/dashboard-projector.js";
import type { DatabasePool } from "../src/database.js";
import type { DomainEventEnvelope, Logger } from "../src/types.js";

const logger: Logger = {
  debug: () => undefined,
  info: () => undefined,
  warn: () => undefined,
  error: () => undefined,
};

const event: DomainEventEnvelope = {
  eventId: "b6260b3d-2d9f-468c-a153-5a4c884178f9",
  eventName: "enrollment.journey_created.v1",
  occurredAt: new Date("2026-07-24T12:00:00.000Z"),
  tenantId: "00000000-0000-7000-8000-000000000001",
  aggregateType: "enrollment_journey",
  aggregateId: "00000000-0000-7000-8000-000000000501",
  aggregateVersion: 1,
  actor: {
    type: "student",
    id: "00000000-0000-7000-8000-000000000100",
  },
  correlationId: "request-1",
  causationId: "command-1",
  data: {
    studentId: "00000000-0000-7000-8000-000000000101",
    offerId: "00000000-0000-7000-8000-000000000201",
    journeyId: "00000000-0000-7000-8000-000000000501",
  },
};

describe("StudentDashboardProjector", () => {
  it("rebuilds the contract-shaped dashboard and increments its version", async () => {
    let writtenDashboard: Record<string, unknown> | null = null;
    const client = {
      async query(sql: string, parameters?: unknown[]) {
        if (
          sql === "BEGIN" ||
          sql === "COMMIT" ||
          sql === "ROLLBACK" ||
          sql.includes("pg_advisory_xact_lock")
        ) {
          return { rows: [], rowCount: null };
        }
        if (sql.includes("INSERT INTO public.projection_event_receipt")) {
          return { rows: [{ event_id: event.eventId }], rowCount: 1 };
        }
        if (sql.includes("SELECT projection_version")) {
          return { rows: [{ projection_version: "1" }], rowCount: 1 };
        }
        if (sql.includes("FROM public.student\n")) {
          return {
            rows: [
              {
                student_id: event.data.studentId,
                preferred_name: "Alex",
                first_name: "Alex",
                last_name: "Morgan",
                class_year: 2027,
                offer_id: event.data.offerId,
                program_name: "Computer Science",
                term_name: "Fall 2027",
                campus_name: "Main Campus",
                response_deadline: "2027-08-15",
                deposit_amount_cents: 50_000,
                offer_status: "accepted",
                journey_id: event.data.journeyId,
                journey_status: "in_progress",
              },
            ],
            rowCount: 1,
          };
        }
        if (sql.includes("FROM public.student_requirement")) {
          return {
            rows: [
              {
                id: "requirement-1",
                code: "profile",
                title: "Complete your profile",
                description: "Tell us about yourself.",
                status: "ready",
                blocking: 1,
                due_at: "2027-08-20T00:00:00.000Z",
                progress_percent: 20,
              },
            ],
            rowCount: 1,
          };
        }
        if (sql.includes("INSERT INTO public.student_portal_projection")) {
          writtenDashboard = JSON.parse(String(parameters?.[3])) as Record<
            string,
            unknown
          >;
          return { rows: [], rowCount: 1 };
        }
        throw new Error(`Unexpected SQL in test: ${sql}`);
      },
      release() {},
    };
    const pool = {
      async connect() {
        return client;
      },
    } as unknown as DatabasePool;

    const projector = new StudentDashboardProjector(
      pool,
      "student-dashboard-projector",
      logger,
    );
    await projector.handle(event);

    assert.ok(writtenDashboard);
    assert.equal(writtenDashboard.projectionVersion, 2);
    assert.equal(
      (writtenDashboard.student as Record<string, unknown>).preferredName,
      "Alex",
    );
    assert.equal(
      (
        (
          writtenDashboard.journey as Record<string, unknown>
        ).nextAction as Record<string, unknown>
      ).code,
      "profile",
    );
    assert.equal(
      (
        (
          writtenDashboard.journey as Record<string, unknown>
        ).requirements as Array<Record<string, unknown>>
      )[0]?.blocking,
      true,
    );
  });

  it("does no projection work when the event receipt already exists", async () => {
    let queryCount = 0;
    const client = {
      async query(sql: string) {
        queryCount += 1;
        if (sql === "BEGIN" || sql === "COMMIT" || sql === "ROLLBACK") {
          return { rows: [], rowCount: null };
        }
        if (sql.includes("INSERT INTO public.projection_event_receipt")) {
          return { rows: [], rowCount: 0 };
        }
        throw new Error("Projection should stop after duplicate receipt");
      },
      release() {},
    };
    const pool = {
      async connect() {
        return client;
      },
    } as unknown as DatabasePool;

    const projector = new StudentDashboardProjector(
      pool,
      "student-dashboard-projector",
      logger,
    );
    await projector.handle(event);
    assert.equal(queryCount, 3);
  });
});
