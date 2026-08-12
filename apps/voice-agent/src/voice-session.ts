import { randomUUID } from "node:crypto";
import { VoiceBackendError } from "./backend-client.js";
import {
  emitRecoverableError,
  emitUnrecoverableError,
  safeErrorMetadata,
  nonNegativeMilliseconds,
  VoiceMetrics,
  type VoiceErrorCategory,
} from "./observability.js";
import type {
  BrowserTransport,
  RecoverableVoiceError,
  VoiceAgentState,
} from "./livekit-transport.js";
import type {
  AudioPlaybackSink,
  StreamingSynthesizer,
  TranscriptEvent,
} from "./providers.js";
import type {
  Logger,
  VoiceBackend,
  VoiceSessionBinding,
} from "./types.js";

interface VoiceSessionOptions {
  binding: VoiceSessionBinding;
  backend: VoiceBackend;
  transport: BrowserTransport;
  synthesizer: StreamingSynthesizer;
  audio: AudioPlaybackSink;
  logger: Logger;
  createId?: () => string;
  now?: () => number;
  monotonicNow?: () => number;
  metrics?: VoiceMetrics;
}

interface ActiveSpeech {
  controller: AbortController;
  responseId: string;
}

const duplicateFinalWindowMs = 5_000;
const duplicateFinalLimit = 128;

export class VoiceSession {
  private readonly createId: () => string;
  private readonly now: () => number;
  private readonly monotonicNow: () => number;
  private readonly metrics: VoiceMetrics;
  private readonly sessionStartedAt: number;
  private readonly duplicateFinals = new Map<string, number>();
  private readonly captionSegments = new Map<string, string>();
  private readonly speechEndedAt = new Map<string, number>();
  private turnQueue: Promise<void> = Promise.resolve();
  private activeSpeech: ActiveSpeech | null = null;
  private interruptionEpoch = 0;
  private state: VoiceAgentState = "initializing";
  private started = false;
  private closed = false;
  private shutdownPromise: Promise<void> | null = null;
  private reconnectStartedAt: number | null = null;
  private finalizedTurnCount = 0;
  private successfulBackendTurnCount = 0;
  private failedBackendTurnCount = 0;
  private ttsSuccessCount = 0;
  private ttsFailureCount = 0;
  private interruptionCount = 0;
  private reconnectCount = 0;
  private duplicateFinalsSuppressed = 0;
  private readonly recoverableErrors: Partial<Record<VoiceErrorCategory, number>> = {};
  private readonly providerUsageUnits: Record<string, number> = {};

  constructor(private readonly options: VoiceSessionOptions) {
    this.createId = options.createId ?? randomUUID;
    this.now = options.now ?? Date.now;
    this.monotonicNow = options.monotonicNow ?? (() => performance.now());
    this.metrics = options.metrics ?? new VoiceMetrics(options.logger);
    this.sessionStartedAt = this.monotonicNow();
  }

  async start(): Promise<void> {
    if (this.started) return;
    this.started = true;
    this.options.transport.registerStopSpeaking(() =>
      this.interruptSpeech("explicit_stop"),
    );
    this.options.synthesizer.prewarm();
    await this.publishState("initializing");
    await this.publishState("listening");
    this.options.logger.info("voice_session_started", {
      voiceSessionId: this.options.binding.voiceSessionId,
      conversationId: this.options.binding.conversationId,
    });
  }

