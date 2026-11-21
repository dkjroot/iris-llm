"""Acceptance gate for provisional voice turns."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from threading import Event, Lock
from time import monotonic

from iris_domain.replies import (
    TurnCancelled,
)
from iris_turn import check_deadline, remaining_timeout


class VoiceDecision:
    def __init__(self, notify: Callable[[str], None]) -> None:
        self._notify = notify
        self.on_accept: Callable[[], None] | None = None
        self._decided = Event()
        self._lock = Lock()
        self._accept: bool | None = None
        self._ready = False
        self._failed = False

    def decide(self, accept: bool) -> bool:
        with self._lock:
            if self._failed or (accept and not self._ready):
                return False
            if self._accept is None:
                self._accept = accept
                if not accept:
                    # Wake the client even if the current inference call is blocked.
                    with suppress(OSError, ValueError):
                        self._notify("cancelled")
                self._decided.set()
            return self._accept == accept

    def close(self) -> None:
        with self._lock:
            self._failed = True

    def fail(self, notify: Callable[[], None]) -> None:
        with self._lock:
            if self._accept is not False:
                self._failed = True
                notify()

    def check(self) -> None:
        if self._decided.is_set() and self._accept is False:
            raise TurnCancelled()
        check_deadline()

    @property
    def accepted(self) -> bool:
        return self._accept is True

    def require_acceptance(self) -> None:
        if not self._decided.is_set():
            try:
                with self._lock:
                    if self._accept is None:
                        self._notify("ready")
                        self._ready = True
            except OSError as error:
                self.decide(False)
                raise TurnCancelled() from error
            expires = monotonic() + remaining_timeout(15)
            while not self._decided.wait(min(0.1, max(0, expires - monotonic()))):
                self.check()
                if monotonic() >= expires:
                    self.decide(False)
                    break
        self.check()
        if self.accepted and self.on_accept is not None:
            self.on_accept()
