"""Keep the audio format explicit instead of guessing from the microphone."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import numpy.typing as npt

if TYPE_CHECKING:
    from speech_recognition import AudioData


def pcm_samples(audio: AudioData) -> npt.NDArray[np.float32]:
    raw = audio.get_raw_data(convert_rate=16000, convert_width=2)
    # SpeechRecognition supplies signed little-endian PCM, not floats in [-1, 1].
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
