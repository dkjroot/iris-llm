"""Server audio capabilities available to the application."""

from collections.abc import Generator
from dataclasses import dataclass
from typing import Protocol

import numpy as np
import numpy.typing as npt


@dataclass(frozen=True)
class AudioBuffer:
    pcm: bytes
    sample_rate: int
    channels: int = 1
    sample_width: int = 2


class Synthesizer(Protocol):
    def synthesize(self, text: str) -> tuple[npt.NDArray[np.float32], int]: ...


class StreamingSynthesizer(Synthesizer, Protocol):
    @property
    def sample_rate(self) -> int: ...

    def stream(self, text: str) -> Generator[bytes]: ...
