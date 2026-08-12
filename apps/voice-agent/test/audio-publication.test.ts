import type { Room } from "@livekit/rtc-node";
import { describe, expect, it, vi } from "vitest";
import { publishDiagnosticTone } from "../src/audio-diagnostic.js";
import { LiveKitAudioPlayback } from "../src/livekit-transport.js";
import type { Logger } from "../src/types.js";

const rtc = vi.hoisted(() => ({
  sources: [] as Array<{
    captureFrame: ReturnType<typeof vi.fn>;
    waitForPlayout: ReturnType<typeof vi.fn>;
    clearQueue: ReturnType<typeof vi.fn>;
    close: ReturnType<typeof vi.fn>;
  }>,
  trackClose: vi.fn(async () => undefined),
}));

vi.mock("@livekit/rtc-node", async (importOriginal) => {
  const original = await importOriginal<typeof import("@livekit/rtc-node")>();
  class FakeAudioSource {
    captureFrame = vi.fn(async () => undefined);
    waitForPlayout = vi.fn(async () => undefined);
    clearQueue = vi.fn();
    close = vi.fn(async () => undefined);

    constructor(
      readonly sampleRate: number,
      readonly numChannels: number,
      readonly queueSize: number,
    ) {
      rtc.sources.push(this);
    }
  }
  return {
    ...original,
    AudioSource: FakeAudioSource,
    LocalAudioTrack: {
      createAudioTrack: vi.fn(() => ({ close: rtc.trackClose })),
    },
    TrackPublishOptions: class TrackPublishOptions {
      constructor(readonly options: unknown) {}
    },
    TrackSource: { SOURCE_MICROPHONE: "microphone" },
  };
});

describe("LiveKit audio publication", () => {
  it("publishes the track before the generated tone captures its first frame", async () => {
    rtc.sources.length = 0;
    const publication = { sid: "TR_agent_audio" };
    const localParticipant = {
      publishTrack: vi.fn(async () => publication),
      unpublishTrack: vi.fn(async () => undefined),
    };
    const logger: Logger = {
      debug: vi.fn(),
      info: vi.fn(),
      warn: vi.fn(),
      error: vi.fn(),
    };
    const playback = new LiveKitAudioPlayback(
      { localParticipant } as unknown as Room,
      24_000,
      1,
      logger,
    );

    await playback.start();
    const result = await publishDiagnosticTone(playback, 24_000, 1, logger);

    const source = rtc.sources[0]!;
    expect(result).toEqual({ totalBytes: 14_400, frames: 3, trailingBytes: 0 });
    expect(localParticipant.publishTrack).toHaveBeenCalledTimes(1);
    expect(source.captureFrame).toHaveBeenCalledTimes(3);
    expect(localParticipant.publishTrack.mock.invocationCallOrder[0]).toBeLessThan(
      source.captureFrame.mock.invocationCallOrder[0]!,
    );
    expect(source.waitForPlayout).toHaveBeenCalledTimes(1);
    expect(logger.info).toHaveBeenCalledWith(
      "voice_audio_track_published",
      expect.objectContaining({ trackSid: publication.sid }),
    );
    expect(logger.info).toHaveBeenCalledWith(
      "voice_audio_first_frame_captured",
      expect.objectContaining({ trackSid: publication.sid }),
    );
  });
});
