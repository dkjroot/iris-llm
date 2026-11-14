"""Select the server's local speech synthesizer."""

from collections.abc import Generator, Iterator
from contextlib import contextmanager
from typing import Protocol

import numpy as np
import numpy.typing as npt

from iris_settings import Settings


class Synthesizer(Protocol):
    def synthesize(self, text: str) -> tuple[npt.NDArray[np.float32], int]: ...


class StreamingSynthesizer(Synthesizer, Protocol):
    @property
    def sample_rate(self) -> int: ...

    def stream(self, text: str) -> Generator[bytes]: ...


@contextmanager
def open_tts(settings: Settings) -> Iterator[Synthesizer]:
    if settings.tts_engine == "chatterbox":
        from iris_tts_engines.chatterbox import open_chatterbox

        with open_chatterbox(settings) as tts:
            yield tts
    elif settings.tts_engine == "pocket":
        from iris_tts_engines.pocket import open_pocket

        with open_pocket(settings) as tts:
            yield tts
    else:
        from iris_tts_engines.kokoro_offline import TTS

        yield TTS(settings)
