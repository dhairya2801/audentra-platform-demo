import {
  AutoSubscribe,
  defineAgent,
  type JobContext,
} from "@livekit/agents";
import {
  RemoteAudioTrack,
  RoomEvent,
  type RemoteParticipant,
  type RemoteTrack,
  type RemoteTrackPublication,
} from "@livekit/rtc-node";
import { VoiceBackendClient } from "./backend-client.js";
import { publishDiagnosticTone } from "./audio-diagnostic.js";
import { loadConfig } from "./config.js";
import {
  LiveKitAudioPlayback,
  LiveKitBrowserTransport,
} from "./livekit-transport.js";
import { createLogger } from "./logger.js";
import {
  emitUnrecoverableError,
  nonNegativeMilliseconds,
  safeErrorMetadata,
  VoiceMetrics,
} from "./observability.js";
import {
  AssemblyAiStreamingTranscriber,
  CartesiaStreamingSynthesizer,
  type TranscriptionHandle,
} from "./providers.js";
import type { VoiceSessionBinding } from "./types.js";
import { VoiceSession } from "./voice-session.js";

const participantWaitTimeoutMs = 30_000;

export default defineAgent({
  entry: async (ctx: JobContext) => {
    const jobAcceptedAt = performance.now();
    const config = loadConfig();
    const voiceSessionId = parseDispatchMetadata(ctx.job.metadata);
    const logger = createLogger(config.logLevel, {
      jobId: ctx.job.id,
      voiceSessionId,
    });
    const backend = new VoiceBackendClient({
      baseUrl: config.internalApiUrl,
      token: config.internalToken,
      timeoutMs: config.internalApiTimeoutMs,
      maxAttempts: config.internalApiMaxAttempts,
      retryBaseMs: config.internalApiRetryBaseMs,
      logger,
    });
    const metrics = new VoiceMetrics(logger);
    let workerReadyAt: number | undefined;
    logger.info("voice_worker_accepted", {
      provider: "livekit",
    });

    logger.info("voice_session_lookup_started");
    const binding = await backend.lookupSession(voiceSessionId);
    assertAuthoritativeBinding(ctx, voiceSessionId, binding);
    logger.info("voice_session_lookup_completed", {
      conversationId: binding.conversationId,
    });

    let session: VoiceSession | null = null;
    let transcription: TranscriptionHandle | null = null;
    let pendingMicrophone: {
      track: RemoteAudioTrack;
      livekitStreamId: string;
    } | null = null;
    let shutdownPromise: Promise<void> | null = null;
    let transcriptionSwap = Promise.resolve();

    const transcriber = new AssemblyAiStreamingTranscriber(
      config.assemblyAiApiKey,
      logger,
    );

    const closeTranscription = async (): Promise<void> => {
      const active = transcription;
      transcription = null;
      if (active) await active.close();
    };

    const startTranscription = (
      track: RemoteAudioTrack,
      livekitStreamId: string,
    ): void => {
      transcriptionSwap = transcriptionSwap
        .then(async () => {
          await closeTranscription();
          if (!session) {
            pendingMicrophone = { track, livekitStreamId };
            return;
          }
          transcription = transcriber.start(track, livekitStreamId, {
            onTranscript(event) {
              void session?.handleTranscript(event).catch((error: unknown) => {
                logger.warn("voice_transcript_handling_failed", {
                  livekitStreamId,
                  errorCategory: "unknown_voice_error",
                  ...safeErrorMetadata(error),
                });
              });
            },
            onError(error) {
              void session?.handleSttFailure(error).then(() =>
                session?.markReconnecting(),
              );
              const restart = setTimeout(() => {
                if (!shutdownPromise) {
                  startTranscription(track, livekitStreamId);
                }
              }, 250);
              restart.unref();
            },
          });
          const microphoneSubscribedAt = performance.now();
          const readyToMicrophoneDurationMs =
            workerReadyAt === undefined
              ? undefined
              : nonNegativeMilliseconds(microphoneSubscribedAt - workerReadyAt);
          logger.info("microphone_track_subscribed", {
            conversationId: binding.conversationId,
            participantIdentity: binding.participantIdentity,
            streamId: livekitStreamId,
            provider: "livekit",
            ...(readyToMicrophoneDurationMs === undefined
              ? {}
              : { workerReadyToMicrophoneDurationMs: readyToMicrophoneDurationMs }),
          });
          if (readyToMicrophoneDurationMs !== undefined) {
            metrics.observe(
              "worker_ready_to_microphone_latency_ms",
              readyToMicrophoneDurationMs,
              {
                conversationId: binding.conversationId,
                voiceSessionId,
                jobId: ctx.job.id,
                streamId: livekitStreamId,
                provider: "livekit",
              },
            );
          }
          logger.info("stt_session_started", {
            conversationId: binding.conversationId,
            participantIdentity: binding.participantIdentity,
            streamId: livekitStreamId,
            provider: "assemblyai",
          });
          logger.info("voice_microphone_stream_started", {
            livekitStreamId,
            conversationId: binding.conversationId,
            participantIdentity: binding.participantIdentity,
            provider: "assemblyai",
          });
          void session.markReconnected();
        })
        .catch((error: unknown) => {
          logger.warn("voice_microphone_stream_start_failed", {
            livekitStreamId,
            errorCategory: "stt_connection_failure",
            ...safeErrorMetadata(error),
          });
          void session?.handleSttFailure(error, "stt_connection_failure");
        });
    };

    const onTrackSubscribed = (
      track: RemoteTrack,
      publication: RemoteTrackPublication,
      participant: RemoteParticipant,
    ): void => {
      if (
        participant.identity !== binding.participantIdentity ||
        !(track instanceof RemoteAudioTrack)
      ) {
        return;
      }
      const livekitStreamId = publication.sid;
      if (!livekitStreamId) {
        logger.warn("voice_microphone_stream_missing_id");
        return;
      }
      startTranscription(track, livekitStreamId);
    };

    const onTrackUnsubscribed = (
      _track: RemoteTrack,
      publication: RemoteTrackPublication,
      participant: RemoteParticipant,
    ): void => {
      if (
        participant.identity === binding.participantIdentity &&
        publication.sid === transcription?.livekitStreamId
      ) {
        transcriptionSwap = transcriptionSwap.then(closeTranscription);
        void session?.markReconnecting();
      }
    };

    const onParticipantDisconnected = (participant: RemoteParticipant): void => {
      if (participant.identity !== binding.participantIdentity) return;
      logger.info("voice_participant_disconnected");
      emitUnrecoverableError(logger, "participant_disconnected", {
        conversationId: binding.conversationId,
        voiceSessionId,
        jobId: ctx.job.id,
        participantIdentity: binding.participantIdentity,
        provider: "livekit",
      });
      void session?.participantDisconnected();
      void shutdown("participant_disconnected").finally(() => {
        ctx.shutdown("bound participant disconnected");
      });
    };

    const onReconnecting = (): void => {
      logger.warn("voice_livekit_reconnecting");
      void session?.markReconnecting();
    };
    const onReconnected = (): void => {
      logger.info("voice_livekit_reconnected");
      void session?.markReconnected();
    };

    const shutdown = (reason: string): Promise<void> => {
      shutdownPromise ??= (async () => {
        ctx.room.off(RoomEvent.TrackSubscribed, onTrackSubscribed);
        ctx.room.off(RoomEvent.TrackUnsubscribed, onTrackUnsubscribed);
        ctx.room.off(RoomEvent.ParticipantDisconnected, onParticipantDisconnected);
        ctx.room.off(RoomEvent.Reconnecting, onReconnecting);
        ctx.room.off(RoomEvent.Reconnected, onReconnected);
        await closeTranscription();
        await transcriber.close();
        if (session) {
          await session.shutdown(reason);
        } else {
          await backend.endSession(binding.voiceSessionId).catch(
            (error: unknown) => {
              logger.warn("voice_session_end_notification_failed", {
                errorCategory: "backend_turn_failure",
                ...safeErrorMetadata(error),
              });
            },
          );
        }
      })();
      return shutdownPromise;
    };

    ctx.room.on(RoomEvent.TrackSubscribed, onTrackSubscribed);
    ctx.room.on(RoomEvent.TrackUnsubscribed, onTrackUnsubscribed);
    ctx.room.on(RoomEvent.ParticipantDisconnected, onParticipantDisconnected);
    ctx.room.on(RoomEvent.Reconnecting, onReconnecting);
    ctx.room.on(RoomEvent.Reconnected, onReconnected);
    ctx.addShutdownCallback(() => shutdown("livekit_job_shutdown"));

    await ctx.connect(undefined, AutoSubscribe.AUDIO_ONLY);
    assertRoomBinding(ctx, binding);
    const participant = await waitForBoundParticipant(
      ctx,
      binding.participantIdentity,
      participantWaitTimeoutMs,
    );

    const synthesizer = new CartesiaStreamingSynthesizer(
      config.cartesiaApiKey,
      config.cartesiaVoiceId,
      logger,
    );
    const audio = new LiveKitAudioPlayback(
      ctx.room,
      synthesizer.sampleRate,
      synthesizer.numChannels,
      logger,
    );
    await audio.start();
    if (config.diagnosticToneEnabled) {
      logger.info("voice_diagnostic_tone_started", {
        sampleRate: synthesizer.sampleRate,
        channels: synthesizer.numChannels,
      });
      await publishDiagnosticTone(
        audio,
        synthesizer.sampleRate,
        synthesizer.numChannels,
        logger,
      );
    }
    const transport = new LiveKitBrowserTransport(ctx.room, binding, logger);
    session = new VoiceSession({
      binding,
      backend,
      transport,
      synthesizer,
      audio,
      logger,
      metrics,
    });
    await session.start();
    workerReadyAt = performance.now();
    const workerReadyDurationMs = nonNegativeMilliseconds(
      workerReadyAt - jobAcceptedAt,
    );
    const crossServiceWorkerReadyDurationMs = nonNegativeMilliseconds(
      Date.now() - Date.parse(binding.createdAt),
    );
    logger.info("voice_worker_ready", {
      conversationId: binding.conversationId,
      roomName: binding.roomName,
      participantIdentity: binding.participantIdentity,
      provider: "livekit",
      durationMs: workerReadyDurationMs,
      voiceSessionRequestToReadyDurationMs: crossServiceWorkerReadyDurationMs,
      timingBasis: "cross_service_wall_clock",
    });
    metrics.increment("voice_sessions_ready", {
      conversationId: binding.conversationId,
      voiceSessionId,
      jobId: ctx.job.id,
      provider: "livekit",
    });
    metrics.observe(
      "worker_ready_latency_ms",
      crossServiceWorkerReadyDurationMs,
      {
        conversationId: binding.conversationId,
        voiceSessionId,
        jobId: ctx.job.id,
        provider: "livekit",
      },
      "cross_service_wall_clock",
    );

    await transcriptionSwap;
    if (!transcription) {
      const pending = pendingMicrophone as {
        track: RemoteAudioTrack;
        livekitStreamId: string;
      } | null;
      if (pending) {
        pendingMicrophone = null;
        startTranscription(pending.track, pending.livekitStreamId);
      } else {
        for (const publication of participant.trackPublications.values()) {
          if (publication.track instanceof RemoteAudioTrack && publication.sid) {
            startTranscription(publication.track, publication.sid);
            break;
          }
        }
      }
    }
  },
});

