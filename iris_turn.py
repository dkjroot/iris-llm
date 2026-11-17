"""A cooperative turn deadline shared by synchronous orchestration and adapters.

Native inference is not interrupted. Once it returns, expiry prevents subsequent
work or commits. Already accepted external effects cannot be undone or replayed.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from time import monotonic


class TurnExpired(Exception):
    """The total request budget expired; its effects may already have happened."""


@dataclass(frozen=True)
class Deadline:
    expires: float

    def remaining(self, limit: float) -> float:
        remaining = self.expires - monotonic()
        if remaining <= 0:
            raise TurnExpired("The Iris turn deadline expired")
        return min(limit, remaining)


CURRENT_DEADLINE: ContextVar[Deadline | None] = ContextVar("iris_turn_deadline", default=None)


def remaining_timeout(limit: float) -> float:
    deadline = CURRENT_DEADLINE.get()
    return deadline.remaining(limit) if deadline is not None else limit


def check_deadline() -> None:
    remaining_timeout(float("inf"))


@contextmanager
def turn_budget(seconds: float) -> Iterator[None]:
    """Nested stages inherit the original deadline, never a fresh budget."""
    current = CURRENT_DEADLINE.get()
    token = CURRENT_DEADLINE.set(current or Deadline(monotonic() + seconds))
    try:
        check_deadline()
        yield
    finally:
        CURRENT_DEADLINE.reset(token)
