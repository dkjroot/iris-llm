"""A cooperative turn deadline shared by synchronous orchestration and adapters.

Native inference is not interrupted. Once it returns, expiry prevents subsequent
work or commits. Already accepted external effects cannot be undone or replayed.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from threading import Event
from time import monotonic
from typing import ParamSpec, TypeVar
from uuid import uuid4

P = ParamSpec("P")
T = TypeVar("T")
_PERFORMANCE = logging.getLogger("iris.performance")
_CORRELATION: ContextVar[str | None] = ContextVar("iris_diagnostic_turn", default=None)
_STAGES = frozenset(
    {
        "turn",
        "queue",
        "application_queue",
        "recognition",
        "memory_interpretation",
        "summary",
        "indexing",
        "retrieval",
        "model",
        "tts",
        "stream_delivery",
        "playback",
        "archive_commit",
    }
)


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


CURRENT_STOP: ContextVar[Event | None] = ContextVar("iris_turn_stop", default=None)


def remaining_timeout(limit: float) -> float:
    stop = CURRENT_STOP.get()
    if stop is not None and stop.is_set():
        raise TurnExpired("Iris is shutting down")
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


# This logger is independent of prompt tracing. Enable iris.performance at INFO
# to collect durations without enabling conversation text in diagnostic output.
@contextmanager
def diagnostic_turn() -> Iterator[None]:
    token = _CORRELATION.set(_CORRELATION.get() or uuid4().hex)
    try:
        yield
    finally:
        _CORRELATION.reset(token)


@contextmanager
def stage(name: str) -> Iterator[None]:
    """Emit only fixed stage names, local durations and safe outcome categories.

    Nested durations are inclusive. Never pass text, identifiers from a request,
    exception messages, tool arguments or credentials to this interface.
    """
    if name not in _STAGES:
        raise ValueError("Unknown diagnostic stage")
    if not _PERFORMANCE.isEnabledFor(logging.INFO):
        yield
        return
    started = monotonic()
    outcome = "ok"
    try:
        yield
    except BaseException as error:
        outcome = "expired" if isinstance(error, TurnExpired) else "failed"
        raise
    finally:
        _PERFORMANCE.info(
            "iris_stage %s",
            json.dumps(
                {
                    "turn": _CORRELATION.get(),
                    "stage": name,
                    "duration_ms": round((monotonic() - started) * 1000, 3),
                    "outcome": outcome,
                },
                separators=(",", ":"),
            ),
        )


def measured(name: str) -> Callable[[Callable[P, T]], Callable[P, T]]:
    def decorate(operation: Callable[P, T]) -> Callable[P, T]:
        @wraps(operation)
        def invoke(*args: P.args, **kwargs: P.kwargs) -> T:
            with diagnostic_turn(), stage(name):
                return operation(*args, **kwargs)

        return invoke

    return decorate


def configure_diagnostics() -> None:
    """Opt into timing events independently of raw prompt/response tracing."""
    if os.environ.get("IRIS_PERFORMANCE_LOG") == "1":
        logging.basicConfig(level=logging.WARNING, format="%(message)s")
        _PERFORMANCE.setLevel(logging.INFO)
