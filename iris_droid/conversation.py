"""The client owns whether a wake conversation is open and its idle deadline."""

from __future__ import annotations

from collections.abc import Callable
from time import monotonic


class WakeConversation:
    def __init__(self, idle_seconds: float = 300, clock: Callable[[], float] = monotonic) -> None:
        if not 0 < idle_seconds <= 3600:
            raise ValueError("Conversation timeout must be greater than 0 and at most 3600 seconds")
        self.idle_seconds = idle_seconds
        self.clock = clock
        self.active = False
        self._deadline = 0.0

    def activate(self) -> None:
        self.active = True
        self._deadline = self.clock() + self.idle_seconds

    def sleep(self) -> None:
        self.active = False
        self._deadline = 0.0

    def remaining(self) -> float:
        return max(0.0, self._deadline - self.clock()) if self.active else 0.0
