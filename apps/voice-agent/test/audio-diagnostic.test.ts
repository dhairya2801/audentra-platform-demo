import { describe, expect, it, vi } from "vitest";
import { publishDiagnosticTone } from "../src/audio-diagnostic.js";
import type { AudioPlaybackSink } from "../src/providers.js";
import type { Logger } from "../src/types.js";

describe("provider-independent audio diagnostic", () => {
  it("passes a generated PCM tone through capture and queued playout", async () => {
    const order: string[] = [];
    const sink = {
      captureFrame: vi.fn(async () => {
        order.push("capture");
      }),
      waitForPlayout: vi.fn(async () => {
        order.push("playout");
      }),
      clear: vi.fn(),
    } satisfies AudioPlaybackSink;
    const logger: Logger = {
      debug: vi.fn(),
      info: vi.fn(),
      warn: vi.fn(),
      error: vi.fn(),
    };

    const result = await publishDiagnosticTone(sink, 24_000, 1, logger);

    expect(result).toEqual({ totalBytes: 14_400, frames: 3, trailingBytes: 0 });
    expect(sink.captureFrame).toHaveBeenCalledTimes(3);
    expect(order).toEqual(["capture", "capture", "capture", "playout"]);
    expect(sink.captureFrame.mock.calls[0]![0]).toMatchObject({
      sampleRate: 24_000,
      channels: 1,
      samplesPerChannel: 2_400,
    });
  });
});
