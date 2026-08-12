import type { Logger } from "./types.js";

export const voiceErrorCategories = [
  "microphone_permission_denied",
  "microphone_unavailable",
  "livekit_token_failure",
  "livekit_dispatch_failure",
  "livekit_connection_failure",
  "worker_not_ready",
  "stt_connection_failure",
  "stt_timeout",
  "stt_provider_error",
  "backend_timeout",
  "backend_unauthorized",
  "backend_turn_failure",
  "student_data_unavailable",
  "graph_safe_fallback",
  "tts_connection_failure",
  "tts_invalid_voice",
  "tts_format_error",
  "tts_provider_error",
  "browser_autoplay_rejected",
  "audio_track_failure",
  "reconnect_failure",
  "session_expired",
  "participant_disconnected",
  "unknown_voice_error",
] as const;

export type VoiceErrorCategory = (typeof voiceErrorCategories)[number];

export interface VoiceMetricSnapshot {
  counters: Record<string, number>;
  latencies: Record<string, readonly number[]>;
}

export class VoiceMetrics {
  private readonly counters = new Map<string, number>();
  private readonly latencies = new Map<string, number[]>();

  constructor(private readonly logger: Logger) {}

  increment(
    name: string,
    context: VoiceCorrelationContext = {},
    value = 1,
  ): void {
    const next = (this.counters.get(name) ?? 0) + value;
    this.counters.set(name, next);
    this.logger.info("voice_metric", {
      metricType: "counter",
      metricName: name,
      value,
      total: next,
      ...compactContext(context),
    });
  }

  observe(
    name: string,
    durationMs: number,
    context: VoiceCorrelationContext = {},
    timingBasis: "monotonic" | "cross_service_wall_clock" = "monotonic",
  ): void {
    const value = nonNegativeMilliseconds(durationMs);
    const values = this.latencies.get(name) ?? [];
    values.push(value);
    this.latencies.set(name, values);
    this.logger.info("voice_metric", {
      metricType: "latency",
      metricName: name,
      durationMs: value,
      timingBasis,
      ...compactContext(context),
    });
  }

  snapshot(): VoiceMetricSnapshot {
    return {
      counters: Object.fromEntries(this.counters),
      latencies: Object.fromEntries(
        [...this.latencies].map(([name, values]) => [name, [...values]]),
      ),
    };
  }
}

export function nonNegativeMilliseconds(value: number): number {
  return Math.max(0, Math.round(Number.isFinite(value) ? value : 0));
}

export interface VoiceCorrelationContext {
  requestId?: string | undefined;
  conversationId?: string | undefined;
  voiceSessionId?: string | undefined;
  clientMessageId?: string | undefined;
  userMessageId?: string | undefined;
  assistantMessageId?: string | undefined;
  jobId?: string | undefined;
  roomName?: string | undefined;
  participantIdentity?: string | undefined;
  trackId?: string | undefined;
  streamId?: string | undefined;
  graphVersion?: string | undefined;
  inputMode?: "voice" | "text" | undefined;
  provider?: string | undefined;
  errorCategory?: VoiceErrorCategory | undefined;
}

export function compactContext(
  context: VoiceCorrelationContext,
): Record<string, string> {
  return Object.fromEntries(
    Object.entries(context).filter(
      (entry): entry is [string, string] =>
        typeof entry[1] === "string" && entry[1].length > 0,
    ),
  );
}

export function safeErrorMetadata(error: unknown): Record<string, unknown> {
  if (!error || typeof error !== "object") return { errorType: typeof error };
  const candidate = error as Record<string, unknown>;
  const status = numericStatus(candidate.status);
  const code = boundedCode(candidate.code);
  const providerRequestId = boundedIdentifier(
    candidate.requestId ?? candidate.request_id,
  );
  return {
    errorType:
      typeof candidate.name === "string"
        ? candidate.name.slice(0, 80)
        : error.constructor?.name?.slice(0, 80) ?? "Error",
    ...(code ? { errorCode: code } : {}),
    ...(status ? { httpStatusClass: `${Math.floor(status / 100)}xx` } : {}),
    ...(providerRequestId ? { providerRequestId } : {}),
  };
}

export function emitRecoverableError(
  logger: Logger,
  category: VoiceErrorCategory,
  context: VoiceCorrelationContext,
  error?: unknown,
): void {
  logger.warn("recoverable_voice_error", {
    ...compactContext({ ...context, errorCategory: category }),
    ...(error === undefined ? {} : safeErrorMetadata(error)),
  });
  logger.info("voice_metric", {
    metricType: "counter",
    metricName: "recoverable_errors",
    value: 1,
    ...compactContext({ ...context, errorCategory: category }),
  });
}

export function emitUnrecoverableError(
  logger: Logger,
  category: VoiceErrorCategory,
  context: VoiceCorrelationContext,
  error?: unknown,
): void {
  logger.error("unrecoverable_voice_error", {
    ...compactContext({ ...context, errorCategory: category }),
    ...(error === undefined ? {} : safeErrorMetadata(error)),
  });
  logger.info("voice_metric", {
    metricType: "counter",
    metricName: "unrecoverable_errors",
    value: 1,
    ...compactContext({ ...context, errorCategory: category }),
  });
}

function boundedCode(value: unknown): string | undefined {
  return typeof value === "string" && /^[A-Za-z0-9_.:-]{1,80}$/.test(value)
    ? value
    : undefined;
}

function boundedIdentifier(value: unknown): string | undefined {
  return typeof value === "string" && /^[A-Za-z0-9_.:-]{1,128}$/.test(value)
    ? value
    : undefined;
}

function numericStatus(value: unknown): number | undefined {
  return typeof value === "number" && value >= 100 && value <= 599
    ? Math.trunc(value)
    : undefined;
}
