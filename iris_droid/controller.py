"""Client conversation state, independent of HTTP, microphones and displays."""

from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock

from iris_domain.personality import personality_command
from iris_domain.replies import TurnReply


class ClientController:
    def __init__(self, personalities: dict[str, str]) -> None:
        self.personalities = personalities
        self.personality = next(iter(personalities), None)
        self.private_mode = False
        self._revision = 0
        self._pending = False
        self.closed = False
        self._lock = Lock()

    @contextmanager
    def turn(self) -> Iterator[None]:
        with self._lock:
            if self.closed or self._pending:
                raise ValueError("Client is closed or already processing a turn")
            self._pending = True
        try:
            yield
        finally:
            with self._lock:
                self._pending = False

    def close(self) -> None:
        with self._lock:
            self.closed = True

    def select(self, name: str) -> bool:
        if name not in self.personalities:
            return False
        with self._lock:
            if self.closed:
                return False
            self.personality = name
            self._revision += 1
        return True

    def selection(self) -> tuple[str | None, int]:
        with self._lock:
            return self.personality, self._revision

    def acknowledge(self, reply: TurnReply, revision: int, *, companion_idle: bool) -> None:
        selected = reply.selected_personality
        if selected is None:
            return
        if (
            companion_idle
            or personality_command(reply.heard) != selected.casefold()
            or selected not in self.personalities
        ):
            raise ValueError("Unexpected personality switch in server reply")
        with self._lock:
            if not self.closed and self._revision == revision:
                self.personality = selected
                self._revision += 1
