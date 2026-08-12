import { randomUUID } from "node:crypto";
import { AudioFrame } from "@livekit/rtc-node";
import { describe, expect, it, vi } from "vitest";
import type {
  BrowserTransport,
  RecoverableVoiceError,
  VoiceAgentState,
} from "../src/livekit-transport.js";
import type {
  AudioPlaybackSink,
  StreamingSynthesizer,
  SynthesisObservability,
  TranscriptEvent,
} from "../src/providers.js";
import type {
  CanonicalAssistantResponse,
  FinalizedVoiceTurn,
  Logger,
  VoiceBackend,
  VoiceSessionBinding,
} from "../src/types.js";
import { VoiceSession } from "../src/voice-session.js";

const binding: VoiceSessionBinding = {
  voiceSessionId: "00000000-0000-4000-8000-000000000201",
  conversationId: "00000000-0000-4000-8000-000000000202",
  provider: "livekit",
  roomName: "ev1_room_bound",
  participantIdentity: "ev1_participant_bound",
  pageContext: { path: "/onboarding", label: "Onboarding" },
  status: "active",
  expiresAt: "2026-08-03T18:00:00.000Z",
  endedAt: null,
  createdAt: "2026-08-03T17:00:00.000Z",
};

const graphResult = {
  message: "You still need to complete About You and Housing.",
  requestType: "remaining_steps",
  completedSteps: [],
  remainingSteps: [{ code: "about_you" }, { code: "housing" }],
  blockedSteps: [],
  missingDocuments: [],
  deadlines: [],
  supportOptions: [],
  contextReceipts: [{ id: "receipt-1", source: "getOnboardingChecklist" }],
  suggestedActions: [],
  unavailableData: [],
  safeFailure: null,
  graphExecution: {
    graphVersion: "student-onboarding-v1",
    durationMs: 12,
    selectedTools: ["getOnboardingChecklist"],
  },
};

const canonicalResponse: CanonicalAssistantResponse = {
  conversationId: binding.conversationId,
  userMessageId: "00000000-0000-4000-8000-000000000203",
  assistantMessageId: "00000000-0000-4000-8000-000000000204",
  requestId: "req-canonical-voice",
  message: graphResult.message,
  provider: "guided",
  model: null,
  usage: null,
  suggestedActions: [{ label: "Review onboarding", href: "/onboarding" }],
  contextReceipts: [{ source: "onboarding" }],
  widgets: [],
  studentAssistant: graphResult,
};

