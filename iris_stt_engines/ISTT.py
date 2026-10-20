"""The bits of a recogniser the conversation loop needs."""

from typing import Protocol

from speech_recognition import AudioData


class ISTT(Protocol):
    def recognize(self, audio: AudioData) -> str: ...
    def is_offline(self) -> bool: ...