  async handleTranscript(event: TranscriptEvent): Promise<void> {
    if (this.closed) return;
    if (event.type === "speech_started") {
      this.captionSegments.set(event.livekitStreamId, this.createId());
      this.interruptSpeech("natural_barge_in");
      await this.publishState("listening");
      return;
    }
    if (event.type === "speech_ended") {
      this.speechEndedAt.set(event.livekitStreamId, this.monotonicNow());
      return;
    }

    const text = event.text.trim();
    if (!text) return;
    if (event.type === "interim") {
      await this.options.transport.publishTranscript({
        segmentId: this.captionSegment(event.livekitStreamId),
        livekitStreamId: event.livekitStreamId,
        text,
        language: event.language,
        final: false,
      });
      return;
    }

    if (this.isDuplicateFinal(event, text)) {
      this.options.logger.debug("voice_final_transcript_duplicate_suppressed", {
        voiceSessionId: this.options.binding.voiceSessionId,
        livekitStreamId: event.livekitStreamId,
      });
      this.metrics.increment("duplicate_final_transcripts_suppressed", {
        ...this.baseContext("assemblyai"),
        streamId: event.livekitStreamId,
      });
      this.duplicateFinalsSuppressed += 1;
      return;
    }
    this.finalizedTurnCount += 1;

    const finalReceivedAt = this.monotonicNow();
    const speechEndedAt = this.speechEndedAt.get(event.livekitStreamId);
    this.speechEndedAt.delete(event.livekitStreamId);
    const sttFinalDurationMs =
      speechEndedAt === undefined
        ? undefined
        : nonNegativeMilliseconds(finalReceivedAt - speechEndedAt);
    this.options.logger.info("stt_final_received", {
      ...this.baseContext("assemblyai"),
      streamId: event.livekitStreamId,
      language: event.language,
      transcriptCharacters: text.length,
      ...(sttFinalDurationMs === undefined
        ? {}
        : { speechEndToFinalDurationMs: sttFinalDurationMs }),
    });
    this.metrics.increment("final_transcripts_received", {
      ...this.baseContext("assemblyai"),
      streamId: event.livekitStreamId,
    });
    if (sttFinalDurationMs !== undefined) {
      this.metrics.observe("stt_final_latency_ms", sttFinalDurationMs, {
        ...this.baseContext("assemblyai"),
        streamId: event.livekitStreamId,
      });
    }

    const segmentId = this.captionSegment(event.livekitStreamId);
    this.captionSegments.delete(event.livekitStreamId);
    await this.options.transport.publishTranscript({
      segmentId,
      livekitStreamId: event.livekitStreamId,
      text,
      language: event.language,
      final: true,
    });

    const clientMessageId = this.createId();
    const epoch = this.interruptionEpoch;
    const processTurn = () =>
      this.processFinalTurn({
        clientMessageId,
        text,
        livekitStreamId: event.livekitStreamId,
        interruptionEpoch: epoch,
        finalReceivedAt,
        speechEndedAt,
      });
    this.turnQueue = this.turnQueue.then(processTurn, processTurn);
    await this.turnQueue;
  }

  async handleSttFailure(
    error: unknown,
    category: VoiceErrorCategory = "stt_provider_error",
  ): Promise<void> {
    if (this.closed) return;
    this.options.logger.warn("voice_stt_failed", {
      voiceSessionId: this.options.binding.voiceSessionId,
      errorCategory: category,
      ...safeErrorMetadata(error),
    });
    emitRecoverableError(
      this.options.logger,
      category,
      this.baseContext("assemblyai"),
      error,
    );
    this.recordRecoverableError(category);
    await this.publishState("error");
    await this.publishError({
      code: "VOICE_STT_UNAVAILABLE",
      message: "Speech recognition was interrupted. Please try speaking again.",
      recoverable: true,
    });
  }

  async markReconnecting(): Promise<void> {
    if (this.closed) return;
    if (this.reconnectStartedAt === null) {
      this.reconnectStartedAt = this.monotonicNow();
      this.reconnectCount += 1;
      this.options.logger.info("reconnect_started", {
        ...this.baseContext("livekit"),
      });
      this.metrics.increment("reconnect_attempts", this.baseContext("livekit"));
    }
    await this.publishState("reconnecting");
  }

