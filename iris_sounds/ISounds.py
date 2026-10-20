"""The feedback cues used by the conversation loop."""

from abc import ABC, abstractmethod


class ISounds(ABC):
    @abstractmethod
    def play_working_sound(self) -> None: ...
    @abstractmethod
    def play_chirp(self) -> None: ...
    @abstractmethod
    def play_bleep(self) -> None: ...
    @abstractmethod
    def play_long_bleep(self) -> None: ...
