"""A chat engine takes text; microphone capture belongs to the conversation loop."""

from typing import Protocol


class IChatEngine(Protocol):
    def get_response(self, user_text: str) -> str: ...