  async markReconnected(): Promise<void> {
    if (this.closed) return;
    if (this.reconnectStartedAt !== null) {
      const durationMs = nonNegativeMilliseconds(
        this.monotonicNow() - this.reconnectStartedAt,
      );
      this.reconnectStartedAt = null;
      this.options.logger.info("reconnect_completed", {
        ...this.baseContext("livekit"),
        durationMs,
      });
      this.metrics.increment("reconnect_successes", this.baseContext("livekit"));
      this.metrics.observe(
        "reconnect_latency_ms",
        durationMs,
        this.baseContext("livekit"),
      );
    }
    await this.publishState("listening");
  }

  participantDisconnected(): Promise<void> {
    return this.shutdown("participant_disconnected");
  }

  interruptSpeech(reason: "natural_barge_in" | "explicit_stop"): boolean {
    const requestedAt = this.monotonicNow();
    this.interruptionEpoch += 1;
    const active = this.activeSpeech;
    if (!active) return false;
    this.options.logger.info("interruption_requested", {
      ...this.baseContext(),
      assistantMessageId: active.responseId,
      reason,
    });
    active.controller.abort();
    this.options.audio.clear();
    const interruptionDurationMs = nonNegativeMilliseconds(
      this.monotonicNow() - requestedAt,
    );
    this.options.logger.info("interruption_completed", {
      ...this.baseContext(),
      assistantMessageId: active.responseId,
      reason,
      durationMs: interruptionDurationMs,
    });
    this.metrics.increment("interruptions", this.baseContext());
    this.interruptionCount += 1;
    this.metrics.observe(
      "interruption_stop_latency_ms",
      interruptionDurationMs,
      this.baseContext(),
    );
    this.options.logger.info("voice_speech_interrupted", {
      voiceSessionId: this.options.binding.voiceSessionId,
      assistantMessageId: active.responseId,
      reason,
    });
    return true;
  }

  shutdown(reason: string): Promise<void> {
    this.shutdownPromise ??= this.performShutdown(reason);
    return this.shutdownPromise;
  }

