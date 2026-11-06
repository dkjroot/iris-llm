"""Exact spoken memory controls and a short-lived, source-bound deletion request."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

MemoryAction = Literal[
    "private_on",
    "private_off",
    "status",
    "forget",
    "forget_recalled",
    "confirm_forget",
    "cancel_forget",
]


def memory_command(text: str) -> MemoryAction | None:
    phrase = " ".join(text.casefold().split()).strip(" .!?,")
    commands: dict[str, MemoryAction] = {
        "private mode on": "private_on",
        "start a private conversation": "private_on",
        "private mode off": "private_off",
        "end private conversation": "private_off",
        "end the private conversation": "private_off",
        "memory status": "status",
        "forget this conversation": "forget",
        "forget the current conversation": "forget",
        "confirm forget this conversation": "confirm_forget",
        "forget that conversation": "forget_recalled",
        "forget the recalled conversation": "forget_recalled",
        "cancel forgetting": "cancel_forget",
    }
    return commands.get(phrase)


@dataclass(frozen=True)
class PendingForget:
    conversation: str
    revision: str | None
    count: int
    expires_at: datetime
    epoch: int = 0
    scope: tuple[tuple[str, str | None], ...] = ()
    require_active: bool = True


class MemoryPolicyError(RuntimeError):
    """The archive policy or deletion target changed; do not commit this turn."""
