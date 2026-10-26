"""Local small-vocabulary recognition; background audio never leaves the droid."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


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
        detector = self.recognizer_type(self.model, 16000, json.dumps([self.phrase, "[unk]"]))
        print(f'(waiting for "{self.phrase}"...)', flush=True)
        while True:
            data = source.stream.read(source.CHUNK)
            if not data:
                raise OSError("Microphone stopped during wake detection")
            if detector.AcceptWaveform(data):
                result = json.loads(detector.Result())
                if result.get("text") == self.phrase:
                    return
