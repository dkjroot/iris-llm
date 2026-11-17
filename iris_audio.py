"""Keep the audio format explicit instead of guessing from the microphone."""

from __future__ import annotations

import io
import wave
from typing import TYPE_CHECKING

import numpy as np
import numpy.typing as npt

if TYPE_CHECKING:
    from speech_recognition import AudioData


def pcm_samples(audio: AudioData) -> npt.NDArray[np.float32]:
    raw = audio.get_raw_data(convert_rate=16000, convert_width=2)
    # SpeechRecognition supplies signed little-endian PCM, not floats in [-1, 1].
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


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
