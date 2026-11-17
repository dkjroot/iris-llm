// Energy-based articulation, not guessed phonemes. The same noise gate and
// compression are used by the native client's 20 ms PCM envelope.
export function mouthLevel(rms) {
  return Math.min(1, Math.sqrt(Math.max(0, rms - .008) / .18));
}

export function speechEnvelope(buffer) {
  const step = Math.max(1, Math.round(buffer.sampleRate * .02));
  const levels = new Float32Array(Math.ceil(buffer.length / step));
  for (let channel = 0; channel < buffer.numberOfChannels; channel++) {
    const samples = buffer.getChannelData(channel);
    for (let i = 0; i < levels.length; i++) {
      const end = Math.min(samples.length, (i + 1) * step);
      let energy = 0;
      for (let j = i * step; j < end; j++) energy += samples[j] * samples[j];
      levels[i] += energy / (end - i * step) / buffer.numberOfChannels;
    }
  }
  for (let i = 0; i < levels.length; i++) levels[i] = mouthLevel(Math.sqrt(levels[i]));
  return { levels, step: step / buffer.sampleRate };
}

export function envelopeAt(envelope, seconds) {
  const index = Math.floor(seconds / envelope.step);
  return index >= 0 && index < envelope.levels.length ? envelope.levels[index] : 0;
}

export function outputTime(audio, now) {
  // Where available, use the audible output clock, including device latency.
  const stamp = audio.getOutputTimestamp?.();
  if (stamp && stamp.contextTime > 0 && stamp.performanceTime > 0) {
    return Math.min(audio.currentTime, stamp.contextTime + (now - stamp.performanceTime) / 1000);
  }
  return audio.currentTime - (audio.outputLatency || audio.baseLatency || 0);
}
