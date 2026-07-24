import { randomUUID } from "node:crypto";
import type { NestFastifyApplication } from "@nestjs/platform-fastify";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { createApiApplication } from "../src/create-app";
import {
  DEMO_IDS,
  type AppConfig,
} from "../src/config/app-config";
import { InMemoryPlatformStore } from "./support/in-memory-platform.store";

const testConfig: AppConfig = {
  environment: "test",
  port: 4000,
  databaseUrl: "postgresql://unused/unused",
  webOrigins: ["http://localhost:3000"],
  authMode: "demo",
  demoIds: {
    tenantId: DEMO_IDS.tenantId,
    studentId: DEMO_IDS.studentId,
    actorId: DEMO_IDS.personId,
  },
};

describe("API vertical slice", () => {
  let app: NestFastifyApplication;
  let store: InMemoryPlatformStore;

  beforeAll(async () => {
    store = new InMemoryPlatformStore();
    app = await createApiApplication({
      config: testConfig,
      platformStoreOverride: store,
      logger: false,
    });
  });

  afterAll(async () => {
    await app.close();
  });

  it("serves liveness with correlated request and OpenTelemetry IDs", async () => {
    const response = await app.inject({
      method: "GET",
      url: "/health",
      headers: {
        "x-request-id": "request.health.ignored",
        "x-correlation-id": "correlation.health.0001",
      },
    });

    expect(response.statusCode).toBe(200);
    expect(response.headers["x-request-id"]).toBe(
      "correlation.health.0001",
    );
    expect(response.headers["x-correlation-id"]).toBe(
      "correlation.health.0001",
    );
    expect(response.headers["x-trace-id"]).toMatch(/^[a-f0-9]{32}$/);
    expect(response.json()).toMatchObject({
      status: "ok",
      service: "vv-api",
    });

    const readiness = await app.inject({
      method: "GET",
      url: "/health/ready",
    });
    expect(readiness.statusCode).toBe(200);
    expect(readiness.json()).toMatchObject({
      status: "ready",
      service: "vv-api",
    });
  });

  it("returns the demo student's typed dashboard", async () => {
    const response = await app.inject({
      method: "GET",
      url: "/v1/student/dashboard",
    });

    expect(response.statusCode).toBe(200);
    expect(response.json()).toMatchObject({
      student: { id: DEMO_IDS.studentId, preferredName: "Alex" },
      offer: { id: DEMO_IDS.offerId, status: "offered" },
      journey: {
        id: null,
        status: "not_started",
        nextAction: { code: "accept_offer" },
      },
      projectionVersion: 1,
    });
  });

  it("does not reveal another tenant's student data", async () => {
    const response = await app.inject({
      method: "GET",
      url: "/v1/student/dashboard",
      headers: {
        "x-demo-tenant-id": "00000000-0000-7000-8000-000000009999",
      },
    });

    expect(response.statusCode).toBe(404);
    expect(response.json()).toMatchObject({
      error: {
        code: "STUDENT_DASHBOARD_NOT_FOUND",
      },
    });
  });

  it("rejects malformed demo identity headers", async () => {
    const response = await app.inject({
      method: "GET",
      url: "/v1/student/dashboard",
      headers: { "x-demo-student-id": "not-an-id" },
    });

    expect(response.statusCode).toBe(401);
    expect(response.json()).toMatchObject({
      error: { code: "UNAUTHORIZED" },
    });
  });

  it("requires a valid idempotency key when accepting an offer", async () => {
    const response = await app.inject({
      method: "POST",
      url: `/v1/admission-offers/${DEMO_IDS.offerId}/accept`,
    });

    expect(response.statusCode).toBe(400);
    expect(response.json()).toMatchObject({
      error: {
        code: "IDEMPOTENCY_KEY_REQUIRED",
      },
    });
  });

  it("accepts the offer once and replays the exact result safely", async () => {
    const request = {
      method: "POST" as const,
      url: `/v1/admission-offers/${DEMO_IDS.offerId}/accept`,
      headers: { "idempotency-key": "accept.offer.0001" },
    };
    const first = await app.inject(request);
    const replay = await app.inject(request);

    expect(first.statusCode).toBe(200);
    expect(replay.statusCode).toBe(200);
    expect(replay.json()).toEqual(first.json());
    expect(first.json()).toMatchObject({
      offerId: DEMO_IDS.offerId,
      offerStatus: "accepted",
      journeyStatus: "in_progress",
      projectionVersion: 2,
    });
    expect(store.effects).toEqual({
      journeys: 1,
      requirementSets: 1,
      auditEvents: 1,
      outboxEvents: 2,
    });
  });

  it("rejects reuse of an idempotency key for a different command", async () => {
    const key = "accept.offer.reuse.0001";
    const first = await app.inject({
      method: "POST",
      url: `/v1/admission-offers/${DEMO_IDS.offerId}/accept`,
      headers: { "idempotency-key": key },
    });
    const second = await app.inject({
      method: "POST",
      url: "/v1/admission-offers/00000000-0000-7000-8000-000000000999/accept",
      headers: { "idempotency-key": key },
    });

    expect(first.statusCode).toBe(200);
    expect(second.statusCode).toBe(409);
    expect(second.json()).toMatchObject({
      error: { code: "IDEMPOTENCY_KEY_REUSED" },
    });
  });

  it("ingests activity idempotently and rejects non-allowlisted properties", async () => {
    const eventId = randomUUID();
    const payload = {
      events: [
        {
          eventId,
          eventName: "ui.dashboard_viewed.v1",
          occurredAt: "2026-07-24T12:00:00.000Z",
          sessionId: "session.0001",
          pageInstanceId: "page.0001",
          properties: {
            projection_version: 2,
            journey_status: "in_progress",
          },
        },
      ],
    };
    const first = await app.inject({
      method: "POST",
      url: "/v1/activity-events/batch",
      payload,
    });
    const replay = await app.inject({
      method: "POST",
      url: "/v1/activity-events/batch",
      payload,
    });
    const prohibited = await app.inject({
      method: "POST",
      url: "/v1/activity-events/batch",
      payload: {
        events: [
          {
            ...payload.events[0],
            eventId: randomUUID(),
            properties: { email: "student@example.test" },
          },
        ],
      },
    });

    expect(first.statusCode).toBe(202);
    expect(first.json()).toEqual({ accepted: 1, duplicates: 0 });
    expect(replay.statusCode).toBe(202);
    expect(replay.json()).toEqual({ accepted: 0, duplicates: 1 });
    expect(prohibited.statusCode).toBe(400);
    expect(prohibited.json()).toMatchObject({
      error: { code: "PROHIBITED_ACTIVITY_PROPERTY" },
    });
  });

  it("enforces strict DTO validation and configured CORS", async () => {
    const invalid = await app.inject({
      method: "POST",
      url: "/v1/activity-events/batch",
      payload: {
        events: [],
        unexpected: true,
      },
    });
    const cors = await app.inject({
      method: "OPTIONS",
      url: "/v1/student/dashboard",
      headers: {
        origin: "http://localhost:3000",
        "access-control-request-method": "GET",
      },
    });

    expect(invalid.statusCode).toBe(400);
    expect(invalid.json()).toMatchObject({
      error: { code: "VALIDATION_ERROR" },
    });
    expect(cors.statusCode).toBe(204);
    expect(cors.headers["access-control-allow-origin"]).toBe(
      "http://localhost:3000",
    );
    expect(cors.headers["access-control-allow-credentials"]).toBe("true");
  });
});
