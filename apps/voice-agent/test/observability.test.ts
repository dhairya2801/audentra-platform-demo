import { describe, expect, it, vi } from "vitest";
import { createLogger } from "../src/logger.js";
import {
  compactContext,
  nonNegativeMilliseconds,
  safeErrorMetadata,
  VoiceMetrics,
  voiceErrorCategories,
} from "../src/observability.js";
import type { Logger } from "../src/types.js";

describe("voice observability safety", () => {
  it("omits missing identifiers instead of fabricating them", () => {
    expect(
      compactContext({
        voiceSessionId: "voice-session",
        requestId: undefined,
        assistantMessageId: undefined,
      }),
    ).toEqual({ voiceSessionId: "voice-session" });
  });

  it("normalizes durations to numeric non-negative milliseconds", () => {
    expect(nonNegativeMilliseconds(12.6)).toBe(13);
    expect(nonNegativeMilliseconds(-4)).toBe(0);
    expect(nonNegativeMilliseconds(Number.NaN)).toBe(0);
  });

  it("exposes only bounded provider error metadata", () => {
    const error = Object.assign(new Error("token=secret transcript=private"), {
      code: "PROVIDER_TIMEOUT",
      status: 503,
      requestId: "provider-request-1",
      body: { apiKey: "secret-api-key" },
    });

    expect(safeErrorMetadata(error)).toEqual({
      errorType: "Error",
      errorCode: "PROVIDER_TIMEOUT",
      httpStatusClass: "5xx",
      providerRequestId: "provider-request-1",
    });
  });

  it("never serializes credentials, tokens, student data, or raw error text", () => {
    const write = vi.spyOn(process.stdout, "write").mockReturnValue(true);
    const logger = createLogger("debug");
    logger.warn("provider_failed", {
      error: new Error(
        "Bearer internal-token api-key student@example.test Student Name raw transcript",
      ),
    });

    const output = String(write.mock.calls[0]?.[0]);
    expect(output).toContain('"errorType":"Error"');
    expect(output).not.toMatch(
      /internal-token|api-key|student@example|Student Name|raw transcript|Bearer/,
    );
    write.mockRestore();
  });

  it("uses the bounded V1 error taxonomy", () => {
    expect(voiceErrorCategories).toContain("microphone_permission_denied");
    expect(voiceErrorCategories).toContain("graph_safe_fallback");
    expect(voiceErrorCategories).toContain("unknown_voice_error");
    expect(new Set(voiceErrorCategories).size).toBe(voiceErrorCategories.length);
  });

  it("aggregates counters and latency observations in process", () => {
    const logger = fakeLogger();
    const metrics = new VoiceMetrics(logger);
    metrics.increment("final_transcripts_received", { voiceSessionId: "voice-1" });
    metrics.increment("final_transcripts_received", { voiceSessionId: "voice-1" });
    metrics.observe("first_audio_latency_ms", -10, {
      voiceSessionId: "voice-1",
    });

    expect(metrics.snapshot()).toEqual({
      counters: { final_transcripts_received: 2 },
      latencies: { first_audio_latency_ms: [0] },
    });
  });
});

function fakeLogger(): Logger {
  return {
    debug: vi.fn(),
    info: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
  };
}