  private async processFinalTurn(input: {
    clientMessageId: string;
    text: string;
    livekitStreamId: string;
    interruptionEpoch: number;
    finalReceivedAt: number;
    speechEndedAt?: number | undefined;
  }): Promise<void> {
    if (this.closed) return;
    await this.publishState("thinking");
    const backendStartedAt = this.monotonicNow();
    const finalToBackendDurationMs = nonNegativeMilliseconds(
      backendStartedAt - input.finalReceivedAt,
    );
    this.options.logger.info("backend_turn_started", {
      voiceSessionId: this.options.binding.voiceSessionId,
      conversationId: this.options.binding.conversationId,
      clientMessageId: input.clientMessageId,
      streamId: input.livekitStreamId,
      inputMode: "voice",
      finalToBackendDurationMs,
    });
    // Compatibility alias retained for existing log consumers.
    this.options.logger.info("voice_backend_turn_started", {
      voiceSessionId: this.options.binding.voiceSessionId,
      conversationId: this.options.binding.conversationId,
      clientMessageId: input.clientMessageId,
      livekitStreamId: input.livekitStreamId,
      inputMode: "voice",
      finalToBackendDurationMs,
    });

    let response;
    try {
      response = await this.options.backend.submitFinalizedTurn(
        this.options.binding.voiceSessionId,
        {
          clientMessageId: input.clientMessageId,
          text: input.text,
          inputMode: "voice",
          pageContext: this.options.binding.pageContext,
          livekitStreamId: input.livekitStreamId,
        },
      );
    } catch (error) {
      this.options.logger.warn("voice_backend_turn_failed", {
        voiceSessionId: this.options.binding.voiceSessionId,
        clientMessageId: input.clientMessageId,
        livekitStreamId: input.livekitStreamId,
        errorCategory: backendErrorCategory(error),
        ...safeErrorMetadata(error),
      });
      const category = backendErrorCategory(error);
      const emit = category === "backend_unauthorized"
        ? emitUnrecoverableError
        : emitRecoverableError;
      emit(
        this.options.logger,
        category,
        {
          ...this.baseContext(),
          clientMessageId: input.clientMessageId,
          streamId: input.livekitStreamId,
          inputMode: "voice",
        },
        error,
      );
      this.metrics.increment("backend_turns_failed", {
        ...this.baseContext(),
        errorCategory: category,
      });
      this.failedBackendTurnCount += 1;
      if (category !== "backend_unauthorized") {
        this.recordRecoverableError(category);
      }
      await this.publishState("error");
      await this.publishError(backendVoiceError(error));
      return;
    }

    const canonicalReceivedAt = this.monotonicNow();
    const backendDurationMs = nonNegativeMilliseconds(
      canonicalReceivedAt - backendStartedAt,
    );
    this.options.logger.info("backend_turn_completed", {
      voiceSessionId: this.options.binding.voiceSessionId,
      clientMessageId: input.clientMessageId,
      conversationId: response.conversationId,
      userMessageId: response.userMessageId,
      assistantMessageId: response.assistantMessageId,
      requestId: response.requestId,
      graphVersion: graphVersion(response),
      inputMode: "voice",
      durationMs: backendDurationMs,
    });
    // Compatibility alias retained for existing log consumers.
    this.options.logger.info("voice_backend_turn_completed", {
      voiceSessionId: this.options.binding.voiceSessionId,
      clientMessageId: input.clientMessageId,
      conversationId: response.conversationId,
      userMessageId: response.userMessageId,
      assistantMessageId: response.assistantMessageId,
      requestId: response.requestId,
      durationMs: backendDurationMs,
    });
    this.metrics.increment("backend_turns_completed", {
      ...this.baseContext(),
      requestId: response.requestId,
    });
    this.successfulBackendTurnCount += 1;
    this.captureProviderUsage(response);
    this.metrics.observe("backend_turn_latency_ms", backendDurationMs, {
      ...this.baseContext(),
      requestId: response.requestId,
    });
    const graphDuration = graphDurationMs(response);
    if (graphDuration !== undefined) {
      this.metrics.observe("graph_latency_ms", graphDuration, {
        ...this.baseContext(),
        requestId: response.requestId,
        graphVersion: graphVersion(response),
      });
    }

    // Publish the complete canonical result before starting TTS. The browser
    // retains the full answer even when speech is interrupted or TTS fails.
    let canonicalPublishedAt: number;
    try {
      await this.options.transport.publishCanonicalResponse(response);
      canonicalPublishedAt = this.monotonicNow();
      this.options.logger.info("canonical_response_published", {
        ...this.baseContext(),
        clientMessageId: input.clientMessageId,
        userMessageId: response.userMessageId,
        assistantMessageId: response.assistantMessageId,
        requestId: response.requestId,
        streamId: input.livekitStreamId,
        graphVersion: graphVersion(response),
        inputMode: "voice",
      });
    } catch (error) {
      this.options.logger.warn("voice_canonical_response_publish_failed", {
        voiceSessionId: this.options.binding.voiceSessionId,
        assistantMessageId: response.assistantMessageId,
        errorCategory: "audio_track_failure",
        ...safeErrorMetadata(error),
      });
      this.recordRecoverableError("audio_track_failure");
      return;
    }

    if (
      this.closed ||
      input.interruptionEpoch !== this.interruptionEpoch
    ) {
      await this.publishState("listening");
      return;
    }

    const controller = new AbortController();
    // Voice turns are always persisted to a conversation, so the id is present
    // at runtime; the shared contract keeps it optional for stateless asks.
    this.activeSpeech = {
      controller,
      responseId: response.assistantMessageId ?? response.requestId ?? "unpersisted-turn",
    };
    await this.publishState("speaking");
    let ttsStartedAt: number | undefined;
    let firstTtsChunkAt: number | undefined;
    let firstAudioFrameAt: number | undefined;
    const correlation = {
      ...this.baseContext("cartesia"),
      requestId: response.requestId,
      clientMessageId: input.clientMessageId,
      userMessageId: response.userMessageId,
      assistantMessageId: response.assistantMessageId,
      streamId: input.livekitStreamId,
      graphVersion: graphVersion(response),
      inputMode: "voice" as const,
    };
    const observedAudio = this.observedAudioSink(() => {
      if (firstAudioFrameAt !== undefined) return;
      firstAudioFrameAt = this.monotonicNow();
      const canonicalToAudioDurationMs = nonNegativeMilliseconds(
        firstAudioFrameAt - canonicalPublishedAt,
      );
      const chunkToAudioDurationMs =
        firstTtsChunkAt === undefined
          ? undefined
          : nonNegativeMilliseconds(firstAudioFrameAt - firstTtsChunkAt);
      const speechEndToAudioDurationMs =
        input.speechEndedAt === undefined
          ? undefined
          : nonNegativeMilliseconds(firstAudioFrameAt - input.speechEndedAt);
      this.options.logger.info("audio_first_frame_published", {
        ...correlation,
        canonicalToAudioDurationMs,
        ...(chunkToAudioDurationMs === undefined
          ? {}
          : { firstChunkToAudioDurationMs: chunkToAudioDurationMs }),
        ...(speechEndToAudioDurationMs === undefined
          ? {}
          : { speechEndToAudioDurationMs }),
      });
      this.metrics.observe(
        "first_audio_latency_ms",
        canonicalToAudioDurationMs,
        correlation,
      );
    });
    try {
      // Only canonical message text is sent to Cartesia. IDs, structured
      // response data, receipts, widgets, and internal metadata stay local.
      await this.options.synthesizer.speak(
        response.message,
        observedAudio,
        controller.signal,
        {
          context: correlation,
          onStarted: () => {
            ttsStartedAt = this.monotonicNow();
            this.options.logger.info("tts_started_latency", {
              ...correlation,
              canonicalToTtsStartDurationMs: nonNegativeMilliseconds(
                ttsStartedAt - canonicalPublishedAt,
              ),
            });
            this.metrics.observe(
              "canonical_to_tts_start_latency_ms",
              ttsStartedAt - canonicalPublishedAt,
              correlation,
            );
          },
          onFirstChunk: () => {
            firstTtsChunkAt = this.monotonicNow();
            const start = ttsStartedAt ?? firstTtsChunkAt;
            const durationMs = nonNegativeMilliseconds(firstTtsChunkAt - start);
            this.metrics.observe(
              "tts_first_chunk_latency_ms",
              durationMs,
              correlation,
            );
          },
        },
      );
      if (!controller.signal.aborted) {
        this.metrics.increment("tts_turns_completed", correlation);
        this.ttsSuccessCount += 1;
      }
    } catch (error) {
      if (!controller.signal.aborted) {
        this.options.logger.warn("voice_tts_failed", {
          voiceSessionId: this.options.binding.voiceSessionId,
          assistantMessageId: response.assistantMessageId,
          errorCategory: "tts_provider_error",
          ...safeErrorMetadata(error),
        });
        emitRecoverableError(
          this.options.logger,
          "tts_provider_error",
          {
            ...this.baseContext("cartesia"),
            requestId: response.requestId,
            clientMessageId: input.clientMessageId,
            userMessageId: response.userMessageId,
            assistantMessageId: response.assistantMessageId,
            streamId: input.livekitStreamId,
            graphVersion: graphVersion(response),
            inputMode: "voice",
          },
          error,
        );
        this.metrics.increment("tts_turns_failed", correlation);
        this.ttsFailureCount += 1;
        this.recordRecoverableError("tts_provider_error");
        await this.publishState("error");
        await this.publishError({
          code: "VOICE_TTS_UNAVAILABLE",
          message: "The answer is available as text, but audio playback failed.",
          recoverable: true,
        });
      }
    } finally {
      if (this.activeSpeech?.controller === controller) {
        this.activeSpeech = null;
      }
    }
    if (!this.closed) await this.publishState("listening");
  }

