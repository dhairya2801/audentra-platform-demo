import { describe, expect, it } from "vitest";
import { loadConfig } from "../src/config.js";

const validEnvironment = {
  LIVEKIT_URL: "wss://student-assistant.livekit.cloud",
  LIVEKIT_API_KEY: "test-livekit-key",
  LIVEKIT_API_SECRET: "test-livekit-secret",
  VOICE_AGENT_INTERNAL_API_URL: "http://api:4000",
  VOICE_AGENT_INTERNAL_TOKEN: "test-internal-token-at-least-24-characters",
  ASSEMBLYAI_API_KEY: "test-assembly-key",
  CARTESIA_API_KEY: "test-cartesia-key",
  CARTESIA_VOICE_ID: "00000000-0000-4000-8000-000000000001",
} satisfies NodeJS.ProcessEnv;

describe("voice agent configuration", () => {
  it("loads required provider and internal transport settings", () => {
    const config = loadConfig(validEnvironment);

    expect(config.livekitUrl).toBe("wss://student-assistant.livekit.cloud");
    expect(config.internalApiUrl).toBe("http://api:4000");
    expect(config.internalApiTimeoutMs).toBe(15_000);
    expect(config.internalApiMaxAttempts).toBe(2);
    expect(config.cartesiaVoiceId).toBe(
      "00000000-0000-4000-8000-000000000001",
    );
    expect(config.diagnosticToneEnabled).toBe(false);
  });

  it("enables the diagnostic tone only outside production", () => {
    expect(
      loadConfig({
        ...validEnvironment,
        NODE_ENV: "development",
        VOICE_AGENT_DIAGNOSTIC_TONE: "true",
      }).diagnosticToneEnabled,
    ).toBe(true);
    expect(
      loadConfig({
        ...validEnvironment,
        NODE_ENV: "production",
        VOICE_AGENT_DIAGNOSTIC_TONE: "true",
      }).diagnosticToneEnabled,
    ).toBe(false);
  });

  it.each([
    "LIVEKIT_URL",
    "LIVEKIT_API_KEY",
    "LIVEKIT_API_SECRET",
    "VOICE_AGENT_INTERNAL_API_URL",
    "VOICE_AGENT_INTERNAL_TOKEN",
    "ASSEMBLYAI_API_KEY",
    "CARTESIA_API_KEY",
    "CARTESIA_VOICE_ID",
  ])("rejects missing %s", (name) => {
    const environment = { ...validEnvironment };
    delete environment[name as keyof typeof environment];
    expect(() => loadConfig(environment)).toThrow(name);
  });

  it("rejects unsafe URLs, short tokens, and invalid retry bounds", () => {
    expect(() =>
      loadConfig({ ...validEnvironment, LIVEKIT_URL: "https://not-websocket" }),
    ).toThrow(/LIVEKIT_URL/);
    expect(() =>
      loadConfig({
        ...validEnvironment,
        VOICE_AGENT_INTERNAL_TOKEN: "short",
      }),
    ).toThrow(/VOICE_AGENT_INTERNAL_TOKEN/);
    expect(() =>
      loadConfig({
        ...validEnvironment,
        VOICE_AGENT_INTERNAL_API_MAX_ATTEMPTS: "0",
      }),
    ).toThrow(/VOICE_AGENT_INTERNAL_API_MAX_ATTEMPTS/);
  });
});
