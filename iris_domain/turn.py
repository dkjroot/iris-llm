"""Inputs and effects belonging to a single application turn.

Effects are deliberately outside session rollback: a failed reply cannot undo a
request already sent to a device. Receipts contain no arguments or private text.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Literal

from iris_domain.personality import PersonalityContext


@dataclass(frozen=True)
class TurnInput:
    session: str
    body: bytes
    text_only: bool = False
    wake_conversation: bool = False
    companion_idle: bool = False
    volume_supported: bool = False
    face: bool = False
    personality: PersonalityContext | None = None


@dataclass
class EffectReceipt:
    tool: str
    outcome: Literal["attempted", "returned", "uncertain"] = "attempted"


@dataclass
class TurnEffects:
    receipts: list[EffectReceipt] = field(default_factory=list)
    attempted: set[str] = field(default_factory=set)
    blocked: set[str] = field(default_factory=set)


@dataclass
class TurnExecution:
    phase: Literal["prepared", "accepted", "completed", "discarded", "failed"] = "prepared"
    effects: TurnEffects = field(default_factory=TurnEffects)


CURRENT_EFFECTS: ContextVar[TurnEffects | None] = ContextVar("iris_turn_effects", default=None)


@contextmanager
def effect_scope() -> Iterator[TurnEffects]:
    existing = CURRENT_EFFECTS.get()
    effects = existing if existing is not None else TurnEffects()
    token = CURRENT_EFFECTS.set(effects)
    try:
        yield effects
    finally:
        if existing is None:
            effects.attempted.clear()
            effects.blocked.clear()
        CURRENT_EFFECTS.reset(token)
