import { stt, tts } from "@livekit/agents";
import * as assemblyai from "@livekit/agents-plugin-assemblyai";
import * as cartesia from "@livekit/agents-plugin-cartesia";
import { AudioStream, type AudioFrame, type RemoteAudioTrack } from "@livekit/rtc-node";
import {
  assertPcm16Frame,
  defaultAudioFrameDurationMs,
  pcm16BytesPerSample,
  pcm16Encoding,
} from "./pcm-audio.js";
import type { Logger } from "./types.js";
import { safeErrorMetadata } from "./observability.js";
import type { VoiceCorrelationContext } from "./observability.js";

export type TranscriptEvent =
  | { type: "speech_started"; livekitStreamId: string }
  | {
      type: "interim";
      livekitStreamId: string;
      text: string;
      language: string;
      startTime: number;
      endTime: number;
    }
  | {
      type: "final";
      livekitStreamId: string;
      text: string;
      language: string;
      startTime: number;
      endTime: number;
    }
  | { type: "speech_ended"; livekitStreamId: string };

export interface TranscriptionHandle {
  readonly livekitStreamId: string;
  readonly done: Promise<void>;
  close(): Promise<void>;
}

export interface TranscriptionEventSink {
  onTranscript(event: TranscriptEvent): void | Promise<void>;
  onError(error: Error): void | Promise<void>;
}

export interface StreamingTranscriber {
  start(
    track: RemoteAudioTrack,
    livekitStreamId: string,
    sink: TranscriptionEventSink,
  ): TranscriptionHandle;
  close(): Promise<void>;
}

export interface AudioPlaybackSink {
  captureFrame(frame: AudioFrame): Promise<void>;
  waitForPlayout(): Promise<void>;
  clear(): void;
}

export interface StreamingSynthesizer {
  readonly sampleRate: number;
  readonly numChannels: number;
  prewarm(): void;
  speak(
    text: string,
    sink: AudioPlaybackSink,
    signal: AbortSignal,
    observability?: SynthesisObservability,
  ): Promise<void>;
  close(): Promise<void>;
}

export interface SynthesisObservability {
  context: VoiceCorrelationContext;
  onStarted?(): void;
  onFirstChunk?(): void;
}

export class AssemblyAiStreamingTranscriber implements StreamingTranscriber {
  private readonly provider: assemblyai.STT;
  private readonly errorSinks = new Set<(error: Error) => void>();

  constructor(apiKey: string, private readonly logger: Logger) {
    this.provider = new assemblyai.STT({
      apiKey,
      speechModel: "universal-3-5-pro",
      formatTurns: true,
      mode: "min_latency",
    });
    this.provider.on("error", (event: Parameters<stt.STTCallbacks["error"]>[0]) => {
      this.logger.warn("voice_stt_provider_error", {
        provider: "assemblyai",
        recoverable: event.recoverable,
        errorCategory: "stt_provider_error",
        ...safeErrorMetadata(event.error),
      });
      for (const sink of this.errorSinks) sink(event.error);
    });
  }

  start(
    track: RemoteAudioTrack,
    livekitStreamId: string,
    sink: TranscriptionEventSink,
  ): TranscriptionHandle {
    const speechStream = this.provider.stream();
    const audioStream = new AudioStream(track, {
      sampleRate: 16_000,
      numChannels: 1,
    });
    let closed = false;
    let reportedError = false;

    const reportError = (error: Error): void => {
      if (closed || reportedError) return;
      reportedError = true;
      void sink.onError(error);
    };
    this.errorSinks.add(reportError);

    const pumpAudio = async (): Promise<void> => {
      const reader = audioStream.getReader();
      try {
        while (!closed) {
          const next = await reader.read();
          if (next.done) break;
          speechStream.pushFrame(next.value);
        }
      } catch (error) {
        if (!closed) reportError(asError(error));
      } finally {
        reader.releaseLock();
      }
    };

    const consumeTranscripts = async (): Promise<void> => {
      try {
        for await (const event of speechStream) {
          if (closed) break;
          const mapped = mapTranscriptEvent(event, livekitStreamId);
          if (mapped) await sink.onTranscript(mapped);
        }
      } catch (error) {
        if (!closed) reportError(asError(error));
      }
    };

    const done = Promise.allSettled([pumpAudio(), consumeTranscripts()]).then(
      () => undefined,
    );

    return {
      livekitStreamId,
      done,
      close: async () => {
        if (closed) return;
        closed = true;
        this.errorSinks.delete(reportError);
        speechStream.close();
        await audioStream.cancel().catch(() => undefined);
        await done;
      },
    };
  }

  async close(): Promise<void> {
    this.errorSinks.clear();
    await this.provider.close();
  }
}

export class CartesiaStreamingSynthesizer implements StreamingSynthesizer {
  private readonly provider: cartesia.TTS;
  private readonly errorSinks = new Set<(error: Error) => void>();

  constructor(
    apiKey: string,
    voiceId: string,
    private readonly logger: Logger,
    provider?: cartesia.TTS,
  ) {
    this.provider = provider ?? new cartesia.TTS({
      apiKey,
      voice: voiceId,
      model: "sonic-3",
      language: "en",
      encoding: pcm16Encoding,
      sampleRate: 24_000,
    });
    this.provider.on("error", (event: Parameters<tts.TTSCallbacks["error"]>[0]) => {
      this.logger.warn("voice_tts_provider_error", {
        provider: "cartesia",
        recoverable: event.recoverable,
        errorCategory: "tts_provider_error",
        ...safeErrorMetadata(event.error),
      });
      for (const sink of this.errorSinks) sink(event.error);
    });
  }

