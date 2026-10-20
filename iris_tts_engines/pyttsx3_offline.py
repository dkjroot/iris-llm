"""Use an installed system voice; no particular Windows registry entry required."""

from __future__ import annotations

import logging

from iris_settings import Settings


class TTS:
    def __init__(self, settings: Settings) -> None:
        import pyttsx3

        self.engine = pyttsx3.init()
        self.engine.setProperty("rate", settings.rate)
        if settings.voice:
            voices = self.engine.getProperty("voices")
            if any(voice.id == settings.voice for voice in voices):
                self.engine.setProperty("voice", settings.voice)
            else:
                logging.getLogger(__name__).warning(
                    "Configured voice isn't installed; using the system default voice"
                )

    def speak(self, text: str) -> None:
        # SAPI markup isn't portable, and ordinary replies may contain angle brackets.
        self.engine.say(text)
        self.engine.runAndWait()

    def close(self) -> None:
        self.engine.stop()
