"""Keep dropped microphone audio from being sent as an apparently valid turn."""

from __future__ import annotations

from typing import Protocol

DEFAULT_MICROPHONE_BUFFER = 8192


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