export function parseDispatchMetadata(metadata: string): string {
  let value: unknown;
  try {
    value = JSON.parse(metadata);
  } catch {
    throw new Error("LiveKit dispatch metadata must be valid JSON");
  }
  if (
    !value ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    !("voiceSessionId" in value) ||
    typeof value.voiceSessionId !== "string" ||
    !isUuid(value.voiceSessionId)
  ) {
    throw new Error(
      "LiveKit dispatch metadata must contain a UUID voiceSessionId",
    );
  }
  return value.voiceSessionId;
}

function assertAuthoritativeBinding(
  ctx: JobContext,
  dispatchedVoiceSessionId: string,
  binding: VoiceSessionBinding,
): void {
  if (
    binding.voiceSessionId !== dispatchedVoiceSessionId ||
    binding.status !== "active"
  ) {
    throw new Error("Voice backend returned a non-active session binding");
  }
  assertRoomBinding(ctx, binding);
}

export function assertRoomBinding(
  ctx: JobContext,
  binding: VoiceSessionBinding,
): void {
  const roomName = ctx.room.name || ctx.job.room?.name;
  if (roomName !== binding.roomName) {
    throw new Error("Dispatched LiveKit room does not match the session binding");
  }
}

function waitForBoundParticipant(
  ctx: JobContext,
  identity: string,
  timeoutMs: number,
): Promise<RemoteParticipant> {
  const current = ctx.room.remoteParticipants.get(identity);
  if (current) return Promise.resolve(current);

  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      cleanup();
      reject(new Error("Timed out waiting for the session-bound participant"));
    }, timeoutMs);
    timeout.unref();

    const onConnected = (participant: RemoteParticipant): void => {
      if (participant.identity !== identity) return;
      cleanup();
      resolve(participant);
    };
    const onDisconnected = (): void => {
      cleanup();
      reject(new Error("LiveKit room disconnected while waiting for participant"));
    };
    const cleanup = (): void => {
      clearTimeout(timeout);
      ctx.room.off(RoomEvent.ParticipantConnected, onConnected);
      ctx.room.off(RoomEvent.Disconnected, onDisconnected);
    };
    ctx.room.on(RoomEvent.ParticipantConnected, onConnected);
    ctx.room.on(RoomEvent.Disconnected, onDisconnected);
  });
}

function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
    value,
  );
}
