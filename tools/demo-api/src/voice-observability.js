export const voiceErrorCategories = Object.freeze([
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
]);

const counters = new Map();
const latencies = new Map();

export function emitVoiceEvent(logger, clock, event, fields = {}) {
  logger?.info?.(
    JSON.stringify({
      timestamp: clock().toISOString(),
      level: "info",
      service: "vv-demo-api",
      event,
      ...Object.fromEntries(
        Object.entries(fields).filter(([, value]) => value !== undefined),
      ),
    }),
  );
  if (
    event === "recoverable_voice_error" ||
    event === "unrecoverable_voice_error"
  ) {
    const metricName =
      event === "recoverable_voice_error"
        ? "recoverable_errors"
        : "unrecoverable_errors";
    const total = (counters.get(metricName) ?? 0) + 1;
    counters.set(metricName, total);
    logger?.info?.(
      JSON.stringify({
        timestamp: clock().toISOString(),
        level: "info",
        service: "vv-demo-api",
        event: "voice_metric",
        metricType: "counter",
        metricName,
        value: 1,
        total,
        ...Object.fromEntries(
          Object.entries(fields).filter(([, value]) => value !== undefined),
        ),
      }),
    );
  }
}

export function incrementVoiceCounter(
  logger,
  clock,
  metricName,
  fields = {},
  value = 1,
) {
  const total = (counters.get(metricName) ?? 0) + value;
  counters.set(metricName, total);
  emitVoiceEvent(logger, clock, "voice_metric", {
    metricType: "counter",
    metricName,
    value,
    total,
    ...fields,
  });
}

export function observeVoiceLatency(
  logger,
  clock,
  metricName,
  durationMs,
  fields = {},
) {
  const value = Math.max(0, Math.round(Number.isFinite(durationMs) ? durationMs : 0));
  const values = latencies.get(metricName) ?? [];
  values.push(value);
  latencies.set(metricName, values);
  emitVoiceEvent(logger, clock, "voice_metric", {
    metricType: "latency",
    metricName,
    durationMs: value,
    timingBasis: "monotonic",
    ...fields,
  });
}

export function voiceMetricSnapshot() {
  return {
    counters: Object.fromEntries(counters),
    latencies: Object.fromEntries(
      [...latencies].map(([name, values]) => [name, [...values]]),
    ),
  };
}

export function resetVoiceMetricsForTest() {
  counters.clear();
  latencies.clear();
}