  get sampleRate(): number {
    return this.provider.sampleRate;
  }

  get numChannels(): number {
    return this.provider.numChannels;
  }

  prewarm(): void {
    this.provider.prewarm();
  }

  async speak(
    text: string,
    sink: AudioPlaybackSink,
    signal: AbortSignal,
    observability?: SynthesisObservability,
  ): Promise<void> {
    let providerError: Error | null = null;
    const recordError = (error: Error): void => {
      providerError ??= error;
    };
    this.errorSinks.add(recordError);
    const stream = this.provider.stream();
    const closeOnAbort = (): void => stream.close();
    signal.addEventListener("abort", closeOnAbort, { once: true });
    const expectedBytesPerFrame =
      (this.sampleRate * defaultAudioFrameDurationMs / 1_000) *
      this.numChannels * pcm16BytesPerSample;
    let totalBytes = 0;
    let completeFrames = 0;
    let partialFrames = 0;
    let trailingBytesAtFlush = 0;
    let firstChunkReceived = false;
    observability?.onStarted?.();
    this.logger.info("tts_started", {
      ...observability?.context,
      provider: "cartesia",
      characters: text.length,
    });
    // Compatibility alias retained for existing log consumers.
    this.logger.info("tts_request_started", {
      ...observability?.context,
      provider: "cartesia",
      characters: text.length,
    });
    this.logger.info("voice_tts_format_selected", {
      provider: "cartesia",
      transport: "websocket",
      container: "raw",
      encoding: pcm16Encoding,
      sampleRate: this.sampleRate,
      channels: this.numChannels,
      bytesPerSample: pcm16BytesPerSample,
      expectedBytesPerFrame,
    });
    try {
      // endInput() performs the one and only utterance flush. Cartesia's
      // WebSocket adapter carries arbitrary chunk remainders between writes.
      stream.pushText(text);
      stream.endInput();
      for await (const audio of stream) {
        if (typeof audio === "symbol") continue;
        if (signal.aborted) break;
        assertPcm16Frame(audio.frame, {
          encoding: pcm16Encoding,
          sampleRate: this.sampleRate,
          numChannels: this.numChannels,
        });
        const frameBytes = audio.frame.data.byteLength;
        if (!firstChunkReceived) {
          firstChunkReceived = true;
          observability?.onFirstChunk?.();
          this.logger.info("tts_first_chunk", {
            ...observability?.context,
            provider: "cartesia",
            bytes: frameBytes,
          });
          // Compatibility alias retained for existing log consumers.
          this.logger.info("tts_first_chunk_received", {
            ...observability?.context,
            provider: "cartesia",
            bytes: frameBytes,
          });
        }
        totalBytes += frameBytes;
        if (frameBytes === expectedBytesPerFrame) completeFrames += 1;
        else {
          partialFrames += 1;
          trailingBytesAtFlush = frameBytes;
        }
        await sink.captureFrame(audio.frame);
      }
      if (!signal.aborted && providerError) throw providerError;
      if (!signal.aborted && totalBytes === 0) {
        throw new Error("Cartesia TTS completed without emitting PCM audio frames");
      }
      if (!signal.aborted) await sink.waitForPlayout();
      this.logger.info("tts_completed", {
        ...observability?.context,
        provider: "cartesia",
        totalTtsBytesReceived: totalBytes,
        completeFramesEmitted: completeFrames,
        partialFramesEmitted: partialFrames,
        trailingBytesAtFlush,
        trailingBytesDropped: 0,
        flushCalls: 1,
        interrupted: signal.aborted,
      });
    } finally {
      this.errorSinks.delete(recordError);
      signal.removeEventListener("abort", closeOnAbort);
      if (signal.aborted) stream.close();
    }
  }

  async close(): Promise<void> {
    this.errorSinks.clear();
    await this.provider.close();
  }
}

function mapTranscriptEvent(
  event: stt.SpeechEvent,
  livekitStreamId: string,
): TranscriptEvent | null {
  if (event.type === stt.SpeechEventType.START_OF_SPEECH) {
    return { type: "speech_started", livekitStreamId };
  }
  if (event.type === stt.SpeechEventType.END_OF_SPEECH) {
    return { type: "speech_ended", livekitStreamId };
  }
  if (
    event.type !== stt.SpeechEventType.INTERIM_TRANSCRIPT &&
    event.type !== stt.SpeechEventType.FINAL_TRANSCRIPT
  ) {
    return null;
  }
  const alternative = event.alternatives?.[0];
  if (!alternative?.text.trim()) return null;
  const transcript = {
    livekitStreamId,
    text: alternative.text,
    language: alternative.language,
    startTime: alternative.startTime,
    endTime: alternative.endTime,
  };
  return event.type === stt.SpeechEventType.FINAL_TRANSCRIPT
    ? { type: "final", ...transcript }
    : { type: "interim", ...transcript };
}

function asError(error: unknown): Error {
  return error instanceof Error ? error : new Error(String(error));
}
