"""Optional online recognition: only constructed after explicit remote opt-in."""

from speech_recognition import AudioData, Recognizer

from iris_settings import Settings
from iris_turn import check_deadline, remaining_timeout


class STT:
    def __init__(self, settings: Settings) -> None:
        self.recognizer = Recognizer()
        self.recognizer.operation_timeout = settings.timeout
        self.timeout = settings.timeout
        self.language = settings.language or "en-US"

    def recognize(self, audio: AudioData) -> str:
        self.recognizer.operation_timeout = remaining_timeout(self.timeout)
        try:
            result = str(self.recognizer.recognize_google(audio, language=self.language))
        finally:
            check_deadline()
        return result

    def is_offline(self) -> bool:
        return False