describe("voice finalized-turn flow", () => {
  it("publishes interim captions with zero backend calls", async () => {
    const fixture = createFixture();
    await fixture.session.start();

    await fixture.session.handleTranscript(interimEvent());

    expect(fixture.backend.turns).toHaveLength(0);
    expect(fixture.transport.transcripts).toEqual([
      expect.objectContaining({ text: "What steps", final: false }),
    ]);
  });

  it("creates exactly one backend turn for one final transcript", async () => {
    const fixture = createFixture();
    await fixture.session.start();

    await fixture.session.handleTranscript(finalEvent());

    expect(fixture.backend.turns).toHaveLength(1);
    expect(fixture.backend.turns[0]).toMatchObject({
      voiceSessionId: binding.voiceSessionId,
      turn: {
        text: "What onboarding steps am I yet to do?",
        inputMode: "voice",
        pageContext: binding.pageContext,
        livekitStreamId: "TR_stream_1",
      },
    });
    expect(fixture.backend.turns[0]!.turn.clientMessageId).toMatch(
      /^[0-9a-f-]{36}$/,
    );
    expect(fixture.backend.turns[0]!.turn).not.toHaveProperty("studentId");
  });

  it("suppresses duplicate final STT events", async () => {
    const fixture = createFixture();
    await fixture.session.start();
    const event = finalEvent();

    await fixture.session.handleTranscript(event);
    await fixture.session.handleTranscript(event);

    expect(fixture.backend.turns).toHaveLength(1);
    expect(fixture.logger.info).toHaveBeenCalledWith(
      "voice_metric",
      expect.objectContaining({
        metricName: "duplicate_final_transcripts_suppressed",
        value: 1,
      }),
    );
  });

  it("preserves canonical IDs and the structured Student Assistant result", async () => {
    const fixture = createFixture();
    await fixture.session.start();

    await fixture.session.handleTranscript(finalEvent());

    expect(fixture.transport.responses).toHaveLength(1);
    expect(fixture.transport.responses[0]).toEqual(canonicalResponse);
    expect(fixture.transport.responses[0]).toMatchObject({
      conversationId: binding.conversationId,
      userMessageId: canonicalResponse.userMessageId,
      assistantMessageId: canonicalResponse.assistantMessageId,
      requestId: canonicalResponse.requestId,
      studentAssistant: graphResult,
    });
    expect(fixture.logger.info).toHaveBeenCalledWith(
      "canonical_response_published",
      expect.objectContaining({
        voiceSessionId: binding.voiceSessionId,
        conversationId: binding.conversationId,
        userMessageId: canonicalResponse.userMessageId,
        assistantMessageId: canonicalResponse.assistantMessageId,
        requestId: canonicalResponse.requestId,
      }),
    );
  });

  it("sends only the returned canonical message to TTS", async () => {
    const fixture = createFixture();
    await fixture.session.start();

    await fixture.session.handleTranscript(finalEvent());

    expect(fixture.synthesizer.texts).toEqual([canonicalResponse.message]);
  });

  it("does not disconnect or end the voice session when TTS completes", async () => {
    const fixture = createFixture();
    await fixture.session.start();

    await fixture.session.handleTranscript(finalEvent());

    expect(fixture.backend.endCalls).toBe(0);
    expect(fixture.transport.closed).toBe(0);
    expect(fixture.transport.states.at(-1)).toBe("listening");
  });

  it("natural barge-in cancels speech without repeating or mutating the backend turn", async () => {
    const fixture = createFixture({ synthesis: "blocked" });
    await fixture.session.start();
    const finalPromise = fixture.session.handleTranscript(finalEvent());
    await eventually(() => fixture.synthesizer.signals.length === 1);

    await fixture.session.handleTranscript({
      type: "speech_started",
      livekitStreamId: "TR_stream_1",
    });
    await finalPromise;

    expect(fixture.synthesizer.signals[0]!.aborted).toBe(true);
    expect(fixture.audio.clear).toHaveBeenCalledTimes(1);
    expect(fixture.backend.turns).toHaveLength(1);
    expect(fixture.backend.endCalls).toBe(0);
    expect(fixture.transport.responses).toEqual([canonicalResponse]);
  });

  it("explicit stop-speaking RPC callback cancels active speech", async () => {
    const fixture = createFixture({ synthesis: "blocked" });
    await fixture.session.start();
    const finalPromise = fixture.session.handleTranscript(finalEvent());
    await eventually(() => fixture.synthesizer.signals.length === 1);

    expect(fixture.transport.invokeStop()).toBe(true);
    await finalPromise;

    expect(fixture.synthesizer.signals[0]!.aborted).toBe(true);
    expect(fixture.audio.clear).toHaveBeenCalledTimes(1);
    expect(fixture.backend.turns).toHaveLength(1);
    expect(fixture.backend.endCalls).toBe(0);
    expect(fixture.logger.info).toHaveBeenCalledWith(
      "interruption_completed",
      expect.objectContaining({ durationMs: expect.any(Number) }),
    );
  });

  it("publishes a recoverable STT failure without invoking the backend", async () => {
    const fixture = createFixture();
    await fixture.session.start();

    await fixture.session.handleSttFailure(new Error("fake STT disconnect"));

    expect(fixture.backend.turns).toHaveLength(0);
    expect(fixture.transport.errors).toContainEqual({
      code: "VOICE_STT_UNAVAILABLE",
      message: "Speech recognition was interrupted. Please try speaking again.",
      recoverable: true,
    });
    expect(fixture.transport.states).toContain("error");
  });

  it("keeps canonical text available when TTS fails", async () => {
    const fixture = createFixture({ synthesis: "failed" });
    await fixture.session.start();

    await fixture.session.handleTranscript(finalEvent());

    expect(fixture.transport.responses).toEqual([canonicalResponse]);
    expect(fixture.transport.errors).toContainEqual(
      expect.objectContaining({
        code: "VOICE_TTS_UNAVAILABLE",
        recoverable: true,
      }),
    );
  });

  it("participant disconnect ends the backend voice session", async () => {
    const fixture = createFixture();
    await fixture.session.start();

    await fixture.session.participantDisconnected();

    expect(fixture.backend.endCalls).toBe(1);
    expect(fixture.transport.closed).toBe(1);
    expect(fixture.synthesizer.closed).toBe(1);
  });

  it("normal shutdown notifies session-end exactly once", async () => {
    const fixture = createFixture();
    await fixture.session.start();

    await fixture.session.shutdown("SIGTERM");
    await fixture.session.shutdown("SIGTERM");

    expect(fixture.backend.endCalls).toBe(1);
  });

  it("uses canonical publication and the first per-turn frame for first-audio latency", async () => {
    const fixture = createFixture();
    await fixture.session.start();

    await fixture.session.handleTranscript(finalEvent());

    expect(fixture.logger.info).toHaveBeenCalledWith(
      "audio_first_frame_published",
      expect.objectContaining({
        requestId: canonicalResponse.requestId,
        assistantMessageId: canonicalResponse.assistantMessageId,
        canonicalToAudioDurationMs: expect.any(Number),
      }),
    );
    const event = findInfoEvent(fixture.logger, "audio_first_frame_published");
    expect(event.canonicalToAudioDurationMs).toBeGreaterThanOrEqual(0);
    expect(event).not.toHaveProperty("message");
  });

  it("emits a bounded session summary with exact counts", async () => {
    const fixture = createFixture();
    await fixture.session.start();
    const event = finalEvent();
    await fixture.session.handleTranscript(event);
    await fixture.session.handleTranscript(event);
    await fixture.session.handleSttFailure(new Error("provider disconnected"));

    await fixture.session.shutdown("livekit_job_shutdown");

    expect(fixture.logger.info).toHaveBeenCalledWith(
      "voice_session_ended",
      expect.objectContaining({
        voiceSessionId: binding.voiceSessionId,
        conversationId: binding.conversationId,
        sessionDurationMs: expect.any(Number),
        finalizedTurnCount: 1,
        successfulBackendTurnCount: 1,
        failedBackendTurnCount: 0,
        ttsSuccessCount: 1,
        ttsFailureCount: 0,
        duplicateFinalsSuppressed: 1,
        recoverableErrorCountByCategory: { stt_provider_error: 1 },
        endState: "normal",
      }),
    );
  });

  it("keeps transcript and answer text out of infrastructure events", async () => {
    const fixture = createFixture();
    await fixture.session.start();
    await fixture.session.handleTranscript(finalEvent());
    await fixture.session.shutdown("livekit_job_shutdown");

    const serializedEvents = JSON.stringify([
      ...fixture.logger.debug.mock.calls,
      ...fixture.logger.info.mock.calls,
      ...fixture.logger.warn.mock.calls,
      ...fixture.logger.error.mock.calls,
    ]);
    expect(serializedEvents).not.toContain(finalEvent().text);
    expect(serializedEvents).not.toContain(canonicalResponse.message);
  });
});

