"""Per-session conversation state and owner-scoped privacy policy tracking."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from iris_chat_engines.compatible import ChatEngine


class SessionStore:
    """Bounded LRU store of active chat sessions and owner privacy policies."""

    def __init__(
        self,
        make_chat: Callable[[], ChatEngine],
        *,
        max_sessions: int = 16,
        default_owner: str = "",
    ) -> None:
        self._make_chat = make_chat
        self._max_sessions = max_sessions
        self._default_owner = default_owner
        self.sessions: OrderedDict[str, ChatEngine] = OrderedDict()
        self._policies: dict[str, tuple[bool, int]] = {}

    def get(self, session: str) -> ChatEngine | None:
        return self.sessions.get(session)

    def session(self, session: str) -> ChatEngine:
        if session not in self.sessions:
            if len(self.sessions) >= self._max_sessions:
                self.sessions.popitem(last=False)
            self.sessions[session] = self._make_chat()
        self.sessions.move_to_end(session)
        return self.sessions[session]

    def reset_all(self) -> None:
        for session, old in list(self.sessions.items()):
            fresh = self._make_chat()
            fresh.companion_mode = old.companion_mode
            self.sessions[session] = fresh

    def pop(self, session: str) -> ChatEngine | None:
        return self.sessions.pop(session, None)

    def snapshot(self) -> OrderedDict[str, ChatEngine]:
        return self.sessions.copy()

    def restore(self, sessions: OrderedDict[str, ChatEngine]) -> None:
        self.sessions = sessions

    def policy(self, owner: str | None = None) -> tuple[bool, int]:
        key = self._default_owner if owner is None else owner
        return self._policies.get(key, (False, 0))

    def set_policy(self, private: bool, epoch: int, *, owner: str | None = None) -> bool:
        key = self._default_owner if owner is None else owner
        next_policy = (private, epoch)
        changed = self._policies.get(key, (False, 0)) != next_policy
        self._policies[key] = next_policy
        return changed
