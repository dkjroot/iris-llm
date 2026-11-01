"""Conversation history and optional skill execution, independent of the model host."""

from __future__ import annotations

import html

import httpx

from iris_chat_engines.backends import ChatBackend, make_backend
from iris_chat_engines.messages import ChatError as ChatError
from iris_chat_engines.messages import Message as Message
from iris_settings import Settings
from iris_tools import ToolRegistry


class ChatEngine:
    def __init__(
        self, settings: Settings, client: httpx.Client, *, backend: ChatBackend | None = None
    ) -> None:
        self.settings = settings
        self.backend = backend if backend is not None else make_backend(settings, client)
        self.tools = ToolRegistry(settings, client)
        self.history: list[Message] = []

    def get_response(self, user_text: str) -> str:
        if not user_text.strip():
            raise ChatError("There isn't any text to send")
        messages = [
            Message(role="system", content=self.settings.system_prompt + self.tools.instructions()),
            *self.history,
            Message(role="user", content=user_text),
        ]
        turn_start = len(messages) - 1
        call_ids: set[str] = set()
        for round_number in range(5):
            reply = self.backend.complete(messages, self.tools.definitions())
            if reply.role != "assistant":
                raise ChatError("The server returned an invalid assistant reply")
            if not reply.tool_calls:
                text = reply.content
                if text is None or not text.strip():
                    raise ChatError("The server returned an empty assistant reply")
                # Only decode for a server explicitly configured to escape HTML.
                passes = {"text": 0, "html": 1, "double-html": 2}[self.settings.reply_encoding]
                for _ in range(passes):
                    text = html.unescape(text)
                messages.append(Message(role="assistant", content=text))
                history = [*self.history, *messages[turn_start:]]
                starts = [index for index, message in enumerate(history) if message.role == "user"]
                limit = self.settings.history_turns
                self.history = history[starts[max(0, len(starts) - limit)] :] if limit else []
                return text
            if not self.tools.skills:
                raise ChatError("The model requested a tool, but tools are disabled")
            if round_number == 4 or len(call_ids) + len(reply.tool_calls) > 8:
                raise ChatError("The model exceeded the tool-call limit; try a simpler question")
            # Preserve the complete exchange, including reasoning needed by some
            # model templates, and trim history only at whole user-turn boundaries.
            messages.append(reply)
            for call in reply.tool_calls:
                if call.id in call_ids:
                    raise ChatError("The model returned duplicate tool-call IDs")
                call_ids.add(call.id)
                messages.append(
                    Message(
                        role="tool",
                        tool_call_id=call.id,
                        content=self.tools.execute(call.function.name, call.function.arguments),
                    )
                )
        raise ChatError("The model didn't finish its response")
