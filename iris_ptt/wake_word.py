"""The original two-stage wake idea, still using local transcription.

A dedicated wake detector would be cheaper. For now this deliberately reuses
our local recogniser and never sends background listening to an online STT.
"""

from __future__ import annotations

import speech_recognition as sr

from iris_settings import Settings
from iris_stt_engines.ISTT import ISTT


class PTT:
    def __init__(self, settings: Settings, stt: ISTT) -> None:
        if not stt.is_offline():
            raise ValueError("Wake mode needs a local STT engine; use enter with Google STT")
        self.settings = settings
        self.stt = stt
        self.awake = False
        self.recognizer = sr.Recognizer()

    def wait_to_talk(self) -> bool:
        print("(awaiting wakeword...)", flush=True)
        with sr.Microphone(
            device_index=self.settings.microphone_index, sample_rate=16000
        ) as source:
            self.recognizer.adjust_for_ambient_noise(source, duration=0.2)
            while True:
                try:
                    audio = self.recognizer.listen(
                        source,
                        timeout=self.settings.listen_timeout,
                        phrase_time_limit=self.settings.phrase_time_limit,
                    )
                    text = self.stt.recognize(audio).lower().strip(" .,!?:;")
                except (sr.WaitTimeoutError, sr.UnknownValueError):
                    continue
                if text in {
                    "stop paying attention",
                    "iris stop paying attention",
                    "stop paying attention iris",
                }:
                    self.awake = False
                elif text in {"pay attention iris", "iris pay attention"}:
                    self.awake = True
                    return True
                elif self.awake and text in {"iris", "hey iris"}:
                    return True
