"""Speech output can be swapped without changing the conversation loop."""

from typing import Protocol


class ITTS(Protocol):
    def speak(self, text: str) -> None: ...
    def close(self) -> None: ...