class FakeBackend implements VoiceBackend {
  turns: { voiceSessionId: string; turn: FinalizedVoiceTurn }[] = [];
  endCalls = 0;

  async lookupSession(): Promise<VoiceSessionBinding> {
    return binding;
  }

  async submitFinalizedTurn(
    voiceSessionId: string,
    turn: FinalizedVoiceTurn,
  ): Promise<CanonicalAssistantResponse> {
    this.turns.push({ voiceSessionId, turn: structuredClone(turn) });
    return structuredClone(canonicalResponse);
  }

  async endSession(): Promise<VoiceSessionBinding> {
    this.endCalls += 1;
    return { ...binding, status: "ended", endedAt: new Date().toISOString() };
  }
}

class FakeTransport implements BrowserTransport {
  states: VoiceAgentState[] = [];
  transcripts: Parameters<BrowserTransport["publishTranscript"]>[0][] = [];
  responses: CanonicalAssistantResponse[] = [];
  errors: RecoverableVoiceError[] = [];
  closed = 0;
  private stopHandler: (() => boolean) | null = null;

  async publishState(state: VoiceAgentState): Promise<void> {
    this.states.push(state);
  }

  async publishTranscript(
    input: Parameters<BrowserTransport["publishTranscript"]>[0],
  ): Promise<void> {
    this.transcripts.push(structuredClone(input));
  }

