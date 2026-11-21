"""One touch request at a time, consumed by the voice client's own turn loop."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from threading import Event, Lock


@lru_cache(maxsize=4)
def touch_commands(target: str) -> frozenset[str]:
    catalog = json.loads(files("iris_droid").joinpath("web/commands.json").read_text("utf-8"))
    return frozenset(
        phrase for phrase, entry in catalog["tap_commands"].items() if target in entry["targets"]
    )


class TouchCommands:
    def __init__(self) -> None:
        self.interrupt = Event()
        self._lock = Lock()
        self._accepting = False
        self._pending: str | None = None
        self.allowed = touch_commands("client")

    @property
    def ready(self) -> bool:
        with self._lock:
            return self._accepting and self._pending is None

    def listen(self) -> None:
        with self._lock:
            self._accepting = True

    def submit(self, phrase: str) -> bool:
        with self._lock:
            if phrase not in self.allowed or not self._accepting or self._pending is not None:
                return False
            self._pending = phrase
            self.interrupt.set()
            return True

    def take(self) -> str | None:
        with self._lock:
            phrase = self._pending
            if phrase is not None:
                self._pending = None
                self._accepting = False
                self.interrupt.clear()
            return phrase

    def begin_voice(self) -> bool:
        with self._lock:
            if self._pending is not None:
                return False
            self._accepting = False
            return True

    def close(self) -> None:
        with self._lock:
            self._accepting = False
            self._pending = None
            self.interrupt.clear()
