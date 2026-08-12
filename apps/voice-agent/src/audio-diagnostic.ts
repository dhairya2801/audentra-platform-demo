import type { AudioPlaybackSink } from "./providers.js";
import {
  Pcm16FrameBuffer,
  pcm16BytesPerSample,
  pcm16Encoding,
} from "./pcm-audio.js";
import type { Logger } from "./types.js";

const diagnosticToneDurationMs = 300;
const diagnosticToneFrequencyHz = 440;

export async function publishDiagnosticTone(
  sink: AudioPlaybackSink,
  sampleRate: number,
  numChannels: number,
  logger: Logger,
): Promise<{ totalBytes: number; frames: number; trailingBytes: number }> {
  const format = { encoding: pcm16Encoding, sampleRate, numChannels };
  const buffer = new Pcm16FrameBuffer(format);
  const samplesPerChannel = Math.floor(
    (sampleRate * diagnosticToneDurationMs) / 1_000,
  );
  const bytes = new Uint8Array(
    samplesPerChannel * numChannels * pcm16BytesPerSample,
  );
  const view = new DataView(bytes.buffer);
  for (let sample = 0; sample < samplesPerChannel; sample += 1) {
    const value = Math.round(
      Math.sin((2 * Math.PI * diagnosticToneFrequencyHz * sample) / sampleRate) *
        8_000,
    );
    for (let channel = 0; channel < numChannels; channel += 1) {
      view.setInt16(
        (sample * numChannels + channel) * pcm16BytesPerSample,
        value,
        true,
      );
    }
  }

  // Deliberately irregular transport chunks exercise the same framing rules as
  // provider WebSocket data without persisting or logging audio contents.
  const chunkSizes = [137, 4_093, 911, 7_001];
  let offset = 0;
  let frames = 0;
  for (let index = 0; offset < bytes.byteLength; index += 1) {
    const end = Math.min(offset + chunkSizes[index % chunkSizes.length]!, bytes.byteLength);
    for (const frame of buffer.write(bytes.subarray(offset, end))) {
      await sink.captureFrame(frame);
      frames += 1;
    }
    offset = end;
  }
  const flushed = buffer.flush();
  for (const frame of flushed.frames) {
    await sink.captureFrame(frame);
    frames += 1;
  }
  await sink.waitForPlayout();
  const result = {
    totalBytes: bytes.byteLength,
    frames,
    trailingBytes: flushed.trailingBytes,
  };
  logger.info("voice_diagnostic_tone_completed", result);
  return result;
}
