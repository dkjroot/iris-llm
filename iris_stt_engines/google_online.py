"""Optional online recognition: only constructed after explicit remote opt-in."""

from speech_recognition import AudioData, Recognizer

from iris_settings import Settings


class STT:
    def __init__(self, settings: Settings) -> None:
        self.recognizer = Recognizer()
        self.language = settings.language or "en-US"

    def recognize(self, audio: AudioData) -> str:
        return str(self.recognizer.recognize_google(audio, language=self.language))

    def is_offline(self) -> bool:
        return False
