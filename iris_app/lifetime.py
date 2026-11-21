"""Cooperative shutdown without closing borrowed resources beneath active work."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from threading import Condition, Event
from time import monotonic

from iris_turn import CURRENT_STOP, TurnExpired


class ApplicationLifetime:
    def __init__(self) -> None:
        self.stopping = Event()
        self._condition = Condition()
        self._active = 0
        self._cleanup: Callable[[], None] | None = None

    def own(self, cleanup: Callable[[], None]) -> None:
        """Transfer each resource cleanup before admitting requests."""
        with self._condition:
            if self.stopping.is_set() or self._active:
                raise RuntimeError("Resources must be attached before serving turns")
            previous = self._cleanup
            if previous is None:
                self._cleanup = cleanup
            else:

                def close_all() -> None:
                    try:
                        cleanup()
                    finally:
                        previous()

                self._cleanup = close_all

    def _take_cleanup(self) -> Callable[[], None] | None:
        if self.stopping.is_set() and self._active == 0:
            cleanup, self._cleanup = self._cleanup, None
            return cleanup
        return None

    @contextmanager
    def lease(self) -> Iterator[None]:
        with self._condition:
            if self.stopping.is_set():
                raise TurnExpired("Iris is shutting down")
            self._active += 1
        token = CURRENT_STOP.set(self.stopping)
        try:
            yield
        finally:
            CURRENT_STOP.reset(token)
            with self._condition:
                self._active -= 1
                cleanup = self._take_cleanup()
                self._condition.notify_all()
            if cleanup is not None:
                cleanup()

    def close(self, timeout: float = 5) -> bool:
        """Stop admission; a stuck native call retains ownership until it returns.

        Python cannot safely kill native inference. The wait is bounded and the
        last lease performs deferred cleanup, exactly once, after inference exits.
        """
        expires = monotonic() + timeout
        with self._condition:
            self.stopping.set()
            while self._active and (remaining := expires - monotonic()) > 0:
                self._condition.wait(remaining)
            drained = self._active == 0
            cleanup = self._take_cleanup()
        if cleanup is not None:
            cleanup()
        return drained
