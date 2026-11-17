"""The complete reversible state of a conversation, separate from its services."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, fields

from iris_domain.memory import PendingDetail, PendingForget, PendingRecall
from iris_domain.messages import Message


@dataclass
class SessionState:
    history: list[Message] = field(default_factory=list)
    memory_revision: tuple[str, str | None] | None = None
    memory_context: str = ""
    memory_sources: set[str] = field(default_factory=set)
    memory_search_calls: int = 0
    pending_recall: PendingRecall | None = None
    pending_forget: PendingForget | None = None
    selected_detail: str | None = None
    pending_detail: PendingDetail | None = None
    last_followup: str | None = None
    last_tool_names: tuple[str, ...] = ()
    companion_mode: bool = False
    last_companion_seed: str | None = None

    def checkpoint(self) -> SessionState:
        """Copy values, never transports, locks or backend instances.

        Field discovery is deliberately restricted to this dataclass contract.
        Adding state here automatically includes it in both snapshot and restore.
        """
        return SessionState(
            **{f.name: deepcopy(getattr(self, f.name)) for f in fields(SessionState)}
        )

    def restore(self, checkpoint: SessionState) -> None:
        for definition in fields(SessionState):
            setattr(self, definition.name, deepcopy(getattr(checkpoint, definition.name)))
