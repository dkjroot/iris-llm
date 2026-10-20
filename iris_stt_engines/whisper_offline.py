"""Whisper recognition, with downloads kept in the explicit setup step."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, cast

import numpy as np
import numpy.typing as npt
from speech_recognition import AudioData

from iris_audio import pcm_samples
from iris_settings import Settings


class Segment(Protocol):
    text: str


class WhisperBackend(Protocol):
    def transcribe(
        self, audio: npt.NDArray[np.float32], *, language: str | None, vad_filter: bool
    ) -> tuple[Iterable[Segment], object]: ...


def load_model(settings: Settings, *, download: bool = False) -> WhisperBackend:
    from faster_whisper import WhisperModel

    return cast(
        WhisperBackend,
        WhisperModel(
            settings.whisper_model,
            device=settings.device,
            compute_type=settings.compute_type,
            local_files_only=not download,
        ),
    )


class STT:
    def __init__(self, settings: Settings) -> None:
        try:
            self.model = load_model(settings)
        except (OSError, ValueError) as error:
            raise ValueError(
                "Whisper model isn't available. Run python iris.py --download-models "
                "during setup, or set whisper_model to a local model directory."
            ) from error
        self.language = settings.language

    def recognize(self, audio: AudioData) -> str:
        samples = pcm_samples(audio)
        if not samples.size:
            return ""
        segments, _ = self.model.transcribe(samples, language=self.language, vad_filter=True)
        return " ".join(segment.text.strip() for segment in segments).strip()

    def is_offline(self) -> bool:
        return True
