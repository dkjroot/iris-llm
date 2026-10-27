"""Version-one, turn-based droid messages. No recordings are written to disk."""

from __future__ import annotations

import io
import warnings
import wave

from pydantic import BaseModel, ConfigDict

MAX_BODY = 10_000_000


def validate_token(token: str) -> None:
    if (
        not token.strip()
        or not token.isascii()
        or any(ord(char) < 32 or ord(char) == 127 for char in token)
    ):
        raise ValueError(
            "Set IRIS_DROID_TOKEN to a non-empty ASCII key, the same on server and client"
        )
    if len(token) < 24:
        warnings.warn(
            "Your droid connection key is short. We recommend a longer random key; "
            "see README.md for how to generate one. Your chosen key will still work.",
            UserWarning,
            stacklevel=2,
        )


class TurnReply(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    heard: str
    text: str
    audio: str = ""  # Base64-encoded mono PCM WAV, at the synthesizer's sample rate.
    finished: bool = False


def read_wav(data: bytes, *, input_audio: bool = False) -> tuple[bytes, int]:
    try:
        with wave.open(io.BytesIO(data), "rb") as source:
            rate = source.getframerate()
            frames = source.getnframes()
            if (
                source.getnchannels() != 1
                or source.getsampwidth() != 2
                or source.getcomptype() != "NONE"
                or rate < 8000
                or rate > 48000
                or (input_audio and rate != 16000)
                or not 0 < frames <= rate * 300
            ):
                raise ValueError("Expected mono 16-bit PCM WAV, at 16 kHz for microphone input")
            pcm = source.readframes(frames)
            if len(pcm) != frames * 2:
                raise ValueError("Truncated WAV recording")
            return pcm, rate
    except (wave.Error, EOFError) as error:
        raise ValueError("Invalid WAV recording") from error


def write_wav(pcm: bytes, rate: int) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(rate)
        target.writeframes(pcm)
    return output.getvalue()
