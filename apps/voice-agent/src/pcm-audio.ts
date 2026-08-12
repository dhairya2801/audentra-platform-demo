import { AudioFrame } from "@livekit/rtc-node";

export const pcm16Encoding = "pcm_s16le" as const;
export const pcm16BytesPerSample = 2;
export const defaultAudioFrameDurationMs = 100;

export interface Pcm16Format {
  encoding: string;
  sampleRate: number;
  numChannels: number;
}

export interface Pcm16FlushResult {
  frames: AudioFrame[];
  trailingBytes: number;
}

/**
 * Buffers arbitrary raw PCM chunks into LiveKit AudioFrames. Cartesia chunks
 * are transport chunks, not frame boundaries, so incomplete bytes remain here
 * until the next write and flush is intentionally one-shot per utterance.
 */
export class Pcm16FrameBuffer {
  readonly samplesPerChannel: number;
  readonly bytesPerFrame: number;
  private remainder = new Uint8Array();
  private flushed = false;

  constructor(
    readonly format: Pcm16Format,
    frameDurationMs = defaultAudioFrameDurationMs,
  ) {
    assertPcm16Format(format);
    const samples = (format.sampleRate * frameDurationMs) / 1_000;
    if (!Number.isSafeInteger(samples) || samples <= 0) {
      throw new Error("PCM frame duration must produce a whole number of samples");
    }
    this.samplesPerChannel = samples;
    this.bytesPerFrame =
      samples * format.numChannels * pcm16BytesPerSample;
  }

  get trailingBytes(): number {
    return this.remainder.byteLength;
  }

  write(chunk: ArrayBufferLike | ArrayBufferView): AudioFrame[] {
    if (this.flushed) throw new Error("PCM frame buffer is already flushed");
    const incoming = ArrayBuffer.isView(chunk)
      ? new Uint8Array(chunk.buffer, chunk.byteOffset, chunk.byteLength)
      : new Uint8Array(chunk);
    const combined = new Uint8Array(this.remainder.byteLength + incoming.byteLength);
    combined.set(this.remainder);
    combined.set(incoming, this.remainder.byteLength);

    const frames: AudioFrame[] = [];
    let offset = 0;
    while (combined.byteLength - offset >= this.bytesPerFrame) {
      frames.push(this.toFrame(combined.subarray(offset, offset + this.bytesPerFrame)));
      offset += this.bytesPerFrame;
    }
    this.remainder = combined.slice(offset);
    return frames;
  }

  flush(): Pcm16FlushResult {
    if (this.flushed) throw new Error("PCM frame buffer must be flushed only once");
    this.flushed = true;
    const sampleWidth = this.format.numChannels * pcm16BytesPerSample;
    const alignedLength = this.remainder.byteLength - (this.remainder.byteLength % sampleWidth);
    const trailingBytes = this.remainder.byteLength - alignedLength;
    const frames = alignedLength > 0
      ? [this.toFrame(this.remainder.subarray(0, alignedLength))]
      : [];
    this.remainder = new Uint8Array();
    return { frames, trailingBytes };
  }

  private toFrame(bytes: Uint8Array): AudioFrame {
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const samples = new Int16Array(bytes.byteLength / pcm16BytesPerSample);
    for (let index = 0; index < samples.length; index += 1) {
      samples[index] = view.getInt16(index * pcm16BytesPerSample, true);
    }
    return new AudioFrame(
      samples,
      this.format.sampleRate,
      this.format.numChannels,
      samples.length / this.format.numChannels,
    );
  }
}

export function assertPcm16Format(format: Pcm16Format): void {
  if (format.encoding !== pcm16Encoding) {
    throw new Error(
      `Unsupported TTS encoding ${format.encoding}; expected raw signed 16-bit little-endian PCM`,
    );
  }
  if (!Number.isSafeInteger(format.sampleRate) || format.sampleRate <= 0) {
    throw new Error("PCM sample rate must be a positive integer");
  }
  if (!Number.isSafeInteger(format.numChannels) || format.numChannels !== 1) {
    throw new Error("TTS audio must be mono PCM");
  }
}

export function assertPcm16Frame(frame: AudioFrame, format: Pcm16Format): void {
  assertPcm16Format(format);
  if (!(frame.data instanceof Int16Array)) {
    throw new Error("TTS audio must contain decoded signed 16-bit PCM samples");
  }
  if (frame.sampleRate !== format.sampleRate) {
    throw new Error(
      `TTS sample rate mismatch: expected ${format.sampleRate}, received ${frame.sampleRate}`,
    );
  }
  if (frame.channels !== format.numChannels) {
    throw new Error(
      `TTS channel mismatch: expected ${format.numChannels}, received ${frame.channels}`,
    );
  }
  const expectedBytes =
    frame.samplesPerChannel * frame.channels * pcm16BytesPerSample;
  if (frame.data.byteLength !== expectedBytes) {
    throw new Error(
      `TTS PCM byte length mismatch: expected ${expectedBytes}, received ${frame.data.byteLength}`,
    );
  }
}
