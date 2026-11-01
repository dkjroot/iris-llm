"""Keep dropped microphone audio from being sent as an apparently valid turn."""

from __future__ import annotations

from typing import Protocol

DEFAULT_MICROPHONE_BUFFER = 8192


def validate_capture_options(
    *,
    buffer_frames: int = DEFAULT_MICROPHONE_BUFFER,
    pause_threshold: float = 1.2,
    sample_rate: int | None = None,
    energy_threshold: float | None = None,
    calibration_duration: float = 0.6,
) -> None:
    if not 256 <= buffer_frames <= 65536:
        raise ValueError("Microphone buffer must be between 256 and 65536 frames")
    if not 0.5 <= pause_threshold <= 10:
        raise ValueError("Pause threshold must be between 0.5 and 10 seconds")
    if sample_rate is not None and not 8000 <= sample_rate <= 192000:
        raise ValueError("Microphone rate must be between 8000 and 192000 Hz")
    if energy_threshold is not None and not 0 < energy_threshold <= 32768:
        raise ValueError("Energy threshold must be greater than 0 and at most 32768")
    if not 0.1 <= calibration_duration <= 10:
        raise ValueError("Calibration duration must be between 0.1 and 10 seconds")


class CaptureReader(Protocol):
    def read(self, num_frames: int, exception_on_overflow: bool = True) -> bytes: ...


class MicrophoneStream(Protocol):
    pyaudio_stream: CaptureReader

    def close(self) -> None: ...


class CheckedMicrophoneStream:
    def __init__(self, stream: MicrophoneStream) -> None:
        self._stream = stream

    def read(self, size: int) -> bytes:
        try:
            # SpeechRecognition's normal wrapper suppresses this error. Missing
            # chunks shorten the recording and join unrelated samples together.
            data = self._stream.pyaudio_stream.read(size, exception_on_overflow=True)
        except OSError as error:
            if error.errno == -9981:  # PortAudio paInputOverflowed
                raise OSError(
                    "Microphone input overflow: audio was lost; recording discarded. "
                    "Try a larger --microphone-buffer (for example 16384), and check CPU load."
                ) from error
            raise
        if len(data) != size * 2:  # The microphone is mono, signed 16-bit PCM.
            raise OSError("Microphone returned incomplete audio; recording discarded.")
        return data

    def close(self) -> None:
        self._stream.close()
