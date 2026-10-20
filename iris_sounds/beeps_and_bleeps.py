"""Play the bundled procedural cues, loaded only when this provider is selected."""

from __future__ import annotations

import sys
import wave
from array import array
from pathlib import Path

import sounddevice as sd

from iris_sounds.ISounds import ISounds


def load_cue(filename: str) -> tuple[array[int], int]:
    """Read mono 16-bit PCM from the module directory, independent of cwd."""
    path = Path(__file__).resolve().parent / filename
    try:
        with wave.open(str(path), "rb") as source:
            if source.getnchannels() != 1 or source.getsampwidth() != 2:
                raise ValueError(f"{filename} must be a mono 16-bit PCM WAV file")
            rate = source.getframerate()
            samples = array("h", source.readframes(source.getnframes()))
    except FileNotFoundError as error:
        raise FileNotFoundError(
            f"Missing sound {path}. Run iris_sounds/generate_sounds.py or "
            "select iris_sounds.no_sounds. See iris_sounds/README.md."
        ) from error
    if sys.byteorder != "little":
        samples.byteswap()
    return samples, rate


class Sounds(ISounds):
    """Use blocking short cues before recording, with background working feedback."""

    def __init__(self, settings: dict[str, object]) -> None:
        self.working, self.working_rate = load_cue("workinglongquiet.wav")
        self.chirp, self.chirp_rate = load_cue("chirp.wav")
        self.bleep, self.bleep_rate = load_cue("bleep.wav")
        self.longbleep, self.longbleep_rate = load_cue("longbleep.wav")

    def play_working_sound(self) -> None:
        """Start quiet feedback without delaying model loading or inference."""
        sd.stop()
        sd.play(self.working, self.working_rate, blocking=False)

    def play_chirp(self) -> None:
        """Signal readiness for the activation gesture."""
        sd.stop()
        sd.play(self.chirp, self.chirp_rate, blocking=True)

    def play_bleep(self) -> None:
        """Finish the listening cue before microphone capture begins."""
        sd.stop()
        sd.play(self.bleep, self.bleep_rate, blocking=True)

    def play_long_bleep(self) -> None:
        """Play a longer, descending feedback cue."""
        sd.stop()
        sd.play(self.longbleep, self.longbleep_rate, blocking=True)
