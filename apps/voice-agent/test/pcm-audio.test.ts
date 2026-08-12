import { describe, expect, it } from "vitest";
import {
  Pcm16FrameBuffer,
  assertPcm16Format,
  pcm16Encoding,
} from "../src/pcm-audio.js";

const format = {
  encoding: pcm16Encoding,
  sampleRate: 24_000,
  numChannels: 1,
};

describe("PCM16 frame buffering", () => {
  it("carries arbitrary partial chunks into multiple complete frames", () => {
    const buffer = new Pcm16FrameBuffer(format);
    const input = patternedBytes(buffer.bytesPerFrame * 2 + 2_400);
    const chunks = [1, 137, 4_663, 17, 8_111, input.byteLength];
    const frames = [];
    let offset = 0;

    for (const size of chunks) {
      const end = Math.min(offset + size, input.byteLength);
      frames.push(...buffer.write(input.subarray(offset, end)));
      offset = end;
      if (offset === input.byteLength) break;
    }
    const flushed = buffer.flush();
    frames.push(...flushed.frames);

    expect(frames).toHaveLength(3);
    expect(frames.map((frame) => frame.data.byteLength)).toEqual([
      4_800,
      4_800,
      2_400,
    ]);
    expect(frames.every((frame) => frame.sampleRate === 24_000)).toBe(true);
    expect(frames.every((frame) => frame.channels === 1)).toBe(true);
    expect(flushed.trailingBytes).toBe(0);
  });

  it("drops only a final sub-sample byte without losing complete frames", () => {
    const buffer = new Pcm16FrameBuffer(format);
    const frames = buffer.write(patternedBytes(buffer.bytesPerFrame + 3));
    const flushed = buffer.flush();

    expect(frames).toHaveLength(1);
    expect(frames[0]!.data.byteLength).toBe(4_800);
    expect(flushed.frames).toHaveLength(1);
    expect(flushed.frames[0]!.data.byteLength).toBe(2);
    expect(flushed.trailingBytes).toBe(1);
  });

  it("allows exactly one end-of-utterance flush", () => {
    const buffer = new Pcm16FrameBuffer(format);
    buffer.write(new Uint8Array([0, 0]));
    buffer.flush();
    expect(() => buffer.flush()).toThrow(/only once/);
    expect(() => buffer.write(new Uint8Array())).toThrow(/already flushed/);
  });

  it.each(["mp3", "wav", "pcm_mulaw", "opus"])(
    "rejects encoded/non-PCM input %s clearly",
    (encoding) => {
      expect(() => assertPcm16Format({ ...format, encoding })).toThrow(
        /signed 16-bit little-endian PCM/,
      );
    },
  );
});

function patternedBytes(length: number): Uint8Array {
  return Uint8Array.from({ length }, (_, index) => index % 251);
}
