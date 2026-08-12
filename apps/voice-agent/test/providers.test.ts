import { AudioFrame } from "@livekit/rtc-node";
import { describe, expect, it, vi } from "vitest";
import {
  CartesiaStreamingSynthesizer,
  type AudioPlaybackSink,
} from "../src/providers.js";
import type { Logger } from "../src/types.js";

describe("Cartesia streaming synthesizer", () => {
  it("uses WebSocket streaming, emits multiple PCM frames, and waits for playout", async () => {
    const first = frame(2_400);
    const second = frame(2_400);
    const trailing = frame(1_724);
    const provider = fakeProvider([first, second, trailing]);
    const order: string[] = [];
    const sink = fakeSink(order);
    const logger = fakeLogger();
    const synthesizer = new CartesiaStreamingSynthesizer(
      "unused-test-key",
      "unused-test-voice",
      logger,
      provider.value,
    );

    await synthesizer.speak("Hello from Edward", sink, new AbortController().signal);

    expect(provider.stream).toHaveBeenCalledTimes(1);
    expect(provider.pushText).toHaveBeenCalledWith("Hello from Edward");
    expect(provider.endInput).toHaveBeenCalledTimes(1);
    expect(sink.captureFrame).toHaveBeenCalledTimes(3);
    expect(order).toEqual(["capture", "capture", "capture", "playout"]);
    expect(logger.info).toHaveBeenCalledWith(
      "tts_completed",
      expect.objectContaining({
        totalTtsBytesReceived: 13_048,
        completeFramesEmitted: 2,
        partialFramesEmitted: 1,
        trailingBytesAtFlush: 3_448,
        trailingBytesDropped: 0,
        flushCalls: 1,
      }),
    );
  });

  it("rejects a mismatched Cartesia sample rate before publication", async () => {
    const provider = fakeProvider([frame(2_400, 16_000)]);
    const sink = fakeSink([]);
    const synthesizer = new CartesiaStreamingSynthesizer(
      "unused-test-key",
      "unused-test-voice",
      fakeLogger(),
      provider.value,
    );

    await expect(
      synthesizer.speak("Mismatch", sink, new AbortController().signal),
    ).rejects.toThrow(/sample rate mismatch/);
    expect(sink.captureFrame).not.toHaveBeenCalled();
  });

  it("fails clearly when the provider emits no PCM audio", async () => {
    const provider = fakeProvider([]);
    const synthesizer = new CartesiaStreamingSynthesizer(
      "unused-test-key",
      "unused-test-voice",
      fakeLogger(),
      provider.value,
    );

    await expect(
      synthesizer.speak("No audio", fakeSink([]), new AbortController().signal),
    ).rejects.toThrow(/without emitting PCM audio frames/);
  });
});

function frame(samplesPerChannel: number, sampleRate = 24_000): AudioFrame {
  return new AudioFrame(
    new Int16Array(samplesPerChannel),
    sampleRate,
    1,
    samplesPerChannel,
  );
}

function fakeProvider(frames: AudioFrame[]) {
  const pushText = vi.fn();
  const endInput = vi.fn();
  const closeStream = vi.fn();
  const stream = vi.fn(() => ({
    pushText,
    endInput,
    close: closeStream,
    async *[Symbol.asyncIterator]() {
      for (const value of frames) {
        yield {
          requestId: "cartesia-request",
          segmentId: "cartesia-segment",
          final: value === frames.at(-1),
          frame: value,
        };
      }
    },
  }));
  const value = {
    sampleRate: 24_000,
    numChannels: 1,
    on: vi.fn(),
    prewarm: vi.fn(),
    stream,
    close: vi.fn(async () => undefined),
  } as unknown as import("@livekit/agents-plugin-cartesia").TTS;
  return { value, stream, pushText, endInput, closeStream };
}

function fakeSink(order: string[]): AudioPlaybackSink & {
  captureFrame: ReturnType<typeof vi.fn>;
} {
  return {
    captureFrame: vi.fn(async () => {
      order.push("capture");
    }),
    waitForPlayout: vi.fn(async () => {
      order.push("playout");
    }),
    clear: vi.fn(),
  };
}

function fakeLogger(): Logger & { info: ReturnType<typeof vi.fn> } {
  return {
    debug: vi.fn(),
    info: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
  };
}
