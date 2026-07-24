import { trace } from "@opentelemetry/api";
import { describe, expect, it } from "vitest";
import {
  getRuntimeLineage,
} from "../src/observability/runtime-lineage";
import {
  initializeTelemetry,
} from "../src/observability/telemetry";

describe("runtime CRM lineage", () => {
  it("links a registry effect to an active OpenTelemetry trace", () => {
    initializeTelemetry();
    const lineage = trace
      .getTracer("vv.runtime-lineage.test")
      .startActiveSpan("select-payment-plan", (span) => {
        try {
          return getRuntimeLineage({
            correlationId: "correlation.financials.0001",
            eventName: "student_financial.payment_plan_selected.v1",
          });
        } finally {
          span.end();
        }
      });

    expect(lineage).toMatchObject({
      correlationId: "correlation.financials.0001",
      effectRegistryVersion: 1,
      effectId: "financials.selectPaymentPlan",
    });
    expect(lineage.traceId).toMatch(/^[a-f0-9]{32}$/);
    expect(lineage.spanId).toMatch(/^[a-f0-9]{16}$/);
  });
});
