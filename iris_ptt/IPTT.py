"""Activation finishes before the conversation opens its microphone."""

from typing import Protocol

from speech_recognition import AudioData, AudioSource


class IPTT(Protocol):
    def wait_to_talk(self) -> bool: ...


class HoldToTalk(Protocol):
    def record(self, source: AudioSource, phrase_time_limit: float) -> AudioData: ...