  private async performShutdown(reason: string): Promise<void> {
    if (this.closed) return;
    this.closed = true;
    if (this.reconnectStartedAt !== null) {
      this.reconnectStartedAt = null;
      this.recordRecoverableError("reconnect_failure");
      emitRecoverableError(
        this.options.logger,
        "reconnect_failure",
        this.baseContext("livekit"),
      );
    }
    this.interruptSpeech("explicit_stop");
    await this.turnQueue.catch(() => undefined);
    this.options.logger.info("voice_session_shutdown_started", {
      voiceSessionId: this.options.binding.voiceSessionId,
      reason,
    });
    await this.options.backend.endSession(this.options.binding.voiceSessionId).catch(
      (error: unknown) => {
        this.options.logger.warn("voice_session_end_notification_failed", {
          voiceSessionId: this.options.binding.voiceSessionId,
          errorCategory: "backend_turn_failure",
          ...safeErrorMetadata(error),
        });
      },
    );
    await Promise.allSettled([
      this.options.transport.close(),
      this.options.synthesizer.close(),
      closeAudio(this.options.audio),
    ]);
    this.options.logger.info("voice_session_shutdown_completed", {
      voiceSessionId: this.options.binding.voiceSessionId,
      reason,
    });
    const sessionDurationMs = nonNegativeMilliseconds(
      this.monotonicNow() - this.sessionStartedAt,
    );
    this.options.logger.info("voice_session_ended", {
      ...this.baseContext(),
      sessionDurationMs,
      finalizedTurnCount: this.finalizedTurnCount,
      successfulBackendTurnCount: this.successfulBackendTurnCount,
      failedBackendTurnCount: this.failedBackendTurnCount,
      ttsSuccessCount: this.ttsSuccessCount,
      ttsFailureCount: this.ttsFailureCount,
      interruptionCount: this.interruptionCount,
      reconnectCount: this.reconnectCount,
      duplicateFinalsSuppressed: this.duplicateFinalsSuppressed,
      recoverableErrorCountByCategory: { ...this.recoverableErrors },
      recoverableErrorCount: Object.values(this.recoverableErrors).reduce(
        (total, value) => total + (value ?? 0),
        0,
      ),
      endState: sessionEndState(reason),
      endReason: reason,
      providerUsageUnits: { ...this.providerUsageUnits },
    });
  }

