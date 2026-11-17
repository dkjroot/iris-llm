"""Companion personality, conversation seeds and client-owned silence timing."""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from queue import Empty, Queue
from threading import Thread
from time import monotonic

from iris_domain.companion import (
    COMPANION_PROMPT as COMPANION_PROMPT,
)
from iris_domain.companion import (
    ICE_BREAKERS as ICE_BREAKERS,
)


class CompanionTimer:
    """No timer during replies; ignored openers double the next random interval."""

    def __init__(
        self,
        minimum: float = 90,
        maximum: float = 240,
        *,
        clock: Callable[[], float] = monotonic,
        uniform: Callable[[float, float], float] = random.uniform,
    ) -> None:
        if not (
            math.isfinite(minimum) and math.isfinite(maximum) and 5 <= minimum <= maximum <= 3600
        ):
            raise ValueError("Companion idle range must satisfy 5 <= min <= max <= 3600 seconds")
        self.minimum, self.maximum = minimum, maximum
        self.clock, self.uniform = clock, uniform
        self.enabled = False
        self.unanswered = 0
        self._deadline = 0.0

    def update(self, enabled: bool, *, proactive: bool = False) -> None:
        self.enabled = enabled
        self.unanswered = min(3, self.unanswered + 1) if proactive and enabled else 0
        self._deadline = (
            self.clock() + self.uniform(self.minimum, self.maximum) * 2**self.unanswered
            if enabled
            else 0.0
        )

    def remaining(self) -> float:
        return max(0.0, self._deadline - self.clock()) if self.enabled else 0.0


class ConsoleInput:
    """Keep a single pending input call across idle timeouts, including on Windows."""

    def __init__(self) -> None:
        self._pending = False
        self._results: Queue[str | BaseException] = Queue()

    def read(self, prompt: str, timeout: float | None = None) -> str | None:
        if timeout is None and not self._pending:
            return input(prompt)
        if not self._pending:
            self._pending = True

            def read_line() -> None:
                try:
                    self._results.put(input(prompt))
                except (EOFError, KeyboardInterrupt) as error:
                    self._results.put(error)

            Thread(target=read_line, daemon=True).start()
        try:
            result = self._results.get(timeout=timeout)
        except Empty:
            return None
        self._pending = False
        if isinstance(result, BaseException):
            raise result
        return result
