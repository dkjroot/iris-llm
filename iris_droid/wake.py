"""Local small-vocabulary recognition; background audio never leaves the droid."""

from __future__ import annotations

import json
from pathlib import Path
from threading import Event
from typing import Any

CANCEL_PHRASE = "cancel cancel"


class WakeDetector:
    def __init__(self, model_path: Path, phrase: str) -> None:
        if not model_path.is_dir():
            raise ValueError("Unpack a small Vosk model and pass its folder with --wake-model")
        from vosk import KaldiRecognizer, Model

        self.model = Model(str(model_path))  # Local path only; never auto-download.
        self.recognizer_type = KaldiRecognizer
        self.phrase = " ".join(phrase.casefold().split())
        if not self.phrase or not self.phrase.replace(" ", "").isalpha():
            raise ValueError("The wake phrase must contain words separated by spaces")

    def wait(self, source: Any) -> None:
        detector = self.recognizer_type(
            self.model, source.SAMPLE_RATE, json.dumps([self.phrase, "[unk]"])
        )
        print(f'(waiting for "{self.phrase}"...)', flush=True)
        while True:
            data = source.stream.read(source.CHUNK)
            if not data:
                raise OSError("Microphone stopped during wake detection")
            if detector.AcceptWaveform(data):
                result = json.loads(detector.Result())
                if result.get("text") == self.phrase:
                    return

    def listen_for_cancel(self, source: Any, stop: Event) -> bool:
        import speech_recognition as sr

        detector = self.recognizer_type(self.model, 16000, json.dumps([CANCEL_PHRASE, "[unk]"]))
        converter_state = None
        while not stop.is_set():
            data = source.stream.read(min(source.CHUNK, 2048))
            if not data:
                raise OSError("Microphone stopped during cancel detection")
            if source.SAMPLE_RATE != 16000:
                data, converter_state = sr.audioop.ratecv(
                    data, 2, 1, source.SAMPLE_RATE, 16000, converter_state
                )
            if detector.AcceptWaveform(data):
                if json.loads(detector.Result()).get("text") == CANCEL_PHRASE:
                    return True
        # Check buffered speech at the moment the server is ready for a decision.
        result = json.loads(detector.FinalResult()).get("text")
        return isinstance(result, str) and result == CANCEL_PHRASE
