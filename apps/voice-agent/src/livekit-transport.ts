import type { AssistantVoiceSessionDetails } from "@vv/contracts";
import {
  AudioSource,
  LocalAudioTrack,
  Room,
  TrackPublishOptions,
  TrackSource,
  type LocalTrackPublication,
  type RpcInvocationData,
} from "@livekit/rtc-node";
import type { AudioPlaybackSink } from "./providers.js";
import { assertPcm16Frame, pcm16Encoding } from "./pcm-audio.js";
import type { CanonicalAssistantResponse, Logger } from "./types.js";

export const voiceTopics = {
  response: "student-assistant.voice.response.v1",
  error: "student-assistant.voice.error.v1",
  state: "student-assistant.voice.state.v1",
} as const;

export const stopSpeakingRpcMethod = "student-assistant.stop-speaking.v1";

export type VoiceAgentState =
  | "initializing"
  | "listening"
  | "thinking"
  | "speaking"
  | "reconnecting"
  | "error";

export interface RecoverableVoiceError {
  code: string;
  message: string;
  recoverable: boolean;
}

export interface BrowserTransport {
  publishState(state: VoiceAgentState): Promise<void>;
  publishTranscript(input: {
    segmentId: string;
    livekitStreamId: string;
    text: string;
    language: string;
    final: boolean;
  }): Promise<void>;
  publishCanonicalResponse(response: CanonicalAssistantResponse): Promise<void>;
  publishError(error: RecoverableVoiceError): Promise<void>;
  registerStopSpeaking(handler: () => boolean): void;
  close(): Promise<void>;
}

const nativeAgentStates = new Set<VoiceAgentState>([
  "initializing",
  "listening",
  "thinking",
  "speaking",
]);

export class LiveKitBrowserTransport implements BrowserTransport {
  constructor(
    private readonly room: Room,
    private readonly binding: AssistantVoiceSessionDetails,
    private readonly logger: Logger,
  ) {}

  async publishState(state: VoiceAgentState): Promise<void> {
    const participant = this.localParticipant();
    await participant.setAttributes({
      "vv.voice.state": state,
      ...(nativeAgentStates.has(state) ? { "lk.agent.state": state } : {}),
    });
    await this.sendJson(voiceTopics.state, {
      type: voiceTopics.state,
      voiceSessionId: this.binding.voiceSessionId,
      state,
    });
  }

  async publishTranscript(input: {
    segmentId: string;
    livekitStreamId: string;
    text: string;
    language: string;
    final: boolean;
  }): Promise<void> {
    await this.localParticipant().publishTranscription({
      participantIdentity: this.binding.participantIdentity,
      trackSid: input.livekitStreamId,
      segments: [
        {
          id: input.segmentId,
          text: input.text,
          final: input.final,
          startTime: BigInt(0),
          endTime: BigInt(0),
          language: input.language,
        },
      ],
    });
  }

  async publishCanonicalResponse(
    response: CanonicalAssistantResponse,
  ): Promise<void> {
    await this.sendJson(voiceTopics.response, {
      type: voiceTopics.response,
      voiceSessionId: this.binding.voiceSessionId,
      response,
    });
  }

  async publishError(error: RecoverableVoiceError): Promise<void> {
    await this.sendJson(voiceTopics.error, {
      type: voiceTopics.error,
      voiceSessionId: this.binding.voiceSessionId,
      error,
    });
  }

  registerStopSpeaking(handler: () => boolean): void {
    this.localParticipant().registerRpcMethod(
      stopSpeakingRpcMethod,
      async (data: RpcInvocationData) => {
        if (data.callerIdentity !== this.binding.participantIdentity) {
          this.logger.warn("voice_stop_rpc_rejected", {
            errorCategory: "unknown_voice_error",
            callerMatchedBoundParticipant: false,
          });
          return JSON.stringify({ stopped: false });
        }
        return JSON.stringify({ stopped: handler() });
      },
    );
  }

  async close(): Promise<void> {
    this.room.localParticipant?.unregisterRpcMethod(stopSpeakingRpcMethod);
  }

  private localParticipant(): NonNullable<Room["localParticipant"]> {
    const participant = this.room.localParticipant;
    if (!participant) throw new Error("LiveKit agent participant is not connected");
    return participant;
  }

  private async sendJson(topic: string, payload: unknown): Promise<void> {
    await this.localParticipant().sendText(JSON.stringify(payload), { topic });
  }
}

export class LiveKitAudioPlayback implements AudioPlaybackSink {
  private readonly source: AudioSource;
  private track: LocalAudioTrack | null = null;
  private publication: LocalTrackPublication | null = null;
  private firstFrameCaptured = false;

  constructor(
    private readonly room: Room,
    private readonly sampleRate: number,
    private readonly numChannels: number,
    private readonly logger: Logger,
  ) {
    this.source = new AudioSource(sampleRate, numChannels, 200);
  }

  async start(): Promise<void> {
    if (this.track) return;
    const participant = this.room.localParticipant;
    if (!participant) throw new Error("LiveKit agent participant is not connected");
    this.track = LocalAudioTrack.createAudioTrack(
      "student-assistant-voice",
      this.source,
    );
    this.publication = await participant.publishTrack(
      this.track,
      new TrackPublishOptions({ source: TrackSource.SOURCE_MICROPHONE }),
    );
    this.logger.info("voice_audio_track_published", {
      trackSid: this.publication.sid,
      sampleRate: this.sampleRate,
      channels: this.numChannels,
    });
  }

  async captureFrame(
    frame: Parameters<AudioSource["captureFrame"]>[0],
  ): Promise<void> {
    if (!this.publication) {
      throw new Error("Voice audio track must be published before capturing audio");
    }
    assertPcm16Frame(frame, {
      encoding: pcm16Encoding,
      sampleRate: this.sampleRate,
      numChannels: this.numChannels,
    });
    await this.source.captureFrame(frame);
    if (!this.firstFrameCaptured) {
      this.firstFrameCaptured = true;
      this.logger.info("voice_audio_first_frame_captured", {
        trackSid: this.publication.sid,
        bytes: frame.data.byteLength,
        samplesPerChannel: frame.samplesPerChannel,
      });
    }
  }

  waitForPlayout(): Promise<void> {
    return this.source.waitForPlayout();
  }

  clear(): void {
    this.source.clearQueue();
  }

  async close(): Promise<void> {
    this.clear();
    const publicationSid = this.publication?.sid;
    if (publicationSid && this.room.localParticipant) {
      await this.room.localParticipant
        .unpublishTrack(publicationSid, true)
        .catch(() => undefined);
    }
    await this.track?.close();
    if (!this.track) await this.source.close();
    this.track = null;
    this.publication = null;
  }
}