  private captionSegment(livekitStreamId: string): string {
    const existing = this.captionSegments.get(livekitStreamId);
    if (existing) return existing;
    const segmentId = this.createId();
    this.captionSegments.set(livekitStreamId, segmentId);
    return segmentId;
  }

  private isDuplicateFinal(
    event: Extract<TranscriptEvent, { type: "final" }>,
    text: string,
  ): boolean {
    const now = this.now();
    for (const [key, seenAt] of this.duplicateFinals) {
      if (now - seenAt > duplicateFinalWindowMs) this.duplicateFinals.delete(key);
    }
    const fingerprint = [
      event.livekitStreamId,
      event.startTime.toFixed(3),
      event.endTime.toFixed(3),
      text.toLocaleLowerCase("en-US").replace(/\s+/g, " "),
    ].join("|");
    if (this.duplicateFinals.has(fingerprint)) return true;
    this.duplicateFinals.set(fingerprint, now);
    if (this.duplicateFinals.size > duplicateFinalLimit) {
      const oldest = this.duplicateFinals.keys().next().value;
      if (oldest) this.duplicateFinals.delete(oldest);
    }
    return false;
  }

  private async publishState(state: VoiceAgentState): Promise<void> {
    this.state = state;
    await this.options.transport.publishState(state).catch((error: unknown) => {
      this.options.logger.warn("voice_state_publish_failed", {
        voiceSessionId: this.options.binding.voiceSessionId,
        state,
        errorCategory: "livekit_connection_failure",
        ...safeErrorMetadata(error),
      });
    });
  }

  private async publishError(error: RecoverableVoiceError): Promise<void> {
    await this.options.transport.publishError(error).catch((cause: unknown) => {
      this.options.logger.warn("voice_error_event_publish_failed", {
        voiceSessionId: this.options.binding.voiceSessionId,
        errorCode: error.code,
        errorCategory: "livekit_connection_failure",
        ...safeErrorMetadata(cause),
      });
    });
  }

