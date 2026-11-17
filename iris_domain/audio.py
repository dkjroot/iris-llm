"""Server audio capabilities available to the application."""

from collections.abc import Generator
from typing import Protocol

import numpy as np
import numpy.typing as npt


class Synthesizer(Protocol):
    def synthesize(self, text: str) -> tuple[npt.NDArray[np.float32], int]: ...


class StreamingSynthesizer(Synthesizer, Protocol):
    @property
    def sample_rate(self) -> int: ...

    def stream(self, text: str) -> Generator[bytes]: ...
