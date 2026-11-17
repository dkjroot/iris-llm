"""Memory control and pending-selection values, independent of SQLite."""

from __future__ import annotations

import re
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


@dataclass(frozen=True)
class DetailCommand:
    action: str
    label: str = ""
    text: str = ""


def detail_command(text: str) -> DetailCommand | None:
    """Deliberate complete utterances; recall questions never become saved assertions."""
    text = " ".join(text.strip().split()).rstrip(".!?")
    fixed = {
        "list remembered details": "list",
        "what have you remembered": "list",
        "confirm memory change": "confirm",
        "cancel memory change": "cancel",
        "forget that detail": "forget_selected",
        "show that detail": "show_selected",
    }
    if text.casefold() in fixed:
        return DetailCommand(fixed[text.casefold()])
    selected = re.fullmatch(r"correct that detail to (.{1,500})", text, re.I)
    if selected:
        return DetailCommand("correct_selected", text=selected[1])
    for pattern, action in (
        (r"remember (.{1,80}?) as (.{1,500})", "remember"),
        (r"correct (.{1,80}?) to (.{1,500})", "correct"),
        (r"forget the detail(?: about)? (.{1,80})", "forget"),
        (r"show the detail(?: about)? (.{1,80})", "show"),
    ):
        match = re.fullmatch(pattern, text, re.I)
        if match:
            return DetailCommand(
                action, match[1].casefold(), match[2] if match.lastindex == 2 else ""
            )
    return None


@dataclass(frozen=True)
class PendingDetail:
    command: DetailCommand
    epoch: int
    conversations: tuple[tuple[str, str | None], ...]
    expires_at: datetime


@dataclass(frozen=True)
class DateWindow:
    start: datetime
    end: datetime
    description: str


@dataclass(frozen=True)
class Candidate:
    conversation: str
    day: str
    label: str
    sources: tuple[str, ...]
    score: int
    semantic_similarity: float = 0.0
    semantic_only: bool = False
    hierarchy_activity: float = 0.0


@dataclass(frozen=True)
class PendingRecall:
    active: str
    candidates: tuple[Candidate, ...]
    expires_at: datetime
    selected: int | None = None
    window: DateWindow | None = None
    query: tuple[str, ...] = ()