  private baseContext(provider?: string) {
    return {
      voiceSessionId: this.options.binding.voiceSessionId,
      conversationId: this.options.binding.conversationId,
      roomName: this.options.binding.roomName,
      participantIdentity: this.options.binding.participantIdentity,
      ...(provider ? { provider } : {}),
    };
  }

  private observedAudioSink(onFirstFrame: () => void): AudioPlaybackSink {
    let captured = false;
    return {
      captureFrame: async (frame) => {
        await this.options.audio.captureFrame(frame);
        if (!captured) {
          captured = true;
          onFirstFrame();
        }
      },
      waitForPlayout: () => this.options.audio.waitForPlayout(),
      clear: () => this.options.audio.clear(),
    };
  }

  private recordRecoverableError(category: VoiceErrorCategory): void {
    this.recoverableErrors[category] = (this.recoverableErrors[category] ?? 0) + 1;
  }

  private captureProviderUsage(response: {
    provider: string;
    usage?: unknown;
  }): void {
    const usage = response.usage;
    if (!usage || typeof usage !== "object") return;
    const totalTokens = (usage as Record<string, unknown>).totalTokens;
    if (typeof totalTokens !== "number" || !Number.isFinite(totalTokens)) return;
    this.providerUsageUnits[response.provider] =
      (this.providerUsageUnits[response.provider] ?? 0) +
      Math.max(0, Math.round(totalTokens));
  }
}

function backendErrorCategory(error: unknown): VoiceErrorCategory {
  if (error instanceof VoiceBackendError) {
    if (error.code === "timeout") return "backend_timeout";
    if (error.code === "unauthorized") return "backend_unauthorized";
  }
  return "backend_turn_failure";
}

function graphVersion(response: { studentAssistant?: unknown }): string | undefined {
  const studentAssistant = response.studentAssistant;
  if (!studentAssistant || typeof studentAssistant !== "object") return undefined;
  const execution = (studentAssistant as Record<string, unknown>).graphExecution;
  if (!execution || typeof execution !== "object") return undefined;
  const version = (execution as Record<string, unknown>).graphVersion;
  return typeof version === "string" ? version : undefined;
}

function graphDurationMs(response: { studentAssistant?: unknown }): number | undefined {
  const studentAssistant = response.studentAssistant;
  if (!studentAssistant || typeof studentAssistant !== "object") return undefined;
  const execution = (studentAssistant as Record<string, unknown>).graphExecution;
  if (!execution || typeof execution !== "object") return undefined;
  const duration = (execution as Record<string, unknown>).durationMs;
  return typeof duration === "number" ? nonNegativeMilliseconds(duration) : undefined;
}

function sessionEndState(
  reason: string,
): "normal" | "explicit" | "disconnect" | "error" {
  if (reason === "participant_disconnected") return "disconnect";
  if (reason === "explicit_end") return "explicit";
  if (reason.includes("error") || reason.includes("failed")) return "error";
  return "normal";
}

function backendVoiceError(error: unknown): RecoverableVoiceError {
  if (error instanceof VoiceBackendError) {
    if (error.code === "timeout") {
      return {
        code: "VOICE_BACKEND_TIMEOUT",
        message: "The assistant took too long to respond. Please try again.",
        recoverable: true,
      };
    }
    if (error.code === "unauthorized") {
      return {
        code: "VOICE_BACKEND_UNAUTHORIZED",
        message: "The voice service is not authorized.",
        recoverable: false,
      };
    }
  }
  return {
    code: "VOICE_BACKEND_UNAVAILABLE",
    message: "The assistant is temporarily unavailable. Please try again.",
    recoverable: true,
  };
}

async function closeAudio(audio: AudioPlaybackSink): Promise<void> {
  const close = (audio as AudioPlaybackSink & { close?: () => Promise<void> }).close;
  if (close) await close.call(audio);
}