  async publishCanonicalResponse(
    response: CanonicalAssistantResponse,
  ): Promise<void> {
    this.responses.push(structuredClone(response));
  }

  async publishError(error: RecoverableVoiceError): Promise<void> {
    this.errors.push(structuredClone(error));
  }

  registerStopSpeaking(handler: () => boolean): void {
    this.stopHandler = handler;
  }

  invokeStop(): boolean {
    if (!this.stopHandler) throw new Error("stop-speaking RPC was not registered");
    return this.stopHandler();
  }

  async close(): Promise<void> {
    this.closed += 1;
  }
}

class FakeSynthesizer implements StreamingSynthesizer {
  readonly sampleRate = 24_000;
  readonly numChannels = 1;
  texts: string[] = [];
  signals: AbortSignal[] = [];
  closed = 0;

  constructor(private readonly behavior: "completed" | "blocked" | "failed") {}

  prewarm(): void {}

  async speak(
    text: string,
    sink: AudioPlaybackSink,
    signal: AbortSignal,
    observability?: SynthesisObservability,
  ): Promise<void> {
    this.texts.push(text);
    this.signals.push(signal);
    observability?.onStarted?.();
    if (this.behavior === "failed") throw new Error("fake TTS failure");
    if (this.behavior === "blocked") {
      await new Promise<void>((resolve) => {
        if (signal.aborted) resolve();
        else signal.addEventListener("abort", () => resolve(), { once: true });
      });
      return;
    }
    observability?.onFirstChunk?.();
    await sink.captureFrame(new AudioFrame(new Int16Array(480), 24_000, 1, 480));
    await sink.waitForPlayout();
  }

  async close(): Promise<void> {
    this.closed += 1;
  }
}

function createFixture(
  options: { synthesis?: "completed" | "blocked" | "failed" } = {},
) {
  const backend = new FakeBackend();
  const transport = new FakeTransport();
  const synthesizer = new FakeSynthesizer(
    options.synthesis ?? "completed",
  );
  const audio = {
    captureFrame: vi.fn(async () => undefined),
    waitForPlayout: vi.fn(async () => undefined),
    clear: vi.fn(),
    close: vi.fn(async () => undefined),
  } satisfies AudioPlaybackSink & { close(): Promise<void> };
  const logger = {
    debug: vi.fn(),
    info: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
  } satisfies Logger;
  const session = new VoiceSession({
    binding,
    backend,
    transport,
    synthesizer,
    audio,
    logger,
    createId: randomUUID,
  });
  return { session, backend, transport, synthesizer, audio, logger };
}

function findInfoEvent(
  logger: Logger & { info: ReturnType<typeof vi.fn> },
  name: string,
): Record<string, unknown> {
  const call = logger.info.mock.calls.find(([event]) => event === name);
  if (!call) throw new Error(`Missing ${name} event`);
  return call[1] as Record<string, unknown>;
}

function interimEvent(): TranscriptEvent {
  return {
    type: "interim",
    livekitStreamId: "TR_stream_1",
    text: "What steps",
    language: "en",
    startTime: 1,
    endTime: 1.5,
  };
}

function finalEvent(): TranscriptEvent {
  return {
    type: "final",
    livekitStreamId: "TR_stream_1",
    text: "What onboarding steps am I yet to do?",
    language: "en",
    startTime: 1,
    endTime: 3,
  };
}

async function eventually(predicate: () => boolean): Promise<void> {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 1));
  }
  throw new Error("condition was not reached");
}
