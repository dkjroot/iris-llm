"""Create IRIS's original feedback cues without samples or third-party libraries.

Run with Python 3.10+ from any directory. The resulting mono, 16-bit PCM WAV
files are written beside this script. All sound is synthesized from sine waves.
SPDX-License-Identifier: GPL-3.0-only
"""

import math
import sys
import wave
from array import array
from pathlib import Path

SAMPLE_RATE = 24000
OUTPUT = Path(__file__).resolve().parent


def silence(duration: float) -> list[float]:
    """Return a gap between tones, measured in seconds."""
    return [0.0] * round(duration * SAMPLE_RATE)


def tone(start: float, end: float, duration: float, volume: float) -> list[float]:
    """Synthesize a soft tone with a linear pitch glide and smooth end fades."""
    count = round(duration * SAMPLE_RATE)
    result = []
    for index in range(count):
        time = index / SAMPLE_RATE
        # Integrating the frequency ramp avoids discontinuities in the waveform.
        phase = 2 * math.pi * (start * time + (end - start) * time * time / (2 * duration))
        attack = min(1.0, time / 0.012)
        release = min(1.0, (count - 1 - index) / (SAMPLE_RATE * 0.035))
        envelope = math.sin(math.pi * attack / 2) ** 2 * math.sin(math.pi * release / 2) ** 2
        # A quiet second harmonic gives the cue some warmth without harsh edges.
        result.append(volume * envelope * (0.9 * math.sin(phase) + 0.1 * math.sin(2 * phase)))
    return result


def cues() -> dict[str, list[float]]:
    """Build distinct ready, listening, long-feedback, and quiet working sounds."""
    working = []
    for _ in range(6):
        working += tone(220, 247, 0.16, 0.035) + silence(0.44)
    return {
        "chirp.wav": tone(520, 880, 0.20, 0.18),
        "bleep.wav": tone(660, 660, 0.13, 0.18),
        "longbleep.wav": tone(440, 440, 0.20, 0.16) + silence(0.06) + tone(330, 330, 0.30, 0.16),
        "workinglongquiet.wav": working,
    }


def write_wav(path: Path, samples: list[float]) -> None:
    """Write signed little-endian PCM without normalization or external codecs."""
    pcm = array("h", (round(max(-1.0, min(1.0, value)) * 32767) for value in samples))
    if sys.byteorder != "little":
        pcm.byteswap()
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(pcm.tobytes())


def main() -> None:
    """Regenerate the four bundled cues; no network or audio device is used."""
    for filename, samples in cues().items():
        path = OUTPUT / filename
        write_wav(path, samples)
        print(path.name)


if __name__ == "__main__":
    main()
