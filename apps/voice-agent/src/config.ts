export type LogLevel = "debug" | "info" | "warn" | "error";

export interface VoiceAgentConfig {
  livekitUrl: string;
  livekitApiKey: string;
  livekitApiSecret: string;
  internalApiUrl: string;
  internalToken: string;
  assemblyAiApiKey: string;
  cartesiaApiKey: string;
  cartesiaVoiceId: string;
  internalApiTimeoutMs: number;
  internalApiMaxAttempts: number;
  internalApiRetryBaseMs: number;
  shutdownTimeoutMs: number;
  agentName: string;
  diagnosticToneEnabled: boolean;
  logLevel: LogLevel;
}

const required = (environment: NodeJS.ProcessEnv, name: string): string => {
  const value = environment[name]?.trim();
  if (!value) throw new Error(`${name} is required`);
  return value;
};

function boundedInteger(
  environment: NodeJS.ProcessEnv,
  name: string,
  fallback: number,
  minimum: number,
  maximum: number,
): number {
  const raw = environment[name]?.trim();
  const value = raw ? Number(raw) : fallback;
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) {
    throw new Error(`${name} must be an integer between ${minimum} and ${maximum}`);
  }
  return value;
}

function livekitUrl(value: string): string {
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error("LIVEKIT_URL must be a valid WebSocket URL");
  }
  if (
    (parsed.protocol !== "ws:" && parsed.protocol !== "wss:") ||
    parsed.username ||
    parsed.password ||
    parsed.search ||
    parsed.hash
  ) {
    throw new Error("LIVEKIT_URL must be a ws:// or wss:// URL without credentials, query, or fragment");
  }
  return parsed.toString().replace(/\/$/, "");
}

function internalApiUrl(value: string): string {
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error("VOICE_AGENT_INTERNAL_API_URL must be a valid HTTP URL");
  }
  if (
    (parsed.protocol !== "http:" && parsed.protocol !== "https:") ||
    parsed.username ||
    parsed.password ||
    parsed.search ||
    parsed.hash
  ) {
    throw new Error(
      "VOICE_AGENT_INTERNAL_API_URL must be an http:// or https:// URL without credentials, query, or fragment",
    );
  }
  return parsed.toString().replace(/\/$/, "");
}

export function loadConfig(
  environment: NodeJS.ProcessEnv = process.env,
): VoiceAgentConfig {
  const internalToken = required(environment, "VOICE_AGENT_INTERNAL_TOKEN");
  if (internalToken.length < 24 || internalToken.length > 512) {
    throw new Error(
      "VOICE_AGENT_INTERNAL_TOKEN must contain between 24 and 512 characters",
    );
  }

  const logLevel = environment.LOG_LEVEL?.trim().toLowerCase() ?? "info";
  if (!(["debug", "info", "warn", "error"] as const).includes(logLevel as LogLevel)) {
    throw new Error("LOG_LEVEL must be debug, info, warn, or error");
  }

  return {
    livekitUrl: livekitUrl(required(environment, "LIVEKIT_URL")),
    livekitApiKey: required(environment, "LIVEKIT_API_KEY"),
    livekitApiSecret: required(environment, "LIVEKIT_API_SECRET"),
    internalApiUrl: internalApiUrl(
      required(environment, "VOICE_AGENT_INTERNAL_API_URL"),
    ),
    internalToken,
    assemblyAiApiKey: required(environment, "ASSEMBLYAI_API_KEY"),
    cartesiaApiKey: required(environment, "CARTESIA_API_KEY"),
    cartesiaVoiceId: required(environment, "CARTESIA_VOICE_ID"),
    internalApiTimeoutMs: boundedInteger(
      environment,
      "VOICE_AGENT_INTERNAL_API_TIMEOUT_MS",
      15_000,
      500,
      120_000,
    ),
    internalApiMaxAttempts: boundedInteger(
      environment,
      "VOICE_AGENT_INTERNAL_API_MAX_ATTEMPTS",
      2,
      1,
      4,
    ),
    internalApiRetryBaseMs: boundedInteger(
      environment,
      "VOICE_AGENT_INTERNAL_API_RETRY_BASE_MS",
      250,
      0,
      5_000,
    ),
    shutdownTimeoutMs: boundedInteger(
      environment,
      "VOICE_AGENT_SHUTDOWN_TIMEOUT_MS",
      10_000,
      1_000,
      60_000,
    ),
    agentName:
      environment.VOICE_AGENT_NAME?.trim() || "student-assistant-voice",
    diagnosticToneEnabled:
      environment.NODE_ENV !== "production" &&
      environment.VOICE_AGENT_DIAGNOSTIC_TONE?.trim().toLowerCase() === "true",
    logLevel: logLevel as LogLevel,
  };
}
