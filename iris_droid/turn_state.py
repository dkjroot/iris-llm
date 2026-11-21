"""Explicit client turn phase and origin tracking."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

TurnPhase = Literal["sleeping", "listening", "processing", "speaking"]
TurnOrigin = Literal["voice", "text", "touch", "proactive"]


@dataclass
class ClientTurnState:
    """Explicit client turn state machine replacing implicit boolean flags."""

    phase: TurnPhase = "sleeping"
    origin: TurnOrigin = "voice"

    def begin_listening(self, origin: TurnOrigin = "voice") -> None:
        self.phase = "listening"
        self.origin = origin

    def begin_processing(self, origin: TurnOrigin) -> None:
        self.phase = "processing"
        self.origin = origin

    def begin_speaking(self) -> None:
        self.phase = "speaking"

    def finish_turn(self, *, wake_active: bool) -> None:
        self.phase = "listening" if wake_active else "sleeping"

    def should_activate_wake(self, *, was_wake_active: bool) -> bool:
        """A tapped command in sleeping wake mode must not activate listening."""
        return self.origin != "touch" or was_wake_active
