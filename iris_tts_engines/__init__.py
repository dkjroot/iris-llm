"""Select the server's local speech synthesizer."""

from collections.abc import Iterator
from contextlib import contextmanager

from iris_domain.audio import (
    StreamingSynthesizer as StreamingSynthesizer,
)
from iris_domain.audio import (
    Synthesizer as Synthesizer,
)
from iris_settings import Settings


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
