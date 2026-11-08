export class SpeechCapture {
  constructor(rate, phraseLimit) {
    this.maxSamples = Math.floor(rate * phraseLimit);
    this.pauseSamples = Math.round(rate * 0.7);
    this.startSamples = Math.round(rate * 0.12);
    this.reset();
  }

  reset() {
    this.frames = [];
    this.length = 0;
    this.loud = 0;
    this.quiet = 0;
    this.started = false;
  }

  accept(samples) {
    const power = samples.reduce((sum, sample) => sum + sample * sample, 0) / samples.length;
    const audible = power >= 0.0001;
    if (!this.started && this.length + samples.length > this.maxSamples) {
      this.frames = [];
      this.length = 0;
    }
    this.loud = audible ? this.loud + samples.length : 0;
    this.quiet = audible ? 0 : this.quiet + samples.length;
    this.started ||= this.loud >= Math.min(this.startSamples, this.maxSamples);

    const frame = samples.subarray(0, this.maxSamples - this.length);
    this.frames.push(frame);
    this.length += frame.length;
    if (this.started && (this.quiet >= this.pauseSamples || this.length >= this.maxSamples)) {
      const frames = this.frames;
      this.reset();
      return frames;
    }
    if (!this.started && this.frames.length > 1 &&
        this.length >= Math.min(this.startSamples * 2, this.maxSamples)) {
      this.length -= this.frames.shift().length;
    }
    return null;
  }
}

export function recordingWav(frames, inputRate) {
  const length = frames.reduce((total, frame) => total + frame.length, 0);
  const samples = new Float32Array(length);
  let offset = 0;
  for (const frame of frames) {
    samples.set(frame, offset);
    offset += frame.length;
  }

  const rate = 16000;
  const ratio = inputRate / rate;
  const count = Math.floor(length / ratio);
  const wav = new DataView(new ArrayBuffer(44 + count * 2));
  wav.setUint32(0, 0x52494646); // RIFF
  wav.setUint32(4, wav.byteLength - 8, true);
  wav.setUint32(8, 0x57415645); // WAVE
  wav.setUint32(12, 0x666d7420); // fmt
  wav.setUint32(16, 16, true);
  wav.setUint16(20, 1, true); // PCM
  wav.setUint16(22, 1, true); // Mono
  wav.setUint32(24, rate, true);
  wav.setUint32(28, rate * 2, true);
  wav.setUint16(32, 2, true);
  wav.setUint16(34, 16, true);
  wav.setUint32(36, 0x64617461); // data
  wav.setUint32(40, count * 2, true);

  for (let i = 0; i < count; i++) {
    const start = Math.floor(i * ratio);
    const end = Math.max(start + 1, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = start; j < end; j++) sum += samples[j];
    const sample = Math.max(-1, Math.min(1, sum / (end - start)));
    wav.setInt16(44 + i * 2, Math.round(sample * 32767), true);
  }
  return wav.buffer;
}
