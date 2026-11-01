"""Client-owned feedback; the speaker is idle before capture or reply playback."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from importlib.resources import files
from types import TracebackType
from typing import Protocol


class WavPlayer(Protocol):
    def __call__(self, data: bytes, device: int | None = None, *, loop: bool = False) -> None: ...


class ClientFeedback:
    def __init__(
        self,
        speaker: int | None,
        player: WavPlayer,
        stop: Callable[[], None],
        *,
        enabled: bool = True,
    ) -> None:
        self.speaker = speaker
        self.player = player
        self.stop = stop
        self.enabled = enabled
        self._working = False
        self._cues: dict[str, bytes] = {}

    def _load(self, name: str) -> bytes:
        if name not in self._cues:
            self._cues[name] = files("iris_sounds").joinpath(name).read_bytes()
        return self._cues[name]

    def _unavailable(self, error: Exception) -> None:
        if self.enabled:
            print(f"Feedback sounds unavailable ({error}); continuing without cues.", flush=True)
        self.enabled = False

    def _cue(self, name: str) -> None:
        self.stop_working()
        if self.enabled:
            try:
                self.player(self._load(name), self.speaker)
            except (OSError, ValueError, ImportError) as error:
                self._unavailable(error)

    def ready(self) -> None:
        self._cue("chirp.wav")

    def listening(self) -> None:
        self._cue("bleep.wav")

    def recorded(self) -> None:
        self._cue("longbleep.wav")

    def error(self) -> None:
        self._cue("error.wav")

    def acknowledge(self) -> None:
        self._cue("acknowledge.wav")

    def stop_working(self) -> None:
        if self._working:
            self._working = False
            try:
                self.stop()
            except (OSError, ValueError, ImportError) as error:
                self._unavailable(error)

    @contextmanager
    def working(self) -> Iterator[None]:
        try:
            if self.enabled:
                self._working = True
                try:
                    self.player(self._load("workinglongquiet.wav"), self.speaker, loop=True)
                except (OSError, ValueError, ImportError) as error:
                    self.stop_working()
                    self._unavailable(error)
            yield
        finally:
            self.stop_working()

    def __enter__(self) -> ClientFeedback:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.stop_working()
        if exc is not None and not isinstance(exc, (KeyboardInterrupt, EOFError)):
            self.error()
